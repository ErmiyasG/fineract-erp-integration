"""
Financial Integration Service Application
Connects Financing Partners, Apache Fineract Core Banking, and Mock ERP.
"""

import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.accounts import router as accounts_router
from app.api.clients import router as clients_router
from app.api.loans import router as loans_router
from app.api.reconciliation import router as reconciliation_router
from app.api.transactions import router as transactions_router
from app.core.db import init_db
from app.core.logging import setup_structured_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Initialize structured JSON logging
    setup_structured_logging()
    # Initialize database tables on startup
    try:
        init_db()
    except Exception as e:
        print(f"Warning: Database initialization deferred (DB may be connecting): {e}")
    yield


app = FastAPI(
    title="Financial Integration Service API",
    description="Orchestrator between Financing Partners, Apache Fineract, and Mock ERP",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def correlation_id_middleware(
    request: Request, call_next: Callable[[Request], Any]
) -> Response:
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    response: Response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


from app.services.idempotency import IdempotencyInProgressError


@app.exception_handler(IdempotencyInProgressError)
async def idempotency_in_progress_handler(
    request: Request, exc: IdempotencyInProgressError
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "IN_PROGRESS",
            "message": str(exc),
            "correlation_id": correlation_id,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "HTTP_ERROR",
                "message": str(exc.detail),
                "correlation_id": correlation_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        },
    )


# Include Routers
app.include_router(accounts_router)
app.include_router(clients_router)
app.include_router(loans_router)
app.include_router(transactions_router)
app.include_router(reconciliation_router)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "integration-service"}
