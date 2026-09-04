#!/usr/bin/env python3
"""
Show expected lunar depletion table for 10-000099
"""

import sys
sys.path.insert(0, '/Users/yashusgaonkar/lunar-planner')

import pandas as pd

# Manually set the expected values based on what we know
print("="*80)
print("EXPECTED LUNAR DEPLETION TABLE FOR 10-000099")
print("="*80)

# Allocations we calculated
sienna_allocated = 628000
lunar_allocated = 411115
lunar_on_order = 168000
lunar_total_start = lunar_allocated + lunar_on_order

print(f"\nALLOCATIONS:")
print(f"  Sienna allocated from Lunar: {sienna_allocated:,}")
print(f"  Lunar allocated to itself: {lunar_allocated:,}")
print(f"  Lunar on-order (replenishment): {lunar_on_order:,}")
print(f"  Lunar total starting balance: {lunar_total_start:,}")

# Expected months (rough projection)
months = ["2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02"]

print(f"\nMONTHLY PROJECTION:")
print(f"\nSienna row should show:")
print(f"  This is the Sienna allocation REDUCING as they receive from Lunar")
print(f"  Starting: {sienna_allocated:,}")
print(f"  If they receive evenly: {sienna_allocated/len(months):,.0f} per month")
print(f"  Pattern: {sienna_allocated:,} → {sienna_allocated - sienna_allocated/len(months):,.0f} → ... → 0")

print(f"\nLunar row should show:")
print(f"  This is the Lunar starting balance REDUCING as CMs consume from it")
print(f"  Starting: {lunar_total_start:,}")
print(f"  Minus: Total CM consumption (Sienna + any other CMs)")
print(f"  Plus: Lunar's own replenishment orders")
print(f"  Pattern: {lunar_total_start:,} → reduces each month")

# Build sample tables
print(f"\n" + "="*80)
print("SAMPLE EXPECTED OUTPUT (with even distribution assumptions)")
print("="*80)

sienna_rows = {}
sienna_rows["cm"] = "Sienna"
sienna_rows["part"] = "10-000099"
monthly_reduction = sienna_allocated / len(months)
for i, month in enumerate(months):
    balance = sienna_allocated - (monthly_reduction * (i + 1))
    sienna_rows[f"Lunar_balance_{month}"] = int(max(0, balance))

lunar_rows = {}
lunar_rows["cm"] = "Lunar"
lunar_rows["part"] = "10-000099"
# Assume Lunar supplies roughly match CM consumption (steady state)
for i, month in enumerate(months):
    # Net: starting + replenishment - consumption
    # Assuming no replenishment arrives and total CM consumption = Sienna consumption
    balance = lunar_total_start - (monthly_reduction * (i + 1))
    lunar_rows[f"Lunar_balance_{month}"] = int(max(0, balance))

sienna_df = pd.DataFrame([sienna_rows])
lunar_df = pd.DataFrame([lunar_rows])

print("\nSienna rows (allocated 628,000):")
print(sienna_df.to_string(index=False))

print("\nLunar row (allocated 411,115 + on-order 168,000 = 579,115):")
print(lunar_df.to_string(index=False))

print("\n" + "="*80)
print("KEY POINTS:")
print("="*80)
print("1. Sienna's Lunar_balance columns should DECREASE from 628,000 to 0")
print("2. Lunar's Lunar_balance columns should DECREASE from 579,115")
print("3. The pattern reflects CM consumption based on their PO receipt dates")
print("4. Currently showing FLAT = the calculation is using wrong starting values")
print("="*80)
