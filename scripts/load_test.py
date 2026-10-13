"""Small load test that prints latency percentiles.

python scripts/load_test.py <api-url> <api-key> [requests] [workers]
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


def one_request(args):
    url, key = args
    payload = {
        "txn_id": f"load-{uuid.uuid4()}", "type": "PAYMENT", "amount": 10, "nameOrig": "C1",
        "nameDest": "M1", "oldbalanceOrg": 100, "newbalanceOrig": 90,
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/transactions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": key},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    return status, (time.perf_counter() - start) * 1000


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    api_url, api_key = sys.argv[1], sys.argv[2]
    total = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 10

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one_request, [(api_url, api_key)] * total))
    elapsed = time.perf_counter() - started

    ok_latencies = sorted(ms for status, ms in results if status == 201)
    print(f"requests: {total}, workers: {workers}, seconds: {elapsed:.1f}")
    print("status codes:", dict(Counter(status for status, _ in results)))
    if ok_latencies:
        def pct(p):
            return ok_latencies[min(len(ok_latencies) - 1, int(len(ok_latencies) * p))]
        print(f"successful: {len(ok_latencies)}")
        print(f"latency ms  p50: {statistics.median(ok_latencies):.0f}  p90: {pct(0.90):.0f}  p99: {pct(0.99):.0f}  max: {ok_latencies[-1]:.0f}")
        print(f"throughput: {len(ok_latencies) / elapsed:.1f} successful requests per second")
