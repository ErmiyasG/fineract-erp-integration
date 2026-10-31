"""
End-to-End Loan Lifecycle Integration Test (Part D & H)
Simulates complete loan lifecycle from application to closed obligations met:
Application -> Approval -> Disbursement -> 3 Partial Repayments -> Final Payoff -> Closed
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.db import Base
from app.services.client_service import client_service
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
    # Default returns
    mock.create_client.return_value = {"clientId": 101, "resourceId": 101}
    mock.create_loan_application.return_value = {"loanId": 501, "resourceId": 501}
    mock.approve_loan.return_value = {"resourceId": 501}
    mock.disburse_loan.return_value = {"resourceId": 701}
    mock.repay_loan.return_value = {"resourceId": 801}

    client_service.fineract_client = mock
    loan_service.fineract_client = mock
    yield mock


@pytest.fixture
def mock_erp():
    mock = AsyncMock(spec=ErpClient)
    mock.post_journal_entry.return_value = {"id": 901, "external_reference": "TX-001"}
    mock.get_journal_entry.return_value = {"id": 901, "total_debits": 5000000.0}
    loan_service.erp_client = mock
    yield mock


def test_full_loan_lifecycle_simulation(test_client, mock_fineract, mock_erp):
    """
    Executes all 8 lifecycle stages according to the assessment specifications:
    1. Client Creation
    2. Loan Application (without auto-approval)
    3. Loan Approval
    4. Single Disbursement (5,000,000 RWF) + verify ERP journal entry (DR 1200 / CR 1100)
    5. Partial Repayment 1 (500,000 RWF) + verify ERP journal entry (DR 1100 / CR 1200 / CR 4000)
    6. Partial Repayment 2 (500,000 RWF)
    7. Partial Repayment 3 (500,000 RWF)
    8. Final Payoff (clearing remaining balance) -> Closed (obligations met)
    """
    client_uid = "SME-LIFECYCLE-001"
    loan_ref = "LN-2026-LIFE-001"

    # -------------------------------------------------------------------------
    # STAGE 1: Client Creation
    # -------------------------------------------------------------------------
    res_client = test_client.post(
        "/api/clients",
        json={
            "external_reference": "REF-CLI-LIFE-001",
            "client_uid": client_uid,
            "first_name": "Diane",
            "last_name": "Mugeni",
            "office_id": 1,
            "mobile_no": "+250788112233",
        },
    )
    assert res_client.status_code == 201
    client_body = res_client.json()
    assert client_body["client_uid"] == client_uid
    assert client_body["status"] == "ACTIVE"
    assert "clientId" not in client_body
    assert "fineract_client_id" not in client_body

    # -------------------------------------------------------------------------
    # STAGE 2: Loan Application Submission (No auto-approval)
    # -------------------------------------------------------------------------
    res_loan = test_client.post(
        "/api/loans",
        json={
            "external_reference": "REF-APP-LIFE-001",
            "loan_reference": loan_ref,
            "client_uid": client_uid,
            "principal": 5000000.0,
            "term_months": 12,
            "interest_rate": 18.0,
            "auto_approve": False,
        },
    )
    assert res_loan.status_code == 201
    loan_app_body = res_loan.json()
    assert loan_app_body["loan_reference"] == loan_ref
    assert loan_app_body["client_uid"] == client_uid
    assert loan_app_body["status"] == "SUBMITTED_AND_PENDING_APPROVAL"
    assert loan_app_body["approved_at"] is None
    assert "fineract_loan_id" not in loan_app_body
    assert "id" not in loan_app_body

    # -------------------------------------------------------------------------
    # STAGE 3: Loan Approval
    # -------------------------------------------------------------------------
    res_approve = test_client.post(f"/api/loans/{loan_ref}/approve")
    assert res_approve.status_code == 200
    approve_body = res_approve.json()
    assert approve_body["loan_reference"] == loan_ref
    assert approve_body["status"] == "APPROVED"
    assert approve_body["approved_at"] is not None
    mock_fineract.approve_loan.assert_awaited()

    # -------------------------------------------------------------------------
    # STAGE 4: Loan Disbursement (Single-Disbursement Invariant)
    # -------------------------------------------------------------------------
    disb_res = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={
            "external_reference": "DISB-LIFE-001",
            "disbursement_date": "2026-01-02",
            "amount": 5000000.0,
        },
    )
    assert disb_res.status_code == 200
    disb_body = disb_res.json()
    assert disb_body["status"] == "COMPLETED"
    assert disb_body["disbursed_amount"] == 5000000.0
    mock_fineract.disburse_loan.assert_awaited_once()

    # Verify ERP journal entry for disbursement: DR 1200 / CR 1100
    mock_erp.post_journal_entry.assert_awaited()
    last_erp_call = mock_erp.post_journal_entry.call_args
    assert last_erp_call is not None
    erp_lines = last_erp_call.kwargs["lines"]
    assert len(erp_lines) == 2
    assert erp_lines[0] == {"account_code": "1200", "entry_type": "DEBIT", "amount": 5000000.0}
    assert erp_lines[1] == {"account_code": "1100", "entry_type": "CREDIT", "amount": 5000000.0}

    # Verify Single Disbursement Invariant: Second disbursement must be rejected with 409
    res_disb_dup = test_client.post(
        f"/api/loans/{loan_ref}/disburse",
        json={
            "external_reference": "DISB-LIFE-DUP",
            "disbursement_date": "2026-01-03",
            "amount": 1000000.0,
        },
    )
    assert res_disb_dup.status_code == 409
    assert (
        "Single-disbursement invariant prohibits multiple disbursements"
        in res_disb_dup.json()["error"]["message"]
    )

    # -------------------------------------------------------------------------
    # STAGES 5, 6, 7: Three Partial Repayments
    # -------------------------------------------------------------------------
    repayments = [
        ("REPAY-LIFE-001", "2026-02-02", 500000.0, 425000.0, 75000.0, 4575000.0),
        ("REPAY-LIFE-002", "2026-03-02", 500000.0, 431375.0, 68625.0, 4143625.0),
        ("REPAY-LIFE-003", "2026-04-02", 500000.0, 437845.0, 62155.0, 3705780.0),
    ]

    for ref, date_str, amount, principal_portion, interest_portion, remaining in repayments:
        # Configure mock return for fineract breakdown and loan balance
        mock_fineract.get_loan_transaction.return_value = {
            "id": 801,
            "principalPortion": principal_portion,
            "interestPortion": interest_portion,
            "feeChargesPortion": 0.0,
            "penaltyChargesPortion": 0.0,
        }
        mock_fineract.get_loan_by_id.return_value = {
            "id": 501,
            "status": {"value": "Active"},
            "summary": {"totalOutstanding": remaining},
            "timeline": {"approvedOnDate": "2026-01-01", "actualDisbursementDate": "2026-01-02"},
        }

        res_rep = test_client.post(
            f"/api/loans/{loan_ref}/repayments",
            json={
                "external_reference": ref,
                "transaction_date": date_str,
                "amount": amount,
            },
        )
        assert res_rep.status_code == 200
        rep_body = res_rep.json()
        assert rep_body["status"] == "COMPLETED"
        assert rep_body["breakdown"]["principal"] == principal_portion
        assert rep_body["breakdown"]["interest"] == interest_portion
        assert rep_body["remaining_balance"] == remaining

        # Verify ERP Journal Entry: DR 1100 / CR 1200 / CR 4000
        erp_call = mock_erp.post_journal_entry.call_args
        lines = erp_call.kwargs["lines"]
        assert len(lines) == 3
        # Debit Bank 1100
        assert lines[0] == {"account_code": "1100", "entry_type": "DEBIT", "amount": amount}
        # Credit Loan Receivable 1200 (principal)
        assert lines[1] == {
            "account_code": "1200",
            "entry_type": "CREDIT",
            "amount": principal_portion,
        }
        # Credit Interest Income 4000 (interest)
        assert lines[2] == {
            "account_code": "4000",
            "entry_type": "CREDIT",
            "amount": interest_portion,
        }

    # -------------------------------------------------------------------------
    # STAGE 8: Final Payoff -> Transition to Closed (obligations met)
    # -------------------------------------------------------------------------
    final_amount = 3705780.0
    mock_fineract.get_loan_transaction.return_value = {
        "id": 804,
        "principalPortion": 3705780.0,
        "interestPortion": 0.0,
        "feeChargesPortion": 0.0,
        "penaltyChargesPortion": 0.0,
    }
    mock_fineract.get_loan_by_id.return_value = {
        "id": 501,
        "status": {"value": "Closed (obligations met)"},
        "summary": {"totalOutstanding": 0.0},
        "timeline": {"approvedOnDate": "2026-01-01", "actualDisbursementDate": "2026-01-02"},
    }

    res_final = test_client.post(
        f"/api/loans/{loan_ref}/repayments",
        json={
            "external_reference": "FINAL-PAYOFF-LIFE-001",
            "transaction_date": "2026-05-02",
            "amount": final_amount,
        },
    )
    assert res_final.status_code == 200
    final_rep_body = res_final.json()
    assert final_rep_body["status"] == "COMPLETED"
    assert final_rep_body["remaining_balance"] == 0.0

    # -------------------------------------------------------------------------
    # Verification: GET /api/loans/{loan_ref}
    # -------------------------------------------------------------------------
    res_get_final = test_client.get(f"/api/loans/{loan_ref}")
    assert res_get_final.status_code == 200
    final_state = res_get_final.json()
    assert final_state["loan_reference"] == loan_ref
    assert final_state["status"] == "Closed (obligations met)"
    assert final_state["outstanding_balance"] == 0.0
    assert "fineract_loan_id" not in final_state
    assert "id" not in final_state
