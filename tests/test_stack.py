import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.fraud_guard_stack import FraudGuardStack


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    stack = FraudGuardStack(
        app, "TestStack", env=cdk.Environment(account="123456789012", region="us-east-1")
    )
    return Template.from_stack(stack)


def test_table_key_billing_ttl_and_backups(template):
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [{"AttributeName": "txn_id", "KeyType": "HASH"}],
            "BillingMode": "PAY_PER_REQUEST",
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
        },
    )


def test_bucket_is_private_encrypted_and_https_only(template):
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True, "BlockPublicPolicy": True,
                "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
            },
            "BucketEncryption": Match.object_like({}),
        },
    )
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [Match.object_like({"Effect": "Deny", "Condition": {"Bool": {"aws:SecureTransport": "false"}}})]
                )
            }
        },
    )


def test_lambda_runtime_tracing_and_environment(template):
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Runtime": "python3.12",
            "Handler": "handler.handler",
            "Architectures": ["arm64"],
            "TracingConfig": {"Mode": "Active"},
            "Environment": {
                "Variables": Match.object_like(
                    {"ALERT_THRESHOLD": "3", "TABLE_NAME": Match.any_value(),
                     "STREAM_NAME": Match.any_value(), "TOPIC_ARN": Match.any_value()}
                )
            },
        },
    )


def test_api_method_requires_key_and_validates_the_body(template):
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {
            "HttpMethod": "POST",
            "ApiKeyRequired": True,
            "RequestValidatorId": Match.any_value(),
            "RequestModels": {"application/json": Match.any_value()},
        },
    )


def test_usage_plan_limits_rate_and_volume(template):
    template.has_resource_properties(
        "AWS::ApiGateway::UsagePlan",
        {"Throttle": {"RateLimit": 20, "BurstLimit": 40}, "Quota": {"Limit": 10000, "Period": "DAY"}},
    )


def test_firehose_writes_daily_partitions(template):
    template.has_resource_properties(
        "AWS::KinesisFirehose::DeliveryStream",
        {
            "ExtendedS3DestinationConfiguration": Match.object_like(
                {"Prefix": "raw/dt=!{timestamp:yyyy-MM-dd}/", "CompressionFormat": "GZIP"}
            )
        },
    )


def test_athena_table_uses_partition_projection(template):
    template.has_resource_properties(
        "AWS::Glue::Table",
        {"TableInput": Match.object_like({"Parameters": Match.object_like({"projection.enabled": "true"})})},
    )


def test_athena_workgroup_caps_the_scan_size(template):
    template.has_resource_properties(
        "AWS::Athena::WorkGroup",
        {"WorkGroupConfiguration": Match.object_like({"BytesScannedCutoffPerQuery": 1073741824})},
    )


def _all_statements(template):
    statements = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        raw = policy["Properties"]["PolicyDocument"]["Statement"]
        statements.extend(raw if isinstance(raw, list) else [raw])
    return statements


def _as_list(value):
    return value if isinstance(value, list) else [value]


def test_no_policy_uses_a_wildcard_action(template):
    for statement in _all_statements(template):
        for action in _as_list(statement["Action"]):
            assert action != "*" and not action.endswith(":*"), f"wildcard action: {action}"


def test_only_tracing_may_use_a_wildcard_resource(template):
    for statement in _all_statements(template):
        if "*" in _as_list(statement["Resource"]):
            assert all(a.startswith("xray:") for a in _as_list(statement["Action"])), statement


def test_lambda_can_only_put_to_the_table(template):
    dynamo_actions = set()
    for statement in _all_statements(template):
        for action in _as_list(statement["Action"]):
            if action.startswith("dynamodb:"):
                dynamo_actions.add(action)
    assert dynamo_actions == {"dynamodb:PutItem"}


def test_an_alarm_exists_for_lambda_errors(template):
    template.has_resource_properties("AWS::CloudWatch::Alarm", {"MetricName": "Errors", "Namespace": "AWS/Lambda"})
