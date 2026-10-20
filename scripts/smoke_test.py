"""Check a deployed stack end to end. Exits with an error if anything is wrong.

python scripts/smoke_test.py <api-url> <api-key> <table-name>

The table name is the TableName output from cdk deploy. This needs AWS credentials
because it reads the item back from DynamoDB.
"""
import json
import sys
import time

import boto3

from send_samples import SAMPLES, post


def check(label, condition):
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    api_url, api_key, table_name = sys.argv[1:4]
    run_id = int(time.time())

    def send(sample):
        payload = {**sample, "txn_id": f"smoke-{sample['txn_id']}-{run_id}"}
        status, body = post(api_url, api_key, payload)
        return payload, status, json.loads(body)

    clean, cash_in, drain, fraud, bad = SAMPLES

    _, status, body = send(clean)
    check("clean payment is stored with score 1 and is not flagged",
          status == 201 and body["risk_score"] == 1 and not body["flagged"])

    _, status, body = send(cash_in)
    check("cash in that adds to the balance scores 1", status == 201 and body["risk_score"] == 1)

    _, status, body = send(drain)
    check("small transfer that empties the account scores 3 and is flagged",
          status == 201 and body["risk_score"] == 3 and body["flagged"])

    fraud_payload, status, body = send(fraud)
    check("large cash out that empties the account scores 4, is flagged and alerts",
          status == 201 and body["risk_score"] == 4 and body["flagged"] and body["alerted"])

    _, status, _ = send(bad)
    check("negative amount is rejected with 400", status == 400)

    status, body = post(api_url, api_key, fraud_payload)
    check("sending the same transaction again returns a duplicate",
          status == 200 and json.loads(body)["status"] == "duplicate")

    status, _ = post(api_url, "not-a-real-key", fraud_payload)
    check("a wrong API key is rejected with 403", status == 403)

    table = boto3.resource("dynamodb").Table(table_name)
    item = table.get_item(Key={"txn_id": fraud_payload["txn_id"]}).get("Item")
    check("the flagged transaction is in DynamoDB with score 4", item is not None and item["risk_score"] == 4)

    print("\nall checks passed")