"""
Clients API Endpoints (Part C & E)
Manages client creation and query by business client_uid.
Encapsulates and prevents leakage of Fineract internal IDs.
"""
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.schemas import ClientCreateRequest, ClientResponse, ErrorResponse
from app.services.client_service import (
    ClientConflictError,
    ClientNotFoundError,
    client_service,
)
from app.services.idempotency import IdempotencyConflictError

router = APIRouter(prefix="/api/clients", tags=["Clients"])


@router.post(
    "",
    response_model=ClientResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": ErrorResponse, "description": "Conflict: duplicate reference or client_uid"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def create_client(
    request: ClientCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> ClientResponse:
    """
    Creates a new client in Fineract.
    Business key client_uid is mapped to Fineract externalId.
    Enforces idempotency on external_reference and uniqueness on client_uid.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    try:
        client_res, status_code = await client_service.create_client(
            db, request, correlation_id=correlation_id
        )
        response.status_code = status_code
        return client_res
    except (ClientConflictError, IdempotencyConflictError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "CLIENT_CONFLICT",
                    "message": str(exc),
                    "details": {"client_uid": request.client_uid, "external_reference": request.external_reference},
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
                    "code": "CLIENT_CREATION_FAILED",
                    "message": str(exc),
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )


@router.get(
    "/{client_uid}",
    response_model=ClientResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Client not found"},
    },
)
async def get_client(
    client_uid: str,
    db: Session = Depends(get_db),
    x_correlation_id: str | None = Header(None),
) -> ClientResponse:
    """
    Retrieves client by client_uid.
    Never exposes Fineract numeric ID.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    try:
        return await client_service.get_client_by_uid(db, client_uid, correlation_id=correlation_id)
    except ClientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "CLIENT_NOT_FOUND",
                    "message": str(exc),
                    "details": {"client_uid": client_uid},
                    "correlation_id": correlation_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            },
        )

