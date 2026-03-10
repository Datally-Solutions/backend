# tests/conftest.py
import os
import sys
from unittest.mock import MagicMock

# Mock GCP modules before importing main
sys.modules["functions_framework"] = MagicMock()
sys.modules["google.cloud.logging"] = MagicMock()
sys.modules["google.cloud.bigquery"] = MagicMock()
sys.modules["google.cloud.firestore"] = MagicMock()

# Set dummy env vars so main.py imports without crashing
os.environ.setdefault("PROJECT_ID", "test-project")
os.environ.setdefault("BIGQUERY_DATASET", "test-dataset")
os.environ.setdefault("BIGQUERY_TABLE", "test-table")
os.environ.setdefault("INGEST_TOKEN", "test-token")
