# tests/test_api.py
import pytest
import sys
import os
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import firebase_admin
from main import app
from dependencies import get_current_uid, get_household_id, get_fs_client, get_bq_client

firebase_admin.initialize_app = MagicMock()
firebase_admin._apps = {"default": MagicMock()}


TEST_UID = "test-uid-123"
TEST_HOUSEHOLD_ID = "test-household-456"

# ─── Mock Firestore client ────────────────────────────────────────────────────


def make_mock_fs():
    return MagicMock()


def make_mock_bq():
    return MagicMock()


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_fs():
    fs = make_mock_fs()
    app.dependency_overrides[get_current_uid] = lambda: TEST_UID
    app.dependency_overrides[get_household_id] = lambda: TEST_HOUSEHOLD_ID
    app.dependency_overrides[get_fs_client] = lambda: fs
    yield fs
    app.dependency_overrides.clear()


@pytest.fixture
def mock_bq():
    bq = make_mock_bq()
    app.dependency_overrides[get_current_uid] = lambda: TEST_UID
    app.dependency_overrides[get_household_id] = lambda: TEST_HOUSEHOLD_ID
    app.dependency_overrides[get_bq_client] = lambda: bq
    yield bq
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    app.dependency_overrides[get_current_uid] = lambda: TEST_UID
    app.dependency_overrides[get_household_id] = lambda: TEST_HOUSEHOLD_ID
    yield TestClient(app)
    app.dependency_overrides.clear()


# ─── /health ──────────────────────────────────────────────────────────────────


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ─── households ───────────────────────────────────────────────────────────────


def test_get_my_household(mock_fs):
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.id = TEST_HOUSEHOLD_ID
    mock_doc.to_dict.return_value = {
        "join_code": "ABC123",
        "device_id": "30251CB7B3F8",
        "cats": [
            {"name": "Sully", "weight_kg": 3.5},
            {"name": "Krokmou", "weight_kg": 7.5},
        ],
        "member_uids": [TEST_UID],
        "admin_uid": TEST_UID,
    }
    mock_fs.collection.return_value.document.return_value.get.return_value = mock_doc

    response = TestClient(app).get("/api/v1/households/me")
    assert response.status_code == 200
    data = response.json()
    assert data["join_code"] == "ABC123"
    assert len(data["cats"]) == 2
    assert data["cats"][0]["name"] == "Sully"


def test_get_my_household_not_found(mock_fs):
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_fs.collection.return_value.document.return_value.get.return_value = mock_doc

    response = TestClient(app).get("/api/v1/households/me")
    assert response.status_code == 404


def test_get_box_state(mock_fs):
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "status": "clean",
        "fill_percent": 0.3,
        "usages_since_clean": 5,
        "last_cat": "Krokmou",
        "last_action": "Caca 🟤",
        "last_used": None,
        "last_cleaned": None,
    }
    mock_fs.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = mock_doc

    response = TestClient(app).get("/api/v1/households/me/box-state")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "clean"
    assert data["fill_percent"] == 0.3
    assert data["usages_since_clean"] == 5


def test_get_box_state_not_found(mock_fs):
    mock_doc = MagicMock()
    mock_doc.exists = False
    mock_fs.collection.return_value.document.return_value.collection.return_value.document.return_value.get.return_value = mock_doc

    response = TestClient(app).get("/api/v1/households/me/box-state")
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


# ─── health_alerts ────────────────────────────────────────────────────────────


def test_get_health_alerts(mock_fs):
    mock_doc = MagicMock()
    mock_doc.id = "alert-1"
    mock_doc.to_dict.return_value = {
        "timestamp": datetime.now(timezone.utc),
        "cat_id": "krokmou",
        "cat_name": "Krokmou",
        "alert_type": "no_pee_24h",
        "title": "Krokmou n'a pas fait pipi",
        "description": "...",
        "severity": "critical",
        "acknowledged": False,
        "source": "health_checker",
    }
    mock_fs.collection.return_value.document.return_value.collection.return_value.order_by.return_value.limit.return_value.get.return_value = [
        mock_doc
    ]

    response = TestClient(app).get("/api/v1/health-alerts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["cat_name"] == "Krokmou"
    assert data[0]["severity"] == "critical"


def test_acknowledge_alert(mock_fs):
    mock_fs.collection.return_value.document.return_value.collection.return_value.document.return_value.update = MagicMock()

    response = TestClient(app).patch("/api/v1/health-alerts/alert-1/acknowledge")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ─── queries ──────────────────────────────────────────────────────────────────


def test_daily_usage(mock_bq):
    mock_row = MagicMock()
    mock_row.date = date(2026, 3, 10)
    mock_row.count = 5
    mock_row.avg_duration = 120.0
    mock_bq.query.return_value.result.return_value = [mock_row]

    response = TestClient(app).get("/api/v1/query/daily-usage?days=7")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["count"] == 5


def test_weekly_health(mock_bq):
    mock_row = MagicMock()
    mock_row.week = date(2026, 3, 4)
    mock_row.total_events = 20
    mock_row.anomaly_count = 2
    mock_bq.query.return_value.result.return_value = [mock_row]

    response = TestClient(app).get("/api/v1/query/weekly-health?weeks=4")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["total_events"] == 20


def test_daily_usage_invalid_days(client):
    response = client.get("/api/v1/query/daily-usage?days=0")
    assert response.status_code == 422


def test_weekly_health_invalid_weeks(client):
    response = client.get("/api/v1/query/weekly-health?weeks=100")
    assert response.status_code == 422
