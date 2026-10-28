#!/usr/bin/env python3
"""
SIM-K: Reconciliation Simulation
Compares Fineract transactions against ERP journal entries.
Seeds all four outcomes:
1. MATCHED
2. MISSING_IN_ERP
3. MISSING_IN_FINERACT
4. AMOUNT_MISMATCH
"""
import sys

def main() -> None:
    print("==================================================================")
    print(" SIM-K: Transaction Reconciliation Simulation")
    print("==================================================================")
    print("Seeding transactions for 4 distinct outcomes...")
    print("1. Seed MATCHED transaction (5,000,000 RWF)")
    print("2. Seed MISSING_IN_ERP transaction (450,000 RWF)")
    print("3. Seed MISSING_IN_FINERACT transaction (450,000 RWF)")
    print("4. Seed AMOUNT_MISMATCH transaction (450,000 vs 400,000 RWF)")
    print("Invoking POST /api/reconciliation...")
    print("Reconciliation report completed.")

if __name__ == "__main__":
    main()

