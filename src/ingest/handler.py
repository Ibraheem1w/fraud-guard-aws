import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from scoring import score_transaction, validate_transaction

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TTL_SECONDS = 90 * 24 * 60 * 60


@lru_cache(maxsize=1)
def _get_clients():
    table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return table, boto3.client("firehose"), boto3.client("sns")


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def _parse_body(event):
    body = event.get("body")
    if body is None:
        raise ValueError("request body is empty")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    # DynamoDB does not accept floats, so parse numbers as Decimal
    return json.loads(body, parse_float=Decimal)


def handler(event, context):
    try:
        txn = _parse_body(event)
    except (ValueError, json.JSONDecodeError) as exc:
        return _response(400, {"errors": [f"invalid JSON body: {exc}"]})

    errors = validate_transaction(txn)
    if errors:
        return _response(400, {"errors": errors})

    table, firehose, sns = _get_clients()
    scored = score_transaction(txn)
    threshold = int(os.environ.get("ALERT_THRESHOLD", "3"))
    flagged = scored["risk_score"] >= threshold
    received_at = datetime.now(timezone.utc).isoformat()

    item = {
        "txn_id": txn["txn_id"],
        "type": txn["type"],
        "amount": txn["amount"],
        "name_orig": txn["nameOrig"],
        "name_dest": txn["nameDest"],
        "old_balance_org": txn["oldbalanceOrg"],
        "new_balance_orig": txn["newbalanceOrig"],
        "flagged": flagged,
        "received_at": received_at,
        "expires_at": int(time.time()) + TTL_SECONDS,
        **scored,
    }

    # the condition stops a retry from storing the same txn_id twice
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(txn_id)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            logger.info("duplicate txn_id %s ignored", txn["txn_id"])
            return _response(200, {"txn_id": txn["txn_id"], "status": "duplicate"})
        logger.exception("DynamoDB write failed")
        return _response(500, {"error": "could not store the transaction"})

    # the lake copy and the alert are best effort, so a failure is logged
    # and does not fail the request
    lake_record = {k: v for k, v in item.items() if k != "expires_at"}
    try:
        firehose.put_record(
            DeliveryStreamName=os.environ["STREAM_NAME"],
            Record={"Data": (json.dumps(lake_record, default=_json_default) + "\n").encode("utf-8")},
        )
    except ClientError:
        logger.exception("could not send txn %s to Firehose", txn["txn_id"])

    alerted = False
    if flagged:
        try:
            sns.publish(
                TopicArn=os.environ["TOPIC_ARN"],
                Subject=f"Flagged transaction {txn['txn_id']}"[:100],
                Message=json.dumps(lake_record, default=_json_default),
            )
            alerted = True
        except ClientError:
            logger.exception("could not publish alert for txn %s", txn["txn_id"])

    return _response(
        201,
        {
            "txn_id": txn["txn_id"],
            "status": "stored",
            "risk_score": scored["risk_score"],
            "flagged": flagged,
            "alerted": alerted,
        },
    )
