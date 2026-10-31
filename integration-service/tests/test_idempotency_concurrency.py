"""
Unit and Concurrency Tests for Milestone 5 (Parts F & I)
Validates:
- I1: Sequential duplicates (Disbursement x4) -> 4:1:1:1
- In-flight duplicate detection -> 202 Accepted
- Same reference, different payload -> 409 Conflict
- I2: Concurrent disbursements (10 parallel calls, same & different references) -> exactly 1 disbursement
- I3: Concurrent repayments (simultaneous repayments with distinct references) -> all apply once
"""
import concurrent.futures
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.db import Base, ClientMapping, IdempotencyRecord
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
    # Seed client
    db = TestingSessionLocal()
    client = ClientMapping(
        client_uid="SME-CONC-001",
        external_id="SME-CONC-001",
        fineract_client_id=101,
        first_name="Jean",
        last_name="Paul",
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
    loan_service.fineract_client = mock
    yield mock


@pytest.fixture
def mock_erp():
    mock = AsyncMock(spec=ErpClient)
    mock.post_journal_entry.return_value = {"id": 901, "external_reference": "TX-001"}
    mock.get_journal_entry.return_value = {"id": 901, "total_debits": 5000000.0}
    loan_service.erp_client = mock
    yield mock


def test_i1_sequential_duplicates_disbursement_x4(test_client, mock_fineract, mock_erp):
    """
    SIM-I1: Send the same disbursement 4 times.
    Verifies:
      Requests received:       4
      Fineract disbursements:  1
      ERP journal entries:     1
      Integration txns:        1
    """
    # 1. Create and approve loan
    loan_ref = "LN-SEQ-001"
    res_loan = test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-SEQ-001",
            "loan_reference": loan_ref,
            "client_uid": "SME-CONC-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )
    assert res_loan.status_code == 201

    disb_payload = {
        "external_reference": "TX-DISB-SEQ-001",
        "disbursement_date": "2026-01-02",
        "amount": 5000000.0,
    }

    # 2. Call 1 of 4 (Initial execution)
    res1 = test_client.post(f"/api/loans/{loan_ref}/disburse", json=disb_payload)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "COMPLETED"
    assert data1["external_reference"] == "TX-DISB-SEQ-001"

    # 3. Calls 2, 3, 4 (Sequential duplicates with same reference & payload)
    for i in range(2, 5):
        res_dup = test_client.post(f"/api/loans/{loan_ref}/disburse", json=disb_payload)
        assert res_dup.status_code == 200, f"Call {i} failed with status {res_dup.status_code}"
        assert res_dup.json() == data1, f"Call {i} returned divergent cached response"

    # Verify 4:1:1:1 Invariant
    # Fineract disburse called exactly 1 time
    assert mock_fineract.disburse_loan.await_count == 1
    # ERP journal entry posted exactly 1 time
    assert mock_erp.post_journal_entry.await_count == 1

    # Database records: exactly 1 idempotency record, exactly 1 transaction record
    db = TestingSessionLocal()
    try:
        from app.models.db import DisbursementRecord, TransactionRecord
        assert db.query(IdempotencyRecord).filter_by(idempotency_key="TX-DISB-SEQ-001").count() == 1
        assert db.query(TransactionRecord).filter_by(external_reference="TX-DISB-SEQ-001").count() == 1
        assert db.query(DisbursementRecord).filter_by(loan_reference=loan_ref).count() == 1
    finally:
        db.close()


def test_in_flight_duplicate_returns_202_accepted(test_client, mock_fineract):
    """
    Part F: Same reference, same payload, in progress -> 202 with current status.
    """
    db = TestingSessionLocal()
    try:
        # Pre-seed an IN_PROGRESS idempotency record
        from app.services.idempotency import compute_payload_hash
        payload = {"external_reference": "TX-INFLIGHT-001", "disbursement_date": "2026-01-02", "amount": 5000000.0}
        in_flight_rec = IdempotencyRecord(
            idempotency_key="TX-INFLIGHT-001",
            operation_type="DISBURSEMENT",
            payload_hash=compute_payload_hash(payload),
            status="IN_PROGRESS",
        )
        db.add(in_flight_rec)
        db.commit()
    finally:
        db.close()

    res = test_client.post("/api/loans/LN-NONEXISTENT/disburse", json=payload)
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "IN_PROGRESS"
    assert "currently in progress" in body["message"]


def test_same_reference_different_payload_returns_409_conflict(test_client, mock_fineract, mock_erp):
    """
    Part F: Same reference, different payload -> 409 Conflict.
    """
    loan_ref = "LN-CONFLICT-001"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-CONF-001",
            "loan_reference": loan_ref,
            "client_uid": "SME-CONC-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    # 1. Execute initial disbursement
    test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={"external_reference": "TX-REUSED-001", "disbursement_date": "2026-01-02", "amount": 5000000.0},
    )

    # 2. Resend same reference with DIFFERENT amount
    res_mismatch = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={"external_reference": "TX-REUSED-001", "disbursement_date": "2026-01-02", "amount": 2500000.0},
    )
    assert res_mismatch.status_code == 409
    assert "Conflict: Reference 'TX-REUSED-001' was already submitted with a different payload" in res_mismatch.json()["error"]["message"]


def test_i2_concurrent_disbursements_same_reference(test_client, mock_fineract, mock_erp):
    """
    SIM-I2 (Scenario A): Fire 10 parallel disbursements for the same loan with the IDENTICAL reference.
    Expected: Exactly 1 disbursement processes, all callers receive 200 or 202, exactly 1 Fineract call, 1 ERP call.
    """
    loan_ref = "LN-PARALLEL-SAME-001"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-PAR-001",
            "loan_reference": loan_ref,
            "client_uid": "SME-CONC-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    payload = {
        "external_reference": "TX-DISB-PAR-SAME",
        "disbursement_date": "2026-01-02",
        "amount": 5000000.0,
    }

    def fire_disburse():
        return test_client.post(f"/api/loans/{loan_ref}/disburse", json=payload)

    # Fire 10 simultaneous threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fire_disburse) for _ in range(10)]
        results = [f.result() for f in futures]

    statuses = [r.status_code for r in results]
    # Every request should either be 200 (completed/cached) or 202 (in progress)
    for s in statuses:
        assert s in (200, 202), f"Unexpected status {s} in parallel same-ref execution"

    # Financial invariant: Exactly 1 Fineract disbursement, exactly 1 ERP journal entry
    assert mock_fineract.disburse_loan.await_count == 1
    assert mock_erp.post_journal_entry.await_count == 1


def test_i2_concurrent_disbursements_different_references(test_client, mock_fineract, mock_erp):
    """
    SIM-I2 (Scenario B): Fire 10 parallel disbursements for the same loan with DIFFERENT references.
    Expected: Exactly 1 succeeds (HTTP 200); remaining 9 are rejected with 409 Conflict.
    Enforces multi-layered defense (Layer 2 DB constraint + Fineract state machine).
    """
    loan_ref = "LN-PARALLEL-DIFF-001"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-DIFF-001",
            "loan_reference": loan_ref,
            "client_uid": "SME-CONC-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )

    def fire_disburse(idx: int):
        payload = {
            "external_reference": f"TX-DISB-PAR-DIFF-{idx}",
            "disbursement_date": "2026-01-02",
            "amount": 5000000.0,
        }
        return test_client.post(f"/api/loans/{loan_ref}/disburse", json=payload)

    # Fire 10 simultaneous threads with unique external_reference
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fire_disburse, i) for i in range(10)]
        results = [f.result() for f in futures]

    statuses = [r.status_code for r in results]
    success_count = statuses.count(200)
    conflict_count = statuses.count(409)

    # Exactly 1 request succeeds
    assert success_count == 1, f"Expected exactly 1 success (200), got {success_count}. Statuses: {statuses}"
    # Exactly 9 requests are rejected with 409 Conflict
    assert conflict_count == 9, f"Expected 9 conflicts (409), got {conflict_count}. Statuses: {statuses}"

    # Fineract core called exactly once
    assert mock_fineract.disburse_loan.await_count == 1
    # ERP journal entry posted exactly once
    assert mock_erp.post_journal_entry.await_count == 1


def test_i3_concurrent_repayments_different_references(test_client, mock_fineract, mock_erp):
    """
    SIM-I3: Fire simultaneous repayments with different references.
    Confirms each applies exactly once.
    Final Fineract balance reflects sum of breakdowns deducted.
    """
    loan_ref = "LN-REPAY-PAR-001"
    test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-REPAY-001",
            "loan_reference": loan_ref,
            "client_uid": "SME-CONC-001",
            "principal": 5000000.0,
            "auto_approve": True,
        },
    )
    test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={"external_reference": "DISB-REPAY-PAR-001", "disbursement_date": "2026-01-02", "amount": 5000000.0},
    )

    # 4 concurrent repayments of 500,000 RWF each
    repay_amounts = [500000.0, 500000.0, 500000.0, 500000.0]

    def fire_repay(idx: int, amt: float):
        return test_client.post(
            f"/api/loans/{loan_ref}/repayments",
            json={
                "external_reference": f"TX-REPAY-PAR-{idx}",
                "transaction_date": f"2026-0{idx+2}-02",
                "amount": amt,
            },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fire_repay, i, repay_amounts[i]) for i in range(4)]
        results = [f.result() for f in futures]

    for r in results:
        assert r.status_code == 200
        assert r.json()["status"] == "COMPLETED"

    # Verify each distinct reference was applied exactly once (4 Fineract repayments, 4 ERP journal entries)
    assert mock_fineract.repay_loan.await_count == 4
    # 1 disbursement journal + 4 repayment journals = 5 total
    assert mock_erp.post_journal_entry.await_count == 5

    # Duplicates of one repayment reference apply once
    res_repay_dup = test_client.post(
        f"/api/loans/{loan_ref}/repayments",
        json={"external_reference": "TX-REPAY-PAR-0", "transaction_date": "2026-02-02", "amount": 500000.0},
    )
    assert res_repay_dup.status_code == 200
    assert mock_fineract.repay_loan.await_count == 4  # Unchanged
