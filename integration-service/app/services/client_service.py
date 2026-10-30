"""
Client Management Service (Part C)
Encapsulates Fineract client lifecycle and isolates internal numeric IDs.
Enforces business client_uid identity mapped to Fineract externalId.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.db import ClientMapping
from app.models.schemas import ClientCreateRequest, ClientResponse
from app.services.fineract_client import (
    FineractClient,
)
from app.services.fineract_client import (
    fineract_client as default_fineract_client,
)
from app.services.idempotency import (
    IdempotencyConflictError,
    idempotency_service,
)


class ClientConflictError(Exception):
    pass


class ClientNotFoundError(Exception):
    pass


class ClientService:

    def __init__(self, fineract_client: FineractClient | None = None):
        self.fineract_client = fineract_client or default_fineract_client

    async def create_client(
        self,
        db: Session,
        request: ClientCreateRequest,
        correlation_id: str | None = None,
    ) -> tuple[ClientResponse, int]:
        """
        Creates a new client in Fineract with externalId = client_uid.
        Enforces idempotency on external_reference and uniqueness on client_uid.
        Returns:
            (ClientResponse, http_status_code)
        """
        payload_dict = request.model_dump()
        status_action, cached_body, _http_status = idempotency_service.check_and_acquire(
            db,
            operation_type="CREATE_CLIENT",
            idempotency_key=request.external_reference,
            payload=payload_dict,
        )

        if status_action == "COMPLETED" and cached_body:
            return ClientResponse(**cached_body), 200

        if status_action == "IN_PROGRESS":
            # Concurrent duplicate in-flight
            raise IdempotencyConflictError(
                f"Client creation for reference '{request.external_reference}' is currently in progress."
            )

        # Validate that client_uid is unique in our database
        existing_uid = (
            db.query(ClientMapping)
            .filter(ClientMapping.client_uid == request.client_uid)
            .first()
        )
        if existing_uid:
            idempotency_service.mark_failed(db, "CREATE_CLIENT", request.external_reference)
            raise ClientConflictError(
                f"Client UID '{request.client_uid}' is already assigned to a client (name: {existing_uid.first_name} {existing_uid.last_name})"
            )

        try:
            # Call Fineract to create and activate client
            fineract_resp = await self.fineract_client.create_client(
                client_uid=request.client_uid,
                first_name=request.first_name,
                last_name=request.last_name,
                office_id=request.office_id,
                correlation_id=correlation_id,
            )
            fineract_client_id = fineract_resp.get("clientId") or fineract_resp.get("resourceId")
            if not fineract_client_id:
                raise ValueError("Fineract did not return a valid client identifier")

            # Persist local mapping
            mapping = ClientMapping(
                client_uid=request.client_uid,
                external_id=request.client_uid,
                fineract_client_id=int(fineract_client_id),
                first_name=request.first_name,
                last_name=request.last_name,
                status="ACTIVE",
                created_at=datetime.now(UTC),
            )
            db.add(mapping)
            db.commit()

            response = ClientResponse(
                client_uid=mapping.client_uid,
                external_id=mapping.external_id,
                first_name=mapping.first_name,
                last_name=mapping.last_name,
                status=mapping.status,
                created_at=mapping.created_at.isoformat(),
            )

            idempotency_service.mark_completed(
                db,
                operation_type="CREATE_CLIENT",
                idempotency_key=request.external_reference,
                response_body=response.model_dump(),
                http_status=201,
            )

            return response, 201

        except Exception:
            idempotency_service.mark_failed(db, "CREATE_CLIENT", request.external_reference)
            raise

    async def get_client_by_uid(
        self,
        db: Session,
        client_uid: str,
        correlation_id: str | None = None,
    ) -> ClientResponse:
        """
        Retrieves client details by client_uid.
        Validates existence and active status.
        Never exposes Fineract numeric ID.
        """
        mapping = (
            db.query(ClientMapping)
            .filter(ClientMapping.client_uid == client_uid)
            .first()
        )

        if not mapping:
            # Check Fineract directly by externalId
            fineract_data = await self.fineract_client.get_client_by_external_id(
                client_uid, correlation_id=correlation_id
            )
            if not fineract_data:
                raise ClientNotFoundError(f"Client with UID '{client_uid}' not found.")

            # Backfill mapping if found in Fineract
            fineract_id = fineract_data.get("id")
            first_name = fineract_data.get("firstname", "")
            last_name = fineract_data.get("lastname", "")
            status_val = fineract_data.get("status", {}).get("value", "ACTIVE").upper()

            mapping = ClientMapping(
                client_uid=client_uid,
                external_id=client_uid,
                fineract_client_id=int(fineract_id),
                first_name=first_name,
                last_name=last_name,
                status=status_val,
                created_at=datetime.now(UTC),
            )
            db.add(mapping)
            db.commit()

        return ClientResponse(
            client_uid=mapping.client_uid,
            external_id=mapping.external_id,
            first_name=mapping.first_name,
            last_name=mapping.last_name,
            status=mapping.status,
            created_at=mapping.created_at.isoformat(),
        )


client_service = ClientService()
