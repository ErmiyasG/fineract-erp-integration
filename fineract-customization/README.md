# Apache Fineract Java Customization (Part A)

## 1. Overview & Architectural Mechanism
This module contains the Java / Spring Boot customization applied to Apache Fineract (pinned tag: `1.9.0`):
* **Customization Mechanism Chosen:** Extension of Fineract's core GL command handler (`GLAccountWritePlatformServiceJpaRepositoryImpl`) coupled with a Liquibase schema migration and JPA entity extension.
* **Why this mechanism:**
  1. **Zero Bypass / True Encapsulation:** Enforcing the `external_reference` uniqueness and idempotent return directly within Fineract's write platform pipeline guarantees that all ingress channels (REST API, batch API, internal services) respect the contract without bypassing business logic.
  2. **Atomic Conflict Rejection:** Conflict detection occurs at the database and application boundary, rejecting conflicting mutations with `HTTP 409 Conflict` (`GLAccountConflictException` via `GLAccountConflictExceptionMapper`).
  3. **Preserves Security & Audit:** Uses Fineract's native command pipeline (`commandsSourceWritePlatformService`), ensuring permission verification (`CREATE_GLACCOUNT`) and command audit logging remain intact.

---

## 2. Implemented Behaviors (Enforced Inside Fineract)

1. **ERP `external_reference` Persistence & Database Uniqueness:**
   - Database column `external_reference VARCHAR(100)` added to `acc_gl_account` with a unique constraint `acc_gl_account_external_reference_unique` via Liquibase migration `0131_add_external_reference_to_acc_gl_account.xml`.
   - Mapped in `GLAccount` JPA entity and exposed via `GLAccountData`.
2. **Idempotent Return on Identical Payload (No Duplicates):**
   - If an account with the specified `external_reference` already exists and incoming payload (`name`, `glCode`, `type`) matches the existing account, Fineract returns the existing account (`HTTP 200`) without creating duplicate records.
3. **Conflict Rejection on Mismatched Payload (HTTP 409):**
   - If `external_reference` matches an existing account but payload attributes differ (e.g. mismatched code, name, or classification), Fineract rejects the request with `GLAccountConflictException` mapped to `HTTP 409 Conflict`.
4. **Protected by Fineract Permission Model:**
   - The endpoint `/fineract-provider/api/v1/glaccounts` requires valid authentication and `CREATE_GLACCOUNT` permission; unauthenticated calls are rejected with `HTTP 401 Unauthorized`.

---

## 3. Files Modified & Added in Patch

| Component | Path | Purpose |
| :--- | :--- | :--- |
| **Liquibase Migration** | `db/changelog/tenant/parts/0131_add_external_reference_to_acc_gl_account.xml` | Adds `external_reference` column and unique constraint |
| **Liquibase Master** | `db/changelog/tenant/changelog-tenant.xml` | Registers changelog part 0131 |
| **JPA Entity** | `accounting/glaccount/domain/GLAccount.java` | Maps `external_reference` and unique constraint |
| **Repository** | `accounting/glaccount/domain/GLAccountRepository.java` | Added `findOneByExternalReference(String)` |
| **Command & Deserializer** | `command/GLAccountCommand.java`, `serialization/...Deserializer.java` | Parses and validates `externalReference` / `external_reference` |
| **Write Service** | `service/GLAccountWritePlatformServiceJpaRepositoryImpl.java` | Core conflict detection, idempotent return, and exception handling |
| **Read Service & DTO** | `service/...ReadPlatformServiceImpl.java`, `data/GLAccountData.java` | Serializes `externalReference` in GET responses |
| **Conflict Exception** | `exception/GLAccountConflictException.java` | Domain exception for conflicting payload |
| **Exception Mapper** | `exceptionmapper/GLAccountConflictExceptionMapper.java` | JAX-RS Provider mapping to HTTP 409 Conflict |
| **JUnit Unit Tests** | `service/GLAccountWritePlatformServiceTest.java` | Unit tests for new, duplicate, and conflicting calls |
| **JUnit Integration Tests** | `accounting/GLAccountExternalReferenceSyncIntegrationTest.java` | Integration tests verifying HTTP 200, 409, and 401 |

---

## 4. Building and Testing

### Applying the Patch to Clean Upstream
```bash
git clone --depth 1 --branch 1.9.0 https://github.com/apache/fineract.git
cd fineract
git apply ../patch/fineract-gl-sync.patch
```

### Running Unit & Integration Tests
```bash
# Run unit tests
./gradlew test --tests org.apache.fineract.accounting.glaccount.service.GLAccountWritePlatformServiceTest

# Run integration tests
./gradlew test --tests org.apache.fineract.integrationtests.accounting.GLAccountExternalReferenceSyncIntegrationTest
```

### Baking the Custom Docker Image
```bash
docker build -t fineract-customized:1.9.0 -f Dockerfile .
```
