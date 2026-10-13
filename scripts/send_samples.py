"""Send sample transactions to the API.

python scripts/send_samples.py <api-url> <api-key>
"""
import json
import sys
import time
import urllib.error
import urllib.request

SAMPLES = [
    # clean payment, score 0
    {"txn_id": "demo-clean-001", "type": "PAYMENT", "amount": 42.50, "nameOrig": "C100", "nameDest": "M200",
     "oldbalanceOrg": 800.00, "newbalanceOrig": 757.50},
    # cash in that adds to the balance, still score 0 (the bug the SQL project fixed)
    {"txn_id": "demo-cashin-001", "type": "CASH_IN", "amount": 100, "nameOrig": "C101", "nameDest": "C201",
     "oldbalanceOrg": 50, "newbalanceOrig": 150},
    # small transfer that empties the account, score 2
    {"txn_id": "demo-drain-001", "type": "TRANSFER", "amount": 900, "nameOrig": "C102", "nameDest": "C202",
     "oldbalanceOrg": 900, "newbalanceOrig": 0},
    # large cash out that empties the account, score 3, triggers an alert
    {"txn_id": "demo-fraud-001", "type": "CASH_OUT", "amount": 350000, "nameOrig": "C103", "nameDest": "C203",
     "oldbalanceOrg": 350000, "newbalanceOrig": 0},
    # invalid: negative amount, rejected before it reaches Lambda
    {"txn_id": "demo-bad-001", "type": "PAYMENT", "amount": -5, "nameOrig": "C104", "nameDest": "M204",
     "oldbalanceOrg": 10, "newbalanceOrig": 15},
]


def post(url, key, payload):
    request = urllib.request.Request(
        url.rstrip("/") + "/transactions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    api_url, api_key = sys.argv[1], sys.argv[2]
    run_id = int(time.time())
    for sample in SAMPLES:
        # a fresh id each run, so reruns are not treated as duplicates
        payload = {**sample, "txn_id": f"{sample['txn_id']}-{run_id}"}
        status, body = post(api_url, api_key, payload)
        print(f"{status}  {payload['txn_id']:<28} {body}")
    print("\nSending the last good transaction again to show idempotency:")
    repeat = {**SAMPLES[3], "txn_id": f"{SAMPLES[3]['txn_id']}-{run_id}"}
    status, body = post(api_url, api_key, repeat)
    print(f"{status}  {repeat['txn_id']:<28} {body}")
