"""
Financial Integration Service Application
Connects Financing Partners, Apache Fineract Core Banking, and Mock ERP.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.accounts import router as accounts_router
from app.core.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    try:
        init_db()
    except Exception as e:
        print(f"Warning: Database initialization deferred (DB may be connecting): {e}")
    yield


app = FastAPI(
    title="Financial Integration Service API",
    description="Orchestrator between Financing Partners, Apache Fineract, and Mock ERP",
    version="0.1.0",
    lifespan=lifespan,
)

# Include Routers
app.include_router(accounts_router)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "integration-service"}
