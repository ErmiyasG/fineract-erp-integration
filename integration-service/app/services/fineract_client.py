"""
Apache Fineract REST API Client
Provides HTTP communication with Fineract Core Banking.
Encapsulates authentication, tenant identification, timeouts, and lookup before retry.
"""
import base64
from typing import Any

import httpx

from app.config import settings


class FineractClientException(Exception):
    def __init__(self, message: str, status_code: int | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class FineractConflictException(FineractClientException):
    """Raised when Fineract rejects a request with HTTP 409 Conflict"""


class FineractConnectionException(FineractClientException):
    """Raised when Fineract is unreachable or times out"""


class FineractClient:

    def __init__(
        self,
        base_url: str | None = None,
        tenant_id: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or settings.FINERACT_BASE_URL).rstrip("/")
        self.tenant_id = tenant_id or settings.FINERACT_TENANT_ID
        self.user = user or settings.FINERACT_AUTH_USER
        self.password = password or settings.FINERACT_AUTH_PASSWORD
        self.timeout = timeout

    def _get_headers(self, correlation_id: str | None = None) -> dict[str, str]:
        auth_bytes = f"{self.user}:{self.password}".encode()
        auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Fineract-Platform-TenantId": self.tenant_id,
            "Authorization": f"Basic {auth_b64}",
        }
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        return headers

    async def create_gl_account(
        self,
        name: str,
        gl_code: str,
        account_type: int,
        external_reference: str,
        description: str = "Synced from ERP",
        usage: int = 1,
        manual_entries_allowed: bool = True,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Calls Fineract POST /api/v1/glaccounts.
        Enforces idempotency and conflict rules on Fineract side:
        - 200: Created or existing account returned.
        - 409: Conflict (existing external_reference with different attributes).
        """
        url = f"{self.base_url}/glaccounts"
        payload = {
            "name": name,
            "glCode": gl_code,
            "type": account_type,
            "usage": usage,
            "manualEntriesAllowed": manual_entries_allowed,
            "description": description,
            "external_reference": external_reference,
            "externalReference": external_reference,
        }

        try:
            async with httpx.AsyncClient(verify=not settings.FINERACT_INSECURE_SSL, timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=self._get_headers(correlation_id))

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 409:
                    raise FineractConflictException(
                        f"Conflict in Fineract for GL account with external reference {external_reference}: {response.text}",
                        status_code=409,
                        details={"response": response.text, "external_reference": external_reference},
                    )
                elif response.status_code in (401, 403):
                    raise FineractClientException(
                        f"Authentication or permission failed in Fineract: {response.status_code}",
                        status_code=response.status_code,
                        details={"response": response.text},
                    )
                else:
                    raise FineractClientException(
                        f"Fineract returned HTTP {response.status_code}: {response.text}",
                        status_code=response.status_code,
                        details={"response": response.text},
                    )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise FineractConnectionException(
                f"Fineract connection failed: {exc}",
                details={"error": str(exc), "url": url},
            ) from exc

    async def get_gl_account_by_id(self, account_id: int, correlation_id: str | None = None) -> dict[str, Any] | None:
        """Fetch GL Account by internal ID"""
        url = f"{self.base_url}/glaccounts/{account_id}"
        try:
            async with httpx.AsyncClient(verify=not settings.FINERACT_INSECURE_SSL, timeout=self.timeout) as client:
                res = await client.get(url, headers=self._get_headers(correlation_id))
                if res.status_code == 200:
                    return res.json()
                return None
        except Exception as exc:
            raise FineractConnectionException(f"Failed to fetch GL account: {exc}") from exc


fineract_client = FineractClient()
