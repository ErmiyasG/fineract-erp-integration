"""
Unit and Integration Tests for Client Management & Identities (Part C & E)
Validates:
1. Client creation with business client_uid (e.g. SME-000001) mapped to Fineract externalId
2. Lookup by client_uid validates existence, active status, and name
3. Zero leakage of Fineract internal numeric IDs
4. Database-enforced idempotency on external_reference
5. 409 Conflict when client_uid is reused with different external_reference
6. Correlation ID propagated in headers
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.db import Base
from app.services.client_service import client_service
from app.services.fineract_client import FineractClient

TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def test_client():
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def mock_fineract():
    mock = AsyncMock(spec=FineractClient)
    # Default successful responses
    mock.create_client.return_value = {"clientId": 101, "resourceId": 101}
    mock.get_client_by_external_id.return_value = {
        "id": 101,
        "firstname": "Aline",
        "lastname": "Mugabo",
        "externalId": "SME-000001",
        "status": {"value": "Active"},
    }
    client_service.fineract_client = mock
    yield mock


def test_create_client_and_get_by_uid(test_client, mock_fineract):
    # 1. Create client
    create_payload = {
        "external_reference": "REF-CLIENT-001",
        "client_uid": "SME-000001",
        "first_name": "Aline",
        "last_name": "Mugabo",
        "mobile_no": "+250788112233",
        "office_id": 1,
    }
    res = test_client.post("/api/clients", json=create_payload, headers={"X-Correlation-ID": "test-corr-123"})
    assert res.status_code == 201
    assert res.headers["X-Correlation-ID"] == "test-corr-123"
    data = res.json()

    # Invariant: No Fineract internal ID leaked
    assert "id" not in data
    assert "fineract_client_id" not in data
    assert data["client_uid"] == "SME-000001"
    assert data["external_id"] == "SME-000001"
    assert data["first_name"] == "Aline"
    assert data["last_name"] == "Mugabo"
    assert data["status"] == "ACTIVE"

    # Verify Fineract called with client_uid as externalId
    mock_fineract.create_client.assert_awaited_once_with(
        client_uid="SME-000001",
        first_name="Aline",
        last_name="Mugabo",
        office_id=1,
        correlation_id="test-corr-123",
    )

    # 2. Find by client_uid -> validate exists, active, correct name
    res_get = test_client.get("/api/clients/SME-000001")
    assert res_get.status_code == 200
    get_data = res_get.json()
    assert get_data["client_uid"] == "SME-000001"
    assert get_data["first_name"] == "Aline"
    assert get_data["status"] == "ACTIVE"
    assert "fineract_client_id" not in get_data


def test_client_idempotency_same_payload(test_client, mock_fineract):
    payload = {
        "external_reference": "REF-CLIENT-002",
        "client_uid": "SME-000002",
        "first_name": "Jean",
        "last_name": "Hakizimana",
        "office_id": 1,
    }
    res1 = test_client.post("/api/clients", json=payload)
    assert res1.status_code == 201

    # Resend identical payload with same external_reference -> 200 OK
    res2 = test_client.post("/api/clients", json=payload)
    assert res2.status_code == 200
    assert res2.json()["client_uid"] == "SME-000002"

    # Fineract should only have been called ONCE
    assert mock_fineract.create_client.await_count == 1


def test_client_conflict_same_reference_different_payload(test_client, mock_fineract):
    payload = {
        "external_reference": "REF-CLIENT-003",
        "client_uid": "SME-000003",
        "first_name": "Eric",
        "last_name": "Gasana",
        "office_id": 1,
    }
    res1 = test_client.post("/api/clients", json=payload)
    assert res1.status_code == 201

    # Conflict: same reference, different client_uid
    conflict_payload = dict(payload, client_uid="SME-DIFFERENT")
    res2 = test_client.post("/api/clients", json=conflict_payload)
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "CLIENT_CONFLICT"


def test_client_uid_uniqueness_different_reference(test_client, mock_fineract):
    # Create SME-000004 under REF-1
    res1 = test_client.post(
        "/api/clients",
        json={
            "external_reference": "REF-1",
            "client_uid": "SME-000004",
            "first_name": "Paul",
            "last_name": "Kagabo",
            "office_id": 1,
        },
    )
    assert res1.status_code == 201

    # Attempt to reuse SME-000004 with a different reference REF-2
    res2 = test_client.post(
        "/api/clients",
        json={
            "external_reference": "REF-2",
            "client_uid": "SME-000004",
            "first_name": "Paul",
            "last_name": "Kagabo",
            "office_id": 1,
        },
    )
    assert res2.status_code == 409
    assert "already assigned" in res2.json()["error"]["message"]


def test_client_not_found(test_client, mock_fineract):
    mock_fineract.get_client_by_external_id.return_value = None
    res = test_client.get("/api/clients/SME-NONEXISTENT")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "CLIENT_NOT_FOUND"
