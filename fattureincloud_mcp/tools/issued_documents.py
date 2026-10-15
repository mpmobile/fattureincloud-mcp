"""Issued documents: invoices, credit notes, proformas, quotes."""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, Field

from .. import fic
from ..app import ann, tool
from ..listing import (DEFAULT_MAX_RESULTS, ISSUED_LIST_FIELDS, ISSUED_TYPES, PAYMENT_STATUS_TYPES, IssuedType,
                       PaymentStatus, build_date_clauses, fetch_all_pages, get_total_from_doc, issued_doc_row,
                       payment_status_clause, q_escape, today_str)
from ..lookups import build_entity_from_client, fetch_revenue_centers, get_client_by_id


class InvoiceItem(BaseModel):
    name: str = Field(description="Nome prodotto/servizio")
    description: str | None = Field(None, description="Descrizione estesa")
    qty: float = Field(description="Quantità")
    net_price: float = Field(description="Prezzo netto unitario (sempre positivo)")
    vat_rate: float | None = Field(None, description="Aliquota IVA (es. 22)")


class TextReplace(BaseModel):
    old: str | None = None
    new: str | None = None


DocumentId = Annotated[int, Field(description="ID documento")]
Items = Annotated[list[InvoiceItem], Field(description="Righe documento")]
ClientId = Annotated[int, Field(description="ID cliente")]
DocDate = Annotated[str | None, Field(description="Data YYYY-MM-DD (default: oggi)")]
PaymentDays = Annotated[int, Field(description="Giorni pagamento (default: 30)")]
VisibleSubject = Annotated[str, Field(description="Oggetto visibile")]
RevenueCenter = Annotated[str | None, Field(description="Centro di ricavo (opzionale, deve esistere — vedi list_cost_centers)")]


# --- Document building helpers ------------------------------------------------

def _get_document(document_id):
    return fic.issued_api.get_issued_document(
        company_id=fic.COMPANY_ID, document_id=document_id, fieldset="detailed"
    ).data.to_dict()


def _items_body(items):
    return [{
        "name": item.name,
        "description": item.description if item.description is not None else "",
        "qty": item.qty,
        "net_price": item.net_price,
        "vat": {"id": 0, "value": item.vat_rate if item.vat_rate is not None else 22},
    } for item in items]


def _copy_items(orig, absolute_prices=True, replace=None):
    """Item lines of an existing document, ready to be sent back to the API."""
    items = []
    for i in orig.get("items_list", []):
        name, description = i.get("name", ""), i.get("description", "")
        if replace and replace.old and replace.new:
            name = name.replace(replace.old, replace.new)
            description = description.replace(replace.old, replace.new)
        items.append({
            "name": name,
            "description": description,
            "qty": i.get("qty"),
            "net_price": abs(i.get("net_price", 0)) if absolute_prices else i.get("net_price"),
            "vat": {"id": 0, "value": i.get("vat", {}).get("value", 22)},
        })
    return items


def _gross_total(items_list, absolute=True):
    line = (lambda i: abs(i["qty"] * i["net_price"])) if absolute else (lambda i: i["qty"] * i["net_price"])
    return sum(line(i) * (1 + i["vat"]["value"] / 100) for i in items_list)


def _payment_days_of(orig):
    orig_payments = orig.get("payments_list", [{}])
    return orig_payments[0].get("payment_terms", {}).get("days", 30) if orig_payments else 30


def _single_payment(amount, due_date, payment_days):
    return [{
        "amount": round(amount, 2),
        "due_date": due_date.strftime("%Y-%m-%d"),
        "status": "not_paid",
        "payment_terms": {"days": payment_days, "type": "standard"},
    }]


def _entity_of(orig):
    """(client registry data, entity for the request body) of an existing document's client."""
    client_id = orig.get("entity", {}).get("id")
    client_data = get_client_by_id(client_id) if client_id else None
    if client_id and client_data:
        return client_data, build_entity_from_client(client_id, client_data)
    return client_data, orig.get("entity", {})


def _revenue_center_error(revenue_center, hint=""):
    known = fetch_revenue_centers(company_id=fic.COMPANY_ID)
    if revenue_center not in known:
        return f"revenue_center '{revenue_center}' non esiste. Disponibili: {known}.{hint}"
    return None


def _set_e_invoice(body_data, doc_type):
    if doc_type in ("invoice", "credit_note"):
        body_data["e_invoice"] = True
        body_data["ei_data"] = {"payment_method": "MP05"}


def build_issued_document(doc_type, client_id, items, date_str, payment_days,
                          visible_subject, negate_prices=False, source_invoice_id=None,
                          revenue_center=None):
    client_data = get_client_by_id(client_id)
    if not client_data:
        return None, f"Cliente con ID {client_id} non trovato"
    if revenue_center:
        error = _revenue_center_error(
            revenue_center, " Crearli da web FIC (Impostazioni → Centri di costo/ricavo).")
        if error:
            return None, error

    entity = build_entity_from_client(client_id, client_data)
    items_list = _items_body(items)
    due_date = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=payment_days)
    total_abs = _gross_total(items_list)

    body_data = {
        "type": doc_type,
        "entity": entity,
        "date": date_str,
        "visible_subject": visible_subject,
        "items_list": items_list,
        "payments_list": _single_payment(total_abs, due_date, payment_days),
    }
    if revenue_center:
        body_data["rc_center"] = revenue_center
    _set_e_invoice(body_data, doc_type)
    if source_invoice_id:
        body_data["original_document"] = {"id": source_invoice_id}

    d = fic.issued_api.create_issued_document(
        company_id=fic.COMPANY_ID, create_issued_document_request={"data": body_data}
    ).data.to_dict()

    result = {
        "success": True,
        "id": d.get("id"),
        "number": d.get("number"),
        "date": str(d.get("date", "")),
        "due_date": due_date.strftime("%Y-%m-%d"),
        "client": client_data.get("name"),
        "ei_code": entity.get("ei_code", "N/A"),
        "total": round(-total_abs if negate_prices else total_abs, 2),
        "type": doc_type,
        "status": "bozza",
    }
    if revenue_center:
        result["revenue_center"] = revenue_center
    if source_invoice_id:
        result["linked_to_invoice"] = source_invoice_id
    return result, None


# --- Read tools ----------------------------------------------------------------

@tool(annotations=ann(read_only=True, idempotent=True))
def list_invoices(
    types: Annotated[list[IssuedType] | None,
                     Field(description="Tipi documento (default: [\"invoice\"]). Es. [\"invoice\", \"proforma\"]")] = None,
    type: Annotated[str | None, Field(description="Deprecato: tipo singolo, usa types")] = None,
    client_id: Annotated[int | None, Field(description="ID cliente (da list_clients). Preferibile a client_name")] = None,
    client_name: Annotated[str | None, Field(description="Nome cliente, ricerca parziale case-insensitive")] = None,
    date: Annotated[str | None, Field(description="Data esatta YYYY-MM-DD")] = None,
    date_from: Annotated[str | None, Field(description="Data inizio YYYY-MM-DD (inclusa)")] = None,
    date_to: Annotated[str | None, Field(description="Data fine YYYY-MM-DD (inclusa)")] = None,
    year: Annotated[int | None, Field(description="Anno (es. 2024), se non si usano date/date_from/date_to")] = None,
    month: Annotated[int | None, Field(description="Mese 1-12, insieme a year")] = None,
    payment_status: Annotated[PaymentStatus | None, Field(
        description="paid = saldate, unpaid = da incassare, overdue = scadute, not_overdue = da incassare non ancora scadute")] = None,
    query: Annotated[str | None, Field(description="Filtro testuale su cliente/oggetto (applicato dopo il recupero)")] = None,
    max_results: Annotated[int | None, Field(description=f"Massimo documenti restituiti (default {DEFAULT_MAX_RESULTS})")] = None,
) -> dict:
    """Lista documenti emessi (fatture, NDC, proforma, preventivi) con paginazione automatica. Filtri
    combinabili: tipo/i documento, cliente (client_id o client_name), periodo (date esatta, oppure
    date_from/date_to, oppure year/month), stato pagamento (paid, unpaid, overdue = scadute,
    not_overdue = non ancora scadute; solo fatture e NDC). Ordinati dal più recente. La risposta indica
    total (documenti su FIC) e truncated (true se oltre max_results). Per i totali di un cliente usa
    get_client_situation."""
    types = types or [type or "invoice"]
    unknown = [t for t in types if t not in ISSUED_TYPES]
    if unknown:
        raise ValueError(f"Tipi non validi: {unknown}. Valori: {', '.join(ISSUED_TYPES)}")
    query = (query or "").lower()
    max_results = max_results or DEFAULT_MAX_RESULTS

    base = build_date_clauses(date, date_from, date_to, year, month)
    if client_id:
        base.append(f"entity.id = {int(client_id)}")
    elif client_name:
        base.append(f"entity.name like '%{q_escape(client_name)}%'")

    documents, by_type, notes = [], {}, []
    for doc_type in types:
        clauses = list(base)
        if payment_status:
            if doc_type not in PAYMENT_STATUS_TYPES:
                notes.append(f"{doc_type} esclusi: payment_status vale solo per {', '.join(PAYMENT_STATUS_TYPES)}")
                continue
            clauses.append(payment_status_clause(payment_status))
        docs, total = fetch_all_pages(
            fic.issued_api.list_issued_documents, max_results=max_results,
            type=doc_type, q=" and ".join(clauses) or None, sort="-date", fields=ISSUED_LIST_FIELDS,
        )
        by_type[doc_type] = {"total": total, "fetched": len(docs)}
        for d in docs:
            row = issued_doc_row(d, doc_type)
            if query and query not in f"{row['client']} {row['subject']} {row['description']}".lower():
                continue
            documents.append(row)

    documents.sort(key=lambda x: x["date"], reverse=True)
    truncated = any(t["fetched"] < t["total"] for t in by_type.values()) or len(documents) > max_results
    if truncated:
        notes.append(f"Risultati limitati a {max_results}: restringi il periodo o aumenta max_results")
    result = {
        "total": sum(t["total"] for t in by_type.values()),
        "returned": min(len(documents), max_results),
        "truncated": truncated,
        "by_type": by_type,
        "documents": documents[:max_results],
    }
    if notes:
        result["notes"] = notes
    return result


@tool(annotations=ann(read_only=True, idempotent=True))
def get_invoice(document_id: DocumentId) -> dict:
    """Dettaglio documento per ID (fattura, NDC, proforma)"""
    d = _get_document(document_id)
    entity = d.get("entity") or {}
    items = [{
        "name": i.get("name"),
        "description": i.get("description"),
        "qty": i.get("qty"),
        "net_price": i.get("net_price", 0),
        "gross_price": i.get("gross_price", 0),
        "vat": i.get("vat", {}).get("value") if i.get("vat") else None
    } for i in d.get("items_list", [])]
    payments = []
    for p in d.get("payments_list", []):
        pa = p.get("payment_account")
        if hasattr(pa, 'to_dict'):
            pa = pa.to_dict()
        payments.append({
            "amount": p.get("amount"),
            "due_date": str(p.get("due_date", "")),
            "status": str(p.get("status", "")).replace("IssuedDocumentStatus.", ""),
            "paid_date": str(p.get("paid_date", "")) if p.get("paid_date") else None,
            "payment_account_id": pa.get("id") if isinstance(pa, dict) else None
        })
    result = {
        "id": d.get("id"),
        "number": d.get("number"),
        "date": str(d.get("date", "")),
        "type": d.get("type"),
        "client_id": entity.get("id") if d.get("entity") else None,
        "client": entity.get("name") if d.get("entity") else None,
        "total": get_total_from_doc(d),
        "subject": d.get("subject"),
        "description": d.get("visible_subject"),
        "items": items,
        "payments": payments,
        "ei_status": d.get("ei_status"),
        "original_document": d.get("original_document")
    }
    if d.get("rc_center"):
        result["revenue_center"] = d["rc_center"]
    return result


# --- Write tools ---------------------------------------------------------------

@tool(annotations=ann())
def create_invoice(
    client_id: ClientId,
    items: Items,
    date: DocDate = None,
    payment_days: PaymentDays = 30,
    visible_subject: VisibleSubject = "",
    revenue_center: RevenueCenter = None,
) -> dict:
    """Crea nuova fattura (bozza). IMPORTANTE: Chiedere sempre conferma all'utente prima di eseguire."""
    result, error = build_issued_document("invoice", client_id, items, date or today_str(),
                                          payment_days, visible_subject, revenue_center=revenue_center)
    if error:
        return {"success": False, "error": error}
    result["message"] = f"Fattura #{result['number']} creata come bozza. SDI: {result['ei_code']}. Usa send_to_sdi per inviarla."
    return result


@tool(annotations=ann())
def create_credit_note(
    client_id: ClientId,
    items: Items,
    date: DocDate = None,
    payment_days: PaymentDays = 30,
    visible_subject: VisibleSubject = "",
    source_invoice_id: Annotated[int | None, Field(description="ID fattura originale da stornare (opzionale)")] = None,
    revenue_center: RevenueCenter = None,
) -> dict:
    """Crea nota di credito (bozza). Importi POSITIVI in input, resi negativi automaticamente.
    IMPORTANTE: Chiedere sempre conferma all'utente prima di eseguire."""
    result, error = build_issued_document("credit_note", client_id, items, date or today_str(),
                                          payment_days, visible_subject, negate_prices=True,
                                          source_invoice_id=source_invoice_id, revenue_center=revenue_center)
    if error:
        return {"success": False, "error": error}
    msg = f"NDC #{result['number']} creata come bozza. Totale: {result['total']}."
    if result.get("linked_to_invoice"):
        msg += f" Collegata a fattura ID {result['linked_to_invoice']}."
    result["message"] = msg + " Usa send_to_sdi per inviarla."
    return result


@tool(annotations=ann())
def create_proforma(
    client_id: ClientId,
    items: Items,
    date: DocDate = None,
    payment_days: PaymentDays = 30,
    visible_subject: VisibleSubject = "",
    revenue_center: RevenueCenter = None,
) -> dict:
    """Crea proforma (bozza). Non inviabile allo SDI. IMPORTANTE: Chiedere sempre conferma all'utente
    prima di eseguire."""
    result, error = build_issued_document("proforma", client_id, items, date or today_str(),
                                          payment_days, visible_subject, revenue_center=revenue_center)
    if error:
        return {"success": False, "error": error}
    result["message"] = f"Proforma #{result['number']} creata come bozza. Non inviabile allo SDI."
    return result


@tool(annotations=ann(destructive=True))
def convert_proforma_to_invoice(
    document_id: Annotated[int, Field(description="ID proforma da convertire")],
    date: Annotated[str | None, Field(description="Data fattura YYYY-MM-DD (default: data proforma)")] = None,
    keep_proforma: Annotated[bool, Field(description="Mantieni la proforma originale (default: false)")] = False,
    revenue_center: Annotated[str | None, Field(description="Centro di ricavo (opzionale, eredita da proforma se non passato)")] = None,
) -> dict:
    """Converte una proforma in fattura elettronica (bozza). Di default elimina la proforma originale.
    IMPORTANTE: Chiedere sempre conferma all'utente prima di eseguire."""
    orig = _get_document(document_id)
    if orig.get("type") != "proforma":
        return {"success": False,
                "error": f"Il documento {document_id} non è una proforma (tipo: {orig.get('type')})"}

    client_data, entity = _entity_of(orig)
    date_str = (date or str(orig.get("date", today_str())))[:10]
    items_list = _copy_items(orig)
    payment_days = _payment_days_of(orig)
    due_date = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=payment_days)
    total_gross = _gross_total(items_list, absolute=False)

    revenue_center = revenue_center or orig.get("rc_center")
    if revenue_center and (error := _revenue_center_error(revenue_center)):
        return {"success": False, "error": error}

    body_data = {
        "type": "invoice",
        "entity": entity,
        "date": date_str,
        "visible_subject": orig.get("visible_subject", ""),
        "items_list": items_list,
        "payments_list": _single_payment(total_gross, due_date, payment_days),
    }
    _set_e_invoice(body_data, "invoice")
    if revenue_center:
        body_data["rc_center"] = revenue_center

    d = fic.issued_api.create_issued_document(
        company_id=fic.COMPANY_ID, create_issued_document_request={"data": body_data}
    ).data.to_dict()
    if not keep_proforma:
        fic.issued_api.delete_issued_document(company_id=fic.COMPANY_ID, document_id=document_id)

    result = {
        "success": True,
        "invoice_id": d.get("id"),
        "invoice_number": d.get("number"),
        "date": date_str,
        "due_date": due_date.strftime("%Y-%m-%d"),
        "client": (client_data or {}).get("name", entity.get("name", "")),
        "ei_code": entity.get("ei_code", "N/A"),
        "total": round(total_gross, 2),
        "proforma_deleted": not keep_proforma,
        "message": (f"Fattura #{d.get('number')} creata da proforma #{orig.get('number')}. "
                    f"{'Proforma mantenuta.' if keep_proforma else 'Proforma eliminata.'} Usa send_to_sdi per inviarla.")
    }
    if revenue_center:
        result["revenue_center"] = revenue_center
    return result


@tool(annotations=ann(idempotent=True))
def update_document(
    document_id: Annotated[int, Field(description="ID documento da modificare")],
    date: Annotated[str | None, Field(description="Nuova data YYYY-MM-DD (opzionale)")] = None,
    visible_subject: Annotated[str | None, Field(description="Nuovo oggetto visibile (opzionale)")] = None,
    payment_days: Annotated[int | None, Field(description="Nuovi giorni pagamento (opzionale)")] = None,
    items: Annotated[list[InvoiceItem] | None,
                     Field(description="Nuove righe documento (opzionale). Per NDC, importi sempre positivi.")] = None,
    revenue_center: Annotated[str | None,
                              Field(description="Centro di ricavo (opzionale, mantiene quello esistente se non passato)")] = None,
) -> dict:
    """Modifica parziale di un documento BOZZA (fattura, NDC, proforma). Passa solo i campi da aggiornare.
    Funziona solo su documenti non ancora inviati allo SDI. IMPORTANTE: Chiedere sempre conferma
    all'utente prima di eseguire."""
    orig = _get_document(document_id)
    current_status = orig.get("ei_status")
    if current_status and current_status not in [None, "not_sent"]:
        return {"success": False,
                "error": f"Impossibile modificare: documento già inviato allo SDI. Stato: {current_status}"}

    # Fields left as None keep the document's current value; "" clears a text field.
    doc_type = orig.get("type", "invoice")
    date_str = (date or str(orig.get("date", today_str())))[:10]
    if visible_subject is None:
        visible_subject = orig.get("visible_subject") or ""
    items_list = _items_body(items) if items is not None else _copy_items(orig)
    if payment_days is None:
        payment_days = _payment_days_of(orig)
    if revenue_center is None:
        revenue_center = orig.get("rc_center")

    due_date = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=payment_days)
    total_abs = _gross_total(items_list)
    client_data, entity = _entity_of(orig)

    if revenue_center and (error := _revenue_center_error(revenue_center)):
        return {"success": False, "error": error}

    body_data = {
        "type": doc_type,
        "entity": entity,
        "date": date_str,
        "visible_subject": visible_subject,
        "items_list": items_list,
        "payments_list": _single_payment(total_abs, due_date, payment_days),
    }
    if revenue_center:
        body_data["rc_center"] = revenue_center
    _set_e_invoice(body_data, doc_type)
    if orig.get("original_document"):
        body_data["original_document"] = orig["original_document"]

    d = fic.issued_api.modify_issued_document(
        company_id=fic.COMPANY_ID, document_id=document_id,
        modify_issued_document_request={"data": body_data}
    ).data.to_dict()

    result = {
        "success": True,
        "id": d.get("id"),
        "number": d.get("number"),
        "date": str(d.get("date", "")),
        "due_date": due_date.strftime("%Y-%m-%d"),
        "client": (client_data or {}).get("name", entity.get("name", "")),
        "total": round(-total_abs if doc_type == "credit_note" else total_abs, 2),
        "type": doc_type,
        "status": "bozza",
        "message": f"Documento #{d.get('number')} aggiornato con successo."
    }
    if revenue_center:
        result["revenue_center"] = revenue_center
    return result


@tool(annotations=ann())
def duplicate_invoice(
    source_document_id: Annotated[int, Field(description="ID fattura da duplicare")],
    new_date: Annotated[str | None, Field(description="Nuova data YYYY-MM-DD (default: oggi)")] = None,
    payment_days: Annotated[int | None, Field(description="Giorni pagamento (default: eredita da originale)")] = None,
    description_replace: Annotated[TextReplace | None,
                                   Field(description="Sostituzioni testo nella descrizione (es. 2025->2026)")] = None,
    revenue_center: Annotated[str | None, Field(
        description="Centro di ricavo (opzionale, eredita dalla fattura sorgente se non passato)")] = None,
) -> dict:
    """Duplica una fattura esistente con nuova data (crea bozza). IMPORTANTE: Chiedere sempre conferma
    all'utente prima di eseguire."""
    new_date_str = new_date or today_str()
    orig = _get_document(source_document_id)
    client_data, entity = _entity_of(orig)

    replace = description_replace
    items_list = _copy_items(orig, absolute_prices=False, replace=replace)
    visible_subject = orig.get("visible_subject", "")
    if replace and replace.old and replace.new:
        visible_subject = visible_subject.replace(replace.old, replace.new)

    if payment_days is None:
        payment_days = _payment_days_of(orig)
    due_date = datetime.strptime(new_date_str, "%Y-%m-%d") + timedelta(days=payment_days)
    total_gross = _gross_total(items_list, absolute=False)

    revenue_center = revenue_center or orig.get("rc_center")
    if revenue_center and (error := _revenue_center_error(revenue_center)):
        return {"success": False, "error": error}

    body_data = {
        "type": "invoice", "entity": entity, "date": new_date_str, "visible_subject": visible_subject,
        "items_list": items_list,
        "payments_list": _single_payment(total_gross, due_date, payment_days),
    }
    _set_e_invoice(body_data, "invoice")
    if revenue_center:
        body_data["rc_center"] = revenue_center
    d = fic.issued_api.create_issued_document(
        company_id=fic.COMPANY_ID, create_issued_document_request={"data": body_data}
    ).data.to_dict()
    result = {
        "success": True, "id": d.get("id"), "number": d.get("number"),
        "date": str(d.get("date", "")), "due_date": due_date.strftime("%Y-%m-%d"),
        "client": (client_data or {}).get("name", entity.get("name", "")),
        "ei_code": entity.get("ei_code", "N/A"), "total": round(total_gross, 2),
        "source_invoice": orig.get("number"), "status": "bozza",
        "message": (f"Fattura #{d.get('number')} creata come bozza (duplicata da #{orig.get('number')}). "
                    f"Scadenza: {due_date.strftime('%d/%m/%Y')}.")
    }
    if revenue_center:
        result["revenue_center"] = revenue_center
    return result


@tool(annotations=ann(destructive=True, idempotent=True))
def delete_invoice(document_id: Annotated[int, Field(description="ID documento da eliminare")]) -> dict:
    """Elimina un documento BOZZA (fattura, NDC, proforma). ATTENZIONE: Azione irreversibile! Chiedere
    SEMPRE conferma esplicita all'utente."""
    check = _get_document(document_id)
    current_status = check.get("ei_status")
    if current_status and current_status not in ["null", "not_sent", None]:
        return {"success": False,
                "error": f"Impossibile eliminare: documento già inviato allo SDI. Stato: {current_status}"}
    fic.issued_api.delete_issued_document(company_id=fic.COMPANY_ID, document_id=document_id)
    return {
        "success": True, "document_id": document_id,
        "number": check.get("number"),
        "client": check.get("entity", {}).get("name"),
        "message": f"Documento #{check.get('number')} eliminato con successo."
    }
