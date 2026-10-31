"""
Reconciliation Tests (Part K)
Seeds all four mandatory outcomes:
1. MATCHED
2. MISSING_IN_ERP
3. MISSING_IN_FINERACT
4. AMOUNT_MISMATCH
Validates filtering, read-only behavior, explicit idempotent repair, and reversal handling.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.db import Base, ClientMapping, LoanMapping, TransactionRecord
from app.services.erp_client import ErpClient
from app.services.fineract_client import FineractClient
from app.services.reconciliation_service import reconciliation_service
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
    db = TestingSessionLocal()

    # Seed client and loan
    client = ClientMapping(
        client_uid="SME-REC-001",
        external_id="SME-REC-001",
        fineract_client_id=101,
        first_name="Jean",
        last_name="Paul",
        status="ACTIVE",
    )
    loan = LoanMapping(
        loan_reference="LN-REC-001",
        external_id="LN-REC-001",
        client_uid="SME-REC-001",
        fineract_loan_id=501,
        principal=5000000.0,
        term_months=12,
        status="ACTIVE",
        disbursed=1,
    )
    db.add(client)
    db.add(loan)

    # 1. Seed MATCHED
    t_matched = TransactionRecord(
        external_reference="TX-REC-MATCHED",
        operation_type="DISBURSEMENT",
        loan_reference="LN-REC-001",
        state="COMPLETED",
        payload_hash="hash-1",
        amount=5000000.0,
        fineract_transaction_id=701,
        erp_transaction_id="JE-001",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    # 2. Seed MISSING_IN_ERP
    t_missing_erp = TransactionRecord(
        external_reference="TX-REC-MISSING-ERP",
        operation_type="DISBURSEMENT",
        loan_reference="LN-REC-001",
        state="ERP_PENDING",
        payload_hash="hash-2",
        amount=450000.0,
        fineract_transaction_id=702,
        erp_transaction_id=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    # 3. Seed MISSING_IN_FINERACT
    t_missing_fineract = TransactionRecord(
        external_reference="TX-REC-MISSING-FIN",
        operation_type="DISBURSEMENT",
        loan_reference="LN-REC-001",
        state="FAILED",
        payload_hash="hash-3",
        amount=300000.0,
        fineract_transaction_id=None,
        erp_transaction_id="JE-003",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    # 4. Seed AMOUNT_MISMATCH
    t_mismatch = TransactionRecord(
        external_reference="TX-REC-MISMATCH",
        operation_type="REPAYMENT",
        loan_reference="LN-REC-001",
        state="COMPLETED",
        payload_hash="hash-4",
        amount=500000.0,
        fineract_transaction_id=704,
        erp_transaction_id="JE-004",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    db.add_all([t_matched, t_missing_erp, t_missing_fineract, t_mismatch])
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
def mock_erp():
    mock = AsyncMock(spec=ErpClient)

    async def get_journal_mock(external_reference: str, correlation_id: str | None = None):
        if external_reference == "TX-REC-MATCHED":
            return {"id": 1, "external_reference": external_reference, "total_debits": 5000000.0}
        elif external_reference == "TX-REC-MISSING-ERP":
            return None  # Missing in ERP
        elif external_reference == "TX-REC-MISSING-FIN":
            return {"id": 3, "external_reference": external_reference, "total_debits": 300000.0}
        elif external_reference == "TX-REC-MISMATCH":
            return {
                "id": 4,
                "external_reference": external_reference,
                "total_debits": 450000.0,
            }  # 450k vs 500k
        return None

    mock.get_journal_entry.side_effect = get_journal_mock
    mock.post_journal_entry.return_value = {"id": 999, "external_reference": "TX-REC-MISSING-ERP"}
    reconciliation_service.erp_client = mock
    recovery_service.erp_client = mock
    yield mock


@pytest.fixture
def mock_fineract():
    mock = AsyncMock(spec=FineractClient)
    mock.get_loan_transaction.return_value = {
        "id": 702,
        "principalPortion": 450000.0,
        "interestPortion": 0.0,
        "feeChargesPortion": 0.0,
        "penaltyChargesPortion": 0.0,
    }
    reconciliation_service.fineract_client = mock
    recovery_service.fineract_client = mock
    yield mock


def test_reconciliation_all_four_outcomes(test_client, mock_erp):
    """
    Validates that POST /api/reconciliation identifies all 4 outcomes correctly:
    1. MATCHED
    2. MISSING_IN_ERP
    3. MISSING_IN_FINERACT
    4. AMOUNT_MISMATCH
    """
    res = test_client.post("/api/reconciliation", json={})
    assert res.status_code == 200
    data = res.json()

    summary = data["summary"]
    assert summary["total_records"] == 4
    assert summary["matched"] == 1
    assert summary["missing_in_erp"] == 1
    assert summary["missing_in_fineract"] == 1
    assert summary["amount_mismatch"] == 1

    items_map = {item["external_reference"]: item for item in data["items"]}

    # Verify MATCHED
    assert items_map["TX-REC-MATCHED"]["status"] == "MATCHED"
    assert items_map["TX-REC-MATCHED"]["fineract_amount"] == 5000000.0
    assert items_map["TX-REC-MATCHED"]["erp_amount"] == 5000000.0

    # Verify MISSING_IN_ERP
    assert items_map["TX-REC-MISSING-ERP"]["status"] == "MISSING_IN_ERP"
    assert items_map["TX-REC-MISSING-ERP"]["fineract_amount"] == 450000.0
    assert items_map["TX-REC-MISSING-ERP"]["erp_amount"] is None

    # Verify MISSING_IN_FINERACT
    assert items_map["TX-REC-MISSING-FIN"]["status"] == "MISSING_IN_FINERACT"
    assert items_map["TX-REC-MISSING-FIN"]["fineract_amount"] is None
    assert items_map["TX-REC-MISSING-FIN"]["erp_amount"] == 300000.0

    # Verify AMOUNT_MISMATCH
    assert items_map["TX-REC-MISMATCH"]["status"] == "AMOUNT_MISMATCH"
    assert items_map["TX-REC-MISMATCH"]["fineract_amount"] == 500000.0
    assert items_map["TX-REC-MISMATCH"]["erp_amount"] == 450000.0


def test_reconciliation_filter_by_references(test_client, mock_erp):
    """
    Validates filtering reconciliation by specific external references.
    """
    filter_payload = {
        "references": ["TX-REC-MATCHED", "TX-REC-MISMATCH"],
    }
    res = test_client.post("/api/reconciliation", json=filter_payload)
    assert res.status_code == 200
    data = res.json()

    assert data["summary"]["total_records"] == 2
    assert data["summary"]["matched"] == 1
    assert data["summary"]["amount_mismatch"] == 1
    assert data["summary"]["missing_in_erp"] == 0


def test_reconciliation_is_read_only(test_client, mock_erp):
    """
    Verifies that running reconciliation is strictly read-only and does not mutate ERP or DB records.
    """
    test_client.post("/api/reconciliation", json={})
    # Zero mutations sent to ERP during reconciliation run
    assert mock_erp.post_journal_entry.await_count == 0

    db = TestingSessionLocal()
    try:
        txn = db.query(TransactionRecord).filter_by(external_reference="TX-REC-MISSING-ERP").first()
        assert txn.state == "ERP_PENDING"  # Remains pending until explicit repair
    finally:
        db.close()


def test_explicit_idempotent_repair_action(test_client, mock_erp, mock_fineract):
    """
    Verifies the explicit repair endpoint POST /api/reconciliation/repair.
    Recovers MISSING_IN_ERP transaction by posting missing journal entry to Mock ERP.
    """
    repair_payload = {
        "references": ["TX-REC-MISSING-ERP"],
    }
    res_repair = test_client.post("/api/reconciliation/repair", json=repair_payload)
    assert res_repair.status_code == 200
    repair_data = res_repair.json()
    assert repair_data["repaired_count"] == 1

    # Verify journal entry was posted to ERP
    mock_erp.post_journal_entry.assert_awaited()

    # Re-running repair with the same reference is idempotent
    res_repair_dup = test_client.post("/api/reconciliation/repair", json=repair_payload)
    assert res_repair_dup.status_code == 200
    assert res_repair_dup.json()["repaired_count"] == 1


def test_reversal_reconciliation_handling(test_client, mock_erp):
    """
    Verifies reconciliation properly handles reversals and notes net zero balance.
    """
    db = TestingSessionLocal()
    try:
        t_rev = TransactionRecord(
            external_reference="TX-REC-REV-001",
            operation_type="DISBURSEMENT_REVERSAL",
            loan_reference="LN-REC-001",
            state="COMPLETED",
            payload_hash="hash-rev",
            amount=5000000.0,
            fineract_transaction_id=705,
            erp_transaction_id="JE-REV-001",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(t_rev)
        db.commit()
    finally:
        db.close()

    async def get_journal_with_rev(external_reference: str, correlation_id: str | None = None):
        if external_reference == "TX-REC-REV-001":
            return {"id": 5, "external_reference": external_reference, "total_debits": 5000000.0}
        return None

    mock_erp.get_journal_entry.side_effect = get_journal_with_rev

    res = test_client.post("/api/reconciliation", json={"references": ["TX-REC-REV-001"]})
    assert res.status_code == 200
    data = res.json()
    item = data["items"][0]
    assert item["status"] == "MATCHED"
    assert "Reversal transaction matched" in item["details"]
