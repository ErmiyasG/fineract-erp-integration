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
        timeout: float = 15.0,
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

    async def _post(self, path: str, payload: dict[str, Any], correlation_id: str | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(verify=not settings.FINERACT_INSECURE_SSL, timeout=self.timeout) as client:
                res = await client.post(url, json=payload, headers=self._get_headers(correlation_id))
                if res.status_code in (200, 201):
                    return res.json()
                elif res.status_code == 409:
                    raise FineractConflictException(
                        f"Fineract 409 Conflict at {path}: {res.text}",
                        status_code=409,
                        details={"response": res.text, "path": path},
                    )
                else:
                    raise FineractClientException(
                        f"Fineract returned HTTP {res.status_code} at {path}: {res.text}",
                        status_code=res.status_code,
                        details={"response": res.text, "path": path},
                    )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise FineractConnectionException(f"Fineract connection error at {path}: {exc}") from exc

    async def _get(self, path: str, correlation_id: str | None = None) -> dict[str, Any] | None:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(verify=not settings.FINERACT_INSECURE_SSL, timeout=self.timeout) as client:
                res = await client.get(url, headers=self._get_headers(correlation_id))
                if res.status_code == 200:
                    return res.json()
                elif res.status_code == 404:
                    return None
                else:
                    raise FineractClientException(
                        f"Fineract returned HTTP {res.status_code} at {path}: {res.text}",
                        status_code=res.status_code,
                        details={"response": res.text, "path": path},
                    )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise FineractConnectionException(f"Fineract connection error at {path}: {exc}") from exc

    # -------------------------------------------------------------------------
    # GL Account Methods
    # -------------------------------------------------------------------------
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
        return await self._post("glaccounts", payload, correlation_id)

    async def get_gl_account_by_id(self, account_id: int, correlation_id: str | None = None) -> dict[str, Any] | None:
        return await self._get(f"glaccounts/{account_id}", correlation_id)

    # -------------------------------------------------------------------------
    # Client Methods (Part C)
    # -------------------------------------------------------------------------
    async def create_client(
        self,
        client_uid: str,
        first_name: str,
        last_name: str,
        office_id: int = 1,
        activation_date: str = "2026-01-01",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Creates and activates a client in Fineract with externalId = client_uid."""
        payload = {
            "officeId": office_id,
            "firstname": first_name,
            "lastname": last_name,
            "externalId": client_uid,
            "active": True,
            "activationDate": activation_date,
            "dateFormat": "yyyy-MM-dd",
            "locale": "en",
        }
        return await self._post("clients", payload, correlation_id)

    async def get_client_by_external_id(self, client_uid: str, correlation_id: str | None = None) -> dict[str, Any] | None:
        """Looks up client by externalId (client_uid) in Fineract."""
        # Fineract supports /clients?externalId={externalId} or /clients/external-id/{externalId}
        data = await self._get(f"clients?externalId={client_uid}", correlation_id)
        if data and isinstance(data, dict):
            page_items = data.get("pageItems", [])
            if page_items:
                return page_items[0]
        return None

    async def get_client_by_id(self, client_id: int, correlation_id: str | None = None) -> dict[str, Any] | None:
        return await self._get(f"clients/{client_id}", correlation_id)

    # -------------------------------------------------------------------------
    # Loan Methods (Part D & E)
    # -------------------------------------------------------------------------
    async def create_loan_application(
        self,
        client_id: int,
        product_id: int,
        loan_reference: str,
        principal: float,
        term_months: int = 12,
        interest_rate: float = 18.0,
        disbursement_date: str = "2026-01-01",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Submits loan application with externalId = loan_reference."""
        payload = {
            "clientId": client_id,
            "productId": product_id,
            "principal": principal,
            "loanTermFrequency": term_months,
            "loanTermFrequencyType": 2,  # Months
            "numberOfRepayments": term_months,
            "repaymentEvery": 1,
            "repaymentFrequencyType": 2,  # Months
            "interestRatePerPeriod": round(interest_rate / 12.0, 4),
            "interestRateFrequencyType": 2,  # Month
            "amortizationType": 1,  # Equal installments
            "interestType": 0,  # Declining balance
            "interestCalculationPeriodType": 1,
            "transactionProcessingStrategyCode": "mifos-standard-strategy",
            "expectedDisbursementDate": disbursement_date,
            "submittedOnDate": disbursement_date,
            "externalId": loan_reference,
            "dateFormat": "yyyy-MM-dd",
            "locale": "en",
        }
        return await self._post("loans", payload, correlation_id)

    async def approve_loan(
        self,
        loan_id: int,
        approval_date: str = "2026-01-01",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Approves a loan in Fineract."""
        payload = {
            "approvedOnDate": approval_date,
            "dateFormat": "yyyy-MM-dd",
            "locale": "en",
        }
        return await self._post(f"loans/{loan_id}?command=approve", payload, correlation_id)

    async def disburse_loan(
        self,
        loan_id: int,
        amount: float,
        disbursement_date: str = "2026-01-01",
        external_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Disburses a loan with externalId."""
        payload = {
            "actualDisbursementDate": disbursement_date,
            "transactionAmount": amount,
            "externalId": external_id,
            "dateFormat": "yyyy-MM-dd",
            "locale": "en",
        }
        return await self._post(f"loans/{loan_id}?command=disburse", payload, correlation_id)

    async def repay_loan(
        self,
        loan_id: int,
        amount: float,
        transaction_date: str = "2026-02-01",
        external_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Applies a repayment to a loan in Fineract."""
        payload = {
            "transactionDate": transaction_date,
            "transactionAmount": amount,
            "externalId": external_id,
            "dateFormat": "yyyy-MM-dd",
            "locale": "en",
        }
        return await self._post(f"loans/{loan_id}?command=repayment", payload, correlation_id)

    async def get_loan_by_id(self, loan_id: int, correlation_id: str | None = None) -> dict[str, Any] | None:
        """Fetches loan details including current status and outstanding balance."""
        return await self._get(f"loans/{loan_id}?associations=all", correlation_id)

    async def get_loan_by_external_id(self, loan_reference: str, correlation_id: str | None = None) -> dict[str, Any] | None:
        """Looks up loan by externalId (loan_reference) in Fineract."""
        data = await self._get(f"loans/external-id/{loan_reference}", correlation_id)
        return data

    async def get_loan_transaction(
        self,
        loan_id: int,
        transaction_id: int,
        correlation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetches loan transaction breakdown (principal, interest, fees, penalties)."""
        return await self._get(f"loans/{loan_id}/transactions/{transaction_id}", correlation_id)


fineract_client = FineractClient()
