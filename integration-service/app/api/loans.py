"""
Loans API Endpoints (Parts D, E, F, I, J)
Manages Loan Application, Approval, Disbursement, and Repayment.
Isolates Fineract internal IDs and enforces financial invariants across concurrency and failure.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.schemas import (
    DisbursementRequest,
    DisbursementResponse,
    ErrorResponse,
    LoanCreateRequest,
    LoanResponse,
    RepaymentRequest,
    RepaymentResponse,
)
from app.services.idempotency import IdempotencyConflictError, IdempotencyInProgressError
from app.services.loan_service import (
    LoanConflictError,
    LoanNotFoundError,
    loan_service,
)

router = APIRouter(prefix="/api/loans", tags=["Loans"])


@router.post(
    "",
    response_model=LoanResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        202: {"description": "Loan creation in progress"},
        409: {"model": ErrorResponse, "description": "Duplicate loan_reference or conflict"},
        404: {"model": ErrorResponse, "description": "Borrower client UID not found"},
    },
)
async def create_loan(
    request: LoanCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> LoanResponse:
    """Submits a loan application with business key loan_reference."""
    correlation_id = x_correlation_id or str(uuid.uuid4())
    try:
        loan_res, status_code = await loan_service.create_loan(
            db, request, correlation_id=correlation_id
        )
        response.status_code = status_code
        return loan_res
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail={
                "status": "IN_PROGRESS",
                "message": str(exc),
                "details": {"loan_reference": request.loan_reference, "external_reference": request.external_reference},
                "correlation_id": correlation_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    except (LoanConflictError, IdempotencyConflictError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "LOAN_CONFLICT",
                    "message": str(exc),
                    "details": {"loan_reference": request.loan_reference, "external_reference": request.external_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
    except LoanNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "CLIENT_NOT_FOUND",
                    "message": str(exc),
                    "details": {"client_uid": request.client_uid},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "LOAN_CREATION_FAILED",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )


@router.get(
    "/{loan_reference}",
    response_model=LoanResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Loan not found"},
    },
)
async def get_loan(
    loan_reference: str,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> LoanResponse:
    """Retrieves loan status, terms, and live outstanding balance."""
    correlation_id = x_correlation_id or str(uuid.uuid4())
    try:
        return await loan_service.get_loan_by_reference(db, loan_reference, correlation_id=correlation_id)
    except LoanNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "LOAN_NOT_FOUND",
                    "message": str(exc),
                    "details": {"loan_reference": loan_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )


@router.post(
    "/{loan_reference}/approve",
    response_model=LoanResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Loan not found"},
    },
)
async def approve_loan(
    loan_reference: str,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> LoanResponse:
    """Approves a submitted loan application."""
    correlation_id = x_correlation_id or str(uuid.uuid4())
    try:
        return await loan_service.approve_loan(db, loan_reference, correlation_id=correlation_id)
    except LoanNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "LOAN_NOT_FOUND",
                    "message": str(exc),
                    "details": {"loan_reference": loan_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )


@router.post(
    "/{loan_reference}/disburse",
    response_model=DisbursementResponse,
    status_code=status.HTTP_200_OK,
    responses={
        202: {"description": "Disbursement currently in progress"},
        409: {"model": ErrorResponse, "description": "Single-disbursement violation or payload conflict"},
        404: {"model": ErrorResponse, "description": "Loan not found"},
    },
)
async def disburse_loan(
    loan_reference: str,
    request: DisbursementRequest,
    response: Response,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
    x_simulate_failure: str | None = Header(None, alias="X-Simulate-Failure"),
) -> DisbursementResponse:
    """
    Disburses a loan.
    Enforces Single-Disbursement invariant: A loan may only be disbursed once.
    Posts DR 1200 / CR 1100 to Mock ERP.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    try:
        disb_res, status_code = await loan_service.disburse_loan(
            db,
            loan_reference,
            request,
            correlation_id=correlation_id,
            simulate_failure=x_simulate_failure,
        )
        response.status_code = status_code
        return disb_res
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail={
                "status": "IN_PROGRESS",
                "message": str(exc),
                "details": {"loan_reference": loan_reference, "external_reference": request.external_reference},
                "correlation_id": correlation_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    except (LoanConflictError, IdempotencyConflictError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "DISBURSEMENT_CONFLICT",
                    "message": str(exc),
                    "details": {"loan_reference": loan_reference, "external_reference": request.external_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
    except LoanNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "LOAN_NOT_FOUND",
                    "message": str(exc),
                    "details": {"loan_reference": loan_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "DISBURSEMENT_FAILED",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )


@router.post(
    "/{loan_reference}/repayments",
    response_model=RepaymentResponse,
    status_code=status.HTTP_200_OK,
    responses={
        202: {"description": "Repayment currently in progress"},
        409: {"model": ErrorResponse, "description": "Idempotency conflict"},
        404: {"model": ErrorResponse, "description": "Loan not found"},
    },
)
async def repay_loan(
    loan_reference: str,
    request: RepaymentRequest,
    response: Response,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
    x_simulate_failure: str | None = Header(None, alias="X-Simulate-Failure"),
) -> RepaymentResponse:
    """
    Posts repayment to Fineract.
    Derives principal/interest split directly from Fineract transaction breakdown
    and posts balanced double-entry to Mock ERP.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    try:
        repay_res, status_code = await loan_service.repay_loan(
            db,
            loan_reference,
            request,
            correlation_id=correlation_id,
            simulate_failure=x_simulate_failure,
        )
        response.status_code = status_code
        return repay_res
    except IdempotencyInProgressError as exc:
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail={
                "status": "IN_PROGRESS",
                "message": str(exc),
                "details": {"loan_reference": loan_reference, "external_reference": request.external_reference},
                "correlation_id": correlation_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "REPAYMENT_CONFLICT",
                    "message": str(exc),
                    "details": {"loan_reference": loan_reference, "external_reference": request.external_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
    except LoanNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "LOAN_NOT_FOUND",
                    "message": str(exc),
                    "details": {"loan_reference": loan_reference},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "REPAYMENT_FAILED",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )
