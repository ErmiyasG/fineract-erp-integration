"""
Mock ERP REST API Application.
Implements Chart of Accounts management and balanced General Ledger journal entries.
Enforces idempotency on external_reference and self-contained uniqueness validation.
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db import get_db, init_db
from app.models import Account, JournalEntry, JournalEntryLine
from app.schemas import (
    AccountBalanceResponse,
    AccountCreate,
    AccountResponse,
    JournalEntryCreate,
    JournalEntryLineSchema,
    JournalEntryResponse,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    yield


app = FastAPI(
    title="Mock ERP API",
    description="Accounting master data and general ledger journal entries",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "mock-erp"}


# -----------------------------------------------------------------------------
# Chart of Accounts Endpoints
# -----------------------------------------------------------------------------
@app.post(
    "/api/accounts",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Accounts"],
)
def create_or_sync_account(
    account_in: AccountCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> Any:
    """
    Creates a GL Account in the ERP Chart of Accounts.
    Idempotent on external_reference:
    - Same external_reference and matching payload: returns existing account (200 OK)
    - Same external_reference and conflicting payload: rejects with 409 Conflict
    - Reused code with different reference: rejects with 409 Conflict
    """
    existing_by_ref = (
        db.query(Account).filter(Account.external_reference == account_in.external_reference).first()
    )
    if existing_by_ref:
        if (
            existing_by_ref.code == account_in.code
            and existing_by_ref.name == account_in.name
            and existing_by_ref.type == account_in.type
            and existing_by_ref.currency == account_in.currency
        ):
            # Idempotent match
            response.status_code = status.HTTP_200_OK
            return AccountResponse(
                code=str(existing_by_ref.code),
                name=str(existing_by_ref.name),
                type=str(existing_by_ref.type),
                currency=str(existing_by_ref.currency),
                external_reference=str(existing_by_ref.external_reference),
                created_at=existing_by_ref.created_at.isoformat(),
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Account with external_reference '{account_in.external_reference}' already exists "
                f"with differing attributes (code={existing_by_ref.code}, name='{existing_by_ref.name}')"
            ),
        )

    # Check if code is already used with another reference
    existing_by_code = db.query(Account).filter(Account.code == account_in.code).first()
    if existing_by_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Account code '{account_in.code}' is already assigned to reference '{existing_by_code.external_reference}'",
        )

    new_account = Account(
        code=account_in.code,
        name=account_in.name,
        type=account_in.type,
        currency=account_in.currency,
        external_reference=account_in.external_reference,
        created_at=datetime.now(UTC),
    )
    db.add(new_account)
    db.commit()
    db.refresh(new_account)

    return AccountResponse(
        code=str(new_account.code),
        name=str(new_account.name),
        type=str(new_account.type),
        currency=str(new_account.currency),
        external_reference=str(new_account.external_reference),
        created_at=new_account.created_at.isoformat(),
    )


@app.get("/api/accounts", response_model=list[AccountResponse], tags=["Accounts"])
def list_accounts(db: Session = Depends(get_db)) -> list[AccountResponse]:
    """Lists all accounts registered in the Mock ERP Chart of Accounts."""
    accounts = db.query(Account).order_by(Account.code.asc()).all()
    return [
        AccountResponse(
            code=str(acc.code),
            name=str(acc.name),
            type=str(acc.type),
            currency=str(acc.currency),
            external_reference=str(acc.external_reference),
            created_at=acc.created_at.isoformat(),
        )
        for acc in accounts
    ]


@app.get("/api/accounts/{code}/balance", response_model=AccountBalanceResponse, tags=["Accounts"])
def get_account_balance(code: str, db: Session = Depends(get_db)) -> AccountBalanceResponse:
    """
    Computes real-time account balance from all posted journal entries.
    Formula:
    - ASSET / EXPENSE: Normal debit balance = Total Debits - Total Credits
    - LIABILITY / EQUITY / INCOME: Normal credit balance = Total Credits - Total Debits
    """
    account = db.query(Account).filter(Account.code == code).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account with code '{code}' not found in Chart of Accounts",
        )

    lines = db.query(JournalEntryLine).filter(JournalEntryLine.account_code == code).all()
    total_debits = sum(line.amount for line in lines if line.entry_type == "DEBIT")
    total_credits = sum(line.amount for line in lines if line.entry_type == "CREDIT")

    if account.type in ("ASSET", "EXPENSE"):
        balance = round(total_debits - total_credits, 4)
    else:
        balance = round(total_credits - total_debits, 4)

    return AccountBalanceResponse(
        code=str(account.code),
        name=str(account.name),
        type=str(account.type),
        currency=str(account.currency),
        balance=balance,
        total_debits=round(total_debits, 4),
        total_credits=round(total_credits, 4),
    )


# -----------------------------------------------------------------------------
# Journal Entries Endpoints
# -----------------------------------------------------------------------------
@app.post(
    "/api/journal-entries",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Journal Entries"],
)
def create_journal_entry(
    entry_in: JournalEntryCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> Any:
    """
    Posts a double-entry journal entry to the Mock ERP.
    Rules:
    1. Debits must equal Credits (sum(DEBIT) == sum(CREDIT)), otherwise rejected (400).
    2. All account codes must exist in the Chart of Accounts (400).
    3. Idempotent on external_reference:
       - Identical payload returns existing entry (200 OK)
       - Differing payload rejected with 409 Conflict.
    """
    # 1. Enforce debits == credits
    total_debits = sum(line.amount for line in entry_in.lines if line.entry_type == "DEBIT")
    total_credits = sum(line.amount for line in entry_in.lines if line.entry_type == "CREDIT")

    if abs(total_debits - total_credits) > 0.0001:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Journal entry must balance: Debits ({round(total_debits, 4)}) "
                f"!= Credits ({round(total_credits, 4)})"
            ),
        )

    # 2. Check all account codes exist
    referenced_codes = {line.account_code for line in entry_in.lines}
    existing_accounts = db.query(Account).filter(Account.code.in_(referenced_codes)).all()
    found_codes = {acc.code for acc in existing_accounts}
    missing_codes = referenced_codes - found_codes
    if missing_codes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Referenced account codes do not exist in Chart of Accounts: {list(missing_codes)}",
        )

    # 3. Check idempotency on external_reference
    existing_entry = (
        db.query(JournalEntry)
        .filter(JournalEntry.external_reference == entry_in.external_reference)
        .first()
    )
    if existing_entry:
        # Verify lines match
        existing_lines_map = [
            {"account_code": l.account_code, "entry_type": l.entry_type, "amount": l.amount}
            for l in existing_entry.lines
        ]
        new_lines_map = [
            {"account_code": l.account_code, "entry_type": l.entry_type, "amount": l.amount}
            for l in entry_in.lines
        ]
        # Sort to compare
        key_fn = lambda x: (x["account_code"], x["entry_type"], x["amount"])
        if sorted(existing_lines_map, key=key_fn) == sorted(new_lines_map, key=key_fn):
            response.status_code = status.HTTP_200_OK
            return JournalEntryResponse(
                id=int(existing_entry.id),
                external_reference=str(existing_entry.external_reference),
                description=str(existing_entry.description) if existing_entry.description else None,
                entry_date=existing_entry.entry_date.isoformat(),
                lines=[
                    JournalEntryLineSchema(
                        account_code=str(l.account_code),
                        entry_type=l.entry_type,
                        amount=float(l.amount),
                    )
                    for l in existing_entry.lines
                ],
                total_debits=round(total_debits, 4),
                total_credits=round(total_credits, 4),
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Journal entry with external_reference '{entry_in.external_reference}' already exists with different lines",
        )

    entry_date = datetime.now(UTC)
    if entry_in.entry_date:
        try:
            entry_date = datetime.fromisoformat(entry_in.entry_date)
        except ValueError:
            entry_date = datetime.now(UTC)

    new_entry = JournalEntry(
        external_reference=entry_in.external_reference,
        description=entry_in.description,
        entry_date=entry_date,
        created_at=datetime.now(UTC),
    )
    db.add(new_entry)
    db.flush()

    for line in entry_in.lines:
        line_model = JournalEntryLine(
            journal_entry_id=new_entry.id,
            account_code=line.account_code,
            entry_type=line.entry_type,
            amount=line.amount,
        )
        db.add(line_model)

    db.commit()
    db.refresh(new_entry)

    return JournalEntryResponse(
        id=int(new_entry.id),
        external_reference=str(new_entry.external_reference),
        description=str(new_entry.description) if new_entry.description else None,
        entry_date=new_entry.entry_date.isoformat(),
        lines=[
            JournalEntryLineSchema(
                account_code=str(l.account_code),
                entry_type=l.entry_type,
                amount=float(l.amount),
            )
            for l in new_entry.lines
        ],
        total_debits=round(total_debits, 4),
        total_credits=round(total_credits, 4),
    )


@app.get(
    "/api/journal-entries/{external_reference}",
    response_model=JournalEntryResponse,
    tags=["Journal Entries"],
)
def get_journal_entry(
    external_reference: str,
    db: Session = Depends(get_db),
) -> JournalEntryResponse:
    """Retrieves a posted journal entry by its external_reference."""
    entry = (
        db.query(JournalEntry)
        .filter(JournalEntry.external_reference == external_reference)
        .first()
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Journal entry with external_reference '{external_reference}' not found",
        )

    total_debits = sum(line.amount for line in entry.lines if line.entry_type == "DEBIT")
    total_credits = sum(line.amount for line in entry.lines if line.entry_type == "CREDIT")

    return JournalEntryResponse(
        id=int(entry.id),
        external_reference=str(entry.external_reference),
        description=str(entry.description) if entry.description else None,
        entry_date=entry.entry_date.isoformat(),
        lines=[
            JournalEntryLineSchema(
                account_code=str(l.account_code),
                entry_type=l.entry_type,
                amount=float(l.amount),
            )
            for l in entry.lines
        ],
        total_debits=round(total_debits, 4),
        total_credits=round(total_credits, 4),
    )
