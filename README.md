# Fraud Guard on AWS

This is the cloud version of my fraud detection SQL project. Instead of scoring a table after the fact, it scores each transaction as it comes in. You send a transaction to an API, it gets a risk score and is saved, and if the score is high an alert email goes out.

It is all written with the AWS CDK, so one command builds it and one command deletes it. I deployed it to my own AWS account in us-east-1 and tested it there. 47 unit tests pass, and I ran sample transactions, Athena queries, a smoke test and a load test against the live stack.

## How it works

A client sends a POST to /transactions with an API key. API Gateway checks the key, the rate limit and the shape of the JSON. Then a Lambda function scores the transaction from 0 to 4 and does three things with it:

- saves the record in DynamoDB, looked up by transaction id
- sends a copy through Firehose to S3, so I can query it with Athena
- sends an email through SNS if the score is 3 or higher

The score is how many of these rules fire:

- the type is TRANSFER or CASH_OUT
- the amount is 200,000 or more
- the sender's balance changed by exactly the amount (cash in adds, everything else subtracts)
- the sender had money and ends with zero

A score of 3 or more counts as flagged. These are the same rules as my SQL project and they come from one simulated dataset, so treat them as a starting point, not a trained model.

The balance rule looks backwards at first. In PaySim, about 99% of fraud rows have balances that add up, while most normal rows don't, so a balance that adds up is the risk signal there. On real bank data I'd expect that to be different, which is another reason not to trust these rules outside this dataset. The first version had this rule the other way around. I flipped it after the live tests, so the sample and smoke test results I got on AWS used the old scores. The load test numbers don't depend on it. On the full PaySim data in MySQL, a score of 3 or more catches 98.9% of the fraud but also flags about 271,000 normal transactions, so most alerts would be false alarms. The details are in the SQL project.

The reasons for each service choice are in docs/design-decisions.md.

## Tests

The unit tests use moto to mock AWS in memory, so they run without an account and GitHub Actions runs them on every push. To run them yourself:

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements-dev.txt
    python -m pytest -q

test_scoring.py checks each rule and bad inputs. test_handler.py checks the Lambda, including that a retry doesn't store or alert twice and that a failed Firehose or SNS call doesn't fail the request. test_stack.py reads the generated template and checks things like no wildcard permissions.

Mocks can't prove the real services are wired up correctly, so I also checked the deployed stack, which is what the next sections cover.

## Deploy

You need an AWS account with the CLI set up, Python 3.12, Node 20 or newer, and the CDK CLI, which you get with npm install -g aws-cdk.

    cdk bootstrap
    cdk deploy -c alertEmail=you@example.com

Bootstrap only needs to run once per account and region. AWS sends a confirmation email to the alert address and you have to click the link in it, or no alerts will arrive. The deploy prints ApiUrl, ApiKeyId and TableName. To get the key itself:

    aws apigateway get-api-key --api-key <ApiKeyId> --include-value --query value --output text

A new key can take a few minutes to start working. Until then every request returns 403.

## Check that it works

Send some sample transactions (clean, cash in, two risky, one invalid, one repeat):

    python scripts/send_samples.py <ApiUrl> <key>

Firehose takes about a minute to write to S3. After that, open Athena, pick the fraud-guard work group and the fraud_guard database, and run the queries in athena/queries.sql.

To check the whole deployed stack in one go:

    python scripts/smoke_test.py <ApiUrl> <key> <TableName>

It prints ok or FAIL for each check and reads the flagged transaction back from DynamoDB.

## Load test

    python scripts/load_test.py <ApiUrl> <key> 200 10

I sent 200 requests, 10 at a time. 163 went through and 37 got a 429 from the rate limit. Median latency was 137 ms and p90 was 156 ms. The p99 was about 2.3 seconds, which was the slowest couple of requests. Those were Lambda cold starts, about 1.5 seconds of work on the first call in a new environment. Warm requests stayed around 140 to 160 ms.

Estimated from AWS list prices, it costs roughly $5 per million transactions, mostly API Gateway.
## Cleanup

    cdk destroy

This deletes everything, including the stored data. I set a billing alert before deploying, and you should too.

## What's missing

If the Firehose call fails after the DynamoDB write, that record never reaches S3. DynamoDB Streams with a second function would fix that. With more time I'd also add Cognito instead of an API key, a firewall in front of the API, Parquet files so Athena scans less, a customer managed encryption key, and a queue with a dead letter queue in front of the Lambda for traffic spikes.
