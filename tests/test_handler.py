import json
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

import handler as handler_module


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        db = boto3.resource("dynamodb", region_name="us-east-1")
        table = db.create_table(
            TableName="txns",
            KeySchema=[{"AttributeName": "txn_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "txn_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        firehose, sns = MagicMock(), MagicMock()
        monkeypatch.setenv("TABLE_NAME", "txns")
        monkeypatch.setenv("STREAM_NAME", "stream")
        monkeypatch.setenv("TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:alerts")
        monkeypatch.setenv("ALERT_THRESHOLD", "3")
        handler_module._get_clients.cache_clear()
        monkeypatch.setattr(handler_module, "_get_clients", lambda: (table, firehose, sns))
        yield table, firehose, sns


def event(payload):
    return {"body": json.dumps(payload)}


CLEAN = {
    "txn_id": "t-100", "type": "PAYMENT", "amount": 50.25,
    "nameOrig": "C1", "nameDest": "M1", "oldbalanceOrg": 500, "newbalanceOrig": 449.75,
}
FRAUD = {
    "txn_id": "t-200", "type": "CASH_OUT", "amount": 300000,
    "nameOrig": "C2", "nameDest": "C9", "oldbalanceOrg": 300000, "newbalanceOrig": 0,
}


def test_clean_transaction_is_stored_and_not_alerted(aws):
    table, firehose, sns = aws
    result = handler_module.handler(event(CLEAN), None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 201
    assert body["risk_score"] == 1 and body["flagged"] is False
    assert table.get_item(Key={"txn_id": "t-100"})["Item"]["type"] == "PAYMENT"
    firehose.put_record.assert_called_once()
    sns.publish.assert_not_called()


def test_fraud_is_flagged_and_alerted(aws):
    table, firehose, sns = aws
    result = handler_module.handler(event(FRAUD), None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 201
    assert body["risk_score"] == 4 and body["flagged"] is True and body["alerted"] is True
    sns.publish.assert_called_once()
    assert table.get_item(Key={"txn_id": "t-200"})["Item"]["flagged"] is True


def test_retry_is_idempotent(aws):
    table, firehose, sns = aws
    first = handler_module.handler(event(FRAUD), None)
    second = handler_module.handler(event(FRAUD), None)
    assert first["statusCode"] == 201
    assert second["statusCode"] == 200
    assert json.loads(second["body"])["status"] == "duplicate"
    # a retry must not send a second lake record or alert
    assert firehose.put_record.call_count == 1
    assert sns.publish.call_count == 1


def test_invalid_payload_returns_400_and_writes_nothing(aws):
    table, firehose, sns = aws
    result = handler_module.handler(event({**CLEAN, "amount": -1}), None)
    assert result["statusCode"] == 400
    assert table.scan()["Count"] == 0
    firehose.put_record.assert_not_called()


def test_bad_json_returns_400(aws):
    result = handler_module.handler({"body": "{not json"}, None)
    assert result["statusCode"] == 400


def test_empty_body_returns_400(aws):
    assert handler_module.handler({"body": None}, None)["statusCode"] == 400


def test_firehose_failure_does_not_fail_the_request(aws):
    from botocore.exceptions import ClientError

    table, firehose, sns = aws
    firehose.put_record.side_effect = ClientError({"Error": {"Code": "ServiceUnavailableException"}}, "PutRecord")
    result = handler_module.handler(event(CLEAN), None)
    assert result["statusCode"] == 201
    assert table.get_item(Key={"txn_id": "t-100"})["Item"]


def test_alert_failure_is_reported_in_the_response(aws):
    from botocore.exceptions import ClientError

    table, firehose, sns = aws
    sns.publish.side_effect = ClientError({"Error": {"Code": "InternalError"}}, "Publish")
    body = json.loads(handler_module.handler(event(FRAUD), None)["body"])
    assert body["flagged"] is True and body["alerted"] is False
