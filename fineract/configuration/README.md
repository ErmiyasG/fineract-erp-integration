# Fineract Environment Configuration (Part B)

## Configuration Parameters
This module contains the automated configuration scripts to initialize an empty Fineract database with required business entities:

1. **Office:** `Kigali Main Office` (Head Office or sub-branch in Rwanda).
2. **Currency:** `RWF` (Rwandan Franc, decimal places: 0).
3. **GL Accounts:** Linked to ERP-synced accounts:
   - `1100`: Bank Account (Asset)
   - `1200`: Loans Receivable (Asset)
   - `4000`: Interest Income (Income)
4. **Loan Product:** `SME Asset Finance`
   - Nominal Annual Interest: 18% (Monthly calculation)
   - Amortization: Equal installments (12 months)
   - Repayment Frequency: Monthly
   - Tranches: Single-disbursement (no multi-tranche)
   - Accounting Rule: Cash-based or Accrual (configured for Cash-based accounting for standard SME asset finance)
5. **Least-Privilege API User:**
   - Role: `IntegrationUserRole` with limited permissions (`READ_LOAN`, `DISBURSE_LOAN`, `REPAYMENT_LOAN`, `CREATE_CLIENT`, `READ_CLIENT`, `CREATE_GLACCOUNT`).

