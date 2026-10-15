"""Received (supplier) documents: list, detail, create."""

from datetime import datetime
from typing import Annotated

from pydantic import Field

from .. import fic
from ..app import ann, tool
from ..listing import DEFAULT_MAX_RESULTS, build_date_clauses, fetch_all_pages, today_str
from ..lookups import fetch_cost_centers


@tool(annotations=ann(read_only=True, idempotent=True))
def list_received_documents(
    year: Annotated[int | None, Field(description="Anno")] = None,
    month: Annotated[int | None, Field(description="Mese 1-12, insieme a year")] = None,
    date: Annotated[str | None, Field(description="Data esatta YYYY-MM-DD")] = None,
    date_from: Annotated[str | None, Field(description="Data inizio YYYY-MM-DD (inclusa)")] = None,
    date_to: Annotated[str | None, Field(description="Data fine YYYY-MM-DD (inclusa)")] = None,
    type: Annotated[str, Field(description="Tipo: expense, credit_note (default: expense)")] = "expense",
    query: Annotated[str | None, Field(description="Filtro testuale su fornitore/descrizione (applicato dopo il recupero)")] = None,
    max_results: Annotated[int | None, Field(description=f"Massimo documenti restituiti (default {DEFAULT_MAX_RESULTS})")] = None,
) -> dict:
    """Lista fatture PASSIVE (ricevute dai fornitori) con paginazione automatica. Periodo: date esatta,
    oppure date_from/date_to, oppure year/month. La risposta indica total e truncated."""
    if not any((date, date_from, date_to, year)):
        year = datetime.now().year
    rows, total = fetch_all_pages(
        fic.received_api.list_received_documents, max_results=max_results or DEFAULT_MAX_RESULTS,
        type=type, q=" and ".join(build_date_clauses(date, date_from, date_to, year, month)),
        sort="-date", fieldset="detailed",
    )
    docs = []
    for d in rows:
        supplier_name = (d.get('entity') or {}).get('name', '')
        desc = d.get('description', '') or ''
        if query and query.lower() not in f"{supplier_name} {desc}".lower():
            continue
        entry = {
            "id": d.get("id"), "number": d.get("number"),
            "date": str(d.get("date", "")), "supplier": supplier_name,
            "description": desc[:80], "total": d.get('amount_gross') or d.get('amount_net') or 0
        }
        if d.get("rc_center"):
            entry["cost_center"] = d["rc_center"]
        docs.append(entry)
    return {"total": total, "returned": len(docs), "truncated": len(rows) < total, "documents": docs}


@tool(annotations=ann(read_only=True, idempotent=True))
def get_received_document(
    document_id: Annotated[int, Field(description="ID documento ricevuto")],
) -> dict:
    """Dettaglio fattura passiva (ricevuta da fornitore) per ID. Read-only."""
    d = fic.received_api.get_received_document(
        company_id=fic.COMPANY_ID, document_id=document_id, fieldset="detailed"
    ).data.to_dict()
    entity = d.get("entity") or {}
    items = [{
        "name": i.get("name"),
        "description": i.get("description"),
        "qty": i.get("qty"),
        "net_price": i.get("net_price", 0),
        "vat": i.get("vat", {}).get("value") if i.get("vat") else None
    } for i in d.get("items_list", []) or []]
    payments = [{
        "amount": p.get("amount"),
        "due_date": str(p.get("due_date", "")),
        "status": str(p.get("status", "")).replace("ReceivedDocumentStatus.", ""),
        "paid_date": str(p.get("paid_date", "")) if p.get("paid_date") else None,
    } for p in d.get("payments_list", []) or []]
    result = {
        "id": d.get("id"),
        "type": d.get("type"),
        "number": d.get("invoice_number"),
        "date": str(d.get("date", "")),
        "supplier": entity.get("name") if d.get("entity") else None,
        "supplier_vat": entity.get("vat_number") if d.get("entity") else None,
        "description": d.get("description"),
        "category": d.get("category"),
        "amount_net": d.get("amount_net"),
        "amount_vat": d.get("amount_vat"),
        "amount_gross": d.get("amount_gross"),
        "items": items,
        "payments": payments,
    }
    if d.get("rc_center"):
        result["cost_center"] = d["rc_center"]
    return result


@tool(annotations=ann())
def create_received_document(
    supplier_name: Annotated[str, Field(description="Nome/Ragione sociale del fornitore")],
    amount_net: Annotated[float, Field(description="Importo netto")],
    supplier_vat_number: Annotated[str | None, Field(description="Partita IVA del fornitore (opzionale)")] = None,
    type: Annotated[str, Field(description="Tipo: expense (default) o credit_note")] = "expense",
    date: Annotated[str | None, Field(description="Data documento YYYY-MM-DD (default: oggi)")] = None,
    amount_vat: Annotated[float, Field(description="Importo IVA")] = 0,
    category: Annotated[str | None, Field(description="Categoria spesa (opzionale)")] = None,
    description: Annotated[str | None, Field(description="Descrizione/oggetto (opzionale)")] = None,
    cost_center: Annotated[str | None, Field(description="Centro di costo (opzionale, deve esistere — vedi list_cost_centers)")] = None,
) -> dict:
    """Crea documento passivo (fattura ricevuta o NDC ricevuta) registrando una spesa. IMPORTANTE:
    Chiedere conferma all'utente prima di eseguire."""
    if cost_center:
        known = fetch_cost_centers(company_id=fic.COMPANY_ID)
        if cost_center not in known:
            return {"success": False, "error": f"cost_center '{cost_center}' non esiste. Disponibili: {known}."}

    entity = {"name": supplier_name}
    if supplier_vat_number:
        entity["vat_number"] = supplier_vat_number
    body_data = {
        "type": type,
        "entity": entity,
        "date": date or today_str(),
        "amount_net": amount_net,
        "amount_vat": amount_vat,
        "amount_gross": amount_net + amount_vat,
    }
    if category:
        body_data["category"] = category
    if description:
        body_data["description"] = description
    if cost_center:
        body_data["rc_center"] = cost_center

    d = fic.received_api.create_received_document(
        company_id=fic.COMPANY_ID, create_received_document_request={"data": body_data}
    ).data.to_dict()
    result = {
        "success": True,
        "id": d.get("id"),
        "type": d.get("type"),
        "supplier": d.get("entity", {}).get("name") if d.get("entity") else supplier_name,
        "date": str(d.get("date", "")),
        "amount_gross": d.get("amount_gross") or (amount_net + amount_vat),
        "message": f"Documento ricevuto creato (ID {d.get('id')}).",
    }
    if cost_center:
        result["cost_center"] = cost_center
    return result
