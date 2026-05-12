"""Direct unit tests against the mock telecom REST API (no LLM/MCP)."""
import os
import pytest
from fastapi.testclient import TestClient

from telecom_api.app import app
from telecom_api.seed import seed


@pytest.fixture(scope="module", autouse=True)
def _seed_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("data") / "telecom.db"
    os.environ["TELECOM_DB_PATH"] = str(db)
    # patch the cached module path before seeding
    from telecom_api import db as db_mod
    db_mod.DB_PATH = str(db)
    seed(str(db))
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "telecom_api"


def test_get_customer(client):
    r = client.get("/customers/CUST002")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Priya Iyer"
    assert body["account_type"] == "prepaid"


def test_get_customer_404(client):
    r = client.get("/customers/UNKNOWN")
    assert r.status_code == 404


def test_usage_92_pct(client):
    r = client.get("/customers/CUST002/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["pct_used"] == 92.0


def test_overdue_bill(client):
    r = client.get("/customers/CUST003/bills")
    statuses = {b["status"] for b in r.json()}
    assert "overdue" in statuses


def test_change_plan_preview_then_apply(client):
    preview = client.post(
        "/customers/CUST001/plan",
        json={"new_plan_id": "MAX_999", "confirm": False},
    ).json()
    assert preview["requires_confirmation"] is True
    assert "MAX_999" in preview["details"]["new_plan_id"]

    applied = client.post(
        "/customers/CUST001/plan",
        json={"new_plan_id": "MAX_999", "confirm": True},
    ).json()
    assert applied["ok"] is True
    assert applied["details"]["new_plan_id"] == "MAX_999"


def test_recharge_prepaid(client):
    before = client.get("/customers/CUST002").json()["prepaid_balance"]
    r = client.post(
        "/customers/CUST002/recharge",
        json={"amount": 50.0, "payment_method": "upi"},
    ).json()
    assert r["ok"] is True
    after = client.get("/customers/CUST002").json()["prepaid_balance"]
    assert round(after - before, 2) == 50.0


def test_pay_overdue_unsuspends(client):
    # CUST003 starts suspended
    assert client.get("/customers/CUST003").json()["status"] == "suspended"
    bills = client.get("/customers/CUST003/bills").json()
    overdue = next(b for b in bills if b["status"] == "overdue")
    pay = client.post(
        f"/customers/CUST003/bills/{overdue['bill_id']}/pay",
        json={"payment_method": "card"},
    ).json()
    assert pay["ok"] is True
    # service reactivates
    assert client.get("/customers/CUST003").json()["status"] == "active"


def test_block_sim(client):
    r = client.post(
        "/customers/CUST004/sim/block",
        json={"reason": "lost"},
    ).json()
    assert r["ok"] is True
    assert client.get("/customers/CUST004").json()["status"] == "blocked"


def test_network_outage_for_customer(client):
    r = client.get("/network/status", params={"customer_id": "CUST005"}).json()
    assert r["any_active_outage"] is True
    assert r["area_code"] == "BLR-04"


def test_file_complaint(client):
    r = client.post(
        "/customers/CUST005/complaints",
        json={"category": "network", "description": "Drops in evening"},
    ).json()
    assert r["ok"] is True
    assert r["details"]["ticket_id"].startswith("TKT-")
