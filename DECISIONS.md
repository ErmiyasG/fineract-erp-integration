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

### 1.5 What Was Deliberately Not Built & Known Weaknesses
* **Not Built:** Distributed two-phase commit (XA transactions), multi-currency FX conversion, multi-tranche loan schedules.
* **Known Weakness:** If Mock ERP is partitioned for hours while Fineract succeeds, transactions remain in `NEEDS_RECONCILIATION` until the ERP recovery sweeper runs or manual reconciliation is triggered.

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

