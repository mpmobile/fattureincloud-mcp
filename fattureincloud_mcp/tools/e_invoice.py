"""Electronic invoicing (SDI), PDF links and courtesy emails."""

from typing import Annotated

from pydantic import Field

from .. import fic
from ..app import ann, tool

DocumentId = Annotated[int, Field(description="ID documento")]

EI_STATUS_DESCRIPTIONS = {
    None: "Bozza (non inviata)", "not_sent": "Bozza (non inviata)",
    "pending": "In attesa di invio", "sent": "Inviata, in attesa di risposta SDI",
    "delivered": "Consegnata al destinatario", "accepted": "Accettata",
    "rejected": "Rifiutata", "not_delivered": "Non consegnata (messa a disposizione)"
}


def _get_document(document_id):
    return fic.issued_api.get_issued_document(
        company_id=fic.COMPANY_ID, document_id=document_id, fieldset="detailed"
    ).data.to_dict()


@tool(annotations=ann(read_only=True, idempotent=True))
def get_pdf_url(document_id: DocumentId) -> dict:
    """Restituisce URL PDF e link web di un documento (fattura, NDC, proforma)"""
    d = _get_document(document_id)
    return {
        "id": document_id,
        "number": d.get("number"),
        "type": d.get("type", "invoice"),
        "client": d.get("entity", {}).get("name") if d.get("entity") else "",
        "attachment_url": d.get("attachment_url") or d.get("url") or "",
        "web_url": f"https://secure.fattureincloud.it/issued-documents-view-{document_id}",
        "note": "attachment_url è il PDF diretto (se disponibile). web_url apre il documento nel browser FIC."
    }


@tool(annotations=ann())
def send_to_sdi(document_id: Annotated[int, Field(description="ID documento da inviare")]) -> dict:
    """Invia fattura/NDC allo SDI. ATTENZIONE: Azione irreversibile! Chiedere SEMPRE conferma esplicita
    all'utente."""
    check = _get_document(document_id)
    current_status = check.get("ei_status")
    if current_status and current_status not in ["null", "rejected", None, "not_sent"]:
        return {"success": False, "error": f"Documento già inviato o in elaborazione. Stato: {current_status}"}
    fic.einvoice_api.send_e_invoice(
        company_id=fic.COMPANY_ID, document_id=document_id,
        send_e_invoice_request={"data": {"withholding_tax_causal": None}}
    )
    return {
        "success": True, "document_id": document_id,
        "number": check.get("number"),
        "client": check.get("entity", {}).get("name"),
        "message": f"Fattura #{check.get('number')} inviata allo SDI con successo!"
    }


@tool(annotations=ann(read_only=True, idempotent=True))
def get_invoice_status(document_id: DocumentId) -> dict:
    """Controlla stato e-invoice/SDI di un documento"""
    d = _get_document(document_id)
    ei_status = d.get("ei_status")
    return {
        "id": d.get("id"), "number": d.get("number"),
        "client": d.get("entity", {}).get("name"),
        "ei_status": ei_status,
        "ei_status_description": EI_STATUS_DESCRIPTIONS.get(ei_status, ei_status),
        "date": str(d.get("date", ""))
    }


@tool(annotations=ann())
def send_email(
    document_id: DocumentId,
    recipient_email: Annotated[str | None, Field(description="Email destinatario (opzionale)")] = None,
    subject: Annotated[str | None, Field(description="Oggetto email (opzionale)")] = None,
    body: Annotated[str | None, Field(description="Corpo email (opzionale)")] = None,
) -> dict:
    """Invia copia cortesia via email al cliente. Requires FIC_SENDER_EMAIL to be configured in extension
    settings. IMPORTANTE: Chiedere conferma prima di eseguire."""
    if not fic.SENDER_EMAIL:
        return {
            "success": False,
            "error": "❌ Sender email not configured. Open Claude Desktop → Extensions → FattureInCloud → Settings and set the 'Sender email' field, then retry the operation."
        }
    check = _get_document(document_id)
    recipient_email = recipient_email or check.get("entity", {}).get("email", "")
    if not recipient_email:
        return {"success": False, "error": "Nessuna email specificata e cliente senza email in anagrafica"}
    number = check.get("number")
    email_data = {"data": {
        "sender_email": fic.SENDER_EMAIL, "recipient_email": recipient_email, "cc_email": "",
        "subject": subject or f"Fattura n. {number}",
        "body": body or f"In allegato la fattura n. {number}.\n\nCordiali saluti.",
        "include": {"document": True, "delivery_note": False, "attachment": False, "accompanying_invoice": False},
        "attach_pdf": True, "send_copy": False
    }}
    fic.issued_api.schedule_email(
        company_id=fic.COMPANY_ID, document_id=document_id, schedule_email_request=email_data
    )
    return {
        "success": True, "document_id": document_id,
        "number": number, "recipient": recipient_email,
        "message": f"Email con documento #{number} inviata a {recipient_email}"
    }
