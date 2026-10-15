"""Listing helpers: FIC `q` filter building, pagination, compact document rows."""

import calendar
from datetime import datetime
from typing import Literal

from . import fic
from .lookups import get_client_by_id

PAGE_SIZE = 100            # FIC API max per_page
DEFAULT_MAX_RESULTS = 500  # cap per call, protects the 1000 req/hour rate limit
SITUATION_MAX_PER_TYPE = 2000  # one client, so a larger bound is still cheap
ISSUED_LIST_FIELDS = ("id,type,number,date,entity,subject,visible_subject,"
                      "rc_center,next_due_date,payments_list")
IssuedType = Literal["invoice", "credit_note", "proforma", "quote"]
ISSUED_TYPES = IssuedType.__args__
# FIC sets next_due_date = null once every installment is paid. Proformas always
# carry a next_due_date, so payment filters only make sense on these types.
PAYMENT_STATUS_TYPES = ("invoice", "credit_note")
PaymentStatus = Literal["paid", "unpaid", "overdue", "not_overdue"]


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def get_total_from_doc(d):
    payments = d.get('payments_list', [])
    if payments:
        return sum(p.get('amount', 0) for p in payments)
    items = d.get('items_list', [])
    return sum((i.get('qty', 0) * i.get('gross_price', 0)) for i in items)


def doc_taxable(d):
    """Taxable (VAT-excluded) amount of an issued document, from its item lines.

    FIC returns no amount_net / amount_gross on issued documents, in lists or in the
    detail; computed the same way, the gross from the lines matches the payments total."""
    return sum((i.get("qty") or 0) * (i.get("net_price") or 0) * (1 - (i.get("discount") or 0) / 100)
               for i in d.get("items_list") or [])


def received_gross(d):
    """FIC leaves amount_gross null on received documents: derive it from net + VAT."""
    if d.get("amount_gross") is not None:
        return d["amount_gross"]
    return round((d.get("amount_net") or 0) + (d.get("amount_vat") or 0), 2)


def q_escape(value):
    """Escape a string literal for the FIC `q` filter (SQL-style quote doubling)."""
    return str(value).replace("'", "''")


def parse_date(value, field):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError(f"{field} deve essere nel formato YYYY-MM-DD (ricevuto: {value!r})")


def build_date_clauses(date=None, date_from=None, date_to=None, year=None, month=None):
    """Date filter, by precedence: date > date_from/date_to > year/month."""
    if date:
        return [f"date = '{parse_date(date, 'date')}'"]
    if date_from or date_to:
        clauses = []
        if date_from:
            clauses.append(f"date >= '{parse_date(date_from, 'date_from')}'")
        if date_to:
            clauses.append(f"date <= '{parse_date(date_to, 'date_to')}'")
        return clauses
    if year and month:
        last_day = calendar.monthrange(year, month)[1]
        return [f"date >= '{year}-{month:02d}-01'", f"date <= '{year}-{month:02d}-{last_day}'"]
    if year:
        return [f"date >= '{year}-01-01'", f"date <= '{year}-12-31'"]
    return []


def payment_status_clause(status, today=None):
    today = today or today_str()
    return {
        "paid": "next_due_date = null",
        "unpaid": "next_due_date != null",
        "overdue": f"next_due_date < '{today}'",
        "not_overdue": f"next_due_date >= '{today}'",
    }[status]


def payment_status_of(d, today=None):
    """Derive paid / overdue / not_overdue from next_due_date (invoice, credit_note only)."""
    if d.get("type") and str(d["type"]).replace("IssuedDocumentType.", "").lower() not in PAYMENT_STATUS_TYPES:
        return None
    due = d.get("next_due_date")
    if not due:
        return "paid"
    today = today or today_str()
    return "overdue" if str(due) < today else "not_overdue"


def payment_status_name(p):
    return str(p.get("status", "")).replace("IssuedDocumentStatus.", "").lower()


def fetch_all_pages(list_fn, max_results=DEFAULT_MAX_RESULTS, **kwargs):
    """Call a FIC list endpoint page by page. Returns (docs as dicts, total on FIC).
    max_results=None fetches everything (use only for bounded queries, e.g. one year)."""
    docs, page = [], 1
    while True:
        resp = list_fn(company_id=fic.COMPANY_ID, per_page=PAGE_SIZE, page=page, **kwargs)
        docs.extend(d.to_dict() for d in (resp.data or []))
        total = resp.total or 0
        if (max_results and len(docs) >= max_results) or page >= (resp.last_page or 1):
            break
        page += 1
    return (docs[:max_results] if max_results else docs), total


def issued_doc_row(d, doc_type):
    """Compact representation of an issued document for list outputs."""
    entity = d.get("entity") or {}
    row = {
        "id": d.get("id"),
        "type": doc_type,
        "number": d.get("number"),
        "date": str(d.get("date", "")),
        "client": entity.get("name"),
        "client_id": entity.get("id"),
        "total": get_total_from_doc(d),
        "subject": d.get("subject"),
        "description": d.get("visible_subject"),
    }
    status = payment_status_of({**d, "type": doc_type})
    if status:
        row["payment_status"] = status
        row["next_due_date"] = str(d["next_due_date"]) if d.get("next_due_date") else None
    if d.get("rc_center"):
        row["revenue_center"] = d["rc_center"]
    return row


def resolve_client(client_id=None, client_name=None):
    """Return (client dict, None) or (None, error/candidates payload) from client_id or client_name."""
    if client_id:
        client = get_client_by_id(int(client_id))
        if not client:
            return None, {"error": f"Cliente {client_id} non trovato"}
        return {"id": client.get("id"), "name": client.get("name")}, None
    name = (client_name or "").strip()
    if not name:
        return None, {"error": "Specificare client_id oppure client_name"}
    rows, total = fetch_all_pages(fic.clients_api.list_clients, max_results=50,
                                  q=f"name like '%{q_escape(name)}%'", sort="name")
    exact = [r for r in rows if (r.get("name") or "").strip().lower() == name.lower()]
    if len(exact) == 1:
        rows = exact
    if len(rows) == 1:
        return {"id": rows[0]["id"], "name": rows[0]["name"]}, None
    if not rows:
        return None, {"error": f"Nessun cliente corrisponde a '{name}'"}
    return None, {
        "error": f"'{name}' corrisponde a {total} clienti: richiamare con client_id",
        "candidates": [{"id": r["id"], "name": r["name"], "vat": r.get("vat_number")} for r in rows],
    }
