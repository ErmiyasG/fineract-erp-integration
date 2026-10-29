"""
Accounting Mapping and Domain Rules
Maps ERP account types to Apache Fineract GL account classifications.
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
    Raises ValueError if unknown type is supplied.
    """
    normalized = erp_type.strip().upper()
    if normalized not in ERP_TO_FINERACT_TYPE_MAP:
        raise ValueError(f"Unsupported ERP account type: '{erp_type}'. Expected one of {list(ERP_TO_FINERACT_TYPE_MAP.keys())}")
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
) -> dict[str, Any]:
    """
    Pure unit-tested function deriving double-entry balanced journal entries
    directly from Fineract transaction data breakdown.

    Disbursement:
      DR 1200 Loan Receivable
      CR 1100 Bank

    Repayment:
      DR 1100 Bank
      CR 1200 Loan Receivable (Principal portion)
      CR 4000 Interest Income (Interest portion)
    """
    debits = []
    credits = []

    op = operation_type.upper()
    if op == "DISBURSEMENT":
        debits.append({"account_code": receivable_account_code, "amount": total_amount})
        credits.append({"account_code": bank_account_code, "amount": total_amount})
    elif op == "REPAYMENT":
        principal = float(breakdown.get("principal", 0.0))
        interest = float(breakdown.get("interest", 0.0))
        fee = float(breakdown.get("fee", 0.0))
        penalty = float(breakdown.get("penalty", 0.0))

        debits.append({"account_code": bank_account_code, "amount": total_amount})

        if principal > 0:
            credits.append({"account_code": receivable_account_code, "amount": principal})
        if interest > 0:
            credits.append({"account_code": interest_income_code, "amount": interest})

        # Overpayment or unallocated amount
        allocated = principal + interest + fee + penalty
        unallocated = total_amount - allocated
        if unallocated > 0:
            credits.append({"account_code": bank_account_code, "amount": unallocated})
    else:
        raise ValueError(f"Unknown operation type: {operation_type}")

    total_debits = sum(d["amount"] for d in debits)
    total_credits = sum(c["amount"] for c in credits)

    if round(total_debits, 4) != round(total_credits, 4):
        raise ValueError(f"Unbalanced journal entry generated: debits={total_debits}, credits={total_credits}")

    return {
        "external_reference": external_reference,
        "transaction_date": transaction_date,
        "total_amount": total_amount,
        "debits": debits,
        "credits": credits,
    }
