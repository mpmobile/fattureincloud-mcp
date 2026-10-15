"""Aggregated reports: yearly dashboard, per-client position, numbering check."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from .. import fic
from ..app import ann, tool
from ..listing import (ISSUED_LIST_FIELDS, SITUATION_MAX_PER_TYPE, build_date_clauses, fetch_all_pages,
                       get_total_from_doc, issued_doc_row, payment_status_name, q_escape, resolve_client,
                       today_str)


@tool(annotations=ann(read_only=True, idempotent=True))
def get_situation(
    year: Annotated[int | None, Field(description="Anno (default: corrente)")] = None,
    client_name: Annotated[str | None, Field(description="Filtro per nome cliente (opzionale, ricerca parziale)")] = None,
) -> dict:
    """Dashboard anno: fatturato netto (fatture - NDC), incassato, da incassare, costi, margine.
    Supporta filtro per cliente."""
    year = year or datetime.now().year
    client_filter = (client_name or "").strip()
    year_q = " and ".join(build_date_clauses(year=year))
    q = year_q + (f" and entity.name like '%{q_escape(client_filter)}%'" if client_filter else "")

    # One year is bounded, so fetch every page: partial totals would be wrong totals.
    emesse, _ = fetch_all_pages(fic.issued_api.list_issued_documents, max_results=None,
                                type="invoice", q=q, fields=ISSUED_LIST_FIELDS)
    ndc_docs, _ = fetch_all_pages(fic.issued_api.list_issued_documents, max_results=None,
                                  type="credit_note", q=q, fields=ISSUED_LIST_FIELDS)

    totale_fatturato = totale_incassato = totale_da_incassare = 0
    fatture_non_pagate = []
    for d in emesse:
        totale_fatturato += get_total_from_doc(d)
        for p in d.get('payments_list') or []:
            status = payment_status_name(p)
            if status == 'paid':
                totale_incassato += p.get('amount', 0)
            elif status == 'not_paid':
                totale_da_incassare += p.get('amount', 0)
                fatture_non_pagate.append({
                    "number": d.get("number"),
                    "client": (d.get('entity') or {}).get('name', ''),
                    "amount": p.get('amount', 0),
                    "due_date": str(p.get('due_date', ''))
                })
    totale_ndc = sum(abs(get_total_from_doc(d)) for d in ndc_docs)
    fatturato_netto = totale_fatturato - totale_ndc

    totale_costi = 0
    if not client_filter:
        ricevute, _ = fetch_all_pages(fic.received_api.list_received_documents, max_results=None,
                                      type="expense", q=year_q, fieldset="detailed")
        totale_costi = sum(d.get('amount_gross') or d.get('amount_net') or 0 for d in ricevute)

    fatture_non_pagate.sort(key=lambda x: x.get('due_date', ''))
    today = today_str()
    scadute = [f for f in fatture_non_pagate if f["due_date"] < today]
    in_scadenza = [f for f in fatture_non_pagate if f["due_date"] >= today]
    return {
        "anno": year,
        "filtro_cliente": client_filter or None,
        "fatturato_lordo": round(totale_fatturato, 2),
        "note_credito": round(totale_ndc, 2),
        "fatturato_netto": round(fatturato_netto, 2),
        "incassato": round(totale_incassato, 2),
        "da_incassare": round(totale_da_incassare, 2),
        "costi_totali": round(totale_costi, 2) if not client_filter else "N/A (filtro cliente attivo)",
        "margine_lordo": round(fatturato_netto - totale_costi, 2) if not client_filter else "N/A",
        "scadute": {
            "numero": len(scadute),
            "totale": round(sum(f["amount"] for f in scadute), 2),
            "piu_vecchie": scadute[:10],
        },
        "prossime_scadenze": in_scadenza[:10],
    }


def _invoice_position(invoices, today):
    """Totals over invoice installments: paid, reversed (cancelled, typically by a credit
    note) and not_paid, plus the list of open installments."""
    totals = {"fatturato": 0, "incassato": 0, "da_incassare": 0, "scaduto": 0, "stornato": 0}
    aperte = []
    for d in invoices:
        totals["fatturato"] += get_total_from_doc(d)
        for p in d.get("payments_list") or []:
            amount = p.get("amount", 0) or 0
            status = payment_status_name(p)
            if status == "paid":
                totals["incassato"] += amount
            elif status == "reversed":
                totals["stornato"] += amount
            elif status == "not_paid":
                due = str(p.get("due_date") or "")
                overdue = bool(due and due < today)
                totals["da_incassare"] += amount
                if overdue:
                    totals["scaduto"] += amount
                aperte.append({"id": d.get("id"), "number": d.get("number"), "date": str(d.get("date", "")),
                               "amount": amount, "due_date": due or None, "overdue": overdue})
    aperte.sort(key=lambda x: x["due_date"] or "")
    return totals, aperte


def _proforma_position(proformas, today):
    """A proforma is matured once its document date is reached: it counts in the client
    position. Future-dated proformas are scheduled billing and don't count."""
    matured = [d for d in proformas if str(d.get("date", "")) <= today]
    future = sorted((d for d in proformas if str(d.get("date", "")) > today),
                    key=lambda d: str(d.get("date", "")))
    matured_open = sum(p.get("amount", 0) or 0 for d in matured
                       for p in d.get("payments_list") or [] if payment_status_name(p) == "not_paid")
    maturate = {
        "numero": len(matured),
        "da_incassare": round(matured_open, 2),
        "documenti": [issued_doc_row(d, "proforma") for d in matured],
    }
    future_summary = {
        "numero": len(future),
        "totale": round(sum(get_total_from_doc(d) for d in future), 2),
        "nota": "Non conteggiate nella posizione: data non ancora raggiunta",
        "prossime": [issued_doc_row(d, "proforma") for d in future[:10]],
    }
    return maturate, future_summary, matured_open


@tool(annotations=ann(read_only=True, idempotent=True))
def get_client_situation(
    client_id: Annotated[int | None, Field(description="ID cliente (preferibile)")] = None,
    client_name: Annotated[str | None, Field(description="Nome cliente, ricerca parziale")] = None,
    date_from: Annotated[str | None, Field(description="Data inizio YYYY-MM-DD (default: tutto lo storico)")] = None,
    date_to: Annotated[str | None, Field(description="Data fine YYYY-MM-DD")] = None,
    year: Annotated[int | None, Field(description="Anno, in alternativa a date_from/date_to")] = None,
    include: Annotated[list[Literal["invoice", "credit_note", "proforma"]] | None,
                       Field(description="Tipi da considerare (default: tutti e tre)")] = None,
    include_documents: Annotated[bool, Field(description="Includi l'elenco completo dei documenti (default: false)")] = False,
) -> dict:
    """Situazione di un singolo cliente su tutto lo storico o su un periodo: fatturato, note di credito,
    netto, incassato, da incassare, scaduto, elenco fatture aperte con scadenze e proforma maturate
    (data raggiunta, conteggiate nell'esposizione_totale) e future (non conteggiate). Se client_name
    corrisponde a più clienti restituisce i candidati: richiamare con client_id."""
    client, problem = resolve_client(client_id, client_name)
    if problem:
        return problem
    include = include or ["invoice", "credit_note", "proforma"]
    period_clauses = build_date_clauses(date_from=date_from, date_to=date_to, year=year)
    q = " and ".join(period_clauses + [f"entity.id = {client['id']}"])
    today = today_str()

    docs_by_type, counts, truncated = {}, {}, False
    for doc_type in include:
        docs, total = fetch_all_pages(
            fic.issued_api.list_issued_documents, max_results=SITUATION_MAX_PER_TYPE,
            type=doc_type, q=q, sort="-date", fields=ISSUED_LIST_FIELDS,
        )
        docs_by_type[doc_type] = docs
        counts[doc_type] = total
        truncated = truncated or len(docs) < total

    # Future-dated proformas are scheduled billing, reported in proforma_future: not "issued" yet.
    all_dates = [str(d.get("date")) for docs in docs_by_type.values() for d in docs
                 if d.get("date") and str(d.get("date")) <= today]
    periodo = {k: v for k, v in (("date_from", date_from), ("date_to", date_to), ("year", year)) if v}
    result = {
        "cliente": client,
        "periodo": periodo or "tutto lo storico",
        "numero_documenti": counts,
        "primo_documento": min(all_dates) if all_dates else None,
        "ultimo_documento": max(all_dates) if all_dates else None,
    }

    totals, aperte = _invoice_position(docs_by_type.get("invoice", []), today)
    if "invoice" in include:
        result.update({
            "fatturato_lordo": round(totals["fatturato"], 2),
            "incassato": round(totals["incassato"], 2),
            "da_incassare": round(totals["da_incassare"], 2),
            "di_cui_scaduto": round(totals["scaduto"], 2),
            "stornato": round(totals["stornato"], 2),
            "fatture_aperte": aperte,
        })

    credit_notes = docs_by_type.get("credit_note", [])
    ndc = sum(abs(get_total_from_doc(d)) for d in credit_notes)
    if "credit_note" in include:
        result["note_credito"] = round(ndc, 2)
        ndc_aperte = [{"id": d.get("id"), "number": d.get("number"), "date": str(d.get("date", "")),
                       "total": get_total_from_doc(d)} for d in credit_notes if d.get("next_due_date")]
        if ndc_aperte:
            result["note_credito_non_saldate"] = ndc_aperte
    if "invoice" in include and "credit_note" in include:
        result["fatturato_netto"] = round(totals["fatturato"] - ndc, 2)

    if "proforma" in include:
        maturate, future, matured_open = _proforma_position(docs_by_type.get("proforma", []), today)
        result["proforma_maturate"] = maturate
        result["proforma_future"] = future
        result["esposizione_totale"] = round(totals["da_incassare"] + matured_open, 2)

    if truncated:
        result["attenzione"] = f"Oltre {SITUATION_MAX_PER_TYPE} documenti per tipo: totali parziali, restringere il periodo"
    if include_documents:
        result["documenti"] = sorted(
            (issued_doc_row(d, t) for t, docs in docs_by_type.items() for d in docs),
            key=lambda x: x["date"], reverse=True)
    return result


@tool(annotations=ann(read_only=True, idempotent=True))
def check_numeration(
    year: Annotated[int, Field(description="Anno da verificare (es. 2025)")],
) -> dict:
    """Verifica continuità numerica delle fatture emesse per un dato anno."""
    docs, _ = fetch_all_pages(fic.issued_api.list_issued_documents, max_results=None,
                              type="invoice", q=" and ".join(build_date_clauses(year=year)))
    if not docs:
        return {"year": year, "status": "Nessuna fattura trovata per questo anno"}
    numbers = sorted(set(
        d.get("number") for d in docs
        if d.get("number") is not None and d.get("number") > 0
    ))
    gaps = []
    if numbers:
        if numbers[0] != 1:
            gaps.append({"type": "start", "expected": 1, "actual": numbers[0],
                         "missing": list(range(1, numbers[0])),
                         "note": f"La numerazione parte da {numbers[0]} invece che da 1"})
        for i in range(len(numbers) - 1):
            if numbers[i + 1] - numbers[i] > 1:
                missing = list(range(numbers[i] + 1, numbers[i + 1]))
                gaps.append({"type": "gap", "after": numbers[i], "before": numbers[i + 1],
                             "missing": missing,
                             "note": f"Mancano i numeri {missing} tra fattura {numbers[i]} e {numbers[i+1]}"})
    return {
        "year": year, "total_invoices": len(numbers),
        "first_number": numbers[0] if numbers else None,
        "last_number": numbers[-1] if numbers else None,
        "continuous": len(gaps) == 0,
        "status": "✓ Numerazione continua" if len(gaps) == 0 else f"⚠ Trovati {len(gaps)} problemi",
        "gaps": gaps
    }
