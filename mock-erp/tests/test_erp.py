"""
Tests for Mock ERP API: Accounts, Balances, Journal Entries, Idempotency, and Balancing rules.
"""

import os

import pytest
from fastapi.testclient import TestClient

os.environ["TESTING"] = "1"
from app.db import Base, engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def test_health_check() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "mock-erp"}


def test_account_creation_and_listing():
    # 1. Create 1100 Bank Account
    payload_1100 = {
        "code": "1100",
        "name": "Bank Main Account",
        "type": "ASSET",
        "currency": "RWF",
        "external_reference": "GL-1100",
    }
    res = client.post("/api/accounts", json=payload_1100)
    assert res.status_code == 201
    data = res.json()
    assert data["code"] == "1100"
    assert data["external_reference"] == "GL-1100"

    # 2. Create 1200 Loan Receivable
    payload_1200 = {
        "code": "1200",
        "name": "Loan Receivable",
        "type": "ASSET",
        "currency": "RWF",
        "external_reference": "GL-1200",
    }
    res = client.post("/api/accounts", json=payload_1200)
    assert res.status_code == 201

    # 3. Create 4000 Interest Income
    payload_4000 = {
        "code": "4000",
        "name": "Interest Income",
        "type": "INCOME",
        "currency": "RWF",
        "external_reference": "GL-4000",
    }
    res = client.post("/api/accounts", json=payload_4000)
    assert res.status_code == 201

    # 4. List accounts
    res = client.get("/api/accounts")
    assert res.status_code == 200
    accounts = res.json()
    assert len(accounts) == 3
    codes = [a["code"] for a in accounts]
    assert codes == ["1100", "1200", "4000"]


def test_account_idempotency_and_conflicts():
    payload = {
        "code": "1100",
        "name": "Bank Main Account",
        "type": "ASSET",
        "currency": "RWF",
        "external_reference": "GL-1100",
    }
    # First creation
    res1 = client.post("/api/accounts", json=payload)
    assert res1.status_code == 201

    # Second creation with identical payload returns 200 OK
    res2 = client.post("/api/accounts", json=payload)
    assert res2.status_code == 200
    assert res2.json()["code"] == "1100"

    # Conflicting creation with same external_reference but different name
    conflict_payload = dict(payload, name="Altered Name")
    res_conf = client.post("/api/accounts", json=conflict_payload)
    assert res_conf.status_code == 409

    # Reused code with different reference
    code_conflict = {
        "code": "1100",
        "name": "Another Bank",
        "type": "ASSET",
        "currency": "RWF",
        "external_reference": "GL-OTHER",
    }
    res_code_conf = client.post("/api/accounts", json=code_conflict)
    assert res_code_conf.status_code == 409


def test_journal_entry_balancing_rule():
    # Setup accounts
    client.post(
        "/api/accounts",
        json={
            "code": "1100",
            "name": "Bank",
            "type": "ASSET",
            "currency": "RWF",
            "external_reference": "GL-1100",
        },
    )
    client.post(
        "/api/accounts",
        json={
            "code": "1200",
            "name": "Loans",
            "type": "ASSET",
            "currency": "RWF",
            "external_reference": "GL-1200",
        },
    )

    # Unbalanced entry: Debits (100) != Credits (90)
    unbalanced = {
        "external_reference": "TX-UNBALANCED",
        "description": "Unbalanced disbursement",
        "lines": [
            {"account_code": "1200", "entry_type": "DEBIT", "amount": 100.0},
            {"account_code": "1100", "entry_type": "CREDIT", "amount": 90.0},
        ],
    }
    res = client.post("/api/journal-entries", json=unbalanced)
    assert res.status_code == 400
    assert "Debits" in res.json()["detail"]


def test_journal_entry_unknown_account():
    client.post(
        "/api/accounts",
        json={
            "code": "1100",
            "name": "Bank",
            "type": "ASSET",
            "currency": "RWF",
            "external_reference": "GL-1100",
        },
    )
    payload = {
        "external_reference": "TX-UNKNOWN",
        "lines": [
            {"account_code": "9999", "entry_type": "DEBIT", "amount": 50.0},
            {"account_code": "1100", "entry_type": "CREDIT", "amount": 50.0},
        ],
    }
    res = client.post("/api/journal-entries", json=payload)
    assert res.status_code == 400
    assert "not exist" in res.json()["detail"]


def test_journal_entry_idempotency_and_balances():
    # Setup accounts
    for acc in [
        {
            "code": "1100",
            "name": "Bank",
            "type": "ASSET",
            "currency": "RWF",
            "external_reference": "GL-1100",
        },
        {
            "code": "1200",
            "name": "Receivable",
            "type": "ASSET",
            "currency": "RWF",
            "external_reference": "GL-1200",
        },
        {
            "code": "4000",
            "name": "Interest",
            "type": "INCOME",
            "currency": "RWF",
            "external_reference": "GL-4000",
        },
    ]:
        client.post("/api/accounts", json=acc)

    # 1. Post Disbursement: DR 1200 (5,000,000) / CR 1100 (5,000,000)
    disburse_payload = {
        "external_reference": "TX-DISBURSE-001",
        "description": "Loan disbursement LN-001",
        "lines": [
            {"account_code": "1200", "entry_type": "DEBIT", "amount": 5000000.0},
            {"account_code": "1100", "entry_type": "CREDIT", "amount": 5000000.0},
        ],
    }
    res = client.post("/api/journal-entries", json=disburse_payload)
    assert res.status_code == 201

    # Idempotent resend
    res_repeat = client.post("/api/journal-entries", json=disburse_payload)
    assert res_repeat.status_code == 200
    assert res_repeat.json()["external_reference"] == "TX-DISBURSE-001"

    # Conflicting resend
    conflict_payload = dict(
        disburse_payload,
        lines=[
            {"account_code": "1200", "entry_type": "DEBIT", "amount": 4000000.0},
            {"account_code": "1100", "entry_type": "CREDIT", "amount": 4000000.0},
        ],
    )
    res_conflict = client.post("/api/journal-entries", json=conflict_payload)
    assert res_conflict.status_code == 409

    # Check Balances after disbursement:
    # 1200 Asset (Debit 5M): balance = +5,000,000
    bal_1200 = client.get("/api/accounts/1200/balance").json()
    assert bal_1200["balance"] == 5000000.0

    # 1100 Asset (Credit 5M): balance = -5,000,000
    bal_1100 = client.get("/api/accounts/1100/balance").json()
    assert bal_1100["balance"] == -5000000.0

    # 2. Post Repayment: DR 1100 (500,000) / CR 1200 (450,000) / CR 4000 (50,000)
    repay_payload = {
        "external_reference": "TX-REPAY-001",
        "description": "Repayment 1",
        "lines": [
            {"account_code": "1100", "entry_type": "DEBIT", "amount": 500000.0},
            {"account_code": "1200", "entry_type": "CREDIT", "amount": 450000.0},
            {"account_code": "4000", "entry_type": "CREDIT", "amount": 50000.0},
        ],
    }
    res_repay = client.post("/api/journal-entries", json=repay_payload)
    assert res_repay.status_code == 201

    # Check updated balances:
    # 1200: 5,000,000 - 450,000 = 4,550,000
    bal_1200 = client.get("/api/accounts/1200/balance").json()
    assert bal_1200["balance"] == 4550000.0

    # 4000 (Income: Credit 50,000): balance = +50,000
    bal_4000 = client.get("/api/accounts/4000/balance").json()
    assert bal_4000["balance"] == 50000.0

    # 1100: -5,000,000 + 500,000 = -4,500,000
    bal_1100 = client.get("/api/accounts/1100/balance").json()
    assert bal_1100["balance"] == -4500000.0

    # Get entry by reference
    res_get = client.get("/api/journal-entries/TX-DISBURSE-001")
    assert res_get.status_code == 200
    assert res_get.json()["total_debits"] == 5000000.0
