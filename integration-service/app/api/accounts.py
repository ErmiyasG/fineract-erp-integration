"""
Accounts API Endpoints
Handles ERP Chart of Accounts synchronization into Fineract.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.db import AccountSyncMapping
from app.models.schemas import AccountSyncRequest, AccountSyncResponse, ErrorResponse
from app.services.gl_sync_service import GLSyncConflictError, gl_sync_service

router = APIRouter(prefix="/api/accounts", tags=["Chart of Accounts"])


@router.post(
    "/sync",
    response_model=AccountSyncResponse,
    responses={
        409: {"model": ErrorResponse, "description": "Conflict: same reference with different attributes"},
        422: {"model": ErrorResponse, "description": "Validation error or invalid account type"},
    },
)
async def sync_account(
    request: AccountSyncRequest,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> AccountSyncResponse:
    """
    Synchronizes an ERP GL Account into Apache Fineract.
    - Idempotent: repeated calls with identical payload return existing account (HTTP 200).
    - Conflict check: same external_reference with different attributes returns HTTP 409.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())

    try:
        response = await gl_sync_service.sync_account(db, request, correlation_id=correlation_id)
        return response
    except GLSyncConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "CONFLICT_DETECTED",
                    "message": str(exc),
                    "details": {"external_reference": request.external_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )


@router.get("/sync/{external_reference}", response_model=AccountSyncResponse)
def get_synced_account(
    external_reference: str,
    db: Session = Depends(get_db),
) -> AccountSyncResponse:
    """
    Retrieves the synchronization status and details of an account.
    """
    mapping = db.query(AccountSyncMapping).filter(
        AccountSyncMapping.external_reference == external_reference
    ).first()

    if not mapping:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": f"No account sync record found for reference '{external_reference}'"},
        )

    return AccountSyncResponse(
        external_reference=mapping.external_reference,
        code=mapping.code,
        name=mapping.name,
        type=mapping.account_type,
        fineract_gl_id=mapping.fineract_id,
        status=mapping.sync_status,
        retry_count=mapping.retry_count,
        message="Account sync record retrieved",
    )
