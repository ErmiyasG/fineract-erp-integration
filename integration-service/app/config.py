from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://fintech:fintech_secret@app-db:5432/integration_db"
    FINERACT_BASE_URL: str = "https://fineract:8443/fineract-provider/api/v1"
    FINERACT_TENANT_ID: str = "default"
    FINERACT_AUTH_USER: str = "mifos"
    FINERACT_AUTH_PASSWORD: str = "password"
    FINERACT_INSECURE_SSL: bool = True
    MOCK_ERP_URL: str = "http://mock-erp:8001"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    API_KEY: str = "test-partner-api-key"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

