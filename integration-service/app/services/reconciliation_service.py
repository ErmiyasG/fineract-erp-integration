"""
Transaction Reconciliation Service (Part K)
Compares Fineract transactions against Mock ERP journal entries.
Categorizes transactions into:
- MATCHED: Present in both, same amount and type.
- MISSING_IN_ERP: Succeeded in Fineract, absent in ERP.
- MISSING_IN_FINERACT: Journal entry in ERP, absent in Fineract.
- AMOUNT_MISMATCH: Present in both, amounts differ.
Supports date-range and reference filtering, reversal auditing, and explicit idempotent repair.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.db import TransactionRecord
from app.models.schemas import (
    ReconciliationFilterRequest,
    ReconciliationItem,
    ReconciliationResponse,
    ReconciliationSummary,
)
from app.services.erp_client import ErpClient
from app.services.erp_client import erp_client as default_erp_client
from app.services.fineract_client import FineractClient
from app.services.fineract_client import fineract_client as default_fineract_client
from app.services.recovery_service import recovery_service

logger = logging.getLogger(__name__)


class ReconciliationService:
    def __init__(
        self,
        erp_client: ErpClient | None = None,
        fineract_client: FineractClient | None = None,
    ):
        self.erp_client = erp_client or default_erp_client
        self.fineract_client = fineract_client or default_fineract_client

    async def run_reconciliation(
        self,
        db: Session,
        filter_req: ReconciliationFilterRequest | None = None,
        correlation_id: str | None = None,
    ) -> ReconciliationResponse:
        """
        Executes read-only two-way reconciliation between Apache Fineract and Mock ERP.
        Keyed strictly by external_reference.
        """
        query = db.query(TransactionRecord)

        if filter_req:
            if filter_req.references:
                query = query.filter(
                    TransactionRecord.external_reference.in_(filter_req.references)
                )
            if filter_req.start_date:
                query = query.filter(TransactionRecord.created_at >= filter_req.start_date)
            if filter_req.end_date:
                query = query.filter(TransactionRecord.created_at <= filter_req.end_date)

        records = query.all()

        items: list[ReconciliationItem] = []
        matched_count = 0
        missing_in_erp_count = 0
        missing_in_fineract_count = 0
        amount_mismatch_count = 0

        for rec in records:
            # 1. Determine Fineract presence
            fineract_amount = None
            if rec.fineract_transaction_id is not None and rec.state != "FAILED":
                fineract_amount = float(rec.amount)

            # 2. Determine ERP presence
            erp_amount = None
            try:
                erp_entry = await self.erp_client.get_journal_entry(
                    rec.external_reference, correlation_id=correlation_id
                )
                if erp_entry:
                    erp_amount = float(erp_entry.get("total_debits", 0.0))
            except Exception as e:
                logger.warning("Error fetching ERP entry for %s: %s", rec.external_reference, e)

            # 3. Categorize outcome
            if fineract_amount is not None and erp_amount is not None:
                diff = abs(fineract_amount - erp_amount)
                if diff < 0.01:
                    status_val = "MATCHED"
                    if rec.operation_type in ("DISBURSEMENT_REVERSAL", "REPAYMENT_REVERSAL"):
                        details = "Reversal transaction matched in both systems; net ledger effect is 0.00 RWF"
                    else:
                        details = "Transaction matched in both systems with identical amount"
                    matched_count += 1
                else:
                    status_val = "AMOUNT_MISMATCH"
                    details = f"Amount mismatch: Fineract has {fineract_amount:.2f} RWF, ERP has {erp_amount:.2f} RWF (Diff: {diff:.2f} RWF)"
                    amount_mismatch_count += 1

            elif fineract_amount is not None and erp_amount is None:
                status_val = "MISSING_IN_ERP"
                details = f"Transaction completed in Fineract (ID: {rec.fineract_transaction_id}) but missing in ERP ledger"
                missing_in_erp_count += 1

            elif fineract_amount is None and erp_amount is not None:
                status_val = "MISSING_IN_FINERACT"
                details = (
                    "Journal entry recorded in ERP but transaction not confirmed in Fineract core"
                )
                missing_in_fineract_count += 1

            else:
                # Both None: Failed before either system mutated
                continue

            items.append(
                ReconciliationItem(
                    external_reference=rec.external_reference,
                    status=status_val,
                    fineract_amount=fineract_amount,
                    erp_amount=erp_amount,
                    details=details,
                )
            )

        summary = ReconciliationSummary(
            total_records=len(items),
            matched=matched_count,
            missing_in_erp=missing_in_erp_count,
            missing_in_fineract=missing_in_fineract_count,
            amount_mismatch=amount_mismatch_count,
        )

        return ReconciliationResponse(summary=summary, items=items)

    async def repair_missing_in_erp(
        self,
        db: Session,
        references: list[str],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Explicit, idempotent repair action for transactions that succeeded in Fineract but are MISSING_IN_ERP.
        Replays journal entries to Mock ERP using idempotent external_reference.
        """
        repaired: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for ref in references:
            res = await recovery_service.recover_transaction(db, ref, correlation_id=correlation_id)
            if res.get("status") in ("RECOVERED", "ALREADY_COMPLETED"):
                repaired.append(res)
            else:
                errors.append(res)

        return {
            "repaired_count": len(repaired),
            "error_count": len(errors),
            "repaired": repaired,
            "errors": errors,
            "repaired_at": datetime.now(UTC).isoformat(),
        }


reconciliation_service = ReconciliationService()
