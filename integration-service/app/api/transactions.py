"""
Transactions API Endpoints (Parts E & F)
Provides state machine status and observability for financial transactions.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.db import TransactionRecord
from app.models.schemas import ErrorResponse, TransactionStatusResponse

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])


@router.get(
    "/{external_reference}",
    response_model=TransactionStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Transaction not found"},
    },
)
def get_transaction_status(
    external_reference: str,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> TransactionStatusResponse:
    """Retrieves current state machine status and identifiers for an external reference."""
    correlation_id = x_correlation_id or str(uuid.uuid4())
    record = (
        db.query(TransactionRecord)
        .filter(TransactionRecord.external_reference == external_reference)
        .first()
    )

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "TRANSACTION_NOT_FOUND",
                    "message": f"Transaction with external reference '{external_reference}' not found.",
                    "details": {"external_reference": external_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )

    return TransactionStatusResponse(
        external_reference=record.external_reference,
        operation_type=record.operation_type,
        loan_reference=record.loan_reference,
        state=record.state,
        amount=record.amount,
        fineract_transaction_id=record.fineract_transaction_id,
        erp_transaction_id=record.erp_transaction_id,
        retry_count=record.retry_count,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )

