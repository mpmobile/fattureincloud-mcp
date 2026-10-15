"""Tools that feed the dashboards. Each returns pre-aggregated data in one call.

get_situazione_crediti: receivables (yearly totals, revenue and exposure rankings by client)."""

import time
from collections import defaultdict
from datetime import date as Date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fattureincloud_python_sdk.exceptions import ApiException
from pydantic import Field

from .. import cache, fic
from ..app import ann, tool
from ..listing import (ISSUED_LIST_FIELDS, doc_taxable, fetch_all_pages, fetch_all_pages_parallel, get_total_from_doc,
                       parse_date, payment_status_name, today_str)

CACHE_TTL_SECONDS = 300  # the dashboard calls the tool every time it opens
HISTORY_TTL = timedelta(hours=24)
SAFETY_CAP = 20000       # documents per query; reaching it means totals may be partial
AGING_BUCKETS = (("1_30", 30), ("31_90", 90), ("91_365", 365), ("oltre_365", None))
NO_REGISTRY_PREFIX = "nome:"

_cache = {}


def _empty_aging():
    return {name: 0.0 for name, _ in AGING_BUCKETS}


def _aging_bucket(days_overdue):
    for name, upper in AGING_BUCKETS:
        if upper is None or days_overdue <= upper:
            return name


def _months_between(earlier, later):
    """Whole months from `earlier` to `later` (ISO dates), None if `earlier` is None."""
    if not earlier:
        return None
    a, b = Date.fromisoformat(earlier), Date.fromisoformat(later)
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def _client_key(d):
    """Group by client id; documents without a registry client are grouped by their trimmed name."""
    entity = d.get("entity") or {}
    name = (entity.get("name") or "").strip()
    if entity.get("id"):
        return str(entity["id"]), name
    return f"{NO_REGISTRY_PREFIX}{name}", name


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


class _Exposure:
    """Open installments of one client, kept apart for invoices and matured proformas."""

    def __init__(self):
        self.parts = {kind: {"scaduto": 0.0, "in_scadenza": 0.0, "aging": _empty_aging(), "docs": set(),
                             "scaduto_dal": None, "ultima_scadenza_scaduta": None}
                      for kind in ("fatt", "pf")}

    def add(self, kind, doc_id, amount, due, as_of):
        part = self.parts[kind]
        part["docs"].add(doc_id)
        if due < as_of:
            part["scaduto"] += amount
            part["aging"][_aging_bucket((as_of - due).days)] += amount
            iso = due.isoformat()
            part["scaduto_dal"] = min(part["scaduto_dal"] or iso, iso)
            part["ultima_scadenza_scaduta"] = max(part["ultima_scadenza_scaduta"] or iso, iso)
        else:
            part["in_scadenza"] += amount

    def total(self, kind):
        return self.parts[kind]["scaduto"] + self.parts[kind]["in_scadenza"]

    def combined(self, include_pf):
        """The pre-existing fields: invoices, plus proformas when include_proforma_maturate."""
        kinds = ("fatt", "pf") if include_pf else ("fatt",)
        dates = lambda key, pick: pick((self.parts[k][key] for k in kinds if self.parts[k][key]), default=None)  # noqa: E731
        return {
            "fatture_aperte": sum(len(self.parts[k]["docs"]) for k in kinds),
            "scaduto": sum(self.parts[k]["scaduto"] for k in kinds),
            "in_scadenza": sum(self.parts[k]["in_scadenza"] for k in kinds),
            "aging": {b: sum(self.parts[k]["aging"][b] for k in kinds) for b, _ in AGING_BUCKETS},
            "scaduto_dal": dates("scaduto_dal", min),
            "ultima_scadenza_scaduta": dates("ultima_scadenza_scaduta", max),
        }


def _history_index():
    """Per-client facts that need the whole invoice archive (~60 pages): last invoice date,
    last payment date, and every paid installment (for cash collected in a given year).

    Scanning the archive takes 20-90 s depending on FIC load, so the index is kept in the
    on-disk cache for 24 hours; current-year, open and recent documents are always fetched
    fresh by the caller and merged on top."""
    hit = cache.get("invoice_history_index", fic.COMPANY_ID, ttl=HISTORY_TTL)
    if hit is not None:
        return hit
    fields = ISSUED_LIST_FIELDS + ",items_list"
    invoices, _ = fetch_all_pages_parallel(fic.issued_api.list_issued_documents, type="invoice", fields=fields)
    last_invoice, last_payment, payments = {}, {}, []
    for d in invoices:
        key, _ = _client_key(d)
        doc_date = str(d.get("date", ""))
        last_invoice[key] = max(last_invoice.get(key, doc_date), doc_date)
        gross = get_total_from_doc(d)
        net_factor = doc_taxable(d) / gross if gross else 1.0
        for p in d.get("payments_list") or []:
            if payment_status_name(p) == "paid" and p.get("paid_date"):
                paid = str(p["paid_date"])[:10]
                last_payment[key] = max(last_payment.get(key, paid), paid)
                payments.append([key, doc_date, paid, p.get("amount", 0) or 0, net_factor])
    index = {"last_invoice": last_invoice, "last_payment": last_payment, "payments": payments}
    cache.put("invoice_history_index", fic.COMPANY_ID, index)
    return index


def _compute(year, as_of, open_from, top_n, include_pf, amounts):
    warnings = []
    as_of_date = Date.fromisoformat(as_of)
    year_start, year_end = f"{year}-01-01", f"{year}-12-31"
    r2 = lambda x: round(x, 2)  # noqa: E731

    def factor(d):
        """1 for gross amounts; taxable / gross of the document for net amounts (from its lines,
        each with its own VAT rate)."""
        if amounts == "gross":
            return 1.0
        gross = get_total_from_doc(d)
        return doc_taxable(d) / gross if gross else 1.0

    # --- Fetch: fresh targeted queries + the cached history index ------------------------
    fields = ISSUED_LIST_FIELDS + (",items_list" if amounts == "net" else "")
    year_q = f"date >= '{year_start}' and date <= '{year_end}'"
    open_q = "next_due_date != null" + (f" and date >= '{open_from}'" if open_from else "")
    pf_q = f"date <= '{as_of}'" + (f" and date >= '{open_from}'" if open_from else "")

    def fetch(label, doc_type, q):
        docs, total = fetch_all_pages(fic.issued_api.list_issued_documents, max_results=SAFETY_CAP,
                                      type=doc_type, q=q, fields=fields)
        if len(docs) < total:
            warnings.append(f"{label}: letti {len(docs)} documenti su {total}, totali parziali")
        return docs

    year_invoices = fetch(f"fatture {year}", "invoice", year_q)
    open_invoices = fetch("fatture aperte", "invoice", open_q)
    year_credit_notes = fetch(f"note di credito {year}", "credit_note", year_q)
    open_credit_notes = fetch("note di credito aperte", "credit_note", open_q)
    proformas = fetch("proforma maturate", "proforma", pf_q)
    history = _history_index()
    registry = {str(c["id"]): (c.get("name") or "").strip()
                for c in fetch_all_pages(fic.clients_api.list_clients, max_results=None, fields="id,name")[0]}
    seen_names = {}
    unlinked = {}

    def key_of(d, doc_type):
        key, name = _client_key(d)
        seen_names.setdefault(key, name)
        if key.startswith(NO_REGISTRY_PREFIX):
            unlinked[d.get("id")] = (doc_type, d.get("number"), str(d.get("date", "")), name)
        return key

    def name_of(key):
        return registry.get(key) or seen_names.get(key, "")

    # --- History: last invoice, last payment, cash collected in `year` --------------------
    # Cached index for the archive, fresh documents on top (they may be newer than the index).
    last_invoice, last_payment = dict(history["last_invoice"]), dict(history["last_payment"])
    cash = defaultdict(float)
    for key, doc_date, paid, amount, net_factor in history["payments"]:
        if doc_date < year_start and year_start <= paid <= year_end:  # older invoices paid this year
            cash[key] += amount * (net_factor if amounts == "net" else 1.0)
    for d in year_invoices + open_invoices:
        key, _ = _client_key(d)
        doc_date = str(d.get("date", ""))
        last_invoice[key] = max(last_invoice.get(key, doc_date), doc_date)
        for p in d.get("payments_list") or []:
            if payment_status_name(p) == "paid" and p.get("paid_date"):
                paid = str(p["paid_date"])[:10]
                last_payment[key] = max(last_payment.get(key, paid), paid)
    for d in year_invoices:  # current-year invoices: cash always from fresh data
        key, _ = _client_key(d)
        for p in d.get("payments_list") or []:
            paid = str(p.get("paid_date") or "")[:10]
            if payment_status_name(p) == "paid" and year_start <= paid <= year_end:
                cash[key] += (p.get("amount", 0) or 0) * factor(d)

    # --- Revenue of the year -----------------------------------------------------------
    revenue = defaultdict(lambda: {"importo_fatture": 0.0, "note_credito": 0.0, "incassato": 0.0,
                                   "n_fatture": 0, "n_note_credito": 0})
    totals = {"fatturato_lordo": 0.0, "note_credito": 0.0, "incassato": 0.0, "da_incassare": 0.0}
    for d in year_invoices:
        key, f = key_of(d, "fattura"), factor(d)
        amount = get_total_from_doc(d) * f
        revenue[key]["importo_fatture"] += amount
        revenue[key]["n_fatture"] += 1
        totals["fatturato_lordo"] += amount
        for p in d.get("payments_list") or []:
            status = payment_status_name(p)
            if status == "paid":
                revenue[key]["incassato"] += (p.get("amount", 0) or 0) * f
                totals["incassato"] += (p.get("amount", 0) or 0) * f
            elif status == "not_paid":
                totals["da_incassare"] += (p.get("amount", 0) or 0) * f
    for d in year_credit_notes:
        key = key_of(d, "nota di credito")
        amount = abs(get_total_from_doc(d)) * factor(d)
        revenue[key]["note_credito"] += amount
        revenue[key]["n_note_credito"] += 1
        totals["note_credito"] += amount
    totals["fatturato_netto"] = totals["fatturato_lordo"] - totals["note_credito"]
    totals["incassato_cassa"] = sum(cash.values())

    # --- Exposure: not_paid installments of open invoices and matured proformas -----------
    exposure = defaultdict(_Exposure)
    by_year = defaultdict(lambda: {"fatt_scaduto": 0.0, "fatt_in_scadenza": 0.0, "pf_scaduto": 0.0,
                                   "pf_in_scadenza": 0.0, "fatt_aging_oltre_365": 0.0, "pf_aging_oltre_365": 0.0})
    for kind, label, docs in (("fatt", "fattura", open_invoices), ("pf", "proforma", proformas)):
        for d in docs:
            f = factor(d)
            for p in d.get("payments_list") or []:
                if payment_status_name(p) != "not_paid":  # paid and reversed installments are not owed
                    continue
                key = key_of(d, label)
                amount = (p.get("amount", 0) or 0) * f
                due = Date.fromisoformat(str(p.get("due_date"))[:10]) if p.get("due_date") else as_of_date
                exposure[key].add(kind, d.get("id"), amount, due, as_of_date)
                row = by_year[(key, int(str(d.get("date"))[:4]))]
                if due < as_of_date:
                    row[f"{kind}_scaduto"] += amount
                    if (as_of_date - due).days > 365:
                        row[f"{kind}_aging_oltre_365"] += amount
                else:
                    row[f"{kind}_in_scadenza"] += amount

    # Unpaid credit notes are not netted against exposure: reported separately.
    ndc_aperte = open_credit_notes
    ndc_aperte_totale = sum((p.get("amount", 0) or 0) * factor(d) for d in ndc_aperte
                            for p in d.get("payments_list") or [] if payment_status_name(p) == "not_paid")

    # --- Assemble ------------------------------------------------------------------------
    by_revenue = []
    for key, v in revenue.items():
        netto = v["importo_fatture"] - v["note_credito"]
        by_revenue.append({
            "client_id": key, "client_name": name_of(key),
            "senza_anagrafica": key.startswith(NO_REGISTRY_PREFIX),
            "importo_fatture": r2(v["importo_fatture"]),
            "fatture": r2(v["importo_fatture"]),  # deprecated alias of importo_fatture
            "n_fatture": v["n_fatture"], "note_credito": r2(v["note_credito"]), "n_note_credito": v["n_note_credito"],
            "fatturato_netto": r2(netto), "incassato": r2(v["incassato"]), "incassato_cassa": r2(cash.get(key, 0.0)),
            "quota": round(netto / totals["fatturato_netto"], 4) if totals["fatturato_netto"] else 0.0,
        })
    by_revenue.sort(key=lambda x: x["fatturato_netto"], reverse=True)

    by_exposure = []
    for key, e in exposure.items():
        if e.total("fatt") + e.total("pf") <= 0:
            continue
        c = e.combined(include_pf)
        pf = e.parts["pf"]
        by_exposure.append({
            "client_id": key, "client_name": name_of(key),
            "senza_anagrafica": key.startswith(NO_REGISTRY_PREFIX),
            "fatture_aperte": c["fatture_aperte"], "scaduto": r2(c["scaduto"]), "in_scadenza": r2(c["in_scadenza"]),
            "totale": r2(c["scaduto"] + c["in_scadenza"]),
            "scaduto_dal": c["scaduto_dal"], "ultima_scadenza_scaduta": c["ultima_scadenza_scaduta"],
            "aging": {b: r2(a) for b, a in c["aging"].items()},
            "pf_scaduto": r2(pf["scaduto"]), "pf_in_scadenza": r2(pf["in_scadenza"]),
            "pf_aging_oltre_365": r2(pf["aging"]["oltre_365"]),
            "ultima_fattura_emessa": last_invoice.get(key),
            "ultimo_pagamento": last_payment.get(key),
            "mesi_da_ultima_fattura": _months_between(last_invoice.get(key), as_of),
            "mesi_da_ultimo_pagamento": _months_between(last_payment.get(key), as_of),
        })
    by_exposure.sort(key=lambda x: (x["totale"], x["pf_scaduto"] + x["pf_in_scadenza"]), reverse=True)

    for rows in (by_revenue, by_exposure):
        for pos, row in enumerate(rows, 1):
            row["pos"] = pos

    exposure_by_client = {row["client_id"]: row for row in by_exposure}
    top = []
    for row in by_revenue[:top_n]:
        exp = exposure_by_client.get(row["client_id"], {})
        top.append({"pos": row["pos"], "client_id": row["client_id"], "client_name": row["client_name"],
                    "senza_anagrafica": row["senza_anagrafica"],
                    "fatturato_netto": row["fatturato_netto"], "incassato": row["incassato"],
                    "scaduto": exp.get("scaduto", 0.0), "in_scadenza": exp.get("in_scadenza", 0.0),
                    "esposto": exp.get("totale", 0.0),
                    "pf_scaduto": exp.get("pf_scaduto", 0.0), "pf_in_scadenza": exp.get("pf_in_scadenza", 0.0),
                    "pf_aging_oltre_365": exp.get("pf_aging_oltre_365", 0.0)})

    combined = [e.combined(include_pf) for e in exposure.values()]
    scaduto = sum(c["scaduto"] for c in combined)
    in_scadenza = sum(c["in_scadenza"] for c in combined)
    pf_parts = [e.parts["pf"] for e in exposure.values()]
    pf_scaduto = sum(p["scaduto"] for p in pf_parts)
    pf_in_scadenza = sum(p["in_scadenza"] for p in pf_parts)

    year_rows = sorted(by_year.items(), key=lambda kv: (kv[0][1], kv[0][0]))
    columns = ["client_id", "anno", "fatt_scaduto", "fatt_in_scadenza", "pf_scaduto", "pf_in_scadenza",
               "fatt_aging_oltre_365", "pf_aging_oltre_365"]
    first_year = min((y for (_, y) in by_year), default=as_of_date.year)

    for doc_id, (doc_type, number, doc_date, name) in sorted(unlinked.items(), key=lambda kv: kv[1][2]):
        warnings.append(f"Documento senza anagrafica: {doc_type} n. {number} del {doc_date}, '{name}' (id {doc_id}): "
                        "collegarlo al cliente in FIC")

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": as_of,
        "year": year,
        "open_from": open_from,
        "include_proforma_maturate": include_pf,
        "currency": "EUR",
        "amounts": amounts,
        "totals": {k: r2(totals[k]) for k in ("fatturato_lordo", "note_credito", "fatturato_netto",
                                             "incassato", "incassato_cassa", "da_incassare")},
        "exposure": {
            "totale": r2(scaduto + in_scadenza),
            "scaduto": r2(scaduto),
            "in_scadenza": r2(in_scadenza),
            "quota_scaduta": round(scaduto / (scaduto + in_scadenza), 4) if scaduto + in_scadenza else 0.0,
            "clienti": sum(1 for row in by_exposure if row["totale"] > 0),
            "clienti_con_scaduto": sum(1 for row in by_exposure if row["scaduto"] > 0),
            "fatture_aperte": sum(len(e.parts["fatt"]["docs"]) for e in exposure.values()),
            "proforma_maturate": sum(len(p["docs"]) for p in pf_parts) if include_pf else 0,
            "note_credito_aperte": len(ndc_aperte),
            "note_credito_aperte_totale": r2(ndc_aperte_totale),
            "aging": {b: r2(sum(c["aging"][b] for c in combined)) for b, _ in AGING_BUCKETS},
            "di_cui_proforma": {
                "documenti": sum(len(p["docs"]) for p in pf_parts),
                "totale": r2(pf_scaduto + pf_in_scadenza),
                "scaduto": r2(pf_scaduto),
                "in_scadenza": r2(pf_in_scadenza),
                "aging": {b: r2(sum(p["aging"][b] for p in pf_parts)) for b, _ in AGING_BUCKETS},
            },
        },
        "by_revenue": by_revenue,
        "by_exposure": by_exposure,
        "top": top,
        "exposure_by_year": {
            "anni": list(range(first_year, as_of_date.year + 1)),
            "columns": columns,
            "rows": [[key, y] + [r2(v[c]) for c in columns[2:]] for (key, y), v in year_rows],
        },
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
        description="Le proforma con data <= as_of entrano nei campi di esposizione esistenti (default: true)")] = True,
    amounts: Annotated[Literal["gross", "net"], Field(
        description="gross = IVA inclusa (default); net = imponibile, calcolato dalle righe dei documenti")] = "gross",
) -> dict:
    """Situazione crediti clienti in una sola chiamata, per dashboard: totali dell'anno, classifica clienti
    per fatturato (by_revenue), per esposizione (by_exposure), top N, esposizione per anno di emissione
    (exposure_by_year, colonne + righe). Raggruppato per client_id; righe con senza_anagrafica=true sono
    documenti non collegati a un cliente, elencati in warnings. Importi IVA inclusa (amounts=gross) o
    imponibili (amounts=net). Esposizione = rate not_paid di fatture aperte di qualsiasi anno, più le
    proforma con data <= as_of se include_proforma_maturate (default true); le rate stornate non contano.
    Le colonne pf_* e exposure.di_cui_proforma riportano SEMPRE la parte proforma, così si ottengono entrambe
    le viste con una chiamata. Rata scaduta se la scadenza è precedente ad as_of; aging per giorni di
    ritardo. scaduto_dal = scadenza scaduta più vecchia; ultima_scadenza_scaduta = la più recente.
    incassato = quota pagata delle fatture emesse nell'anno; incassato_cassa = rate con data di pagamento
    nell'anno, qualunque sia l'anno della fattura. anno in exposure_by_year = anno di emissione. Le note di
    credito non saldate non sono compensate, sono riportate a parte. Risultato in cache per 5 minuti; lo
    storico completo (ultima fattura, ultimo pagamento, incassi di fatture di anni precedenti) è
    ricalcolato ogni 24 ore: la prima chiamata del giorno può richiedere fino a un minuto."""
    try:
        year = year or datetime.now().year
        as_of = parse_date(as_of, "as_of") if as_of else today_str()
        open_from = parse_date(open_from, "open_from") if open_from else None
    except ValueError as e:
        return {"error": str(e)}

    key = (year, as_of, open_from, top_n, include_proforma_maturate, amounts)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        result = _compute(year, as_of, open_from, top_n, include_proforma_maturate, amounts)
    except ApiException as e:
        return {"error": _api_error_message(e)}
    except Exception as e:  # the dashboard needs a readable message, not a failed call
        return {"error": f"Errore inatteso: {e}"}
    _cache[key] = (time.monotonic(), result)
    return result
