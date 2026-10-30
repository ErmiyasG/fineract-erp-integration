#!/usr/bin/env python3
"""
Scripted Demonstration of Part A: GL Synchronization Behaviors 1 to 4
Demonstrates:
  Behavior 1: ERP external_reference is persisted and unique (HTTP 200/201)
  Behavior 2: Creating GL account with existing reference & identical payload returns existing account (HTTP 200, no duplicate)
  Behavior 3: Same reference with different payload rejected with conflict (HTTP 409)
  Behavior 4: Endpoint protected by Fineract permission model (HTTP 401/403)
"""
import uuid

import httpx

FINERACT_BASE_URL = "https://localhost:8443/fineract-provider/api/v1"
INTEGRATION_URL = "http://localhost:8000"


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f" {title}")
    print("=" * 70)


def demo_direct_fineract_sync():
    print_header("DEMONSTRATION: DIRECT FINERACT JAVA GL SYNC (BEHAVIORS 1 TO 4)")

    external_ref = f"GL-{uuid.uuid4().hex[:8].upper()}"
    gl_code = f"12{uuid.uuid4().hex[:4].upper()}"

    payload_initial = {
        "name": "Loan Receivable SME",
        "glCode": gl_code,
        "type": 1,  # ASSET
        "usage": 1,  # DETAIL
        "manualEntriesAllowed": True,
        "description": "Asset account synced from ERP",
        "external_reference": external_ref,
    }

    # Headers with admin auth
    headers = {
        "Fineract-Platform-TenantId": "default",
        "Authorization": "Basic bWlmb3M6cGFzc3dvcmQ=",  # mifos:password
        "Content-Type": "application/json",
    }

    with httpx.Client(verify=False, timeout=15.0) as client:
        # -------------------------------------------------------------
        # Behavior 1: New Reference -> Process Once & Persist
        # -------------------------------------------------------------
        print("\n--> Behavior 1: Creating GL Account with new external_reference...")
        print(f"    Payload: external_reference='{external_ref}', code='{gl_code}', name='{payload_initial['name']}'")
        try:
            res1 = client.post(f"{FINERACT_BASE_URL}/glaccounts", json=payload_initial, headers=headers)
            print(f"    [Behavior 1 Response] HTTP {res1.status_code}: {res1.text}")
            account_id_1 = res1.json().get("resourceId")
            print(f"    Account Created successfully with ID: {account_id_1}")
        except (httpx.HTTPError, ValueError) as e:
            print(f"    [Notice] Live Fineract not running locally yet ({e}). Demonstrating contract via Integration Service mock/API...")
            demo_integration_service_sync()
            return 

        # -------------------------------------------------------------
        # Behavior 2: Same Reference, Identical Payload -> Return Existing (No Duplicate)
        # -------------------------------------------------------------
        print("\n--> Behavior 2: Sending identical request with same external_reference...")
        res2 = client.post(f"{FINERACT_BASE_URL}/glaccounts", json=payload_initial, headers=headers)
        print(f"    [Behavior 2 Response] HTTP {res2.status_code}: {res2.text}")
        account_id_2 = res2.json().get("resourceId")
        assert account_id_1 == account_id_2, "Must return existing account ID without creating duplicate"
        print(f"    Verified: returned existing ID {account_id_2} (no duplicate created).")

        # -------------------------------------------------------------
        # Behavior 3: Same Reference, Different Payload -> Reject with 409 Conflict
        # -------------------------------------------------------------
        print("\n--> Behavior 3: Sending request with same external_reference but conflicting payload...")
        payload_conflicting = dict(payload_initial)
        payload_conflicting["name"] = "Conflicting Account Name"
        payload_conflicting["glCode"] = f"CONFLICT-{uuid.uuid4().hex[:4]}"

        res3 = client.post(f"{FINERACT_BASE_URL}/glaccounts", json=payload_conflicting, headers=headers)
        print(f"    [Behavior 3 Response] HTTP {res3.status_code}: {res3.text}")
        assert res3.status_code == 409, f"Expected HTTP 409 Conflict, received {res3.status_code}"
        print("    Verified: Conflicting payload rejected with HTTP 409 Conflict.")

        # -------------------------------------------------------------
        # Behavior 4: Protected by Fineract Permission Model
        # -------------------------------------------------------------
        print("\n--> Behavior 4: Requesting endpoint without credentials...")
        unauth_headers = {"Fineract-Platform-TenantId": "default", "Content-Type": "application/json"}
        res4 = client.post(f"{FINERACT_BASE_URL}/glaccounts", json=payload_initial, headers=unauth_headers)
        print(f"    [Behavior 4 Response] HTTP {res4.status_code}: {res4.text}")
        assert res4.status_code in (401, 403), f"Expected HTTP 401/403, received {res4.status_code}"
        print("    Verified: Protected by Fineract security and permission checks.")


def demo_integration_service_sync():
    print_header("DEMONSTRATION: INTEGRATION SERVICE GL SYNC ENDPOINT (POST /api/accounts/sync)")

    external_ref = f"GL-ERP-{uuid.uuid4().hex[:6].upper()}"
    code = f"12{uuid.uuid4().hex[:4].upper()}"

    payload = {
        "external_reference": external_ref,
        "code": code,
        "name": "Loan Receivable SME",
        "type": "ASSET",
        "currency": "RWF",
        "description": "SME Loan Receivable Asset Account",
    }

    with httpx.Client(base_url=INTEGRATION_URL, timeout=10.0) as client:
        print("\n--> 1. Syncing new account from ERP...")
        res = client.post("/api/accounts/sync", json=payload)
        print(f"    HTTP {res.status_code}: {res.json()}")

        print("\n--> 2. Repeated sync 4 consecutive times (idempotency test)...")
        for i in range(1, 5):
            r = client.post("/api/accounts/sync", json=payload)
            print(f"    Call {i}: HTTP {r.status_code} - Status: {r.json().get('status')}")

        print("\n--> 3. Attempting sync with same reference but conflicting payload...")
        bad_payload = dict(payload)
        bad_payload["code"] = "9999"
        bad_payload["name"] = "Conflicting Account"
        r_conflict = client.post("/api/accounts/sync", json=bad_payload)
        print(f"    HTTP {r_conflict.status_code}: {r_conflict.json()}")


if __name__ == "__main__":
    demo_direct_fineract_sync()

