import os

import aws_cdk as cdk

from infra.fraud_guard_stack import FraudGuardStack

app = cdk.App()
FraudGuardStack(
    app,
    "FraudGuardStack",
    # optional: cdk deploy -c alertEmail=you@example.com
    alert_email=app.node.try_get_context("alertEmail"),
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION"),
    ),
)
app.synth()
