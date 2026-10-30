"""
Pydantic schemas for Mock ERP API.
"""
from typing import Literal

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    code: str = Field(..., description="Account Code (e.g. 1100)")
    name: str = Field(..., description="Account Name (e.g. Bank Main Account)")
    type: Literal["ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"] = Field(
        ..., description="Account classification"
    )
    currency: str = Field(default="RWF", description="Currency ISO code")
    external_reference: str = Field(..., description="External ERP ID (e.g. GL-1100)")


class AccountResponse(BaseModel):
    code: str
    name: str
    type: str
    currency: str
    external_reference: str
    created_at: str


class AccountBalanceResponse(BaseModel):
    code: str
    name: str
    type: str
    currency: str
    balance: float
    total_debits: float
    total_credits: float


class JournalEntryLineSchema(BaseModel):
    account_code: str
    entry_type: Literal["DEBIT", "CREDIT"]
    amount: float = Field(..., gt=0, description="Positive transaction amount")


class JournalEntryCreate(BaseModel):
    external_reference: str = Field(..., description="Unique operation identifier")
    description: str | None = Field(default=None)
    entry_date: str | None = Field(default=None)
    lines: list[JournalEntryLineSchema] = Field(..., min_length=2, description="Debit and Credit lines")


class JournalEntryResponse(BaseModel):
    id: int
    external_reference: str
    description: str | None
    entry_date: str
    lines: list[JournalEntryLineSchema]
    total_debits: float
    total_credits: float
