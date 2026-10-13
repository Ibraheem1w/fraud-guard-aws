# Design decisions

Why I picked what I picked, and what would change my mind.

## Lambda instead of a server

A fraud API gets traffic in bursts, and a server just sits there between them. Lambda scales with the traffic and costs nothing when nobody is calling it. If requests ever needed long running work or a steady heavy load, I'd look at containers, since an always-on container gets cheaper at that point.

## DynamoDB for the live record

The question the live path asks is "have I seen this transaction id, and what was its score." That's a lookup on one key, which DynamoDB does in a few milliseconds. A relational database would need sizing and would run all the time, and I don't need joins here. The table is on demand with a 90 day TTL, and txn_id is the key. If I ever needed to search by account I'd have to add a second index.

The write uses `attribute_not_exists(txn_id)`, so sending the same transaction twice stores it once and doesn't alert twice.

## Firehose before S3

If the Lambda wrote one file per transaction, the lake would fill with tiny files and Athena would be slow and expensive on them. Firehose collects records for up to a minute and writes bigger gzipped files into one folder per day. The cost is about a minute of delay before data shows up in Athena, which doesn't matter for analysis.

## S3 and Athena for analysis

Questions about the history come up now and then, so I didn't want a database running for them. Athena charges per query. Partition projection lets it find the daily folders without a crawler, and the work group cancels any query that would scan more than 1 GB.

## Validation at the API

API Gateway checks the JSON shape and rejects bad requests before they start a Lambda. The Lambda checks again anyway, because the gateway check doesn't cover everything and the function shouldn't trust whoever called it.

## API key and usage plan

This is the simplest thing that works. The plan caps callers at 20 requests a second and 10,000 a day, so a leaked key can only do so much damage. It isn't real user authentication. For production I'd use Cognito or IAM auth.

## Permissions

The Lambda can call `dynamodb:PutItem` on one table, `firehose:PutRecord` on one stream, and publish to one topic. A test fails if any policy uses a wildcard action, or a wildcard resource other than for X-Ray tracing. The bucket is private, encrypted, and HTTPS only.

## Lake copy and alerts are best effort

DynamoDB is the source of truth. If Firehose or SNS fails after the write, the request still succeeds and the failure is logged. Failing the request would make the client retry, the retry would look like a duplicate and be skipped, and the lake copy would be lost anyway. The proper fix is DynamoDB Streams with a second function, which I haven't built.

## CDK

The whole stack is Python, so it can be reviewed, tested, built with one command and removed with one command. The tests read the generated template, so a change that weakens security fails the build before anything is deployed.

## Left out on purpose

QuickSight, because it costs per user and the Athena queries show the same thing. A queue in front of the Lambda, because the usage plan already caps the rate. Multi region, because the project doesn't need it.

## Where it's weak

Reliability has the gaps above: no dead letter queue and no Streams fan-out. A failed lake copy or alert is only logged. The two alarms I have cover Lambda errors and API 5xx, and neither fires for those, so nobody would be told. I ran one load test (see the README). Most requests take about 140 ms, but a request that lands on a new Lambda environment takes around 2 seconds, and I haven't tuned that. There are no dashboards or runbooks. Security stops at an API key, with no Cognito, no firewall, and no customer managed key.