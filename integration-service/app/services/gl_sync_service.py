"""
GL Account Synchronization Service
Orchestrates ERP Chart of Accounts synchronization with Apache Fineract.
"""

from sqlalchemy.orm import Session

from app.models.db import AccountSyncMapping
from app.models.schemas import AccountSyncRequest, AccountSyncResponse
from app.services.accounting import map_erp_type_to_fineract
from app.services.fineract_client import (
    FineractClient,
    FineractConflictException,
    FineractConnectionException,
)
from app.services.fineract_client import (
    fineract_client as default_fineract_client,
)


class GLSyncConflictError(Exception):
    pass


class GLSyncService:
    def __init__(self, fineract_client: FineractClient | None = None):
        self.fineract_client = fineract_client or default_fineract_client

    async def sync_account(
        self,
        db: Session,
        request: AccountSyncRequest,
        correlation_id: str | None = None,
    ) -> AccountSyncResponse:
        """
        Synchronizes an ERP-owned GL account into Apache Fineract.

        Contract & Lifecycle:
        1. Explicitly maps ERP account type to Fineract GL type integer.
        2. Create-only: If account is already SYNCED with identical details, returns existing mapping (no duplicate).
        3. If mapping exists with conflicting attributes, raises GLSyncConflictError (409 Conflict).
        4. State tracking: SYNC_PENDING -> SYNCED (or FAILED if Fineract is unreachable).
        5. Retryable: If previously FAILED, retries Fineract synchronization.
        6. Invariant: Sending the same account 4 times yields 1 mapping and 1 Fineract GL account.
        """
        # Validate and map ERP account type
        fineract_type_int = map_erp_type_to_fineract(request.type)

        # Check existing mapping
        mapping = (
            db.query(AccountSyncMapping)
            .filter(AccountSyncMapping.external_reference == request.external_reference)
            .first()
        )

        if mapping:
            # Check for conflict if attributes changed
            if (
                mapping.code != request.code
                or mapping.name != request.name
                or mapping.account_type != request.type
            ):
                raise GLSyncConflictError(
                    f"Conflict for GL reference '{request.external_reference}': "
                    f"existing code='{mapping.code}', name='{mapping.name}', type='{mapping.account_type}'; "
                    f"incoming code='{request.code}', name='{request.name}', type='{request.type}'"
                )

            # If already successfully synced, return cached mapping (idempotent 200)
            if mapping.sync_status == "SYNCED" and mapping.fineract_id is not None:
                return AccountSyncResponse(
                    external_reference=mapping.external_reference,
                    code=mapping.code,
                    name=mapping.name,
                    type=mapping.account_type,
                    fineract_gl_id=mapping.fineract_id,
                    status=mapping.sync_status,
                    retry_count=mapping.retry_count,
                    message="GL Account already synced (idempotent return)",
                )

            # If previously pending or failed, we will retry
            mapping.sync_status = "SYNC_PENDING"
            mapping.retry_count += 1
            db.commit()
            db.refresh(mapping)
        else:
            # Create new mapping in SYNC_PENDING state
            mapping = AccountSyncMapping(
                external_reference=request.external_reference,
                code=request.code,
                name=request.name,
                account_type=request.type,
                currency=request.currency,
                sync_status="SYNC_PENDING",
                retry_count=0,
            )
            db.add(mapping)
            db.commit()
            db.refresh(mapping)

        # Call Fineract API
        try:
            fineract_response = await self.fineract_client.create_gl_account(
                name=request.name,
                gl_code=request.code,
                account_type=fineract_type_int,
                external_reference=request.external_reference,
                description=request.description or "Synced from ERP",
                correlation_id=correlation_id,
            )

            # Extract resource ID from Fineract response (resourceId or subResourceId)
            fineract_gl_id = fineract_response.get("resourceId") or fineract_response.get(
                "subResourceId"
            )

            mapping.fineract_id = fineract_gl_id
            mapping.sync_status = "SYNCED"
            mapping.last_error = None
            db.commit()
            db.refresh(mapping)

            return AccountSyncResponse(
                external_reference=mapping.external_reference,
                code=mapping.code,
                name=mapping.name,
                type=mapping.account_type,
                fineract_gl_id=mapping.fineract_id,
                status="SYNCED",
                retry_count=mapping.retry_count,
                message="GL Account successfully synchronized to Apache Fineract",
            )

        except FineractConflictException as exc:
            mapping.sync_status = "FAILED"
            mapping.last_error = str(exc)
            db.commit()
            raise GLSyncConflictError(str(exc)) from exc

        except FineractConnectionException as exc:
            mapping.sync_status = "FAILED"
            mapping.last_error = str(exc)
            db.commit()
            return AccountSyncResponse(
                external_reference=mapping.external_reference,
                code=mapping.code,
                name=mapping.name,
                type=mapping.account_type,
                fineract_gl_id=None,
                status="FAILED",
                retry_count=mapping.retry_count,
                message=f"Fineract unavailable during sync; state marked FAILED for retry: {exc}",
            )


gl_sync_service = GLSyncService()
