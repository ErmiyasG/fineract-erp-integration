"""
SQLAlchemy models for Mock ERP: Accounts and Journal Entries.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db import Base


class Account(Base):
    __tablename__ = "accounts"

    code = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=False)
    type = Column(String(20), nullable=False)  # ASSET, LIABILITY, EQUITY, INCOME, EXPENSE
    currency = Column(String(3), nullable=False, default="RWF")
    external_reference = Column(String(100), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    lines = relationship("JournalEntryLine", back_populates="account")


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_reference = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    entry_date = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    lines = relationship(
        "JournalEntryLine", back_populates="journal_entry", cascade="all, delete-orphan"
    )


class JournalEntryLine(Base):
    __tablename__ = "journal_entry_lines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    journal_entry_id = Column(
        Integer, ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False
    )
    account_code = Column(String(50), ForeignKey("accounts.code"), nullable=False)
    entry_type = Column(String(10), nullable=False)  # DEBIT or CREDIT
    amount = Column(Float, nullable=False)

    journal_entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account", back_populates="lines")
