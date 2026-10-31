"""
Unit Tests for Pure Accounting Mapping (Part H)
Tests all accounting transformations derived from Fineract transaction breakdowns:
- Disbursement: DR 1200 / CR 1100
- Disbursement Reversal: DR 1100 / CR 1200
- Repayment (Principal + Interest split): DR 1100 / CR 1200 / CR 4000
- Repayment with Overpayment: DR 1100 / CR 1200 / CR 4000 / CR 2100
- Repayment with Fees & Penalties: DR 1100 / CR 1200 / CR 4000 / CR 4100 / CR 4200
- Repayment Reversal: DR 1200 / DR 4000 / CR 1100
- Unbalanced error verification
"""

import pytest

from app.services.accounting import (
    derive_journal_entries_from_breakdown,
    map_erp_type_to_fineract,
)


def test_erp_to_fineract_type_mapping():
    assert map_erp_type_to_fineract("ASSET") == 1
    assert map_erp_type_to_fineract("LIABILITY") == 2
    assert map_erp_type_to_fineract("EQUITY") == 3
    assert map_erp_type_to_fineract("INCOME") == 4
    assert map_erp_type_to_fineract("EXPENSE") == 5

    # Case insensitivity
    assert map_erp_type_to_fineract("asset") == 1
    assert map_erp_type_to_fineract(" Expense ") == 5

    with pytest.raises(ValueError):
        map_erp_type_to_fineract("INVALID_TYPE")


def test_disbursement_accounting_mapping():
    txn = derive_journal_entries_from_breakdown(
        operation_type="DISBURSEMENT",
        external_reference="TX-DISB-001",
        transaction_date="2026-10-28",
        total_amount=5000000.0,
        breakdown={},
    )
    assert txn["external_reference"] == "TX-DISB-001"
    assert txn["total_amount"] == 5000000.0
    assert len(txn["debits"]) == 1
    assert len(txn["credits"]) == 1
    assert txn["debits"][0] == {"account_code": "1200", "amount": 5000000.0}
    assert txn["credits"][0] == {"account_code": "1100", "amount": 5000000.0}


def test_disbursement_reversal_mapping():
    txn = derive_journal_entries_from_breakdown(
        operation_type="DISBURSEMENT_REVERSAL",
        external_reference="REV-DISB-001",
        transaction_date="2026-10-29",
        total_amount=5000000.0,
        breakdown={},
    )
    assert txn["debits"][0] == {"account_code": "1100", "amount": 5000000.0}
    assert txn["credits"][0] == {"account_code": "1200", "amount": 5000000.0}


def test_repayment_accounting_mapping_principal_and_interest():
    breakdown = {
        "principal": 375000.0,
        "interest": 75000.0,
        "fee": 0.0,
        "penalty": 0.0,
    }
    txn = derive_journal_entries_from_breakdown(
        operation_type="REPAYMENT",
        external_reference="TX-REPAY-001",
        transaction_date="2026-11-28",
        total_amount=450000.0,
        breakdown=breakdown,
    )
    assert txn["total_amount"] == 450000.0
    assert len(txn["debits"]) == 1
    assert len(txn["credits"]) == 2

    # DR 1100 Bank (total)
    assert txn["debits"][0] == {"account_code": "1100", "amount": 450000.0}

    # CR 1200 Loan Receivable (Principal)
    principal_cr = next(c for c in txn["credits"] if c["account_code"] == "1200")
    assert principal_cr["amount"] == 375000.0

    # CR 4000 Interest Income (Interest)
    interest_cr = next(c for c in txn["credits"] if c["account_code"] == "4000")
    assert interest_cr["amount"] == 75000.0


def test_repayment_with_fee_and_penalty():
    breakdown = {
        "principal": 350000.0,
        "interest": 70000.0,
        "fee": 15000.0,
        "penalty": 5000.0,
    }
    txn = derive_journal_entries_from_breakdown(
        operation_type="REPAYMENT",
        external_reference="TX-REPAY-FEES",
        transaction_date="2026-11-28",
        total_amount=440000.0,
        breakdown=breakdown,
    )
    assert len(txn["credits"]) == 4
    fee_cr = next(c for c in txn["credits"] if c["account_code"] == "4100")
    penalty_cr = next(c for c in txn["credits"] if c["account_code"] == "4200")
    assert fee_cr["amount"] == 15000.0
    assert penalty_cr["amount"] == 5000.0


def test_repayment_with_overpayment():
    # Outstanding balance was 200,000 principal + 20,000 interest, but borrower paid 250,000
    breakdown = {
        "principal": 200000.0,
        "interest": 20000.0,
        "fee": 0.0,
        "penalty": 0.0,
    }
    txn = derive_journal_entries_from_breakdown(
        operation_type="REPAYMENT",
        external_reference="TX-OVERPAY-001",
        transaction_date="2026-12-01",
        total_amount=250000.0,
        breakdown=breakdown,
    )
    assert txn["total_amount"] == 250000.0
    # Overpayment (30,000) should be booked to 2100 Overpayment Liability
    overpay_cr = next(c for c in txn["credits"] if c["account_code"] == "2100")
    assert overpay_cr["amount"] == 30000.0

    # Total debits must equal total credits
    assert sum(d["amount"] for d in txn["debits"]) == sum(c["amount"] for c in txn["credits"])


def test_repayment_reversal_mapping():
    breakdown = {
        "principal": 375000.0,
        "interest": 75000.0,
    }
    txn = derive_journal_entries_from_breakdown(
        operation_type="REPAYMENT_REVERSAL",
        external_reference="REV-REPAY-001",
        transaction_date="2026-11-30",
        total_amount=450000.0,
        breakdown=breakdown,
    )
    assert len(txn["debits"]) == 2
    assert len(txn["credits"]) == 1

    rec_dr = next(d for d in txn["debits"] if d["account_code"] == "1200")
    int_dr = next(d for d in txn["debits"] if d["account_code"] == "4000")
    assert rec_dr["amount"] == 375000.0
    assert int_dr["amount"] == 75000.0
    assert txn["credits"][0] == {"account_code": "1100", "amount": 450000.0}


def test_unknown_operation_raises_error():
    with pytest.raises(ValueError):
        derive_journal_entries_from_breakdown(
            operation_type="INVALID_OP",
            external_reference="TX-FAIL",
            transaction_date="2026-10-30",
            total_amount=100.0,
            breakdown={},
        )
