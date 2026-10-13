import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigw,
    aws_athena as athena,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_dynamodb as dynamodb,
    aws_glue as glue,
    aws_iam as iam,
    aws_kinesisfirehose as firehose,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_s3 as s3,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
)
from constructs import Construct

TRANSACTION_TYPES = ["CASH_IN", "CASH_OUT", "TRANSFER", "PAYMENT", "DEBIT"]
DATABASE_NAME = "fraud_guard"

LAKE_COLUMNS = [
    ("txn_id", "string"), ("type", "string"), ("amount", "double"),
    ("name_orig", "string"), ("name_dest", "string"),
    ("old_balance_org", "double"), ("new_balance_orig", "double"),
    ("flagged", "boolean"), ("received_at", "string"),
    ("risky_type", "int"), ("large_amount", "int"),
    ("orig_mismatch", "int"), ("drained_sender", "int"), ("risk_score", "int"),
]


class FraudGuardStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, alert_email: str = None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        lake = s3.Bucket(
            self, "Lake",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,  # destroy deletes the data
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(prefix="raw/", expiration=Duration.days(90)),
                s3.LifecycleRule(prefix="athena-results/", expiration=Duration.days(7)),
            ],
        )

        table = dynamodb.Table(
            self, "Transactions",
            partition_key=dynamodb.Attribute(name="txn_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )

        alerts = sns.Topic(self, "Alerts", display_name="Fraud Guard alerts")
        if alert_email:
            alerts.add_subscription(subs.EmailSubscription(alert_email))

        stream_role = iam.Role(
            self, "FirehoseRole", assumed_by=iam.ServicePrincipal("firehose.amazonaws.com")
        )
        stream_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:AbortMultipartUpload", "s3:GetBucketLocation", "s3:GetObject",
                    "s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:PutObject",
                ],
                resources=[lake.bucket_arn, lake.arn_for_objects("raw/*"), lake.arn_for_objects("errors/*")],
            )
        )
        stream = firehose.CfnDeliveryStream(
            self, "LakeStream",
            delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=lake.bucket_arn,
                role_arn=stream_role.role_arn,
                prefix="raw/dt=!{timestamp:yyyy-MM-dd}/",
                error_output_prefix="errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/",
                buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                    interval_in_seconds=60, size_in_m_bs=1
                ),
                compression_format="GZIP",
            ),
        )
        stream.node.add_dependency(stream_role)  # role policy must exist first

        ingest = _lambda.Function(
            self, "Ingest",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.ARM_64,
            handler="handler.handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "..", "src", "ingest")
            ),
            timeout=Duration.seconds(10),
            memory_size=256,
            tracing=_lambda.Tracing.ACTIVE,
            log_group=logs.LogGroup(
                self, "IngestLogs",
                retention=logs.RetentionDays.ONE_WEEK,
                removal_policy=RemovalPolicy.DESTROY,
            ),
            environment={
                "TABLE_NAME": table.table_name,
                "STREAM_NAME": stream.ref,
                "TOPIC_ARN": alerts.topic_arn,
                "ALERT_THRESHOLD": "3",
            },
        )
        ingest.add_to_role_policy(
            iam.PolicyStatement(actions=["dynamodb:PutItem"], resources=[table.table_arn])
        )
        ingest.add_to_role_policy(
            iam.PolicyStatement(actions=["firehose:PutRecord"], resources=[stream.attr_arn])
        )
        alerts.grant_publish(ingest)

        api = apigw.RestApi(
            self, "Api",
            rest_api_name="fraud-guard",
            endpoint_types=[apigw.EndpointType.REGIONAL],
            cloud_watch_role=False,  # avoid changing the account-wide logging role
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=50,
                throttling_burst_limit=100,
                tracing_enabled=True,
                metrics_enabled=True,
            ),
        )

        transaction_model = api.add_model(
            "TransactionModel",
            model_name="Transaction",
            content_type="application/json",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="Transaction",
                type=apigw.JsonSchemaType.OBJECT,
                required=["txn_id", "type", "amount", "nameOrig", "nameDest", "oldbalanceOrg", "newbalanceOrig"],
                properties={
                    "txn_id": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING, min_length=1, max_length=64),
                    "type": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING, enum=TRANSACTION_TYPES),
                    "amount": apigw.JsonSchema(type=apigw.JsonSchemaType.NUMBER, minimum=0, exclusive_minimum=True),
                    "nameOrig": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING, min_length=1),
                    "nameDest": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING, min_length=1),
                    "oldbalanceOrg": apigw.JsonSchema(type=apigw.JsonSchemaType.NUMBER, minimum=0),
                    "newbalanceOrig": apigw.JsonSchema(type=apigw.JsonSchemaType.NUMBER, minimum=0),
                },
            ),
        )
        validator = api.add_request_validator(
            "BodyValidator", validate_request_body=True, validate_request_parameters=False
        )
        api.root.add_resource("transactions").add_method(
            "POST",
            apigw.LambdaIntegration(ingest),
            api_key_required=True,
            request_validator=validator,
            request_models={"application/json": transaction_model},
        )

        api_key = api.add_api_key("ClientKey")
        plan = api.add_usage_plan(
            "UsagePlan",
            throttle=apigw.ThrottleSettings(rate_limit=20, burst_limit=40),
            quota=apigw.QuotaSettings(limit=10_000, period=apigw.Period.DAY),
        )
        plan.add_api_key(api_key)
        plan.add_api_stage(stage=api.deployment_stage)

        glue.CfnDatabase(
            self, "Database",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(name=DATABASE_NAME),
        )
        raw_table = glue.CfnTable(
            self, "RawTable",
            catalog_id=self.account,
            database_name=DATABASE_NAME,
            table_input=glue.CfnTable.TableInputProperty(
                name="transactions_raw",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "json",
                    # partition projection, so no crawler is needed
                    "projection.enabled": "true",
                    "projection.dt.type": "date",
                    "projection.dt.format": "yyyy-MM-dd",
                    "projection.dt.range": "2026-01-01,NOW",
                    "storage.location.template": f"s3://{lake.bucket_name}/raw/dt=${{dt}}/",
                },
                partition_keys=[glue.CfnTable.ColumnProperty(name="dt", type="string")],
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    columns=[glue.CfnTable.ColumnProperty(name=n, type=t) for n, t in LAKE_COLUMNS],
                    location=f"s3://{lake.bucket_name}/raw/",
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.openx.data.jsonserde.JsonSerDe"
                    ),
                ),
            ),
        )
        raw_table.node.add_dependency(self.node.find_child("Database"))

        athena.CfnWorkGroup(
            self, "WorkGroup",
            name="fraud-guard",
            state="ENABLED",
            recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                bytes_scanned_cutoff_per_query=1_073_741_824,
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{lake.bucket_name}/athena-results/"
                ),
            ),
        )

        lambda_errors = cloudwatch.Alarm(
            self, "IngestErrorsAlarm",
            metric=ingest.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        lambda_errors.add_alarm_action(cw_actions.SnsAction(alerts))
        api_errors = cloudwatch.Alarm(
            self, "Api5xxAlarm",
            metric=api.metric_server_error(period=Duration.minutes(5)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        api_errors.add_alarm_action(cw_actions.SnsAction(alerts))

        CfnOutput(self, "ApiUrl", value=api.url)
        CfnOutput(self, "ApiKeyId", value=api_key.key_id)
        CfnOutput(self, "LakeBucket", value=lake.bucket_name)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "AthenaDatabase", value=DATABASE_NAME)
        CfnOutput(self, "AthenaWorkGroup", value="fraud-guard")
