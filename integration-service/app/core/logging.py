"""
Structured JSON Observability & Logging (Section 9)
Provides structured JSON logging with correlation IDs and domain business fields:
correlation_id, external_reference, client_uid, loan_reference,
fineract_transaction_id, erp_transaction_id, status, timestamp.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }

        # Include structured business context fields if present
        for field in (
            "correlation_id",
            "external_reference",
            "client_uid",
            "loan_reference",
            "fineract_transaction_id",
            "erp_transaction_id",
            "status",
        ):
            val = getattr(record, field, None)
            if val is not None:
                log_data[field] = val

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_structured_logging(level: int = logging.INFO) -> None:
    """Configures root logger with StructuredJsonFormatter."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredJsonFormatter())

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Replace existing handlers to avoid duplicated output
    root_logger.handlers = [handler]


audit_logger = logging.getLogger("fintech.audit")


def log_audit_event(
    message: str,
    level: int = logging.INFO,
    correlation_id: str | None = None,
    external_reference: str | None = None,
    client_uid: str | None = None,
    loan_reference: str | None = None,
    fineract_transaction_id: int | None = None,
    erp_transaction_id: str | None = None,
    status: str | None = None,
) -> None:
    """Emits an audit log event containing structured business identifiers."""
    extra = {
        "correlation_id": correlation_id,
        "external_reference": external_reference,
        "client_uid": client_uid,
        "loan_reference": loan_reference,
        "fineract_transaction_id": fineract_transaction_id,
        "erp_transaction_id": erp_transaction_id,
        "status": status,
    }
    audit_logger.log(level, message, extra=extra)
