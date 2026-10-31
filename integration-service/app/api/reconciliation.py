"""
Reconciliation API Endpoints (Parts E & K)
Performs two-way transaction reconciliation between Apache Fineract and Mock ERP.
Categorizes transactions into:
- MATCHED
- MISSING_IN_ERP
- MISSING_IN_FINERACT
- AMOUNT_MISMATCH
Supports date range/reference filtering and explicit idempotent repair.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.schemas import (
    ReconciliationFilterRequest,
    ReconciliationRepairRequest,
    ReconciliationResponse,
)
from app.services.reconciliation_service import reconciliation_service

router = APIRouter(prefix="/api/reconciliation", tags=["Reconciliation"])


@router.post("", response_model=ReconciliationResponse)
async def run_reconciliation(
    filter_req: ReconciliationFilterRequest | None = None,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> ReconciliationResponse:
    """
    Compares recorded financial transactions between Apache Fineract and Mock ERP.
    Keying is performed by external_reference.
    Read-only by default.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    return await reconciliation_service.run_reconciliation(
        db, filter_req=filter_req, correlation_id=correlation_id
    )


@router.post("/repair", tags=["Reconciliation"])
async def repair_reconciliation_discrepancies(
    repair_req: ReconciliationRepairRequest,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> dict[str, Any]:
    """
    Explicit, idempotent repair action for transactions with MISSING_IN_ERP status.
    Posts the missing journal entries to Mock ERP using idempotent external_reference.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    return await reconciliation_service.repair_missing_in_erp(
        db, references=repair_req.references, correlation_id=correlation_id
    )
