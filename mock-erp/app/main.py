from fastapi import FastAPI
from app.config import settings

app = FastAPI(
    title="Mock ERP API",
    description="Accounting master data and general ledger journal entries",
    version="0.1.0",
)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "mock-erp"}

