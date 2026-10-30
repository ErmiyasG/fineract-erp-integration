#!/usr/bin/env python3
"""
Seed Mock ERP Chart of Accounts
Accounts:
- 1100: Bank Account (Asset)
- 1200: Loan Receivable (Asset)
- 4000: Interest Income (Income)
"""
import sys

import httpx

MOCK_ERP_URL = "http://localhost:8001"

ACCOUNTS = [
    {
        "code": "1100",
        "name": "Bank Main Account",
        "type": "ASSET",
        "currency": "RWF",
        "external_reference": "GL-1100",
    },
    {
        "code": "1200",
        "name": "Loan Receivable",
        "type": "ASSET",
        "currency": "RWF",
        "external_reference": "GL-1200",
    },
    {
        "code": "4000",
        "name": "Interest Income",
        "type": "INCOME",
        "currency": "RWF",
        "external_reference": "GL-4000",
    },
]


def main() -> None:
    print(f"Connecting to Mock ERP at {MOCK_ERP_URL}...")
    try:
        with httpx.Client(base_url=MOCK_ERP_URL, timeout=10.0) as client:
            res = client.get("/health")
            if res.status_code != 200:
                print(f"Mock ERP is not healthy: {res.text}")
                sys.exit(1)
            print("Mock ERP is healthy. Seeding Chart of Accounts...")
            for acc in ACCOUNTS:
                resp = client.post("/api/accounts", json=acc)
                print(f"Account {acc['code']} ({acc['name']}): status {resp.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        print(f"Failed to connect to Mock ERP: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

