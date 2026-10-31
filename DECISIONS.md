# Architectural Decisions & Incident Walkthrough

This document records the architectural rationale, trade-offs, and failure recovery mechanics for the financial integration system.

---

## 1. Trade-offs and Rationale

### 1.1 Fineract Customization Mechanism
* **Options Evaluated:**
  1. *Fineract Extension Hook / Plugin:* Fineract plugin architecture is nascent and differs between versions.
  2. *Wrapper Service:* Expressly forbidden by assessment rules (does not fulfill Java skill requirement).
  3. *Core Modification in Spring Boot:* Extending the GL Account entity (`GLAccount`), Liquibase changelog, and command handler (`GLAccountWritePlatformServiceJpaRepositoryImpl`).
* **Decision:** Modify Fineract core via a cleanly isolated Liquibase migration and custom Spring Boot command handler extension.
* **Rationale:** Enforcing uniqueness and conflict detection (`HTTP 409`) directly inside Fineract's persistence layer ensures transactional integrity within Fineract's database boundary, making bypass impossible even if other components access Fineract directly.

### 1.2 Synchronous Orchestration vs. Asynchronous Message Broker
* **Options Evaluated:**
  1. *Message Broker (Kafka / RabbitMQ / Celery worker)*
  2. *Synchronous Ingress with Persisted Transaction State Machine & Startup/Periodic Recovery Sweeper*
* **Decision:** Synchronous Ingress with Persisted State Machine (PostgreSQL backed).
* **Rationale:** A queue introduces eventual consistency and complex two-phase commits between the queue and core banking. For a high-assurance financial integration where partners demand synchronous confirmation of disbursements and repayments, an explicit state machine with database-level row locking and lookup-before-retry handles failure and concurrency without operational overhead. As per assessment guidelines: *"A queue or worker is not required. Justify every major component; explaining why you did not need one earns credit."*

### 1.3 Database Constraints & Multi-Layered Defense
* **Layer 1: API / Idempotency Table:** Ingress requests are hashed and tracked in PostgreSQL (`status: IN_PROGRESS / COMPLETED / FAILED`).
* **Layer 2: Relational Unique Constraints:** PostgreSQL unique indexes on `(operation_type, external_reference)` and `(loan_reference, operation_type='DISBURSEMENT')`.
* **Layer 3: Core Banking State Machine:** Fineract `externalId` deduplication and native loan state checking (`m_loan.loan_status_id`).
* **Last Line of Defense:** The relational database unique constraint and Fineract's loan state machine guarantee financial invariants cannot be breached even under split-brain or network partitioning.

### 1.4 Retries & Failure Handling: Never Blindly Retry
* If an HTTP call to Fineract times out during disbursement or repayment, the integration service **never blindly retries the mutation**.
* **Resolution Mechanism:** The service queries Fineract by `externalId`:
  * If the transaction exists $\to$ transition state to `FINERACT_DONE` and proceed to ERP posting.
  * If the transaction does not exist $\to$ safe to retry or transition to `FAILED`.

### 1.5 Accounting Rules: Cash-Based vs. Accrual
* **Options Evaluated:**
  1. *Accrual Accounting (`accountingRule: 3`):* Recognizes interest income and fee income periodically before cash is collected.
  2. *Cash-Based Accounting (`accountingRule: 2`):* Recognizes interest and other income only when actual cash repayments are received.
* **Decision:** Cash-Based Accounting.
* **Rationale:** In SME asset financing with discrete monthly installments, cash-based accounting guarantees that income is strictly recognized when cash enters the bank account upon repayment. This eliminates prematurely recognizing interest receivables from potentially delinquent borrowers, directly mirrors the ERP cash journal entries (`DR 1100 Bank / CR 1200 Loan Receivable / CR 4000 Interest Income`), and ensures deterministic two-way reconciliation against bank accounts and general ledgers.

### 1.6 Identity Architecture: Strictly Zero Leaked Fineract Numeric IDs
* **Decision:** Partner contracts strictly use business identifiers (`client_uid`, `loan_reference`, and `external_reference`).
* **Rationale:** Exposing database primary keys (`clientId: 12`, `loanId: 45`) couples external partners to the internal storage engine, breaches multi-tenant isolation, and leaks data volume information. Local mapping tables (`client_mappings`, `loan_mappings`) map business keys to Fineract's `externalId` and internal IDs transparently.

### 1.7 What Was Deliberately Not Built, Known Weaknesses & Future Architecture (Part L)
* **What Was Deliberately Not Built:**
  - *Distributed Two-Phase Commit (XA transactions):* Fragile, blocking coordinator protocols that reduce system availability.
  - *Message Broker / Heavy Queue Infrastructure:* Unnecessary operational complexity for this integration tier; synchronous ingress backed by persisted PostgreSQL state machine and startup/periodic sweeper meets all consistency guarantees with lower failure surface.
* **Known Weaknesses:**
  - If Mock ERP experiences an extended outage (e.g. 12+ hours) while Fineract continues to process disbursements, records accumulate in `ERP_PENDING` / `FAILED_ERP` until the sweeper runs or manual reconciliation repair is invoked.
  - Batching: Currently, journal entries are posted one-by-one; in hyper-scale workloads (>1,000 tx/sec), bulk posting APIs would be required.
* **Tier 3 / Future Extensions (Part L):**
  1. *Transactional Outbox with Change Data Capture (CDC):* Implement the Transactional Outbox pattern using PostgreSQL WAL logs and Debezium/Kafka to stream ledger events asynchronously to ERP without two-phase commit.
  2. *Multi-Currency & FX Gain/Loss:* Support disbursements in local currencies (e.g., KES, UGX, RWF) funded by hard-currency credit facilities (USD, EUR), tracking realized and unrealized FX variance in dedicated general ledger accounts.
  3. *Multi-Tranche Loan Milestone Disbursements:* Extend single-disbursement invariant to support approved multi-tranche schedules with conditional collateral and milestone verification before tranche release.
  4. *OpenTelemetry Distributed Tracing:* Upgrade correlation ID headers to standard W3C `traceparent` headers with Span exports to Jaeger/Tempo.

### 1.8 Source of Truth for Financial Breakdown: Fineract Core vs. Integration Service
* **Options Evaluated:**
  1. *Calculate principal/interest split in Integration Service:* Integration service computes amortization schedules using standard loan formulas.
  2. *Delegate calculation to ERP:* Send total payment to ERP and let ERP allocate between principal and interest.
  3. *Fetch transaction breakdown directly from Fineract core:* Query `GET /loans/{loanId}/transactions/{transactionId}` to retrieve exact portions calculated by Fineract.
* **Decision:** Fetch transaction breakdown directly from Fineract core.
* **Rationale:** Calculating financial amortization outside the core banking platform leads to rounding differences, schedule desynchronization (e.g. 365 vs 360 day counts, leap years, grace periods), and disputes. Fineract is the single source of truth for the loan schedule. Deriving double-entry journal entries directly from Fineract's breakdown ensures the General Ledger in ERP matches the core loan engine down to the cent.

### 1.9 Overpayments & Reversals: Immutable Double-Entry Accounting
* **Overpayments:**
  * When repayment exceeds outstanding obligations, crediting revenue or over-reducing loan receivable violates accounting standards.
  * The excess amount is allocated to Account `2100` (`Customer Overpayment Liability`). This accurately records the institution's debt to the borrower while maintaining debits = credits.
* **Reversals:**
  * In financial ledgers, deleting or directly modifying previously posted journal entries destroys auditability.
  * Reversals are executed as compensating entries (`DISBURSEMENT_REVERSAL`, `REPAYMENT_REVERSAL`) that post exact inverse debit and credit entries.
  * Both the erroneous transaction and its reversal remain permanently visible in the audit trail.

### 1.10 Concurrency Defense-in-Depth: Why One Layer Alone Is Not Enough (Part I2)
* **Assessment Requirement:** *Explain where the final financial invariant is enforced. Distinguish the layers you rely on (API idempotency, integration-DB constraint, locking, Fineract externalId, Fineract loan state), say why one layer alone is not enough, and name the last line of defence.*
* **The Four Defense Layers:**
  1. **Layer 1: API / Idempotency Table (`idempotency_records`):**
     * Scoped by `(operation_type, idempotency_key)`.
     * Filters out sequential duplicates and in-flight requests with identical references.
     * *Why it is not enough on its own:* If 10 concurrent requests arrive with **different** references for the same loan, Layer 1 sees 10 distinct idempotency keys and admits all 10 into execution!
  2. **Layer 2: Relational DB Unique Constraint (`disbursement_records`):**
     * Primary key on `disbursement_records(loan_reference)`.
     * When 10 concurrent requests with different references attempt disbursement at the exact same millisecond, the database engine enforces atomicity: exactly one thread successfully inserts; the remaining 9 encounter `IntegrityError` (PK violation), roll back, and fail with `409 Conflict`.
     * *Why it is not enough on its own:* A split-brain scenario, application bypass, or direct API integration to Fineract could theoretically send a mutation without touching the integration service database.
  3. **Layer 3: Fineract `externalId` Deduplication:**
     * Fineract enforces uniqueness on transaction `externalId`.
     * *Why it is not enough on its own:* Different references produce different `externalId` values in Fineract.
  4. **Layer 4: Core Banking Loan State Machine (The Last Line of Defence):**
     * In Apache Fineract core, a loan exists in status `APPROVED (200)`. Upon successful disbursement, Fineract executes an atomic database transaction changing the status to `ACTIVE (300)` and recording the disbursement transaction.
     * Any subsequent call to `/loans/{loanId}?command=disburse` encounters Fineract's domain state check: `if (!loan.isApproved()) throw LoanCannotBeDisbursedException(...)`.
     * **Conclusion:** The Fineract core loan state machine is the **last line of defence** guaranteeing that no financial loan product can ever be disbursed more than once under any race condition, network anomaly, or architectural failure.

### 1.11 Two-Way Reconciliation Design: Read-Only Invariant & Explicit Repair (Part K)
* **Options Evaluated:**
  1. *Automatic background self-healing reconciliation:* The reconciliation job automatically repairs any missing ERP journal entries on discovery.
  2. *Strict read-only reconciliation with decoupled explicit repair:* Reconciliation only queries and reports discrepancies without mutating either system. Repair is executed via an explicit operator API call.
* **Decision:** Strict Read-Only Reconciliation with Decoupled Explicit Repair (`POST /api/reconciliation/repair`).
* **Rationale:**
  - Automated ledger mutation during reporting introduces profound financial audit risks: if a transaction is missing in ERP due to an upstream data corruption, configuration error, or fraudulent tampering, automatically forcing an entry into ERP compounds the error and obscures the root cause.
  - Making reconciliation read-only by default ensures idempotence of the reporting endpoint—auditors and monitoring systems can run reconciliation continuously or across overlapping date windows with zero side effects.
  - When an operator verifies a legitimate discrepancy (such as transient network failure resulting in `MISSING_IN_ERP`), they trigger `POST /api/reconciliation/repair`. This invokes the recovery engine which leverages ERP's own `external_reference` uniqueness to post missing entries idempotently without re-disbursing or mutating Fineract core.
* **Tolerance & Discrepancy Calculation:**
  - Discrepancy is calculated as $\Delta = \text{Amount}_{\text{ERP}} - \text{Amount}_{\text{Fineract}}$.
  - A tolerance of 0.01 RWF is applied to prevent floating-point variances from generating false positives.

---

## 2. Production Incident Investigation Walkthrough

### Scenario
An engineer is paged with the query: **"What happened to FIN-2026-000001?"**

### Step 1: Query API Status
The engineer invokes a single API call:
```bash
curl -s -H "Authorization: Bearer test-partner-api-key" \
  http://localhost:8000/api/transactions/FIN-2026-000001
```
**API Response:**
```json
{
  "external_reference": "FIN-2026-000001",
  "operation_type": "DISBURSEMENT",
  "loan_reference": "LN-2026-000001",
  "state": "COMPLETED",
  "fineract_transaction_id": 84,
  "erp_transaction_id": "JE-2026-0001",
  "amount": 5000000,
  "retry_count": 0,
  "created_at": "2026-10-28T10:10:00.102Z",
  "updated_at": "2026-10-28T10:10:01.450Z"
}
```

### Step 2: Correlated Log Trail
Using the correlation ID `8f8b824a-7140-4bd2-97b4-25e22709d435` or reference `FIN-2026-000001`, the engineer pulls the structured JSON logs:

```json
{"timestamp":"2026-10-28T10:10:00.102Z","level":"INFO","correlation_id":"8f8b824a-7140-4bd2-97b4-25e22709d435","external_reference":"FIN-2026-000001","client_uid":"SME-000001","loan_reference":"LN-2026-000001","status":"RECEIVED","message":"Disbursement request received"}
{"timestamp":"2026-10-28T10:10:00.120Z","level":"INFO","correlation_id":"8f8b824a-7140-4bd2-97b4-25e22709d435","external_reference":"FIN-2026-000001","client_uid":"SME-000001","loan_reference":"LN-2026-000001","status":"FINERACT_PENDING","message":"Dispatching disbursement command to Fineract core"}
{"timestamp":"2026-10-28T10:10:00.850Z","level":"INFO","correlation_id":"8f8b824a-7140-4bd2-97b4-25e22709d435","external_reference":"FIN-2026-000001","client_uid":"SME-000001","loan_reference":"LN-2026-000001","fineract_transaction_id":84,"status":"FINERACT_DONE","message":"Fineract loan disbursed successfully"}
{"timestamp":"2026-10-28T10:10:00.880Z","level":"INFO","correlation_id":"8f8b824a-7140-4bd2-97b4-25e22709d435","external_reference":"FIN-2026-000001","client_uid":"SME-000001","loan_reference":"LN-2026-000001","fineract_transaction_id":84,"status":"ERP_PENDING","message":"Posting DR 1200 / CR 1100 journal entry to ERP"}
{"timestamp":"2026-10-28T10:10:01.445Z","level":"INFO","correlation_id":"8f8b824a-7140-4bd2-97b4-25e22709d435","external_reference":"FIN-2026-000001","client_uid":"SME-000001","loan_reference":"LN-2026-000001","fineract_transaction_id":84,"erp_transaction_id":"JE-2026-0001","status":"COMPLETED","message":"Transaction finalized across all ledgers"}
```

### Conclusion
The engineer determines within seconds that:
1. The disbursement was initiated at `10:10:00.102Z`.
2. Fineract processed it into transaction ID `84`.
3. Mock ERP successfully booked journal entry `JE-2026-0001`.
4. Total execution latency was 1.34s with zero retries.

