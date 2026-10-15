"""Payments: payment accounts and registering collected installments on issued documents."""

from typing import Annotated

from pydantic import Field

from .. import fic
from ..app import ann, tool
from ..listing import parse_date, payment_status_name, today_str


def _payment_accounts():
    return [a.to_dict() for a in (fic.info_api.list_payment_accounts(company_id=fic.COMPANY_ID).data or [])]


def _account_row(a):
    return {"id": a.get("id"), "name": (a.get("name") or "").strip(),
            "type": str(a.get("type", "")).split(".")[-1].lower() or None, "iban": a.get("iban")}


def _resolve_account(value, accounts):
    """(account, None) from an id or a name (exact, then unique partial match, case-insensitive),
    or (None, error message listing the valid accounts)."""
    value = str(value).strip()
    if value.isdigit():
        match = [a for a in accounts if a.get("id") == int(value)]
    else:
        match = [a for a in accounts if (a.get("name") or "").strip().lower() == value.lower()]
        if not match:
            match = [a for a in accounts if value.lower() in (a.get("name") or "").lower()]
    if len(match) == 1:
        return match[0], None
    valid = ", ".join(f"{(a.get('name') or '').strip()} ({a.get('id')})" for a in accounts)
    problem = "corrisponde a più conti" if match else "non corrisponde a nessun conto"
    return None, f"payment_account '{value}' {problem}. Conti disponibili: {valid}"


def _installment_payload(p):
    """Writable fields of an existing installment, unchanged."""
    terms = p.get("payment_terms") or {}
    account = p.get("payment_account") or {}
    return {
        "id": p.get("id"),
        "amount": p.get("amount"),
        "due_date": str(p["due_date"]) if p.get("due_date") else None,
        "status": payment_status_name(p),
        "paid_date": str(p["paid_date"]) if p.get("paid_date") else None,
        "payment_account": {"id": account["id"]} if account.get("id") else None,
        "payment_terms": {"days": terms.get("days"), "type": str(terms.get("type", "standard")).split(".")[-1].lower()}
        if terms else None,
    }


@tool(annotations=ann(read_only=True, idempotent=True))
def list_payment_accounts() -> dict:
    """Lista dei conti di pagamento configurati in FattureInCloud (banche, carte, PayPal, cassa...), da
    usare con register_payment."""
    accounts = [_account_row(a) for a in _payment_accounts()]
    return {"total": len(accounts), "accounts": accounts}


@tool(annotations=ann())
def register_payment(
    document_id: Annotated[int, Field(description="ID del documento (fattura, NDC, proforma)")],
    payment_account: Annotated[str, Field(
        description="Conto su cui è stato incassato: nome (es. 'Banca Alfa') o id, vedi list_payment_accounts")],
    paid_date: Annotated[str | None, Field(description="Data dell'incasso YYYY-MM-DD (default: oggi)")] = None,
    installment: Annotated[int | None, Field(
        description="Numero della rata da segnare pagata (1 = prima scadenza); necessario se ci sono più rate aperte", ge=1)] = None,
    all_open_installments: Annotated[bool, Field(
        description="Segna pagate tutte le rate ancora aperte (default: false)")] = False,
) -> dict:
    """Registra l'incasso di una fattura: segna come pagata una rata (o tutte le rate aperte) con data e
    conto di incasso. Funziona anche su fatture già inviate allo SDI; modifica solo le rate indicate, il
    resto del documento e le altre rate restano invariati. Non gestisce incassi parziali di una rata.
    IMPORTANTE: Chiedere sempre conferma all'utente prima di eseguire."""
    try:
        paid = parse_date(paid_date, "paid_date") if paid_date else today_str()
    except ValueError as e:
        return {"success": False, "error": str(e)}
    if paid > today_str():
        return {"success": False, "error": f"paid_date {paid} è nel futuro: registrare l'incasso quando avviene"}

    account, error = _resolve_account(payment_account, _payment_accounts())
    if error:
        return {"success": False, "error": error}

    doc = fic.issued_api.get_issued_document(
        company_id=fic.COMPANY_ID, document_id=document_id, fieldset="detailed").data.to_dict()
    installments = sorted(doc.get("payments_list") or [], key=lambda p: str(p.get("due_date") or ""))
    if not installments:
        return {"success": False, "error": f"Il documento {document_id} non ha rate di pagamento"}
    describe = lambda i, p: (f"rata {i}: {p.get('amount')} € scadenza {p.get('due_date')} "  # noqa: E731
                             f"({payment_status_name(p)})")
    open_idx = [i for i, p in enumerate(installments, 1) if payment_status_name(p) == "not_paid"]

    if all_open_installments:
        targets = open_idx
    elif installment is not None:
        if installment > len(installments):
            return {"success": False, "error": f"Il documento ha {len(installments)} rate: "
                    + "; ".join(describe(i, p) for i, p in enumerate(installments, 1))}
        targets = [installment]
    elif len(open_idx) == 1:
        targets = open_idx
    else:
        return {"success": False, "error": ("Nessuna rata aperta" if not open_idx else
                f"Ci sono {len(open_idx)} rate aperte: indicare installment o all_open_installments") + ". "
                + "; ".join(describe(i, p) for i, p in enumerate(installments, 1))}
    if not targets:
        return {"success": False, "error": "Nessuna rata aperta da segnare come pagata"}
    for i in targets:
        status = payment_status_name(installments[i - 1])
        if status != "not_paid":
            return {"success": False, "error": f"La rata {i} è già '{status}': non viene modificata"}

    payload = [_installment_payload(p) for p in installments]
    for i in targets:
        payload[i - 1].update(status="paid", paid_date=paid, payment_account={"id": account["id"]})
    fic.issued_api.modify_issued_document(
        company_id=fic.COMPANY_ID, document_id=document_id,
        modify_issued_document_request={"data": {"payments_list": payload}})

    still_open = [p for i, p in enumerate(payload, 1) if p["status"] == "not_paid"]
    return {
        "success": True,
        "document_id": document_id,
        "number": doc.get("number"),
        "type": doc.get("type"),
        "client": (doc.get("entity") or {}).get("name"),
        "installments_paid": [{"installment": i, "amount": payload[i - 1]["amount"],
                               "due_date": payload[i - 1]["due_date"]} for i in targets],
        "paid_date": paid,
        "payment_account": _account_row(account)["name"],
        "remaining_open": round(sum(p["amount"] or 0 for p in still_open), 2),
        "message": (f"Registrato incasso di {round(sum(payload[i - 1]['amount'] or 0 for i in targets), 2)} € "
                    f"su {_account_row(account)['name']} il {paid} per il documento n. {doc.get('number')}."),
    }
