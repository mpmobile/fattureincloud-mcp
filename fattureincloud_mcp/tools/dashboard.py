"""Tools that feed the dashboards. Each returns pre-aggregated data in one call.

get_situazione_crediti: receivables (yearly totals, revenue and exposure rankings by client)."""

import time
from collections import defaultdict
from datetime import date as Date, datetime, timezone
from typing import Annotated

from fattureincloud_python_sdk.exceptions import ApiException
from pydantic import Field

from .. import fic
from ..app import ann, tool
from ..listing import (ISSUED_LIST_FIELDS, fetch_all_pages, get_total_from_doc, parse_date, payment_status_name,
                       today_str)

CACHE_TTL_SECONDS = 300  # the dashboard calls the tool every time it opens
SAFETY_CAP = 20000       # documents per query; reaching it means totals may be partial
AGING_BUCKETS = (("1_30", 30), ("31_90", 90), ("91_365", 365), ("oltre_365", None))

_cache = {}


def _empty_aging():
    return {name: 0.0 for name, _ in AGING_BUCKETS}


def _aging_bucket(days_overdue):
    for name, upper in AGING_BUCKETS:
        if upper is None or days_overdue <= upper:
            return name


def _client_key(d):
    """Group by client id; documents without a registry client are grouped by their trimmed name."""
    entity = d.get("entity") or {}
    if entity.get("id"):
        return str(entity["id"]), (entity.get("name") or "").strip()
    return f"nome:{(entity.get('name') or '').strip()}", (entity.get("name") or "").strip()


def _fetch(warnings, label, **kwargs):
    docs, total = fetch_all_pages(fic.issued_api.list_issued_documents, max_results=SAFETY_CAP,
                                  fields=ISSUED_LIST_FIELDS, **kwargs)
    if len(docs) < total:
        warnings.append(f"{label}: letti {len(docs)} documenti su {total}, totali parziali")
    return docs


def _api_error_message(e):
    status = getattr(e, "status", None)
    messages = {
        401: "Token FattureInCloud non valido, scaduto o revocato: rigenerarlo e aggiornare FIC_ACCESS_TOKEN.",
        403: "Il token FattureInCloud non ha i permessi necessari (servono issued_documents ed entity.clients in lettura).",
        429: "Limite di richieste FattureInCloud raggiunto (1000 all'ora): riprovare più tardi.",
    }
    if status in messages:
        return messages[status]
    if status and status >= 500:
        return f"FattureInCloud non disponibile (errore {status}): riprovare più tardi."
    return f"Errore FattureInCloud ({status}): {getattr(e, 'reason', e)}"


def _compute(year, as_of, open_from, top_n, include_proforma_maturate):
    warnings = []
    as_of_date = Date.fromisoformat(as_of)
    year_q = f"date >= '{year}-01-01' and date <= '{year}-12-31'"
    open_q = "next_due_date != null" + (f" and date >= '{open_from}'" if open_from else "")

    clients = {str(c["id"]): (c.get("name") or "").strip()
               for c in fetch_all_pages(fic.clients_api.list_clients, max_results=None, fields="id,name")[0]}
    names = {}

    def name_of(key, fallback):
        return clients.get(key) or names.get(key) or fallback

    # --- Revenue of the year -------------------------------------------------
    revenue = defaultdict(lambda: {"fatture": 0.0, "note_credito": 0.0, "incassato": 0.0})
    totals = {"fatturato_lordo": 0.0, "note_credito": 0.0, "incassato": 0.0, "da_incassare": 0.0}
    for d in _fetch(warnings, f"fatture {year}", type="invoice", q=year_q):
        key, name = _client_key(d)
        names.setdefault(key, name)
        amount = get_total_from_doc(d)
        revenue[key]["fatture"] += amount
        totals["fatturato_lordo"] += amount
        for p in d.get("payments_list") or []:
            status = payment_status_name(p)
            if status == "paid":
                revenue[key]["incassato"] += p.get("amount", 0) or 0
                totals["incassato"] += p.get("amount", 0) or 0
            elif status == "not_paid":
                totals["da_incassare"] += p.get("amount", 0) or 0
    for d in _fetch(warnings, f"note di credito {year}", type="credit_note", q=year_q):
        key, name = _client_key(d)
        names.setdefault(key, name)
        amount = abs(get_total_from_doc(d))
        revenue[key]["note_credito"] += amount
        totals["note_credito"] += amount
    totals["fatturato_netto"] = totals["fatturato_lordo"] - totals["note_credito"]

    # --- Exposure: open installments, whole history (or from open_from) --------
    exposure = defaultdict(lambda: {"documenti": set(), "scaduto": 0.0, "in_scadenza": 0.0,
                                    "scaduto_dal": None, "aging": _empty_aging()})
    sources = [("fatture aperte", "invoice", open_q)]
    if include_proforma_maturate:
        # A proforma counts once its document date is reached (as for get_client_situation).
        sources.append(("proforma maturate", "proforma",
                        f"date <= '{as_of}'" + (f" and date >= '{open_from}'" if open_from else "")))
    fatture_aperte = proforma_maturate = 0
    for label, doc_type, q in sources:
        for d in _fetch(warnings, label, type=doc_type, q=q):
            key, name = _client_key(d)
            names.setdefault(key, name)
            entry = exposure[key]
            counted = False
            for p in d.get("payments_list") or []:
                if payment_status_name(p) != "not_paid":  # paid and reversed installments are not owed
                    continue
                amount = p.get("amount", 0) or 0
                due = Date.fromisoformat(str(p.get("due_date"))[:10]) if p.get("due_date") else as_of_date
                if due < as_of_date:
                    entry["scaduto"] += amount
                    entry["aging"][_aging_bucket((as_of_date - due).days)] += amount
                    if entry["scaduto_dal"] is None or due.isoformat() < entry["scaduto_dal"]:
                        entry["scaduto_dal"] = due.isoformat()
                else:
                    entry["in_scadenza"] += amount
                counted = True
            if counted:
                entry["documenti"].add((doc_type, d.get("id")))
                if doc_type == "invoice":
                    fatture_aperte += 1
                else:
                    proforma_maturate += 1

    # Unpaid credit notes are not netted against exposure: reported separately.
    ndc_aperte = _fetch(warnings, "note di credito aperte", type="credit_note", q=open_q)
    ndc_aperte_totale = sum(p.get("amount", 0) or 0 for d in ndc_aperte for p in d.get("payments_list") or []
                            if payment_status_name(p) == "not_paid")

    # --- Assemble --------------------------------------------------------------
    r2 = lambda x: round(x, 2)  # noqa: E731
    by_revenue = sorted(
        ({"client_id": key, "client_name": name_of(key, names.get(key, "")),
          "fatture": r2(v["fatture"]), "note_credito": r2(v["note_credito"]),
          "fatturato_netto": r2(v["fatture"] - v["note_credito"]), "incassato": r2(v["incassato"]),
          "quota": round((v["fatture"] - v["note_credito"]) / totals["fatturato_netto"], 4)
          if totals["fatturato_netto"] else 0.0}
         for key, v in revenue.items()),
        key=lambda x: x["fatturato_netto"], reverse=True)
    by_exposure = sorted(
        ({"client_id": key, "client_name": name_of(key, names.get(key, "")),
          "fatture_aperte": len(v["documenti"]), "scaduto": r2(v["scaduto"]), "in_scadenza": r2(v["in_scadenza"]),
          "totale": r2(v["scaduto"] + v["in_scadenza"]), "scaduto_dal": v["scaduto_dal"],
          "aging": {k: r2(a) for k, a in v["aging"].items()}}
         for key, v in exposure.items() if v["scaduto"] + v["in_scadenza"] > 0),
        key=lambda x: x["totale"], reverse=True)
    for pos, row in enumerate(by_revenue, 1):
        row["pos"] = pos
    for pos, row in enumerate(by_exposure, 1):
        row["pos"] = pos

    exposure_by_client = {row["client_id"]: row for row in by_exposure}
    top = []
    for row in by_revenue[:top_n]:
        exp = exposure_by_client.get(row["client_id"], {})
        top.append({"pos": row["pos"], "client_id": row["client_id"], "client_name": row["client_name"],
                    "fatturato_netto": row["fatturato_netto"], "incassato": row["incassato"],
                    "scaduto": exp.get("scaduto", 0.0), "in_scadenza": exp.get("in_scadenza", 0.0),
                    "esposto": exp.get("totale", 0.0)})

    scaduto = sum(v["scaduto"] for v in exposure.values())
    in_scadenza = sum(v["in_scadenza"] for v in exposure.values())
    aging = _empty_aging()
    for v in exposure.values():
        for k, a in v["aging"].items():
            aging[k] += a
    if any(key.startswith("nome:") for key in list(revenue) + list(exposure)):
        warnings.append("Alcuni documenti non hanno un cliente in anagrafica: raggruppati per nome")

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": as_of,
        "year": year,
        "open_from": open_from,
        "include_proforma_maturate": include_proforma_maturate,
        "currency": "EUR",
        "amounts": "gross",
        "totals": {k: r2(totals[k]) for k in
                   ("fatturato_lordo", "note_credito", "fatturato_netto", "incassato", "da_incassare")},
        "exposure": {
            "totale": r2(scaduto + in_scadenza),
            "scaduto": r2(scaduto),
            "in_scadenza": r2(in_scadenza),
            "quota_scaduta": round(scaduto / (scaduto + in_scadenza), 4) if scaduto + in_scadenza else 0.0,
            "clienti": len(by_exposure),
            "clienti_con_scaduto": sum(1 for row in by_exposure if row["scaduto"] > 0),
            "fatture_aperte": fatture_aperte,
            "proforma_maturate": proforma_maturate,
            "note_credito_aperte": len(ndc_aperte),
            "note_credito_aperte_totale": r2(ndc_aperte_totale),
            "aging": {k: r2(a) for k, a in aging.items()},
        },
        "by_revenue": by_revenue,
        "by_exposure": by_exposure,
        "top": top,
        "warnings": warnings,
    }


@tool(annotations=ann(read_only=True, idempotent=True))
def get_situazione_crediti(
    year: Annotated[int | None, Field(description="Anno del fatturato (default: anno corrente)")] = None,
    as_of: Annotated[str | None, Field(description="Data di riferimento YYYY-MM-DD per scaduto / in scadenza (default: oggi)")] = None,
    open_from: Annotated[str | None, Field(
        description="Considera aperte solo le fatture EMESSE da questa data YYYY-MM-DD (default: tutto lo storico)")] = None,
    top_n: Annotated[int, Field(description="Numero di clienti nella sezione top (default: 5)", ge=1)] = 5,
    include_proforma_maturate: Annotated[bool, Field(
        description="Le proforma con data <= as_of entrano nell'esposizione (default: true, come get_client_situation)")] = True,
) -> dict:
    """Situazione crediti clienti in una sola chiamata, per dashboard: totali dell'anno, classifica dei
    clienti per fatturato, classifica per esposizione, top N clienti. Importi lordi (IVA inclusa).
    Esposizione = rate non pagate (not_paid) di tutte le fatture aperte, anche di anni precedenti, più le
    proforma maturate; le rate stornate non contano. Una rata è scaduta se la sua scadenza è precedente
    ad as_of; aging = scaduto per giorni di ritardo. Le note di credito non saldate non sono compensate,
    sono riportate a parte. Raggruppato per client_id. Risultato in cache per 5 minuti."""
    try:
        year = year or datetime.now().year
        as_of = parse_date(as_of, "as_of") if as_of else today_str()
        open_from = parse_date(open_from, "open_from") if open_from else None
    except ValueError as e:
        return {"error": str(e)}

    key = (year, as_of, open_from, top_n, include_proforma_maturate)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        result = _compute(year, as_of, open_from, top_n, include_proforma_maturate)
    except ApiException as e:
        return {"error": _api_error_message(e)}
    except Exception as e:  # the dashboard needs a readable message, not a failed call
        return {"error": f"Errore inatteso: {e}"}
    _cache[key] = (time.monotonic(), result)
    return result
