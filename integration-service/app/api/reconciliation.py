"""
Reconciliation API Endpoints (Parts E & K)
Performs two-way transaction reconciliation between Apache Fineract and Mock ERP.
Categorizes transactions into:
- MATCHED
- MISSING_IN_ERP
- MISSING_IN_FINERACT
- AMOUNT_MISMATCH
"""
import uuid

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.db import TransactionRecord
from app.models.schemas import (
    ReconciliationItem,
    ReconciliationResponse,
    ReconciliationSummary,
)
from app.services.erp_client import erp_client

router = APIRouter(prefix="/api/reconciliation", tags=["Reconciliation"])


@router.post("", response_model=ReconciliationResponse)
async def run_reconciliation(
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> ReconciliationResponse:
    """
    Compares recorded financial transactions between Apache Fineract and Mock ERP.
    Keying is performed by external_reference.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    records = db.query(TransactionRecord).all()

    items: list[ReconciliationItem] = []
    matched_count = 0
    missing_in_erp_count = 0
    missing_in_fineract_count = 0
    amount_mismatch_count = 0

    for rec in records:
        fineract_amount = rec.amount if rec.fineract_transaction_id else None
        erp_entry = await erp_client.get_journal_entry(
            rec.external_reference, correlation_id=correlation_id
        )

        erp_amount = None
        if erp_entry:
            erp_amount = erp_entry.get("total_debits")

        # Determine outcome
        if fineract_amount is not None and erp_amount is not None:
            if abs(fineract_amount - erp_amount) < 0.01:
                status_val = "MATCHED"
                details = "Transaction identical in Fineract and ERP"
                matched_count += 1
            else:
                status_val = "AMOUNT_MISMATCH"
                details = f"Fineract amount ({fineract_amount}) != ERP amount ({erp_amount})"
                amount_mismatch_count += 1
        elif fineract_amount is not None and erp_amount is None:
            status_val = "MISSING_IN_ERP"
            details = "Transaction completed in Fineract but absent in ERP"
            missing_in_erp_count += 1
        elif fineract_amount is None and erp_amount is not None:
            status_val = "MISSING_IN_FINERACT"
            details = "Journal entry posted to ERP but absent in Fineract"
            missing_in_fineract_count += 1
        else:
            status_val = "MISSING_IN_BOTH"
            details = "Transaction recorded locally but not executed in upstream systems"

        items.append(
            ReconciliationItem(
                external_reference=rec.external_reference,
                status=status_val,
                fineract_amount=fineract_amount,
                erp_amount=erp_amount,
                details=details,
            )
        )

    summary = ReconciliationSummary(
        total_records=len(items),
        matched=matched_count,
        missing_in_erp=missing_in_erp_count,
        missing_in_fineract=missing_in_fineract_count,
        amount_mismatch=amount_mismatch_count,
    )

    return ReconciliationResponse(summary=summary, items=items)
