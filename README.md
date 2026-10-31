# Fintech Engineer Take-Home Assessment: Reliable Financial Integration

> **Apache Fineract (Java/Spring Boot) + Python ERP & Integration Service**  
> Demonstrating financial correctness, idempotency, failure recovery, and concurrency control.

---

## 1. Pinned Versions & System Requirements

This project relies on pinned upstream versions to ensure deterministic, reproducible builds:

| Component | Pinned Version / Release | Runtime / Build Tool | Notes |
| :--- | :--- | :--- | :--- |
| **Apache Fineract** | `1.9.0` (Tag: `1.9.0`) | OpenJDK 17, Gradle 8.x | Official release tag, built from source with custom patch |
| **Python** | `3.12.3` | Python 3.12+, `pip` | Used for Integration Service and Mock ERP |
| **PostgreSQL** | `15-alpine` | PostgreSQL 15 | Separate databases for Fineract and Integration/ERP |
| **Docker Compose**| `v2.x` | Docker Engine 24+ | Single-command orchestration |

---

## 2. Tiers Completed

- [x] **Tier 1: Core (Parts A–G)**
  - Part A: ERP-owned GL accounts and Fineract Java customization (Liquibase, entity update, idempotency 200/409, JUnit tests).
  - Part B: Fineract environment scripted setup (Kigali Main Office, RWF currency, SME Asset Finance product, least-privilege user).
  - Part C: External identities mapping (`client_uid`, `loan_reference` -> Fineract `externalId`), zero numeric ID leakage.
  - Part D: Complete end-to-end loan lifecycle (Application -> Approval -> Disburse -> Repayments -> Payoff -> Closed).
  - Part E: Integration REST API with correlation ID tracking, structured JSON errors, and OpenAPI docs.
  - Part F: Multi-tier idempotency framework with strict per-operation scoping.
  - Part G: Mock ERP with chart of accounts seeding and balanced ($DR = CR$) journal entries.
- [x] **Tier 2: Reliability (Parts H–K)**
  - Part H: Pure accounting mapping derived from Fineract transaction breakdowns.
  - Part I: Concurrency & duplicate prevention (I1 sequential duplicates, I2 10x concurrent disbursements, I3 concurrent repayments).
  - Part J: Explicit transaction state machine with fault injection and lookup-before-retry recovery.
  - Part K: Read-only reconciliation report showing all 4 outcomes (`MATCHED`, `MISSING_IN_ERP`, `MISSING_IN_FINERACT`, `AMOUNT_MISMATCH`).
- [ ] **Tier 3: Stretch (Part L)**
  - Documented future extension options in `DECISIONS.md`.

---

## 3. Quickstart: One-Command Startup

### Prerequisites
- Docker Engine & Docker Compose v2+
- `make` (optional, commands can be run directly via bash/docker compose)

### Start All Services
```bash
# Clone and enter the repository
cd fineract-erp-integration

# Start all containers in the background
make up
# OR: docker compose up -d
```

### Verify Service Health
* **Integration Service:** http://localhost:8000/docs (Swagger UI) / http://localhost:8000/health
* **Mock ERP:** http://localhost:8001/docs (Swagger UI) / http://localhost:8001/health
* **Apache Fineract Core:** https://localhost:8443/fineract-provider/api/v1/info

---

## 4. Running Tests

One documented command per language runs the full test suite:

### Java Tests (Fineract Customization)
Runs JUnit 5 unit and integration tests covering the GL synchronization customization, Liquibase migration, and conflict logic:
```bash
make test-java
# Equivalent to: cd fineract-customization && ./gradlew test
```

### Python Tests (Integration Service & Mock ERP)
Runs pytest with type checks and coverage across accounting mapping, state machine, idempotency, and concurrency:
```bash
make test-python
# Equivalent to: cd integration-service && pytest -v && cd ../mock-erp && pytest -v
```

### Run All Tests
```bash
make test
```

---

## 5. Running Simulations & Verifying Evidence

Scripted simulations print structured before/after states and verify system invariants:

```bash
# 1. Initialize Fineract & Seed Mock ERP
make seed-erp
make init-fineract

# 2. Run Complete Loan Lifecycle (Part D)
make simulate-lifecycle

# 3. Test Sequential Duplicates (Part I1)
make simulate-duplicates

# 4. Test Concurrency: 10 parallel disbursements & repayments (Part I2 & I3)
make simulate-concurrency

# 5. Test Failure Recovery & Fault Injection (Part J)
make simulate-failures

# 6. Run Reconciliation Report (Part K)
make simulate-reconciliation
```

See [SIMULATIONS.md](SIMULATIONS.md) for detailed execution transcripts and before/after verification logs.

---

## 6. Project Documentation Index

* [ARCHITECTURE.md](ARCHITECTURE.md): System components, data flow, state machine, GL sync design.
* [API.md](API.md): API contract, endpoints, payload formats, error schemas, idempotency specs.
* [SIMULATIONS.md](SIMULATIONS.md): Detailed evidence logs for all simulations using the official template.
* [DECISIONS.md](DECISIONS.md): Architectural decisions, trade-offs, rationale, and incident investigation walkthrough.

