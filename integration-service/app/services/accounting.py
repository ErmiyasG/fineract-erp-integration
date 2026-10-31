"""
Accounting Mapping and Domain Rules (Part H)
Maps ERP account types to Apache Fineract GL classifications.
Derives double-entry balanced journal entries directly from Fineract transaction data breakdowns.
Handles Disbursements, Repayments, Overpayments, Fees, Penalties, and Reversals.
"""

from enum import Enum
from typing import Any


class ERPAccountType(str, Enum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class FineractGLAccountType(int, Enum):
    ASSET = 1
    LIABILITY = 2
    EQUITY = 3
    INCOME = 4
    EXPENSE = 5


# Explicit, tested mapping of ERP account types to Fineract GL types
ERP_TO_FINERACT_TYPE_MAP: dict[str, int] = {
    ERPAccountType.ASSET.value: FineractGLAccountType.ASSET.value,
    ERPAccountType.LIABILITY.value: FineractGLAccountType.LIABILITY.value,
    ERPAccountType.EQUITY.value: FineractGLAccountType.EQUITY.value,
    ERPAccountType.INCOME.value: FineractGLAccountType.INCOME.value,
    ERPAccountType.EXPENSE.value: FineractGLAccountType.EXPENSE.value,
}


def map_erp_type_to_fineract(erp_type: str) -> int:
    """
    Pure function explicitly mapping ERP account types to Fineract integer classifications.
    Raises ValueError if unsupported type is supplied.
    """
    normalized = erp_type.strip().upper()
    if normalized not in ERP_TO_FINERACT_TYPE_MAP:
        raise ValueError(
            f"Unsupported ERP account type: '{erp_type}'. "
            f"Expected one of {list(ERP_TO_FINERACT_TYPE_MAP.keys())}"
        )
    return ERP_TO_FINERACT_TYPE_MAP[normalized]


def derive_journal_entries_from_breakdown(
    operation_type: str,
    external_reference: str,
    transaction_date: str,
    total_amount: float,
    breakdown: dict[str, Any],
    bank_account_code: str = "1100",
    receivable_account_code: str = "1200",
    interest_income_code: str = "4000",
    fee_income_code: str = "4100",
    penalty_income_code: str = "4200",
    overpayment_account_code: str = "2100",
) -> dict[str, Any]:
    """
    Pure unit-tested function deriving double-entry balanced journal entries
    directly from Fineract transaction data breakdown.

    Standard Operations:
    1. Disbursement:
       DR 1200 Loan Receivable (total_amount)
       CR 1100 Bank (total_amount)

    2. Repayment:
       DR 1100 Bank (total_amount)
       CR 1200 Loan Receivable (Principal portion)
       CR 4000 Interest Income (Interest portion)
       CR 4100 Fee Income (Fee portion, if any)
       CR 4200 Penalty Income (Penalty portion, if any)
       CR 2100 Overpayment Liability (Excess over outstanding, if any)

    Reversals:
    3. Disbursement Reversal:
       DR 1100 Bank (total_amount)
       CR 1200 Loan Receivable (total_amount)

    4. Repayment Reversal:
       DR 1200 Loan Receivable (Principal portion)
       DR 4000 Interest Income (Interest portion)
       CR 1100 Bank (total_amount)
    """
    debits: list[dict[str, Any]] = []
    credits: list[dict[str, Any]] = []

    op = operation_type.strip().upper()

    if op == "DISBURSEMENT":
        debits.append({"account_code": receivable_account_code, "amount": float(total_amount)})
        credits.append({"account_code": bank_account_code, "amount": float(total_amount)})

    elif op == "DISBURSEMENT_REVERSAL":
        debits.append({"account_code": bank_account_code, "amount": float(total_amount)})
        credits.append({"account_code": receivable_account_code, "amount": float(total_amount)})

    elif op == "REPAYMENT":
        principal = float(breakdown.get("principal", 0.0))
        interest = float(breakdown.get("interest", 0.0))
        fee = float(breakdown.get("fee", 0.0))
        penalty = float(breakdown.get("penalty", 0.0))

        debits.append({"account_code": bank_account_code, "amount": float(total_amount)})

        if principal > 0:
            credits.append({"account_code": receivable_account_code, "amount": principal})
        if interest > 0:
            credits.append({"account_code": interest_income_code, "amount": interest})
        if fee > 0:
            credits.append({"account_code": fee_income_code, "amount": fee})
        if penalty > 0:
            credits.append({"account_code": penalty_income_code, "amount": penalty})

        # Overpayment handling: amount collected exceeding loan obligations
        allocated = principal + interest + fee + penalty
        unallocated = float(total_amount) - allocated
        if unallocated > 0.0001:
            credits.append(
                {"account_code": overpayment_account_code, "amount": round(unallocated, 4)}
            )

    elif op == "REPAYMENT_REVERSAL":
        principal = float(breakdown.get("principal", 0.0))
        interest = float(breakdown.get("interest", 0.0))
        fee = float(breakdown.get("fee", 0.0))
        penalty = float(breakdown.get("penalty", 0.0))

        if principal > 0:
            debits.append({"account_code": receivable_account_code, "amount": principal})
        if interest > 0:
            debits.append({"account_code": interest_income_code, "amount": interest})
        if fee > 0:
            debits.append({"account_code": fee_income_code, "amount": fee})
        if penalty > 0:
            debits.append({"account_code": penalty_income_code, "amount": penalty})

        credits.append({"account_code": bank_account_code, "amount": float(total_amount)})

    else:
        raise ValueError(f"Unknown operation type: '{operation_type}'")

    total_debits = sum(float(d["amount"]) for d in debits)
    total_credits = sum(float(c["amount"]) for c in credits)

    if round(total_debits, 4) != round(total_credits, 4):
        raise ValueError(
            f"Unbalanced journal entry generated for operation '{operation_type}': "
            f"debits={round(total_debits, 4)}, credits={round(total_credits, 4)}"
        )

    lines = [
        {"account_code": d["account_code"], "entry_type": "DEBIT", "amount": d["amount"]}
        for d in debits
    ] + [
        {"account_code": c["account_code"], "entry_type": "CREDIT", "amount": c["amount"]}
        for c in credits
    ]

    return {
        "external_reference": external_reference,
        "transaction_date": transaction_date,
        "total_amount": float(total_amount),
        "debits": debits,
        "credits": credits,
        "lines": lines,
    }
