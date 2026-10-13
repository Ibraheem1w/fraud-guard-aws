from decimal import Decimal

ALLOWED_TYPES = {"CASH_IN", "CASH_OUT", "TRANSFER", "PAYMENT", "DEBIT"}
RISKY_TYPES = {"TRANSFER", "CASH_OUT"}
# cash in adds to the balance, the rest subtract
BALANCE_SIGN = {"CASH_IN": 1, "CASH_OUT": -1, "TRANSFER": -1, "PAYMENT": -1, "DEBIT": -1}
LARGE_AMOUNT = 200_000
BALANCE_TOLERANCE = Decimal("1")

REQUIRED_FIELDS = (
    "txn_id", "type", "amount", "nameOrig", "nameDest", "oldbalanceOrg", "newbalanceOrig",
)


def _is_number(value):
    # bool counts as an int in Python, so exclude it
    return isinstance(value, (int, Decimal, float)) and not isinstance(value, bool)


def validate_transaction(txn):
    if not isinstance(txn, dict):
        return ["body must be a JSON object"]

    errors = [f"missing field: {name}" for name in REQUIRED_FIELDS if name not in txn]
    if errors:
        return errors

    if not isinstance(txn["txn_id"], str) or not 1 <= len(txn["txn_id"]) <= 64:
        errors.append("txn_id must be a string of 1 to 64 characters")
    if txn["type"] not in ALLOWED_TYPES:
        errors.append(f"type must be one of {sorted(ALLOWED_TYPES)}")
    for name in ("nameOrig", "nameDest"):
        if not isinstance(txn[name], str) or not txn[name]:
            errors.append(f"{name} must be a non empty string")
    if not _is_number(txn["amount"]) or txn["amount"] <= 0:
        errors.append("amount must be a number above 0")
    for name in ("oldbalanceOrg", "newbalanceOrig"):
        if not _is_number(txn[name]) or txn[name] < 0:
            errors.append(f"{name} must be a number of 0 or more")
    return errors


def score_transaction(txn):
    amount = txn["amount"]
    old_balance = txn["oldbalanceOrg"]
    new_balance = txn["newbalanceOrig"]

    expected_new = old_balance + BALANCE_SIGN[txn["type"]] * amount
    flags = {
        "risky_type": int(txn["type"] in RISKY_TYPES),
        "large_amount": int(amount >= LARGE_AMOUNT),
        "orig_mismatch": int(abs(expected_new - new_balance) > BALANCE_TOLERANCE),
        "drained_sender": int(old_balance > 0 and new_balance == 0),
    }
    flags["risk_score"] = sum(flags.values())
    return flags
