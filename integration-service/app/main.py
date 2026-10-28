from fastapi import FastAPI
from app.config import settings

app = FastAPI(
    title="Financial Integration Service API",
    description="Orchestrator between Financing Partners, Apache Fineract, and Mock ERP",
    version="0.1.0",
)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "integration-service"}

