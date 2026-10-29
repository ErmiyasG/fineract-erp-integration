"""
Unit and Integration Tests for GL Account Synchronization (Part A)
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import get_db
from app.main import app
from app.models.db import AccountSyncMapping, Base
from app.models.schemas import AccountSyncRequest
from app.services.accounting import map_erp_type_to_fineract
from app.services.fineract_client import (
    FineractClient,
    FineractConnectionException,
)
from app.services.gl_sync_service import GLSyncConflictError, GLSyncService

# Test database setup (in-memory SQLite)
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_account_type_mapping():
    """
    Map ERP account types to Fineract GL types explicitly, in one tested place.
    ASSET -> 1
    LIABILITY -> 2
    EQUITY -> 3
    INCOME -> 4
    EXPENSE -> 5
    """
    assert map_erp_type_to_fineract("ASSET") == 1
    assert map_erp_type_to_fineract("LIABILITY") == 2
    assert map_erp_type_to_fineract("EQUITY") == 3
    assert map_erp_type_to_fineract("INCOME") == 4
    assert map_erp_type_to_fineract("EXPENSE") == 5

    # Case insensitivity & whitespace trimming
    assert map_erp_type_to_fineract(" asset ") == 1
    assert map_erp_type_to_fineract("income") == 4

    # Invalid types
    with pytest.raises(ValueError):
        map_erp_type_to_fineract("UNKNOWN_TYPE")


@pytest.mark.asyncio
async def test_gl_sync_new_account_success(db_session):
    """
    Behavior 1: The ERP external_reference is persisted and synchronized.
    """
    mock_fineract = AsyncMock(spec=FineractClient)
    mock_fineract.create_gl_account.return_value = {"resourceId": 42}

    service = GLSyncService(fineract_client=mock_fineract)

    request = AccountSyncRequest(
        external_reference="GL-1200",
        code="1200",
        name="Loan Receivable",
        type="ASSET",
        currency="RWF",
    )

    response = await service.sync_account(db_session, request)

    assert response.status == "SYNCED"
    assert response.fineract_gl_id == 42
    assert response.external_reference == "GL-1200"

    # Verify DB persistence
    mapping = db_session.query(AccountSyncMapping).filter_by(external_reference="GL-1200").first()
    assert mapping is not None
    assert mapping.fineract_id == 42
    assert mapping.sync_status == "SYNCED"
    assert mapping.code == "1200"


@pytest.mark.asyncio
async def test_gl_sync_sending_four_times_yields_one_mapping(db_session):
    """
    Assessment Requirement:
    'Sending the same account 4 times must yield one ERP account, one Fineract GL account, one mapping.'
    """
    mock_fineract = AsyncMock(spec=FineractClient)
    mock_fineract.create_gl_account.return_value = {"resourceId": 55}

    service = GLSyncService(fineract_client=mock_fineract)

    request = AccountSyncRequest(
        external_reference="GL-1100",
        code="1100",
        name="Bank Main Account",
        type="ASSET",
        currency="RWF",
    )

    responses = []
    for _ in range(4):
        resp = await service.sync_account(db_session, request)
        responses.append(resp)

    # 1. Fineract API must be called only once (subsequent calls return cached result)
    assert mock_fineract.create_gl_account.call_count == 1

    # 2. All 4 responses must report SYNCED with the same Fineract ID
    for resp in responses:
        assert resp.status == "SYNCED"
        assert resp.fineract_gl_id == 55
        assert resp.code == "1100"

    # 3. Database must contain exactly one mapping record
    all_mappings = db_session.query(AccountSyncMapping).filter_by(external_reference="GL-1100").all()
    assert len(all_mappings) == 1
    assert all_mappings[0].code == "1100"
    assert all_mappings[0].fineract_id == 55


@pytest.mark.asyncio
async def test_gl_sync_conflict_mismatched_payload(db_session):
    """
    Behavior 3: Same reference with different payload is rejected with a conflict (409).
    """
    mock_fineract = AsyncMock(spec=FineractClient)
    mock_fineract.create_gl_account.return_value = {"resourceId": 77}

    service = GLSyncService(fineract_client=mock_fineract)

    initial_request = AccountSyncRequest(
        external_reference="GL-4000",
        code="4000",
        name="Interest Income",
        type="INCOME",
        currency="RWF",
    )
    await service.sync_account(db_session, initial_request)

    # Conflicting payload with different code
    conflicting_request = AccountSyncRequest(
        external_reference="GL-4000",
        code="4001",
        name="Different Account",
        type="INCOME",
        currency="RWF",
    )

    with pytest.raises(GLSyncConflictError) as exc_info:
        await service.sync_account(db_session, conflicting_request)

    assert "Conflict for GL reference 'GL-4000'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gl_sync_fineract_down_retry_recovery(db_session):
    """
    Assessment Requirement:
    'Sync status per account: SYNC_PENDING -> SYNCED (or FAILED), with retry if Fineract is down.'
    """
    mock_fineract = AsyncMock(spec=FineractClient)
    # First attempt: Fineract connection failure
    mock_fineract.create_gl_account.side_effect = FineractConnectionException("Connection refused")

    service = GLSyncService(fineract_client=mock_fineract)

    request = AccountSyncRequest(
        external_reference="GL-5000",
        code="5000",
        name="Operating Expense",
        type="EXPENSE",
        currency="RWF",
    )

    # Call 1: Fails due to Fineract being down
    resp1 = await service.sync_account(db_session, request)
    assert resp1.status == "FAILED"
    assert resp1.fineract_gl_id is None

    mapping = db_session.query(AccountSyncMapping).filter_by(external_reference="GL-5000").first()
    assert mapping.sync_status == "FAILED"
    assert mapping.retry_count == 0

    # Call 2: Fineract is now back up
    mock_fineract.create_gl_account.side_effect = None
    mock_fineract.create_gl_account.return_value = {"resourceId": 99}

    resp2 = await service.sync_account(db_session, request)
    assert resp2.status == "SYNCED"
    assert resp2.fineract_gl_id == 99

    db_session.refresh(mapping)
    assert mapping.sync_status == "SYNCED"
    assert mapping.fineract_id == 99
    assert mapping.retry_count == 1


def test_api_sync_endpoint_behavior():
    """
    Test the FastAPI REST endpoint POST /api/accounts/sync using TestClient
    """
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Mock the gl_sync_service in the API router
    from unittest.mock import patch

    with patch("app.api.accounts.gl_sync_service.sync_account") as mock_sync:
        from app.models.schemas import AccountSyncResponse
        mock_sync.return_value = AccountSyncResponse(
            external_reference="GL-1200",
            code="1200",
            name="Loan Receivable",
            type="ASSET",
            fineract_gl_id=12,
            status="SYNCED",
            retry_count=0,
            message="GL Account successfully synchronized",
        )

        response = client.post(
            "/api/accounts/sync",
            json={
                "external_reference": "GL-1200",
                "code": "1200",
                "name": "Loan Receivable",
                "type": "ASSET",
                "currency": "RWF",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["external_reference"] == "GL-1200"
        assert data["fineract_gl_id"] == 12
        assert data["status"] == "SYNCED"

    app.dependency_overrides.clear()
