# Fineract Environment Configuration (Part B)

This directory documents the Apache Fineract setup and configuration decisions required for the integration.

## Configuration Parameters

The setup is automated via `scripts/init_fineract.py` and establishes the following configuration on a clean instance:

### 1. Office Configuration
- **Office Name:** `Kigali Main Office`
- **Parent Office:** Head Office (`ID: 1`)
- **Opening Date:** `01 January 2026`
- **Rationale:** Establishes a dedicated Rwandan operating office under which all SME loans and client accounts reside.

### 2. Currency
- **Currency Code:** `RWF` (Rwandan Franc)
- **Decimal Digits:** `0` (RWF does not use fractional currency units)
- **Currency Configuration:** Registered in Fineract organization currencies.

### 3. General Ledger (GL) Accounts
Directly mirrored from the ERP Chart of Accounts via the Fineract GL synchronization customization:
- **1100 - Bank Main Account** (Type: `ASSET`, Detail, Manual entries allowed)
- **1200 - Loan Receivable** (Type: `ASSET`, Detail, Manual entries allowed)
- **4000 - Interest Income** (Type: `INCOME`, Detail, Manual entries allowed)
- **4100 - Fee Income** (Type: `INCOME`, Detail, Manual entries allowed)
- **4200 - Penalty Income** (Type: `INCOME`, Detail, Manual entries allowed)
- **5000 - Loan Loss Expense** (Type: `EXPENSE`, Detail, Manual entries allowed)

### 4. Loan Product: "SME Asset Finance"
- **Nominal Annual Interest:** `18.0%`
- **Interest Rate Frequency:** Monthly (`1.5%` per month)
- **Loan Term & Installments:** 12 months, monthly repayments
- **Amortization Type:** Equal installments (declining balance)
- **Disbursement:** Single disbursement (multi-disbursement disabled)
- **Accounting Rule:** **Cash-Based Accounting (`accountingRule: 2`)**
  - **Rationale:** Cash-based accounting recognizes interest income at the exact moment cash repayments are collected. In SME asset financing with scheduled installments, this approach prevents recognizing revenue prematurely before actual recovery, directly aligns with the cash-oriented journal entries posted to the ERP, and provides deterministic reconciliation.

### 5. Least-Privilege API Role and User
- **Role:** `FintechIntegrationRole`
  - Granular permissions restricted to:
    - Office & Currency: `READ_OFFICE`, `READ_CURRENCY`
    - Accounting: `READ_GLACCOUNT`, `CREATE_GLACCOUNT`, `READ_JOURNALENTRY`
    - Loan Product: `READ_LOANPRODUCT`
    - Client Operations: `CREATE_CLIENT`, `READ_CLIENT`, `ACTIVATE_CLIENT`
    - Loan Operations: `CREATE_LOAN`, `READ_LOAN`, `APPROVE_LOAN`, `DISBURSE_LOAN`, `REPAYMENT_LOAN`
- **API User:** `fintech_api_user`
  - Encapsulates integration traffic and restricts execution strictly to required operations without granting administrative system privileges (`ALL_FUNCTIONS`).
