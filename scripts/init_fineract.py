#!/usr/bin/env python3
"""
Initialize Apache Fineract Environment (Part B)
Automates configuration of a fresh Fineract instance:
1. Configure Currency: RWF (decimal places: 0)
2. Configure Office: Kigali Main Office
3. Synchronize GL Accounts: 1100 (Bank), 1200 (Loan Receivable), 4000 (Interest Income),
   4100 (Fee Income), 4200 (Penalty Income), 5000 (Loss Expense)
4. Configure Loan Product: 'SME Asset Finance' (18% annual, 12 months, cash-based accounting)
5. Provision Least-Privilege API Role and User
"""
import base64
import os
import sys
import time
from typing import Any

import httpx

FINERACT_BASE_URL = os.getenv("FINERACT_BASE_URL", "https://localhost:8443/fineract-provider/api/v1").rstrip("/")
TENANT_ID = os.getenv("FINERACT_TENANT_ID", "default")
ADMIN_USER = os.getenv("FINERACT_ADMIN_USER", "mifos")
ADMIN_PASSWORD = os.getenv("FINERACT_ADMIN_PASSWORD", "password")
INSECURE_SSL = os.getenv("FINERACT_INSECURE_SSL", "true").lower() in ("true", "1", "yes")

API_USER_NAME = os.getenv("FINERACT_API_USER", "fintech_api_user")
API_USER_PASS = os.getenv("FINERACT_API_PASSWORD", "ApiUserSecurePassword123!")


class FineractAdminClient:
    def __init__(self, base_url: str = FINERACT_BASE_URL, tenant_id: str = TENANT_ID, user: str = ADMIN_USER, password: str = ADMIN_PASSWORD):
        self.base_url = base_url
        self.tenant_id = tenant_id
        self.user = user
        self.password = password
        self.client = httpx.Client(verify=not INSECURE_SSL, timeout=20.0)

    def _headers(self) -> dict[str, str]:
        auth_bytes = f"{self.user}:{self.password}".encode()
        auth_b64 = base64.b64encode(auth_bytes).decode("utf-8")
        return {
            "Content-Type": "application/json",
            "Fineract-Platform-TenantId": self.tenant_id,
            "Authorization": f"Basic {auth_b64}",
        }

    def get(self, endpoint: str) -> httpx.Response:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        return self.client.get(url, headers=self._headers())

    def post(self, endpoint: str, json: dict[str, Any]) -> httpx.Response:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        return self.client.post(url, json=json, headers=self._headers())

    def put(self, endpoint: str, json: dict[str, Any]) -> httpx.Response:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        return self.client.put(url, json=json, headers=self._headers())


def wait_for_fineract(client: FineractAdminClient, max_retries: int = 30, delay: int = 3) -> bool:
    print(f"Checking Fineract availability at {client.base_url}...")
    for attempt in range(1, max_retries + 1):
        try:
            res = client.get("/offices")
            if res.status_code == 200:
                print("Connected to Apache Fineract.")
                return True
        except (httpx.HTTPError, ConnectionError, OSError) as e:
            print(f"Waiting for Fineract to become ready (attempt {attempt}/{max_retries}): {e}")
        time.sleep(delay)
    return False


def configure_currency(client: FineractAdminClient) -> None:
    print("Step 1: Configuring Currency (RWF)...")
    res = client.get("/currencies")
    if res.status_code == 200:
        data = res.json()
        selected = [c.get("code") for c in data.get("selectedCurrencyOptions", [])]
        if "RWF" in selected:
            print("  Currency 'RWF' already configured.")
            return

    # Add RWF to selected currencies
    update_payload = {"currencies": ["RWF", "USD"]}
    put_res = client.put("/currencies", json=update_payload)
    if put_res.status_code in (200, 201):
        print("  Successfully configured 'RWF' currency.")
    else:
        print(f"  Warning: Currencies PUT returned {put_res.status_code}: {put_res.text}")


def configure_office(client: FineractAdminClient) -> int:
    print("Step 2: Configuring Office: 'Kigali Main Office'...")
    res = client.get("/offices")
    if res.status_code == 200:
        offices = res.json()
        for off in offices:
            if off.get("name") == "Kigali Main Office":
                office_id = off.get("id")
                print(f"  Office 'Kigali Main Office' exists (ID: {office_id}).")
                return office_id

    # Create Kigali Main Office
    office_payload = {
        "name": "Kigali Main Office",
        "parentId": 1,
        "openingDate": "01 January 2026",
        "dateFormat": "dd MMMM yyyy",
        "locale": "en",
    }
    create_res = client.post("/offices", json=office_payload)
    if create_res.status_code in (200, 201):
        office_id = create_res.json().get("resourceId")
        print(f"  Created Office 'Kigali Main Office' (ID: {office_id}).")
        return office_id
    else:
        print(f"  Failed to create office: {create_res.status_code} {create_res.text}")
        return 1


def configure_gl_accounts(client: FineractAdminClient) -> dict[str, int]:
    print("Step 3: Configuring/Syncing GL Accounts...")
    # Map: code -> (name, type, external_reference)
    # Types: 1=ASSET, 2=LIABILITY, 3=EQUITY, 4=INCOME, 5=EXPENSE
    gl_accounts_to_seed = {
        "1100": ("Bank Main Account", 1, "GL-1100"),
        "1200": ("Loan Receivable", 1, "GL-1200"),
        "4000": ("Interest Income", 4, "GL-4000"),
        "4100": ("Fee Income", 4, "GL-4100"),
        "4200": ("Penalty Income", 4, "GL-4200"),
        "5000": ("Loan Loss Expense", 5, "GL-5000"),
    }

    # Fetch existing
    res = client.get("/glaccounts")
    existing_by_code: dict[str, int] = {}
    if res.status_code == 200:
        for acc in res.json():
            gl_code = acc.get("glCode")
            gl_id = acc.get("id")
            if gl_code and gl_id:
                existing_by_code[str(gl_code)] = gl_id

    account_ids: dict[str, int] = {}
    for code, (name, acc_type, ext_ref) in gl_accounts_to_seed.items():
        if code in existing_by_code:
            account_ids[code] = existing_by_code[code]
            print(f"  GL Account {code} ('{name}') exists (ID: {existing_by_code[code]}).")
            continue

        payload = {
            "name": name,
            "glCode": code,
            "type": acc_type,
            "usage": 1,  # Detail
            "manualEntriesAllowed": True,
            "description": f"ERP-synced account {code}",
            "external_reference": ext_ref,
            "externalReference": ext_ref,
        }
        res_create = client.post("/glaccounts", json=payload)
        if res_create.status_code in (200, 201):
            acc_id = res_create.json().get("resourceId")
            account_ids[code] = acc_id
            print(f"  Created GL Account {code} ('{name}') (ID: {acc_id}).")
        else:
            print(f"  Error creating GL {code}: {res_create.status_code} {res_create.text}")

    return account_ids


def configure_loan_product(client: FineractAdminClient, gl_map: dict[str, int]) -> int:
    print("Step 4: Configuring Loan Product 'SME Asset Finance'...")
    res = client.get("/loanproducts")
    if res.status_code == 200:
        for prod in res.json():
            if prod.get("name") == "SME Asset Finance":
                prod_id = prod.get("id")
                print(f"  Loan product 'SME Asset Finance' exists (ID: {prod_id}).")
                return prod_id

    # Fallback to available GL account IDs
    bank_id = gl_map.get("1100", 1)
    rec_id = gl_map.get("1200", 2)
    interest_id = gl_map.get("4000", 3)
    fee_id = gl_map.get("4100", interest_id)
    penalty_id = gl_map.get("4200", interest_id)
    loss_id = gl_map.get("5000", rec_id)

    payload = {
        "name": "SME Asset Finance",
        "shortName": "SME",
        "currencyCode": "RWF",
        "digitsAfterDecimal": 0,
        "inMultiplesOf": 1,
        "principal": 5000000,
        "minPrincipal": 100000,
        "maxPrincipal": 50000000,
        "numberOfRepayments": 12,
        "minNumberOfRepayments": 1,
        "maxNumberOfRepayments": 60,
        "repaymentEvery": 1,
        "repaymentFrequencyType": 2,  # Months
        "interestRatePerPeriod": 1.5,  # 18% nominal annual / 12 months = 1.5% per month
        "interestRateFrequencyType": 2,  # Per Month
        "annualInterestRate": 18.0,
        "amortizationType": 1,  # Equal installments
        "interestType": 0,  # Declining Balance
        "interestCalculationPeriodType": 1,  # Same as repayment period
        "transactionProcessingStrategyCode": "mifos-standard-strategy",
        "daysInMonthType": 1,
        "daysInYearType": 1,
        "isInterestRecalculationEnabled": False,
        "accountingRule": 2,  # 2 = Cash-based
        "fundSourceAccountId": bank_id,
        "loanPortfolioAccountId": rec_id,
        "interestOnLoanAccountId": interest_id,
        "incomeFromFeeAccountId": fee_id,
        "incomeFromPenaltyAccountId": penalty_id,
        "writeOffAccountId": loss_id,
        "overpaymentAccountId": bank_id,
        "transfersInSuspenseAccountId": bank_id,
        "multiDisburseLoan": False,
        "locale": "en",
        "dateFormat": "dd MMMM yyyy",
    }

    create_res = client.post("/loanproducts", json=payload)
    if create_res.status_code in (200, 201):
        prod_id = create_res.json().get("resourceId")
        print(f"  Created Loan Product 'SME Asset Finance' (ID: {prod_id}).")
        return prod_id
    else:
        print(f"  Failed to create loan product: {create_res.status_code} {create_res.text}")
        return 1


def configure_least_privilege_user(client: FineractAdminClient, office_id: int) -> None:
    print(f"Step 5: Provisioning Least-Privilege API Role and User '{API_USER_NAME}'...")
    role_name = "FintechIntegrationRole"
    role_id = None

    # Check roles
    res_roles = client.get("/roles")
    if res_roles.status_code == 200:
        for r in res_roles.json():
            if r.get("name") == role_name:
                role_id = r.get("id")
                print(f"  Role '{role_name}' exists (ID: {role_id}).")
                break

    if not role_id:
        res_create_role = client.post("/roles", json={"name": role_name, "description": "Least-privilege role for ERP integration"})
        if res_create_role.status_code in (200, 201):
            role_id = res_create_role.json().get("resourceId")
            print(f"  Created Role '{role_name}' (ID: {role_id}).")

    if role_id:
        # Assign required least-privilege permissions
        permissions = {
            "permissions": {
                "READ_OFFICE": True,
                "READ_CURRENCY": True,
                "READ_GLACCOUNT": True,
                "CREATE_GLACCOUNT": True,
                "READ_LOANPRODUCT": True,
                "READ_CLIENT": True,
                "CREATE_CLIENT": True,
                "ACTIVATE_CLIENT": True,
                "READ_LOAN": True,
                "CREATE_LOAN": True,
                "APPROVE_LOAN": True,
                "DISBURSE_LOAN": True,
                "REPAYMENT_LOAN": True,
                "READ_JOURNALENTRY": True,
            }
        }
        client.put(f"/roles/{role_id}/permissions", json=permissions)
        print("  Permissions assigned to role.")

    # Check/Create User
    res_users = client.get("/users")
    user_exists = False
    if res_users.status_code == 200:
        for u in res_users.json():
            if u.get("username") == API_USER_NAME:
                user_exists = True
                print(f"  User '{API_USER_NAME}' already exists.")
                break

    if not user_exists and role_id:
        user_payload = {
            "username": API_USER_NAME,
            "firstname": "Fintech",
            "lastname": "Integration",
            "email": "fintech-integration@example.com",
            "officeId": office_id,
            "roles": [role_id],
            "sendPasswordToEmail": False,
            "password": API_USER_PASS,
            "repeatPassword": API_USER_PASS,
        }
        res_user = client.post("/users", json=user_payload)
        if res_user.status_code in (200, 201):
            print(f"  Created user '{API_USER_NAME}'.")
        else:
            print(f"  Warning creating user: {res_user.status_code} {res_user.text}")


def main() -> None:
    print("=" * 60)
    print("Apache Fineract Environment Automated Setup (Part B)")
    print("=" * 60)
    admin_client = FineractAdminClient()
    if not wait_for_fineract(admin_client, max_retries=10, delay=2):
        print("Could not reach Fineract instance. Ensure services are running.")
        sys.exit(1)

    configure_currency(admin_client)
    office_id = configure_office(admin_client)
    gl_map = configure_gl_accounts(admin_client)
    configure_loan_product(admin_client, gl_map)
    configure_least_privilege_user(admin_client, office_id)
    print("=" * 60)
    print("Fineract Environment Initialization Finished Successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
