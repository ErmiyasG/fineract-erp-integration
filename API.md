# API Specification & Contracts

## 1. Global Standards

### 1.1 Headers
All requests and responses carry a tracking correlation ID:
* `X-Correlation-ID`: `string` (UUID v4). Auto-generated if omitted by caller.
* `Authorization`: `Bearer <API_KEY>` or API Key header.

### 1.2 Error Schema
All error responses adhere to a consistent structure:
```json
{
  "error": {
    "code": "CONFLICT_DETECTED",
    "message": "Resource already exists with conflicting payload",
    "details": {
      "field": "amount",
      "expected": 5000000,
      "received": 6000000
    },
    "correlation_id": "8f8b824a-7140-4bd2-97b4-25e22709d435",
    "timestamp": "2026-10-28T12:00:00Z"
  }
}
```

---

## 2. Idempotency Contract

Every state-changing endpoint requires an `external_reference` in the payload or via `Idempotency-Key` header:

| Case | Condition | HTTP Status | Response Body |
| :--- | :--- | :---: | :--- |
| **New reference** | Never seen before | `200 OK` / `201 Created` | Newly created resource result |
| **Same reference, identical payload, completed** | Previously finished successfully | `200 OK` | Original result cached from first execution |
| **Same reference, identical payload, in-progress** | Currently undergoing processing | `202 Accepted` | Current processing status; no duplicate execution |
| **Same reference, different payload** | Payload checksum differs from prior record | `409 Conflict` | Error indicating idempotent key reuse with different parameters |

### Uniqueness Scope per Operation
* **GL Account Sync:** Unique per `external_reference`, and `code` must be unique.
* **Client Creation:** Unique per `external_reference`. `client_uid` is unique at the database level.
* **Loan Application:** Unique per `external_reference`.
* **Disbursement:** **Unique per loan.** A loan can be disbursed only once. Multiple concurrent requests with different references fail with 409/422 once the loan is disbursed.
* **Repayment:** Unique per `external_reference`. Multiple repayments on the same loan are permitted.

---

## 3. Integration Service Endpoints

### 3.1 Synchronize GL Accounts
* **`POST /api/accounts/sync`**
* **Request:**
```json
{
  "external_reference": "GL-1200",
  "code": "1200",
  "name": "Loan Receivable",
  "type": "ASSET",
  "currency": "RWF"
}
```
* **Response (`200 OK` / `201 Created`):**
```json
{
  "external_reference": "GL-1200",
  "fineract_gl_id": 12,
  "status": "SYNCED",
  "synced_at": "2026-10-28T10:00:00Z"
}
```

---

### 3.2 Client Management
* **`POST /api/clients`**
```json
{
  "external_reference": "REQ-CLI-001",
  "client_uid": "SME-000001",
  "first_name": "Jean",
  "last_name": "Mugabo",
  "office_id": 1,
  "mobile_no": "+250780000001"
}
```
* **`GET /api/clients/{client_uid}`**
```json
{
  "client_uid": "SME-000001",
  "status": "ACTIVE",
  "first_name": "Jean",
  "last_name": "Mugabo",
  "created_at": "2026-10-28T10:05:00Z"
}
```

---

### 3.3 Loan Management
* **`POST /api/loans`** (Application & optional instant approval)
```json
{
  "external_reference": "REQ-LN-APP-001",
  "loan_reference": "LN-2026-000001",
  "client_uid": "SME-000001",
  "product_id": 1,
  "principal": 5000000,
  "term_months": 12,
  "interest_rate": 18.0,
  "auto_approve": true
}
```
* **`GET /api/loans/{loan_reference}`**
```json
{
  "loan_reference": "LN-2026-000001",
  "client_uid": "SME-000001",
  "status": "APPROVED",
  "principal": 5000000,
  "outstanding_balance": 5000000,
  "disbursed_at": null
}
```

---

### 3.4 Loan Disbursement
* **`POST /api/loans/{loan_reference}/disburse`**
```json
{
  "external_reference": "TX-DISB-001",
  "disbursement_date": "2026-10-28",
  "amount": 5000000
}
```
* **Response (`200 OK`):**
```json
{
  "external_reference": "TX-DISB-001",
  "loan_reference": "LN-2026-000001",
  "status": "COMPLETED",
  "fineract_transaction_id": 84,
  "erp_transaction_id": "JE-2026-0001",
  "disbursed_amount": 5000000
}
```

---

### 3.5 Loan Repayment
* **`POST /api/loans/{loan_reference}/repayments`**
```json
{
  "external_reference": "TX-REPAY-001",
  "transaction_date": "2026-11-28",
  "amount": 450000
}
```
* **Response (`200 OK`):**
```json
{
  "external_reference": "TX-REPAY-001",
  "loan_reference": "LN-2026-000001",
  "status": "COMPLETED",
  "fineract_transaction_id": 85,
  "breakdown": {
    "principal": 375000,
    "interest": 75000,
    "fee": 0,
    "penalty": 0
  },
  "erp_transaction_id": "JE-2026-0002",
  "remaining_balance": 4625000
}
```

---

### 3.6 Transaction Inquiry
* **`GET /api/transactions/{external_reference}`**
```json
{
  "external_reference": "TX-DISB-001",
  "operation_type": "DISBURSEMENT",
  "loan_reference": "LN-2026-000001",
  "state": "COMPLETED",
  "fineract_transaction_id": 84,
  "erp_transaction_id": "JE-2026-0001",
  "amount": 5000000,
  "created_at": "2026-10-28T10:10:00Z",
  "updated_at": "2026-10-28T10:10:02Z"
}
```

---

### 3.7 Reconciliation (Part K)

#### 3.7.1 Execute Reconciliation Report (Read-Only)
* **`POST /api/reconciliation`**
* **Headers:** `X-Correlation-ID: <uuid>`
* **Description:** Performs a read-only two-way comparison between Fineract loan transactions and Mock ERP journal entries. Zero state modifications.
* **Request:**
```json
{
  "from_date": "2026-10-01",
  "to_date": "2026-10-31",
  "references": ["TX-DISB-001", "TX-REPAY-002"]
}
```
*Note: All filter fields (`from_date`, `to_date`, `references`) are optional.*

* **Response (`200 OK`):**
```json
{
  "summary": {
    "total_records": 4,
    "matched": 1,
    "missing_in_erp": 1,
    "missing_in_fineract": 1,
    "amount_mismatch": 1
  },
  "items": [
    {
      "external_reference": "TX-MATCHED-001",
      "status": "MATCHED",
      "fineract_amount": 5000000.0,
      "erp_amount": 5000000.0,
      "discrepancy": 0.0,
      "details": "Confirmed in both systems with matching amounts."
    },
    {
      "external_reference": "TX-MISS-ERP-002",
      "status": "MISSING_IN_ERP",
      "fineract_amount": 2500000.0,
      "erp_amount": 0.0,
      "discrepancy": -2500000.0,
      "details": "Present in Fineract core but absent in Mock ERP ledger."
    },
    {
      "external_reference": "TX-MISS-FIN-003",
      "status": "MISSING_IN_FINERACT",
      "fineract_amount": 0.0,
      "erp_amount": 1000000.0,
      "discrepancy": 1000000.0,
      "details": "Present in Mock ERP ledger but absent in Fineract core."
    },
    {
      "external_reference": "TX-MISMATCH-004",
      "status": "AMOUNT_MISMATCH",
      "fineract_amount": 500000.0,
      "erp_amount": 450000.0,
      "discrepancy": -50000.0,
      "details": "Present in both systems but amounts differ beyond 0.01 tolerance."
    }
  ]
}
```

#### 3.7.2 Explicit Idempotent Repair
* **`POST /api/reconciliation/repair`**
* **Headers:** `X-Correlation-ID: <uuid>`
* **Description:** Explicit operator action to resolve `MISSING_IN_ERP` items. Dispatches unposted transactions to ERP idempotently without re-disbursing in Fineract.
* **Request:**
```json
{
  "external_references": [
    "TX-MISS-ERP-002"
  ]
}
```
* **Response (`200 OK`):**
```json
{
  "repaired": [
    "TX-MISS-ERP-002"
  ],
  "failed": []
}
```

---

## 4. Mock ERP Endpoints

* **`POST /api/accounts`**: Idempotently seed or create chart of accounts.
* **`GET /api/accounts`**: List all active master accounts.
* **`GET /api/accounts/{code}/balance`**: Get current balance (sum of debits - credits).
* **`POST /api/journal-entries`**: Create balanced journal entry ($DR == CR$).
* **`GET /api/journal-entries/{external_reference}`**: Fetch journal entry by reference.

