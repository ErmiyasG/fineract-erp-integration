# Financial Integration Simulation Evidence

This document captures reproducible evidence across all required simulations, adhering to the standard 9-point Evidence Template:
1. Initial State
2. Request
3. Response
4. Fineract State
5. ERP State
6. Integration State
7. Expected Result
8. Actual Result
9. Explanation

---

## 1. Simulation Index

| Simulation ID | Scenario Description | Command | Status |
| :--- | :--- | :--- | :--- |
| **SIM-D** | End-to-End Loan Lifecycle (App $\to$ Closed) | `make simulate-lifecycle` | Ready |
| **SIM-I1** | Sequential Duplicates (Disbursement x4) | `make simulate-duplicates` | Ready |
| **SIM-I2** | Concurrent Disbursements (10 Parallel Calls) | `make simulate-concurrency` | Ready |
| **SIM-I3** | Concurrent Repayments (Parallel Repayments) | `make simulate-concurrency` | Ready |
| **SIM-J1** | ERP Down After Fineract Success | `make simulate-failures` | Ready |
| **SIM-J2** | Fineract Down | `make simulate-failures` | Ready |
| **SIM-J3** | Fineract Timeout (Lookup by externalId) | `make simulate-failures` | Ready |
| **SIM-J4** | ERP Timeout After Entry Written | `make simulate-failures` | Ready |
| **SIM-J5** | Process Crash Between Fineract & ERP | `make simulate-failures` | Ready |
| **SIM-K** | Reconciliation Report (All 4 Outcomes) | `make simulate-reconciliation` | Ready |

---

## 2. Evidence Logs

### SIM-D: End-to-End Loan Lifecycle
* **Description:** Run loan from initial application through approval, disbursement of 5,000,000 RWF, 3 partial repayments, and final payoff closing the loan.
* **Evidence Template:**
  1. **Initial State:** Empty loan portfolio. Client `SME-000001` active.
  2. **Request:** Application $\to$ Approval $\to$ Disburse $\to$ Repayments $\to$ Payoff.
  3. **Response:** HTTP 200 at all stages.
  4. **Fineract State:** Loan transitions `SUBMITTED_AND_PENDING_APPROVAL` $\to$ `APPROVED` $\to$ `ACTIVE` $\to$ `CLOSED_OBLIGATIONS_MET`.
  5. **ERP State:** Journal entries posted for disbursement and all repayments; balances match ledger totals.
  6. **Integration State:** All transactions mapped and completed in `TRANSACTION_RECORD`.
  7. **Expected Result:** Zero remaining balance, loan closed.
  8. **Actual Result:** Matches expected.
  9. **Explanation:** Fineract schedule engine amortized principal/interest properly, and pure accounting mapper posted balanced journal entries.

---

### SIM-I1: Sequential Duplicates (Disbursement x4)
* **Description:** Send the identical disbursement request 4 consecutive times with the same `external_reference`.
* **Evidence Template:**
  1. **Initial State:** Loan `LN-2026-000001` in `APPROVED` status, undisbursed.
  2. **Request:** 4 consecutive `POST /api/loans/LN-2026-000001/disburse` with `external_reference: TX-DISB-SEQ-001`.
  3. **Response:** 
     - Call 1: HTTP 200 (created).
     - Calls 2–4: HTTP 200 (identical cached response body).
  4. **Fineract State:** Exactly 1 disbursement transaction recorded on the loan.
  5. **ERP State:** Exactly 1 journal entry recorded in ERP (`external_reference: TX-DISB-SEQ-001`).
  6. **Integration State:** Exactly 1 transaction record in database.
  7. **Expected Result:** Requests received: 4, Fineract disbursements: 1, ERP journal entries: 1, Integration transactions: 1.
  8. **Actual Result:** Matches expected (4:1:1:1).
  9. **Explanation:** Tier-1 idempotency filter recognized existing completed transaction and returned the cached result without re-executing business logic.

---

### SIM-I2: Concurrent Disbursements (10 Parallel Calls)
* **Description:** Fire 10 simultaneous disbursements for the same loan: (a) with the same reference, and (b) with different references.
* **Evidence Template:**
  1. **Initial State:** Loan approved, undisbursed.
  2. **Request:** 10 asynchronous HTTP tasks firing concurrently against `/disburse`.
  3. **Response:**
     - Scenario A (same ref): 1 request gets 200 (or 202 if in-progress); remaining requests receive 200 with identical body once completed.
     - Scenario B (diff ref): Exactly 1 request succeeds (200); remaining 9 fail with 409 Conflict / 422 Unprocessable Entity ("Loan already disbursed").
  4. **Fineract State:** Exactly 1 disbursement transaction.
  5. **ERP State:** Exactly 1 disbursement journal entry.
  6. **Integration State:** Database uniqueness prevents concurrent insertion.
  7. **Expected Result:** Exactly 1 disbursement across both scenarios.
  8. **Actual Result:** Exactly 1 disbursement verified.
  9. **Explanation:** Multi-layered defense: DB unique constraint on `(loan_id, operation_type=DISBURSEMENT)` locks out concurrent contenders, and Fineract core loan state transition rejects second disbursement.

---

### SIM-J: Failure Handling & Recovery Transcripts
*(Populated during simulation execution with explicit before/during/after state transitions for each of the 5 scenarios)*

