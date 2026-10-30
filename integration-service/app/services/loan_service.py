"""
Loan Lifecycle Management Service (Parts D, E, F, H)
Orchestrates:
1. Loan Application & Approval (encapsulates Fineract numeric loanId)
2. Single-Disbursement Invariant enforcement across concurrent requests
3. Repayment processing with Fineract transaction breakdown
4. ERP double-entry posting derived from actual Fineract data
"""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.db import ClientMapping, LoanMapping, TransactionRecord
from app.models.schemas import (
    DisbursementRequest,
    DisbursementResponse,
    LoanCreateRequest,
    LoanResponse,
    RepaymentBreakdown,
    RepaymentRequest,
    RepaymentResponse,
)
from app.services.accounting import derive_journal_entries_from_breakdown
from app.services.erp_client import ErpClient
from app.services.erp_client import erp_client as default_erp_client
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


class LoanConflictError(Exception):
    pass


class LoanNotFoundError(Exception):
    pass


class LoanLifecycleService:

    def __init__(
        self,
        fineract_client: FineractClient | None = None,
        erp_client: ErpClient | None = None,
    ):
        self.fineract_client = fineract_client or default_fineract_client
        self.erp_client = erp_client or default_erp_client

    async def create_loan(
        self,
        db: Session,
        request: LoanCreateRequest,
        correlation_id: str | None = None,
    ) -> tuple[LoanResponse, int]:
        """
        Creates loan application in Fineract.
        Maps client_uid to internal Fineract clientId without leaking it.
        Optionally executes immediate approval.
        """
        payload_dict = request.model_dump()
        status_action, cached_body, _http_status = idempotency_service.check_and_acquire(
            db,
            operation_type="CREATE_LOAN",
            idempotency_key=request.external_reference,
            payload=payload_dict,
        )

        if status_action == "COMPLETED" and cached_body:
            return LoanResponse(**cached_body), 200

        if status_action == "IN_PROGRESS":
            raise IdempotencyConflictError(
                f"Loan creation for reference '{request.external_reference}' is currently in progress."
            )

        # 1. Check loan_reference uniqueness
        existing_loan = (
            db.query(LoanMapping)
            .filter(LoanMapping.loan_reference == request.loan_reference)
            .first()
        )
        if existing_loan:
            idempotency_service.mark_failed(db, "CREATE_LOAN", request.external_reference)
            raise LoanConflictError(
                f"Loan reference '{request.loan_reference}' is already in use."
            )

        # 2. Resolve client_uid
        client_map = (
            db.query(ClientMapping)
            .filter(ClientMapping.client_uid == request.client_uid)
            .first()
        )
        if not client_map:
            idempotency_service.mark_failed(db, "CREATE_LOAN", request.external_reference)
            raise LoanNotFoundError(
                f"Borrower client UID '{request.client_uid}' does not exist in local registry."
            )

        try:
            # 3. Create Loan in Fineract
            fineract_resp = await self.fineract_client.create_loan_application(
                client_id=client_map.fineract_client_id,
                product_id=request.product_id,
                loan_reference=request.loan_reference,
                principal=request.principal,
                term_months=request.term_months,
                interest_rate=request.interest_rate,
                correlation_id=correlation_id,
            )
            fineract_loan_id = fineract_resp.get("loanId") or fineract_resp.get("resourceId")
            if not fineract_loan_id:
                raise ValueError(f"Fineract loan creation failed: {fineract_resp}")

            current_status = "SUBMITTED_AND_PENDING_APPROVAL"
            approved_at = None

            # 4. Optional Auto-Approval
            if request.auto_approve:
                await self.fineract_client.approve_loan(
                    loan_id=int(fineract_loan_id),
                    correlation_id=correlation_id,
                )
                current_status = "APPROVED"
                approved_at = datetime.now(UTC).isoformat()

            # 5. Persist mapping
            mapping = LoanMapping(
                loan_reference=request.loan_reference,
                external_id=request.loan_reference,
                client_uid=request.client_uid,
                fineract_loan_id=int(fineract_loan_id),
                principal=request.principal,
                term_months=request.term_months,
                status=current_status,
                disbursed=0,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            db.add(mapping)
            db.commit()

            response = LoanResponse(
                loan_reference=mapping.loan_reference,
                client_uid=mapping.client_uid,
                status=mapping.status,
                principal=mapping.principal,
                outstanding_balance=mapping.principal,
                approved_at=approved_at,
                disbursed_at=None,
            )

            idempotency_service.mark_completed(
                db,
                operation_type="CREATE_LOAN",
                idempotency_key=request.external_reference,
                response_body=response.model_dump(),
                http_status=201,
            )

            return response, 201

        except Exception:
            idempotency_service.mark_failed(db, "CREATE_LOAN", request.external_reference)
            raise

    async def get_loan_by_reference(
        self,
        db: Session,
        loan_reference: str,
        correlation_id: str | None = None,
    ) -> LoanResponse:
        """Retrieves loan status and real-time outstanding balance from Fineract."""
        mapping = (
            db.query(LoanMapping)
            .filter(LoanMapping.loan_reference == loan_reference)
            .first()
        )
        if not mapping:
            raise LoanNotFoundError(f"Loan reference '{loan_reference}' not found.")

        # Fetch live state from Fineract
        fineract_data = await self.fineract_client.get_loan_by_id(
            mapping.fineract_loan_id, correlation_id=correlation_id
        )

        outstanding = mapping.principal
        current_status = mapping.status
        approved_at = None
        disbursed_at = None

        if fineract_data:
            status_obj = fineract_data.get("status", {})
            current_status = status_obj.get("value", mapping.status)
            summary = fineract_data.get("summary", {})
            outstanding = summary.get("totalOutstanding", mapping.principal)

            timeline = fineract_data.get("timeline", {})
            if timeline.get("approvedOnDate"):
                approved_at = str(timeline.get("approvedOnDate"))
            if timeline.get("actualDisbursementDate"):
                disbursed_at = str(timeline.get("actualDisbursementDate"))

            # Update mapping status if changed
            if mapping.status != current_status:
                mapping.status = current_status
                db.commit()

        return LoanResponse(
            loan_reference=mapping.loan_reference,
            client_uid=mapping.client_uid,
            status=current_status,
            principal=mapping.principal,
            outstanding_balance=float(outstanding),
            approved_at=approved_at,
            disbursed_at=disbursed_at,
        )

    async def disburse_loan(
        self,
        db: Session,
        loan_reference: str,
        request: DisbursementRequest,
        correlation_id: str | None = None,
    ) -> tuple[DisbursementResponse, int]:
        """
        Executes single disbursement for a loan.
        Enforces:
        - Invariant: A loan can be disbursed ONLY ONCE.
        - Idempotent on external_reference.
        - Concurrent requests with different references are rejected with 409 Conflict.
        - Posts DR 1200 / CR 1100 to Mock ERP.
        """
        payload_dict = request.model_dump()
        status_action, cached_body, http_status = idempotency_service.check_and_acquire(
            db,
            operation_type="DISBURSEMENT",
            idempotency_key=request.external_reference,
            payload=payload_dict,
        )

        if status_action == "COMPLETED" and cached_body:
            return DisbursementResponse(**cached_body), http_status or 200

        if status_action == "IN_PROGRESS":
            raise IdempotencyConflictError(
                f"Disbursement for reference '{request.external_reference}' is currently in progress."
            )

        mapping = (
            db.query(LoanMapping)
            .filter(LoanMapping.loan_reference == loan_reference)
            .first()
        )
        if not mapping:
            idempotency_service.mark_failed(db, "DISBURSEMENT", request.external_reference)
            raise LoanNotFoundError(f"Loan reference '{loan_reference}' not found.")

        # Single Disbursement Invariant Check
        if mapping.disbursed == 1:
            idempotency_service.mark_failed(db, "DISBURSEMENT", request.external_reference)
            raise LoanConflictError(
                f"Conflict: Loan '{loan_reference}' has already been disbursed. "
                f"Single-disbursement invariant prohibits multiple disbursements."
            )

        # State machine record
        txn_record = TransactionRecord(
            external_reference=request.external_reference,
            operation_type="DISBURSEMENT",
            loan_reference=loan_reference,
            state="FINERACT_PENDING",
            payload_hash=request.external_reference,
            amount=request.amount,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(txn_record)
        db.commit()

        try:
            # 1. Disburse in Fineract
            fineract_resp = await self.fineract_client.disburse_loan(
                loan_id=mapping.fineract_loan_id,
                amount=request.amount,
                disbursement_date=request.disbursement_date,
                external_id=request.external_reference,
                correlation_id=correlation_id,
            )
            fineract_txn_id = fineract_resp.get("resourceId")

            # Update state
            txn_record.state = "ERP_PENDING"
            txn_record.fineract_transaction_id = int(fineract_txn_id) if fineract_txn_id else None
            db.commit()

            # 2. Derive journal entries: DR 1200 Loan Receivable / CR 1100 Bank
            journal_def = derive_journal_entries_from_breakdown(
                operation_type="DISBURSEMENT",
                external_reference=request.external_reference,
                transaction_date=request.disbursement_date,
                total_amount=request.amount,
                breakdown={},
            )

            # 3. Post to Mock ERP
            erp_resp = await self.erp_client.post_journal_entry(
                external_reference=request.external_reference,
                description=f"Disbursement for {loan_reference}",
                entry_date=request.disbursement_date,
                lines=journal_def["lines"],
                correlation_id=correlation_id,
            )
            erp_txn_id = str(erp_resp.get("id"))

            # 4. Finalize state
            txn_record.state = "COMPLETED"
            txn_record.erp_transaction_id = erp_txn_id
            mapping.disbursed = 1
            mapping.status = "ACTIVE"
            db.commit()

            response = DisbursementResponse(
                external_reference=request.external_reference,
                loan_reference=loan_reference,
                status="COMPLETED",
                fineract_transaction_id=txn_record.fineract_transaction_id,
                erp_transaction_id=erp_txn_id,
                disbursed_amount=request.amount,
            )

            idempotency_service.mark_completed(
                db,
                operation_type="DISBURSEMENT",
                idempotency_key=request.external_reference,
                response_body=response.model_dump(),
                http_status=200,
            )

            return response, 200

        except Exception as exc:
            txn_record.state = "FAILED"
            txn_record.error_message = str(exc)
            db.commit()
            idempotency_service.mark_failed(db, "DISBURSEMENT", request.external_reference)
            raise

    async def repay_loan(
        self,
        db: Session,
        loan_reference: str,
        request: RepaymentRequest,
        correlation_id: str | None = None,
    ) -> tuple[RepaymentResponse, int]:
        """
        Executes a loan repayment in Fineract and posts balanced entries to Mock ERP.
        Derives principal/interest split directly from Fineract transaction details.
        """
        payload_dict = request.model_dump()
        status_action, cached_body, http_status = idempotency_service.check_and_acquire(
            db,
            operation_type="REPAYMENT",
            idempotency_key=request.external_reference,
            payload=payload_dict,
        )

        if status_action == "COMPLETED" and cached_body:
            return RepaymentResponse(**cached_body), http_status or 200

        if status_action == "IN_PROGRESS":
            raise IdempotencyConflictError(
                f"Repayment for reference '{request.external_reference}' is currently in progress."
            )

        mapping = (
            db.query(LoanMapping)
            .filter(LoanMapping.loan_reference == loan_reference)
            .first()
        )
        if not mapping:
            idempotency_service.mark_failed(db, "REPAYMENT", request.external_reference)
            raise LoanNotFoundError(f"Loan reference '{loan_reference}' not found.")

        txn_record = TransactionRecord(
            external_reference=request.external_reference,
            operation_type="REPAYMENT",
            loan_reference=loan_reference,
            state="FINERACT_PENDING",
            payload_hash=request.external_reference,
            amount=request.amount,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(txn_record)
        db.commit()

        try:
            # 1. Post repayment to Fineract
            fineract_resp = await self.fineract_client.repay_loan(
                loan_id=mapping.fineract_loan_id,
                amount=request.amount,
                transaction_date=request.transaction_date,
                external_id=request.external_reference,
                correlation_id=correlation_id,
            )
            fineract_txn_id = fineract_resp.get("resourceId")
            txn_record.fineract_transaction_id = int(fineract_txn_id) if fineract_txn_id else None
            txn_record.state = "ERP_PENDING"
            db.commit()

            # 2. Retrieve transaction breakdown from Fineract
            txn_breakdown = {
                "principal": request.amount,
                "interest": 0.0,
                "fee": 0.0,
                "penalty": 0.0,
            }
            if fineract_txn_id:
                txn_details = await self.fineract_client.get_loan_transaction(
                    mapping.fineract_loan_id, int(fineract_txn_id), correlation_id=correlation_id
                )
                if txn_details:
                    txn_breakdown["principal"] = float(txn_details.get("principalPortion", 0.0))
                    txn_breakdown["interest"] = float(txn_details.get("interestPortion", 0.0))
                    txn_breakdown["fee"] = float(txn_details.get("feeChargesPortion", 0.0))
                    txn_breakdown["penalty"] = float(txn_details.get("penaltyChargesPortion", 0.0))

            # 3. Derive balanced ERP journal entry
            journal_def = derive_journal_entries_from_breakdown(
                operation_type="REPAYMENT",
                external_reference=request.external_reference,
                transaction_date=request.transaction_date,
                total_amount=request.amount,
                breakdown=txn_breakdown,
            )

            # 4. Post to ERP
            erp_resp = await self.erp_client.post_journal_entry(
                external_reference=request.external_reference,
                description=f"Repayment for {loan_reference}",
                entry_date=request.transaction_date,
                lines=journal_def["lines"],
                correlation_id=correlation_id,
            )
            erp_txn_id = str(erp_resp.get("id"))

            # 5. Fetch updated balance
            loan_live = await self.fineract_client.get_loan_by_id(
                mapping.fineract_loan_id, correlation_id=correlation_id
            )
            remaining_balance = 0.0
            if loan_live:
                remaining_balance = float(loan_live.get("summary", {}).get("totalOutstanding", 0.0))
                new_status = loan_live.get("status", {}).get("value", mapping.status)
                mapping.status = new_status

            txn_record.state = "COMPLETED"
            txn_record.erp_transaction_id = erp_txn_id
            db.commit()

            response = RepaymentResponse(
                external_reference=request.external_reference,
                loan_reference=loan_reference,
                status="COMPLETED",
                fineract_transaction_id=txn_record.fineract_transaction_id,
                breakdown=RepaymentBreakdown(
                    principal=txn_breakdown["principal"],
                    interest=txn_breakdown["interest"],
                    fee=txn_breakdown["fee"],
                    penalty=txn_breakdown["penalty"],
                ),
                erp_transaction_id=erp_txn_id,
                remaining_balance=remaining_balance,
            )

            idempotency_service.mark_completed(
                db,
                operation_type="REPAYMENT",
                idempotency_key=request.external_reference,
                response_body=response.model_dump(),
                http_status=200,
            )

            return response, 200

        except Exception as exc:
            txn_record.state = "FAILED"
            txn_record.error_message = str(exc)
            db.commit()
            idempotency_service.mark_failed(db, "REPAYMENT", request.external_reference)
            raise


loan_service = LoanLifecycleService()
