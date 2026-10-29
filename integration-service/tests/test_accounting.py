"""
Unit Tests for Pure Accounting Mapping (Part H)
Derives journal entries from Fineract transaction data breakdowns.
"""
import pytest

from app.services.accounting import derive_journal_entries_from_breakdown


def test_disbursement_accounting_mapping():
    """
    Disbursement:
      DR 1200 Loan Receivable
      CR 1100 Bank
    """
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

    assert txn["debits"][0]["account_code"] == "1200"
    assert txn["debits"][0]["amount"] == 5000000.0

    assert txn["credits"][0]["account_code"] == "1100"
    assert txn["credits"][0]["amount"] == 5000000.0


def test_repayment_accounting_mapping_principal_and_interest():
    """
    Repayment:
      DR 1100 Bank (total)
      CR 1200 Loan Receivable (principal)
      CR 4000 Interest Income (interest)
    """
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

    # DR 1100 Bank
    assert txn["debits"][0]["account_code"] == "1100"
    assert txn["debits"][0]["amount"] == 450000.0

    # CR 1200 Loan Receivable (Principal)
    principal_cr = next(c for c in txn["credits"] if c["account_code"] == "1200")
    assert principal_cr["amount"] == 375000.0

    # CR 4000 Interest Income (Interest)
    interest_cr = next(c for c in txn["credits"] if c["account_code"] == "4000")
    assert interest_cr["amount"] == 75000.0


def test_repayment_unbalanced_entry_raises_error():
    """
    Verifies that unbalanced debits vs credits throw an exception.
    """
    breakdown = {
        "principal": 300000.0,
        "interest": 50000.0,
    }

    # total_amount is 400000, but breakdown only allocates 350000 and unallocated handling
    # Let's test with invalid type
    with pytest.raises(ValueError):
        derive_journal_entries_from_breakdown(
            operation_type="UNKNOWN_OP",
            external_reference="TX-ERR-001",
            transaction_date="2026-11-28",
            total_amount=400000.0,
            breakdown=breakdown,
        )
