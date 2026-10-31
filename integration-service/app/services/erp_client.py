"""
Mock ERP Client for Integration Service
Posts journal entries and retrieves accounting master data.
"""

from typing import Any

import httpx

from app.config import settings


class ErpClientException(Exception):
    def __init__(
        self, message: str, status_code: int | None = None, details: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class ErpConflictException(ErpClientException):
    """Raised when ERP reports a 409 conflict"""


class ErpConnectionException(ErpClientException):
    """Raised when ERP is unreachable or times out"""


class ErpClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (base_url or settings.MOCK_ERP_URL).rstrip("/")
        self.timeout = timeout

    def _headers(self, correlation_id: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        return headers

    async def post_journal_entry(
        self,
        external_reference: str,
        lines: list[dict[str, Any]],
        description: str | None = None,
        entry_date: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Posts a balanced journal entry to Mock ERP."""
        url = f"{self.base_url}/api/journal-entries"
        payload = {
            "external_reference": external_reference,
            "description": description,
            "entry_date": entry_date,
            "lines": lines,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(url, json=payload, headers=self._headers(correlation_id))
                if res.status_code in (200, 201):
                    return res.json()
                elif res.status_code == 409:
                    raise ErpConflictException(
                        f"ERP 409 Conflict: {res.text}",
                        status_code=409,
                        details={"response": res.text, "external_reference": external_reference},
                    )
                else:
                    raise ErpClientException(
                        f"ERP returned HTTP {res.status_code}: {res.text}",
                        status_code=res.status_code,
                        details={"response": res.text},
                    )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ErpConnectionException(f"Failed to connect to ERP: {exc}") from exc

    async def get_journal_entry(
        self, external_reference: str, correlation_id: str | None = None
    ) -> dict[str, Any] | None:
        url = f"{self.base_url}/api/journal-entries/{external_reference}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(url, headers=self._headers(correlation_id))
                if res.status_code == 200:
                    return res.json()
                elif res.status_code == 404:
                    return None
                else:
                    raise ErpClientException(f"ERP returned {res.status_code}: {res.text}")
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ErpConnectionException(f"Failed to connect to ERP: {exc}") from exc

    async def get_account_balance(
        self, code: str, correlation_id: str | None = None
    ) -> dict[str, Any] | None:
        url = f"{self.base_url}/api/accounts/{code}/balance"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(url, headers=self._headers(correlation_id))
                if res.status_code == 200:
                    return res.json()
                elif res.status_code == 404:
                    return None
                else:
                    raise ErpClientException(f"ERP returned {res.status_code}: {res.text}")
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ErpConnectionException(f"Failed to connect to ERP: {exc}") from exc


erp_client = ErpClient()
