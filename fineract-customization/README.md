# Fineract Java Customization (Part A)

## Overview
This module contains the Java / Spring Boot customization applied to Apache Fineract (pinned tag: `1.9.0`):
1. **Schema Extension (Liquibase):** Adds `external_reference` column to `acc_gl_account` with a unique index.
2. **Entity & Command Update:** Updates `GLAccount` JPA entity and `GLAccountWritePlatformServiceJpaRepositoryImpl` to handle external reference persistence.
3. **Idempotency & Conflict Rules:**
   - If an account with the specified `external_reference` already exists and all payload fields (`name`, `glCode`, `classification`, `currency`) are identical: return the existing account without duplicate creation (`HTTP 200`).
   - If the `external_reference` matches but the payload attributes differ: reject with `GLAccountDuplicateExternalReferenceException` (`HTTP 409 Conflict`).
4. **Permissions:** Protected under Fineract's `CREATE_GLACCOUNT` permission.

## Directory Structure
```
fineract-customization/
├── Dockerfile                  # Multi-stage custom build Dockerfile
├── README.md                   # This document
├── patch/
│   └── fineract-gl-sync.patch  # Clean git diff against upstream tag 1.9.0
└── src/                        # Custom code & Liquibase migrations
```

## Running Tests
Run JUnit 5 unit and integration tests:
```bash
./gradlew test --tests org.apache.fineract.accounting.glaccount.service.GLAccountWritePlatformServiceTest
```

