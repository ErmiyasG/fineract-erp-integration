"""
SQLAlchemy Database Models for Integration Service
"""
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class AccountSyncMapping(Base):
    """
    Tracks GL Accounts synchronized between ERP and Apache Fineract.
    Enforces uniqueness: external_reference is PK, code is UNIQUE.
    Status transitions: SYNC_PENDING -> SYNCED (or FAILED with retry)
    """
    __tablename__ = "account_sync_mappings"

    external_reference = Column(String(100), primary_key=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    account_type = Column(String(20), nullable=False)
    currency = Column(String(3), nullable=False, default="RWF")
    fineract_id = Column(Integer, nullable=True)
    sync_status = Column(String(20), nullable=False, default="SYNC_PENDING", index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)


class IdempotencyRecord(Base):
    """
    Tier-1 Ingress Idempotency Record.
    Prevents duplicate executions for any state-changing request.
    """
    __tablename__ = "idempotency_records"

    idempotency_key = Column(String(150), primary_key=True)
    operation_type = Column(String(50), nullable=False, index=True)
    payload_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="IN_PROGRESS")  # IN_PROGRESS, COMPLETED, FAILED
    http_status = Column(Integer, nullable=True)
    response_body = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)

    __table_args__ = (
        UniqueConstraint("operation_type", "idempotency_key", name="uq_op_idempotency_key"),
    )


class ClientMapping(Base):
    """
    Encapsulates Fineract internal client IDs behind client_uid business keys.
    """
    __tablename__ = "client_mappings"

    client_uid = Column(String(100), primary_key=True)
    external_id = Column(String(100), unique=True, nullable=False, index=True)
    fineract_client_id = Column(Integer, nullable=False, unique=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="ACTIVE")
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)


class LoanMapping(Base):
    """
    Encapsulates Fineract internal loan IDs behind loan_reference business keys.
    """
    __tablename__ = "loan_mappings"

    loan_reference = Column(String(100), primary_key=True)
    external_id = Column(String(100), unique=True, nullable=False, index=True)
    client_uid = Column(String(100), nullable=False, index=True)
    fineract_loan_id = Column(Integer, nullable=False, unique=True)
    principal = Column(Float, nullable=False)
    term_months = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="SUBMITTED_AND_PENDING_APPROVAL")
    disbursed = Column(Integer, nullable=False, default=0)  # 0 or 1, enforces single disbursement
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)


class TransactionRecord(Base):
    """
    Persisted state machine record for financial mutations (Disbursement, Repayment).
    """
    __tablename__ = "transaction_records"

    external_reference = Column(String(100), primary_key=True)
    operation_type = Column(String(50), nullable=False)
    loan_reference = Column(String(100), nullable=False, index=True)
    state = Column(String(30), nullable=False, index=True)  # RECEIVED, FINERACT_PENDING, FINERACT_DONE, ERP_PENDING, COMPLETED, FAILED, NEEDS_RECONCILIATION
    payload_hash = Column(String(64), nullable=False)
    amount = Column(Float, nullable=False)
    fineract_transaction_id = Column(Integer, nullable=True)
    erp_transaction_id = Column(String(100), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)
