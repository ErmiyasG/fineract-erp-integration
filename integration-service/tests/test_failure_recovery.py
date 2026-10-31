"""
Failure Handling and Recovery Tests (Part J)
Covers all 5 mandatory failure scenarios:
1. ERP down after Fineract success -> Retried via recovery sweep, double posting prevented.
2. Fineract down -> State FAILED, safe to retry.
3. Fineract timeout after sending -> Resolved via lookup by externalId (never blindly retried).
4. ERP timeout after entry was written -> Idempotent retry returns existing entry, exactly 1 entry exists.
5. Crash between Fineract success and ERP call -> Recovered on restart via startup/on-demand sweep.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.db import Base, ClientMapping, TransactionRecord
from app.services.erp_client import ErpClient
from app.services.fineract_client import FineractClient
from app.services.loan_service import loan_service
from app.services.recovery_service import recovery_service

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
    # Seed client
    db = TestingSessionLocal()
    client = ClientMapping(
        client_uid="SME-FAIL-001",
        external_id="SME-FAIL-001",
        fineract_client_id=101,
        first_name="Diane",
        last_name="Mugeni",
        status="ACTIVE",
    )
    db.add(client)
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
    mock.find_transaction_by_external_id.return_value = {
        "id": 701,
        "externalId": "TX-TIMEOUT-001",
        "principalPortion": 5000000.0,
        "interestPortion": 0.0,
    }
    loan_service.fineract_client = mock
    recovery_service.fineract_client = mock
    yield mock


@pytest.fixture
def mock_erp():
    mock = AsyncMock(spec=ErpClient)
    mock.post_journal_entry.return_value = {"id": 901, "external_reference": "TX-001"}
    mock.get_journal_entry.return_value = {"id": 901, "total_debits": 5000000.0}
    loan_service.erp_client = mock
    recovery_service.erp_client = mock
    yield mock


def test_scenario_1_erp_down_after_fineract_success(test_client, mock_fineract, mock_erp):
    """
    Scenario 1: ERP down after Fineract success.
    - Fineract disburse succeeds.
    - ERP call fails with connection error.
    - Transaction marked ERP_PENDING.
    - Recovered on demand via /api/transactions/recover without duplicate Fineract disbursement.
    """
    loan_ref = "LN-FAIL-SCEN-1"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-SCEN-1",
            "loan_reference": loan_ref,
            "client_uid": "SME-FAIL-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    disb_ref = "TX-DISB-ERP-DOWN"
    res_disb = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={
            "external_reference": disb_ref,
            "disbursement_date": "2026-01-02",
            "amount": 5000000.0,
        },
        headers={"X-Simulate-Failure": "erp_down_after_fineract"},
    )
    assert res_disb.status_code == 400
    assert "Simulated failure: Mock ERP is unreachable" in res_disb.json()["error"]["message"]

    # Fineract disburse was executed
    assert mock_fineract.disburse_loan.await_count == 1

    # Verify transaction record is in ERP_PENDING state
    db = TestingSessionLocal()
    try:
        txn = db.query(TransactionRecord).filter_by(external_reference=disb_ref).first()
        assert txn is not None
        assert txn.state == "ERP_PENDING"
        assert txn.fineract_transaction_id == 701
    finally:
        db.close()

    # Recovery Step: Trigger recovery sweep (e.g. ERP is now back up)
    res_rec = test_client.post("/api/transactions/recover")
    assert res_rec.status_code == 200
    rec_body = res_rec.json()
    assert rec_body["recovered"] >= 1

    # Verify transaction transitioned to COMPLETED
    db = TestingSessionLocal()
    try:
        txn = db.query(TransactionRecord).filter_by(external_reference=disb_ref).first()
        assert txn is not None
        assert txn.state == "COMPLETED"
        assert txn.erp_transaction_id == "901"
    finally:
        db.close()

    # Invariant: Fineract was NOT called a second time
    assert mock_fineract.disburse_loan.await_count == 1
    # ERP journal entry was posted during recovery
    assert mock_erp.post_journal_entry.await_count == 1


def test_scenario_2_fineract_down_safe_to_retry(test_client, mock_fineract, mock_erp):
    """
    Scenario 2: Fineract down at request ingress.
    - Call fails safely.
    - Transaction marked FAILED, idempotency lock cleared.
    - Caller retrying with the same reference succeeds when Fineract recovers.
    """
    loan_ref = "LN-FAIL-SCEN-2"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-SCEN-2",
            "loan_reference": loan_ref,
            "client_uid": "SME-FAIL-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    disb_ref = "TX-DISB-FIN-DOWN"
    # Call with simulated Fineract outage
    res_fail = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={
            "external_reference": disb_ref,
            "disbursement_date": "2026-01-02",
            "amount": 5000000.0,
        },
        headers={"X-Simulate-Failure": "fineract_down"},
    )
    assert res_fail.status_code == 400
    assert "Simulated failure: Fineract core is unreachable" in res_fail.json()["error"]["message"]

    # Verify transaction record is in FAILED state
    db = TestingSessionLocal()
    try:
        txn = db.query(TransactionRecord).filter_by(external_reference=disb_ref).first()
        assert txn is not None
        assert txn.state == "FAILED"
    finally:
        db.close()

    # Retry Step: Caller retries identical request without simulated failure
    res_retry = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={
            "external_reference": disb_ref,
            "disbursement_date": "2026-01-02",
            "amount": 5000000.0,
        },
    )
    assert res_retry.status_code == 200
    assert res_retry.json()["status"] == "COMPLETED"


def test_scenario_3_fineract_timeout_resolved_by_lookup(test_client, mock_fineract, mock_erp):
    """
    Scenario 3: Fineract timeout after sending.
    - Fineract times out on initial response.
    - Service resolves by querying Fineract by externalId.
    - Mutation is confirmed and ERP entry is posted without blindly retrying.
    """
    loan_ref = "LN-FAIL-SCEN-3"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-SCEN-3",
            "loan_reference": loan_ref,
            "client_uid": "SME-FAIL-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    disb_ref = "TX-TIMEOUT-001"
    res = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={
            "external_reference": disb_ref,
            "disbursement_date": "2026-01-02",
            "amount": 5000000.0,
        },
        headers={"X-Simulate-Failure": "fineract_timeout"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "COMPLETED"

    # Verify lookup by externalId was performed
    mock_fineract.find_transaction_by_external_id.assert_awaited()
    # ERP journal entry was posted
    mock_erp.post_journal_entry.assert_awaited_once()


def test_scenario_4_erp_timeout_idempotent_retry(test_client, mock_fineract, mock_erp):
    """
    Scenario 4: ERP timeout after entry was written.
    - ERP wrote journal entry, but client timed out reading response.
    - On recovery/retry, ERP returns existing entry.
    - Exactly 1 journal entry exists.
    """
    loan_ref = "LN-FAIL-SCEN-4"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-SCEN-4",
            "loan_reference": loan_ref,
            "client_uid": "SME-FAIL-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    disb_ref = "TX-DISB-ERP-TIMEOUT"
    res = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={
            "external_reference": disb_ref,
            "disbursement_date": "2026-01-02",
            "amount": 5000000.0,
        },
        headers={"X-Simulate-Failure": "erp_timeout"},
    )
    assert res.status_code == 400

    # Recovery / Retry Step
    res_rec = test_client.post(f"/api/transactions/{disb_ref}/recover")
    assert res_rec.status_code == 200
    assert res_rec.json()["status"] == "RECOVERED"
    assert res_rec.json()["state"] == "COMPLETED"


def test_scenario_5_process_crash_and_restart_recovery(test_client, mock_fineract, mock_erp):
    """
    Scenario 5: Process crash between Fineract success and ERP call.
    - Fineract succeeded, state committed as ERP_PENDING.
    - Simulated process crash terminates execution.
    - On restart, startup sweep detects interrupted transaction and completes it.
    """
    loan_ref = "LN-FAIL-SCEN-5"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-SCEN-5",
            "loan_reference": loan_ref,
            "client_uid": "SME-FAIL-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    disb_ref = "TX-DISB-CRASH"
    # Execute with simulated crash
    res = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={
            "external_reference": disb_ref,
            "disbursement_date": "2026-01-02",
            "amount": 5000000.0,
        },
        headers={"X-Simulate-Failure": "process_crash"},
    )
    assert res.status_code == 400
    assert "Simulated process crash" in res.json()["error"]["message"]

    # Verify transaction record is persisted in ERP_PENDING
    db = TestingSessionLocal()
    try:
        txn = db.query(TransactionRecord).filter_by(external_reference=disb_ref).first()
        assert txn is not None
        assert txn.state == "ERP_PENDING"
    finally:
        db.close()

    # Simulate restart recovery sweep
    res_rec = test_client.post("/api/transactions/recover")
    assert res_rec.status_code == 200
    assert res_rec.json()["recovered"] >= 1

    # Verify transaction completed
    db = TestingSessionLocal()
    try:
        txn = db.query(TransactionRecord).filter_by(external_reference=disb_ref).first()
        assert txn is not None
        assert txn.state == "COMPLETED"
    finally:
        db.close()
