"""
Tier-1 Ingress Idempotency Engine
Enforces operation-scoped idempotency backed by database persistence.
Rules:
1. New reference -> Process once (returns 'NEW')
2. Same reference, same payload, finished -> Returns cached response (200 OK)
3. Same reference, same payload, in progress -> Returns 202 Accepted
4. Same reference, different payload -> Raises IdempotencyConflictError (409 Conflict)
"""
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.orm import Session

from app.models.db import IdempotencyRecord


class IdempotencyConflictError(Exception):
    """Raised when an external reference is reused with different request payload."""


class IdempotencyInProgressError(Exception):
    """Raised when an operation with the same reference is currently in execution."""


def compute_payload_hash(payload: dict[str, Any] | list[Any]) -> str:
    """Computes a canonical SHA-256 hash of a JSON-serializable payload."""
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class IdempotencyService:

    @staticmethod
    def check_and_acquire(
        db: Session,
        operation_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None, int | None]:
        """
        Atomically inspects and registers an incoming idempotent operation.
        Returns:
            (status, response_body, http_status)
            where status is one of: "NEW", "COMPLETED", "IN_PROGRESS"
        """
        payload_hash = compute_payload_hash(payload)

        record = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.operation_type == operation_type,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .first()
        )

        if record:
            # Check payload equality
            if record.payload_hash != payload_hash:
                raise IdempotencyConflictError(
                    f"Conflict: Reference '{idempotency_key}' was already submitted "
                    f"with a different payload for operation '{operation_type}'."
                )

            if record.status == "COMPLETED":
                resp_body = cast(dict[str, Any] | None, record.response_body)
                h_status = cast(int | None, record.http_status)
                return "COMPLETED", resp_body, h_status

            if record.status == "IN_PROGRESS":
                return "IN_PROGRESS", None, 202

            # If previously FAILED, allow re-acquisition
            record.status = "IN_PROGRESS"
            record.updated_at = datetime.now(UTC)
            db.commit()
            return "NEW", None, None

        # New operation registration
        new_record = IdempotencyRecord(
            idempotency_key=idempotency_key,
            operation_type=operation_type,
            payload_hash=payload_hash,
            status="IN_PROGRESS",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(new_record)
        db.commit()
        return "NEW", None, None

    @staticmethod
    def mark_completed(
        db: Session,
        operation_type: str,
        idempotency_key: str,
        response_body: dict[str, Any],
        http_status: int = 200,
    ) -> None:
        """Records successful operation completion and caches response."""
        record = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.operation_type == operation_type,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .first()
        )
        if record:
            record.status = "COMPLETED"
            record.response_body = response_body
            record.http_status = http_status
            record.updated_at = datetime.now(UTC)
            db.commit()

    @staticmethod
    def mark_failed(
        db: Session,
        operation_type: str,
        idempotency_key: str,
    ) -> None:
        """Marks operation as failed, allowing subsequent retries."""
        record = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.operation_type == operation_type,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .first()
        )
        if record:
            record.status = "FAILED"
            record.updated_at = datetime.now(UTC)
            db.commit()


idempotency_service = IdempotencyService()
