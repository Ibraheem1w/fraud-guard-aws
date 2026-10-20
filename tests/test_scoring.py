from decimal import Decimal

import pytest

from scoring import score_transaction, validate_transaction


def txn(**overrides):
    base = {
        "txn_id": "t1", "type": "PAYMENT", "amount": Decimal("50"),
        "nameOrig": "C1", "nameDest": "M1",
        "oldbalanceOrg": Decimal("500"), "newbalanceOrig": Decimal("450"),
    }
    base.update(overrides)
    return base


def test_clean_payment_scores_one_and_is_not_flagged():
    # balances add up, which is the only rule that fires
    scored = score_transaction(txn())
    assert scored["balance_matches"] == 1
    assert scored["risk_score"] == 1


def test_payment_with_balances_that_do_not_add_up_scores_zero():
    assert score_transaction(txn(newbalanceOrig=Decimal("500")))["risk_score"] == 0


def test_drained_transfer_scores_three():
    # risky type, the sender is emptied and balances add up, amount is small
    scored = score_transaction(txn(type="TRANSFER", amount=Decimal("500"), newbalanceOrig=Decimal("0")))
    assert scored["risky_type"] == 1
    assert scored["drained_sender"] == 1
    assert scored["balance_matches"] == 1
    assert scored["large_amount"] == 0
    assert scored["risk_score"] == 3


def test_large_drained_cash_out_scores_four():
    scored = score_transaction(
        txn(type="CASH_OUT", amount=Decimal("300000"), oldbalanceOrg=Decimal("300000"), newbalanceOrig=Decimal("0"))
    )
    assert scored["large_amount"] == 1
    assert scored["risk_score"] == 4


def test_transfer_where_balances_do_not_add_up_is_not_flagged_by_type_alone():
    scored = score_transaction(txn(type="TRANSFER", amount=Decimal("500"), oldbalanceOrg=Decimal("0"), newbalanceOrig=Decimal("0")))
    assert scored["balance_matches"] == 0
    assert scored["risk_score"] == 1


def test_cash_in_adds_to_the_balance():
    # the original SQL bug: cash in must add, not subtract
    scored = score_transaction(txn(type="CASH_IN", amount=Decimal("50"), oldbalanceOrg=Decimal("100"), newbalanceOrig=Decimal("150")))
    assert scored["balance_matches"] == 1


def test_cash_in_with_wrong_balance_does_not_match():
    scored = score_transaction(txn(type="CASH_IN", amount=Decimal("50"), oldbalanceOrg=Decimal("100"), newbalanceOrig=Decimal("50")))
    assert scored["balance_matches"] == 0


def test_balance_tolerance_is_one_unit():
    assert score_transaction(txn(newbalanceOrig=Decimal("450.5")))["balance_matches"] == 1
    assert score_transaction(txn(newbalanceOrig=Decimal("452")))["balance_matches"] == 0


def test_zero_balance_before_is_not_drained():
    scored = score_transaction(txn(oldbalanceOrg=Decimal("0"), newbalanceOrig=Decimal("0")))
    assert scored["drained_sender"] == 0


def test_valid_payload_has_no_errors():
    assert validate_transaction(txn()) == []


@pytest.mark.parametrize(
    "field", ["txn_id", "type", "amount", "nameOrig", "nameDest", "oldbalanceOrg", "newbalanceOrig"]
)
def test_missing_field_is_reported(field):
    payload = txn()
    del payload[field]
    assert f"missing field: {field}" in validate_transaction(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "REFUND"},
        {"amount": Decimal("0")},
        {"amount": Decimal("-5")},
        {"amount": True},
        {"amount": "50"},
        {"txn_id": ""},
        {"txn_id": "x" * 65},
        {"oldbalanceOrg": Decimal("-1")},
        {"nameOrig": ""},
    ],
)
def test_bad_values_are_rejected(overrides):
    assert validate_transaction(txn(**overrides)) != []


def test_non_object_body_is_rejected():
    assert validate_transaction([1, 2, 3]) == ["body must be a JSON object"]
