import os
import sys

# lets the tests import the Lambda code
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "ingest"))

# fake credentials so tests never touch a real account
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
