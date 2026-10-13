-- Run these in the Athena console. Pick the work group "fraud-guard" and the database "fraud_guard".
-- Data appears about a minute after you send transactions, because Firehose batches records.
-- Always filter on dt. It is the partition, so Athena only reads the days you ask for.

-- 1. how many transactions per day and how many were flagged
SELECT dt, COUNT(*) AS txns, SUM(CASE WHEN flagged THEN 1 ELSE 0 END) AS flagged
FROM transactions_raw
WHERE dt >= date_format(current_date - INTERVAL '7' DAY, '%Y-%m-%d')
GROUP BY dt
ORDER BY dt;

-- 2. the risk score distribution (the same table the SQL project builds)
SELECT risk_score, COUNT(*) AS txns
FROM transactions_raw
WHERE dt >= date_format(current_date - INTERVAL '7' DAY, '%Y-%m-%d')
GROUP BY risk_score
ORDER BY risk_score DESC;

-- 3. the flagged transactions, largest first
SELECT txn_id, type, amount, risk_score, received_at
FROM transactions_raw
WHERE dt >= date_format(current_date - INTERVAL '7' DAY, '%Y-%m-%d') AND flagged
ORDER BY amount DESC
LIMIT 50;

-- 4. which rules fire most often
SELECT SUM(risky_type) AS risky_type, SUM(large_amount) AS large_amount,
       SUM(orig_mismatch) AS balance_mismatch, SUM(drained_sender) AS drained_sender
FROM transactions_raw
WHERE dt >= date_format(current_date - INTERVAL '7' DAY, '%Y-%m-%d');
