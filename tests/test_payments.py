"""register_payment / list_payment_accounts with the FIC SDK mocked: checks the exact
payments_list sent to FIC. No request reaches FattureInCloud."""

import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ACCOUNTS = [{"id": 1001, "name": "Banca Alfa", "type": "standard", "iban": None},
            {"id": 1002, "name": "Banca Beta", "type": "standard", "iban": None},
            {"id": 1003, "name": "Cassa", "type": "standard", "iban": None}]


def _installment(pid, amount, due, status="IssuedDocumentStatus.NOT_PAID", paid=None, account=None, days=30):
    return {"id": pid, "amount": amount, "due_date": due, "status": status, "paid_date": paid,
            "payment_account": {"id": account, "name": "x"} if account else None,
            "payment_terms": {"days": days, "type": "PaymentTermsType.STANDARD"}}


def _doc(installments):
    return {"id": 42, "number": 90, "type": "invoice", "entity": {"id": 5, "name": "Acme"},
            "payments_list": installments}


def _run(server, args, installments):
    accounts = SimpleNamespace(data=[SimpleNamespace(to_dict=lambda a=a: dict(a)) for a in ACCOUNTS])
    doc = MagicMock()
    doc.data.to_dict.return_value = _doc(installments)
    with patch.object(server.info_api, "list_payment_accounts", return_value=accounts), \
         patch.object(server.issued_api, "get_issued_document", return_value=doc), \
         patch.object(server.issued_api, "modify_issued_document") as modify:
        result = json.loads(asyncio.run(server.call_tool("register_payment", args))[0].text)
    sent = modify.call_args.kwargs["modify_issued_document_request"]["data"] if modify.called else None
    return result, sent


def test_single_open_installment_by_account_name(server_module):
    result, sent = _run(server_module, {"document_id": 42, "payment_account": "banca alfa", "paid_date": "2026-10-01"},
                        [_installment(1, 610, "2026-10-23")])
    assert result["success"] is True
    assert sent == {"payments_list": [{
        "id": 1, "amount": 610, "due_date": "2026-10-23", "status": "paid", "paid_date": "2026-10-01",
        "payment_account": {"id": 1001}, "payment_terms": {"days": 30, "type": "standard"}}]}
    assert result["remaining_open"] == 0


def test_account_by_id_and_partial_name(server_module):
    _, sent = _run(server_module, {"document_id": 42, "payment_account": "1002", "paid_date": "2026-10-01"},
                   [_installment(1, 100, "2026-10-23")])
    assert sent["payments_list"][0]["payment_account"] == {"id": 1002}
    _, sent = _run(server_module, {"document_id": 42, "payment_account": "cass", "paid_date": "2026-10-01"},
                   [_installment(1, 100, "2026-10-23")])
    assert sent["payments_list"][0]["payment_account"] == {"id": 1003}


def test_unknown_or_ambiguous_account_is_rejected(server_module):
    result, sent = _run(server_module, {"document_id": 42, "payment_account": "Fineco"},
                        [_installment(1, 100, "2026-10-23")])
    assert result["success"] is False and "Banca Alfa (1001)" in result["error"]
    assert sent is None
    result, sent = _run(server_module, {"document_id": 42, "payment_account": "banca"},  # Banca Alfa, Banca Beta
                        [_installment(1, 100, "2026-10-23")])
    assert result["success"] is False and "più conti" in result["error"]
    assert sent is None


def test_several_open_installments_require_a_choice(server_module):
    rates = [_installment(1, 300, "2026-11-30"), _installment(2, 300, "2026-10-31")]
    result, sent = _run(server_module, {"document_id": 42, "payment_account": "Banca Alfa"}, rates)
    assert result["success"] is False and "installment" in result["error"]
    assert sent is None


def test_chosen_installment_only_others_unchanged(server_module):
    # installments are numbered by due date: rata 1 = 2026-10-31 (id 2), rata 2 = 2026-11-30 (id 1)
    rates = [_installment(1, 300, "2026-11-30"), _installment(2, 300, "2026-10-31", days=60)]
    result, sent = _run(server_module, {"document_id": 42, "payment_account": "Banca Alfa",
                                        "paid_date": "2026-10-02", "installment": 1}, rates)
    assert result["success"] is True
    by_id = {p["id"]: p for p in sent["payments_list"]}
    assert by_id[2]["status"] == "paid" and by_id[2]["paid_date"] == "2026-10-02"
    assert by_id[1] == {"id": 1, "amount": 300, "due_date": "2026-11-30", "status": "not_paid", "paid_date": None,
                        "payment_account": None, "payment_terms": {"days": 30, "type": "standard"}}
    assert result["remaining_open"] == 300


def test_all_open_installments_skip_paid_and_reversed(server_module):
    rates = [_installment(1, 100, "2026-01-31", status="IssuedDocumentStatus.PAID", paid="2026-01-30", account=1002),
             _installment(2, 100, "2026-02-28", status="IssuedDocumentStatus.REVERSED"),
             _installment(3, 100, "2026-03-31"), _installment(4, 100, "2026-04-30")]
    result, sent = _run(server_module, {"document_id": 42, "payment_account": "Banca Alfa",
                                        "paid_date": "2026-10-01", "all_open_installments": True}, rates)
    by_id = {p["id"]: p for p in sent["payments_list"]}
    assert by_id[1]["paid_date"] == "2026-01-30" and by_id[1]["payment_account"] == {"id": 1002}
    assert by_id[2]["status"] == "reversed"
    assert by_id[3]["status"] == by_id[4]["status"] == "paid"
    assert [r["installment"] for r in result["installments_paid"]] == [3, 4]


def test_paid_installment_and_future_date_are_rejected(server_module):
    paid = [_installment(1, 100, "2026-01-31", status="IssuedDocumentStatus.PAID", paid="2026-01-30", account=1001)]
    result, sent = _run(server_module, {"document_id": 42, "payment_account": "Banca Alfa", "installment": 1}, paid)
    assert result["success"] is False and "già 'paid'" in result["error"] and sent is None
    future = (date.today().replace(year=date.today().year + 1)).isoformat()
    result, sent = _run(server_module, {"document_id": 42, "payment_account": "Banca Alfa", "paid_date": future},
                        [_installment(1, 100, "2026-10-23")])
    assert result["success"] is False and "futuro" in result["error"] and sent is None


def test_list_payment_accounts(server_module):
    accounts = SimpleNamespace(data=[SimpleNamespace(to_dict=lambda a=a: {**a, "type": "PaymentAccountType.STANDARD"})
                                     for a in ACCOUNTS])
    with patch.object(server_module.info_api, "list_payment_accounts", return_value=accounts):
        result = json.loads(asyncio.run(server_module.call_tool("list_payment_accounts", {}))[0].text)
    assert result["total"] == 3
    assert result["accounts"][0] == {"id": 1001, "name": "Banca Alfa", "type": "standard", "iban": None}
