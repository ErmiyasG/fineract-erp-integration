"""
Pydantic API Schemas and Contracts for Integration Service
"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    correlation_id: str
    timestamp: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# -------------------------------------------------------------
# GL Account Sync Schemas (Part A)
# -------------------------------------------------------------
class AccountSyncRequest(BaseModel):
    external_reference: str = Field(
        ..., description="Unique ERP external identifier (e.g. GL-1200)"
    )
    code: str = Field(..., description="GL Account Code (e.g. 1200)")
    name: str = Field(..., description="GL Account Name (e.g. Loan Receivable)")
    type: str = Field(
        ..., description="ERP Account Type: ASSET, LIABILITY, EQUITY, INCOME, EXPENSE"
    )
    currency: str = Field(default="RWF", description="Currency ISO code")
    description: str | None = Field(default="Synced from ERP", description="Account description")


class AccountSyncResponse(BaseModel):
    external_reference: str
    code: str
    name: str
    type: str
    fineract_gl_id: int | None
    status: str  # SYNC_PENDING, SYNCED, FAILED
    retry_count: int = 0
    message: str


# -------------------------------------------------------------
# Client Schemas (Part C)
# -------------------------------------------------------------
class ClientCreateRequest(BaseModel):
    external_reference: str
    client_uid: str = Field(..., description="Client business ID (e.g. SME-000001)")
    first_name: str
    last_name: str
    mobile_no: str | None = None
    office_id: int = 1


class ClientResponse(BaseModel):
    client_uid: str
    external_id: str
    first_name: str
    last_name: str
    status: str
    created_at: str


# -------------------------------------------------------------
# Loan Schemas (Part D & E)
# -------------------------------------------------------------
class LoanCreateRequest(BaseModel):
    external_reference: str
    loan_reference: str = Field(..., description="Integration-level loan ID (e.g. LN-2026-000001)")
    client_uid: str = Field(..., description="Borrower Client UID")
    product_id: int = 1
    principal: float
    term_months: int = 12
    interest_rate: float = 18.0
    auto_approve: bool = True


class LoanResponse(BaseModel):
    loan_reference: str
    client_uid: str
    status: str
    principal: float
    outstanding_balance: float
    approved_at: str | None = None
    disbursed_at: str | None = None


class DisbursementRequest(BaseModel):
    external_reference: str
    disbursement_date: str
    amount: float


class DisbursementResponse(BaseModel):
    external_reference: str
    loan_reference: str
    status: str
    fineract_transaction_id: int | None
    erp_transaction_id: str | None
    disbursed_amount: float


class RepaymentBreakdown(BaseModel):
    principal: float = 0.0
    interest: float = 0.0
    fee: float = 0.0
    penalty: float = 0.0


class RepaymentRequest(BaseModel):
    external_reference: str
    transaction_date: str
    amount: float


class RepaymentResponse(BaseModel):
    external_reference: str
    loan_reference: str
    status: str
    fineract_transaction_id: int | None
    breakdown: RepaymentBreakdown
    erp_transaction_id: str | None
    remaining_balance: float


class TransactionStatusResponse(BaseModel):
    external_reference: str
    operation_type: str
    loan_reference: str
    state: str
    amount: float
    fineract_transaction_id: int | None
    erp_transaction_id: str | None
    retry_count: int
    created_at: str
    updated_at: str


class ReconciliationFilterRequest(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    references: list[str] | None = None


class ReconciliationRepairRequest(BaseModel):
    references: list[str]


class ReconciliationItem(BaseModel):
    external_reference: str
    status: str  # MATCHED, MISSING_IN_ERP, MISSING_IN_FINERACT, AMOUNT_MISMATCH
    fineract_amount: float | None
    erp_amount: float | None
    details: str


class ReconciliationSummary(BaseModel):
    total_records: int
    matched: int
    missing_in_erp: int
    missing_in_fineract: int
    amount_mismatch: int


class ReconciliationResponse(BaseModel):
    summary: ReconciliationSummary
    items: list[ReconciliationItem]
