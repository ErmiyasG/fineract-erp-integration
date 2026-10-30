"""
Unit and Integration Tests for Loan Lifecycle, Idempotency, Single-Disbursement Invariant, and Repayments
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.db import Base, ClientMapping
from app.services.erp_client import ErpClient
from app.services.fineract_client import FineractClient
from app.services.loan_service import loan_service

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    # Seed a test client
    db = TestingSessionLocal()
    client_entry = ClientMapping(
        client_uid="SME-000001",
        external_id="SME-000001",
        fineract_client_id=101,
        first_name="Aline",
        last_name="Mugabo",
        status="ACTIVE",
    )
    db.add(client_entry)
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def test_client():
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def mock_fineract():
    mock = AsyncMock(spec=FineractClient)
    mock.create_loan_application.return_value = {"loanId": 501, "resourceId": 501}
    mock.approve_loan.return_value = {"resourceId": 501}
    mock.disburse_loan.return_value = {"resourceId": 701}
    mock.repay_loan.return_value = {"resourceId": 801}
    mock.get_loan_by_id.return_value = {
        "id": 501,
        "status": {"value": "Active"},
        "summary": {"totalOutstanding": 4500000.0},
        "timeline": {"approvedOnDate": "2026-01-01", "actualDisbursementDate": "2026-01-02"},
    }
    mock.get_loan_transaction.return_value = {
        "id": 801,
        "principalPortion": 450000.0,
        "interestPortion": 50000.0,
        "feeChargesPortion": 0.0,
        "penaltyChargesPortion": 0.0,
    }
    loan_service.fineract_client = mock
    yield mock


@pytest.fixture
def mock_erp():
    mock = AsyncMock(spec=ErpClient)
    mock.post_journal_entry.return_value = {"id": 901, "external_reference": "TX-001"}
    mock.get_journal_entry.return_value = {"id": 901, "total_debits": 5000000.0}
    loan_service.erp_client = mock
    yield mock


def test_create_loan_and_get_by_reference(test_client, mock_fineract):
    create_payload = {
        "external_reference": "REF-LOAN-001",
        "loan_reference": "LN-2026-000001",
        "client_uid": "SME-000001",
        "principal": 5000000.0,
        "term_months": 12,
        "interest_rate": 18.0,
        "auto_approve": True,
    }
    res = test_client.post("/api/loans", json=create_payload)
    assert res.status_code == 201
    data = res.json()
    assert data["loan_reference"] == "LN-2026-000001"
    assert data["client_uid"] == "SME-000001"
    assert data["status"] == "APPROVED"
    assert data["principal"] == 5000000.0
    # Invariant: No Fineract internal loanId leaked
    assert "id" not in data
    assert "fineract_loan_id" not in data

    # Verify Fineract was called with externalId = loan_reference
    mock_fineract.create_loan_application.assert_awaited_once()
    mock_fineract.approve_loan.assert_awaited_once()

    # Get by loan_reference
    res_get = test_client.get("/api/loans/LN-2026-000001")
    assert res_get.status_code == 200
    assert res_get.json()["loan_reference"] == "LN-2026-000001"


def test_single_disbursement_invariant(test_client, mock_fineract, mock_erp):
    # 1. Create Loan
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-LOAN-002",
            "loan_reference": "LN-2026-000002",
            "client_uid": "SME-000001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    # 2. First Disbursement succeeds
    disburse_payload = {
        "external_reference": "DISB-001",
        "disbursement_date": "2026-01-02",
        "amount": 5000000.0,
    }
    res_disb1 = test_client.post("/api/loans/LN-2026-000002/disburse", json=disburse_payload)
    assert res_disb1.status_code == 200
    data1 = res_disb1.json()
    assert data1["status"] == "COMPLETED"
    assert data1["disbursed_amount"] == 5000000.0
    assert mock_fineract.disburse_loan.await_count == 1
    assert mock_erp.post_journal_entry.await_count == 1

    # 3. Resending with identical payload & reference returns 200 OK (idempotent no-op)
    res_repeat = test_client.post("/api/loans/LN-2026-000002/disburse", json=disburse_payload)
    assert res_repeat.status_code == 200
    assert res_repeat.json()["external_reference"] == "DISB-001"
    # Neither Fineract nor ERP should be invoked again
    assert mock_fineract.disburse_loan.await_count == 1
    assert mock_erp.post_journal_entry.await_count == 1

    # 4. Invariant: Attempting another disbursement with a DIFFERENT reference is rejected with 409 Conflict
    diff_payload = {
        "external_reference": "DISB-DIFF-002",
        "disbursement_date": "2026-01-02",
        "amount": 5000000.0,
    }
    res_diff = test_client.post("/api/loans/LN-2026-000002/disburse", json=diff_payload)
    assert res_diff.status_code == 409
    assert "Single-disbursement invariant prohibits multiple disbursements" in res_diff.json()["error"]["message"]
    assert mock_fineract.disburse_loan.await_count == 1


def test_repayment_derives_journal_and_posts(test_client, mock_fineract, mock_erp):
    # Setup loan and disburse
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-LOAN-003",
            "loan_reference": "LN-2026-000003",
            "client_uid": "SME-000001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )
    test_client.post(
        "/api/loans/LN-2026-000003/disburse",
        json={"external_reference": "DISB-003", "disbursement_date": "2026-01-02", "amount": 5000000.0},
    )

    # Post repayment
    repay_payload = {
        "external_reference": "REPAY-001",
        "transaction_date": "2026-02-02",
        "amount": 500000.0,
    }
    res_repay = test_client.post("/api/loans/LN-2026-000003/repayments", json=repay_payload)
    assert res_repay.status_code == 200
    data = res_repay.json()
    assert data["status"] == "COMPLETED"
    assert data["breakdown"]["principal"] == 450000.0
    assert data["breakdown"]["interest"] == 50000.0
    assert data["remaining_balance"] == 4500000.0

    # Verify transaction status endpoint
    res_status = test_client.get("/api/transactions/REPAY-001")
    assert res_status.status_code == 200
    assert res_status.json()["state"] == "COMPLETED"
    assert res_status.json()["amount"] == 500000.0
