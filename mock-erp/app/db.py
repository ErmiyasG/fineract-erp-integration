"""
Database configuration and session management for Mock ERP.
Supports PostgreSQL in production/docker and SQLite fallback for local testing.
"""

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

DATABASE_URL = settings.DATABASE_URL
# In test environments or when sqlite is specified
if os.getenv("TESTING") == "1" or "sqlite" in DATABASE_URL:
    DATABASE_URL = "sqlite:///./mock_erp_test.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    try:
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    except Exception:
        # Fallback for environments where postgres driver is not yet ready
        engine = create_engine(
            "sqlite:///./mock_erp_local.db", connect_args={"check_same_thread": False}
        )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
