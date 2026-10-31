"""
Transaction Recovery Service (Part J)
Implements explicit state machine recovery for distributed financial transactions:
- Recovers transactions interrupted by crashes or timeouts.
- Resolves Fineract ambiguities via externalId lookup (never blindly retries).
- Replays journal entries to Mock ERP using idempotent external_reference.
- Sweeps pending/failed transactions on startup or on-demand via API.
"""
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.db import LoanMapping, TransactionRecord
from app.services.accounting import derive_journal_entries_from_breakdown
from app.services.erp_client import ErpClient
from app.services.erp_client import erp_client as default_erp_client
from app.services.fineract_client import FineractClient
from app.services.fineract_client import fineract_client as default_fineract_client
from app.services.idempotency import idempotency_service

logger = logging.getLogger(__name__)


class TransactionRecoveryService:

    def __init__(
        self,
        fineract_client: FineractClient | None = None,
        erp_client: ErpClient | None = None,
    ):
        self.fineract_client = fineract_client or default_fineract_client
        self.erp_client = erp_client or default_erp_client

    async def recover_transaction(
        self,
        db: Session,
        external_reference: str,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Recovers a single transaction according to the distributed state machine:
        1. If FINERACT_PENDING: Check Fineract by externalId.
           - If found: set FINERACT_DONE and proceed to ERP.
           - If absent: set FAILED (safe to retry).
        2. If FINERACT_DONE / ERP_PENDING / NEEDS_RECONCILIATION:
           - Post idempotent journal entry to ERP.
           - Transition to COMPLETED upon confirmation.
        """
        txn = (
            db.query(TransactionRecord)
            .filter(TransactionRecord.external_reference == external_reference)
            .first()
        )
        if not txn:
            return {"status": "NOT_FOUND", "external_reference": external_reference}

        if txn.state == "COMPLETED":
            return {
                "status": "ALREADY_COMPLETED",
                "external_reference": external_reference,
                "state": txn.state,
                "erp_transaction_id": txn.erp_transaction_id,
            }

        loan_map = (
            db.query(LoanMapping)
            .filter(LoanMapping.loan_reference == txn.loan_reference)
            .first()
        )
        if not loan_map:
            return {
                "status": "ERROR",
                "message": f"Loan reference '{txn.loan_reference}' not found for transaction '{external_reference}'.",
            }

        # Step 1: Resolve Fineract state if interrupted during Fineract call
        if txn.state in ("FINERACT_PENDING", "RECEIVED"):
            logger.info("Resolving Fineract state via externalId lookup for %s", external_reference)
            fineract_txn = await self.fineract_client.find_transaction_by_external_id(
                loan_id=loan_map.fineract_loan_id,
                external_id=external_reference,
                correlation_id=correlation_id,
            )
            if fineract_txn and fineract_txn.get("id"):
                txn.fineract_transaction_id = int(fineract_txn["id"])
                txn.state = "ERP_PENDING"
                txn.updated_at = datetime.now(UTC)
                db.commit()
            else:
                # Confirmed: Fineract did NOT process this mutation. Safe to mark FAILED.
                txn.state = "FAILED"
                txn.error_message = "Recovery lookup: transaction not found in Fineract core."
                txn.updated_at = datetime.now(UTC)
                idempotency_service.mark_failed(db, txn.operation_type, external_reference)
                db.commit()
                return {
                    "status": "ABORTED_NOT_IN_FINERACT",
                    "external_reference": external_reference,
                    "state": "FAILED",
                    "safe_to_retry": True,
                }

        # Step 2: Post to ERP if Fineract has processed the mutation
        if txn.state in ("FINERACT_DONE", "ERP_PENDING", "NEEDS_RECONCILIATION"):
            try:
                if txn.operation_type == "DISBURSEMENT":
                    journal_def = derive_journal_entries_from_breakdown(
                        operation_type="DISBURSEMENT",
                        external_reference=external_reference,
                        transaction_date=datetime.now(UTC).strftime("%Y-%m-%d"),
                        total_amount=txn.amount,
                        breakdown={},
                    )
                    erp_resp = await self.erp_client.post_journal_entry(
                        external_reference=external_reference,
                        description=f"Recovered disbursement for {txn.loan_reference}",
                        entry_date=datetime.now(UTC).strftime("%Y-%m-%d"),
                        lines=journal_def["lines"],
                        correlation_id=correlation_id,
                    )
                    erp_txn_id = str(erp_resp.get("id"))
                    txn.erp_transaction_id = erp_txn_id
                    txn.state = "COMPLETED"
                    txn.error_message = None
                    txn.updated_at = datetime.now(UTC)
                    loan_map.disbursed = 1
                    loan_map.status = "ACTIVE"
                    db.commit()

                    idempotency_service.mark_completed(
                        db,
                        operation_type="DISBURSEMENT",
                        idempotency_key=external_reference,
                        response_body={
                            "external_reference": external_reference,
                            "loan_reference": txn.loan_reference,
                            "status": "COMPLETED",
                            "fineract_transaction_id": txn.fineract_transaction_id,
                            "erp_transaction_id": erp_txn_id,
                            "disbursed_amount": txn.amount,
                        },
                        http_status=200,
                    )

                    return {
                        "status": "RECOVERED",
                        "external_reference": external_reference,
                        "state": "COMPLETED",
                        "erp_transaction_id": erp_txn_id,
                    }

                elif txn.operation_type == "REPAYMENT":
                    txn_breakdown = {
                        "principal": txn.amount,
                        "interest": 0.0,
                        "fee": 0.0,
                        "penalty": 0.0,
                    }
                    if txn.fineract_transaction_id:
                        fineract_details = await self.fineract_client.get_loan_transaction(
                            loan_map.fineract_loan_id, txn.fineract_transaction_id, correlation_id=correlation_id
                        )
                        if fineract_details:
                            txn_breakdown["principal"] = float(fineract_details.get("principalPortion", 0.0))
                            txn_breakdown["interest"] = float(fineract_details.get("interestPortion", 0.0))
                            txn_breakdown["fee"] = float(fineract_details.get("feeChargesPortion", 0.0))
                            txn_breakdown["penalty"] = float(fineract_details.get("penaltyChargesPortion", 0.0))

                    journal_def = derive_journal_entries_from_breakdown(
                        operation_type="REPAYMENT",
                        external_reference=external_reference,
                        transaction_date=datetime.now(UTC).strftime("%Y-%m-%d"),
                        total_amount=txn.amount,
                        breakdown=txn_breakdown,
                    )
                    erp_resp = await self.erp_client.post_journal_entry(
                        external_reference=external_reference,
                        description=f"Recovered repayment for {txn.loan_reference}",
                        entry_date=datetime.now(UTC).strftime("%Y-%m-%d"),
                        lines=journal_def["lines"],
                        correlation_id=correlation_id,
                    )
                    erp_txn_id = str(erp_resp.get("id"))
                    txn.erp_transaction_id = erp_txn_id
                    txn.state = "COMPLETED"
                    txn.error_message = None
                    txn.updated_at = datetime.now(UTC)
                    db.commit()

                    idempotency_service.mark_completed(
                        db,
                        operation_type="REPAYMENT",
                        idempotency_key=external_reference,
                        response_body={
                            "external_reference": external_reference,
                            "loan_reference": txn.loan_reference,
                            "status": "COMPLETED",
                            "fineract_transaction_id": txn.fineract_transaction_id,
                            "erp_transaction_id": erp_txn_id,
                            "remaining_balance": 0.0,
                        },
                        http_status=200,
                    )

                    return {
                        "status": "RECOVERED",
                        "external_reference": external_reference,
                        "state": "COMPLETED",
                        "erp_transaction_id": erp_txn_id,
                    }

            except Exception as exc:
                txn.retry_count += 1
                if txn.retry_count >= 3:
                    txn.state = "NEEDS_RECONCILIATION"
                txn.error_message = f"ERP posting failed on recovery: {exc}"
                txn.updated_at = datetime.now(UTC)
                db.commit()
                return {
                    "status": "RECOVERY_FAILED",
                    "external_reference": external_reference,
                    "state": txn.state,
                    "retry_count": txn.retry_count,
                    "error": str(exc),
                }

        return {"status": "NOOP", "external_reference": external_reference, "state": txn.state}

    async def sweep_and_recover(
        self,
        db: Session,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Sweeps all pending or interrupted transactions in the database.
        Detects uncompleted mutations on startup or scheduled maintenance.
        """
        pending_txns = (
            db.query(TransactionRecord)
            .filter(TransactionRecord.state.in_(["FINERACT_PENDING", "FINERACT_DONE", "ERP_PENDING", "NEEDS_RECONCILIATION"]))
            .all()
        )
        recovered = 0
        failed = 0
        still_pending = 0
        results = []

        for txn in pending_txns:
            res = await self.recover_transaction(db, txn.external_reference, correlation_id=correlation_id)
            results.append(res)
            if res.get("status") in ("RECOVERED", "ALREADY_COMPLETED"):
                recovered += 1
            elif res.get("status") in ("ABORTED_NOT_IN_FINERACT", "RECOVERY_FAILED"):
                failed += 1
            else:
                still_pending += 1

        return {
            "total_scanned": len(pending_txns),
            "recovered": recovered,
            "failed": failed,
            "still_pending": still_pending,
            "items": results,
        }


recovery_service = TransactionRecoveryService()
