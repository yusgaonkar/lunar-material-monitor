#!/usr/bin/env python3
"""Validate Lunar on-hand allocation sumproduct against expected total."""

import pandas as pd
import numpy as np
from src import io as lio
import sys

print("=" * 80)
print("LUNAR ALLOCATION VALIDATION TEST")
print("=" * 80)

# Load data
print("\nLoading data...")
files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Step 1: Get Lunar unrestricted inventory
print("\nStep 1: Get Lunar unrestricted inventory...")
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()

# Use weighted average unit price, excluding zero prices where possible
def calc_weighted_price(group):
    """Calculate weighted average unit price, excluding zero prices when possible."""
    # First try non-zero prices
    non_zero = group[group['unit_price'] > 0]
    if len(non_zero) > 0:
        return (non_zero['unrestricted_qty'] * non_zero['unit_price']).sum() / non_zero['unrestricted_qty'].sum()
    else:
        # Fall back to all prices (including zeros)
        if group['unrestricted_qty'].sum() > 0:
            return (group['unrestricted_qty'] * group['unit_price']).sum() / group['unrestricted_qty'].sum()
        return 0

lunar_unrestricted = lunar_oh.groupby("lpn").agg(
    unrestricted=("unrestricted_qty", "sum")
).rename_axis("part").reset_index()

# Calculate weighted average unit price per part (don't merge yet, do it later)
lunar_prices_calc = lunar_oh.groupby("lpn").apply(calc_weighted_price, include_groups=False).reset_index()
lunar_prices_calc.columns = ["part", "weighted_price"]

print(f"  Found {len(lunar_unrestricted)} parts with Lunar inventory")

# Step 2: Get CM orders placed to Lunar
print("\nStep 2: Get CM orders from Lunar...")
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(
    r"(Sienna|Qualitel|Celestica|Plexus|Unigen)", expand=False
)

cm_orders_by_cm_part = cm_orders_lunar.groupby(["cm_extracted", "lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

# Step 3: Calculate Lunar position (uncommitted)
print("\nStep 3: Calculate Lunar position...")
total_cm_orders_by_part = cm_orders_by_cm_part.groupby("part")["cm_orders"].sum().reset_index()
total_cm_orders_by_part.columns = ["part", "total_cm_orders"]

lunar_pos = lunar_unrestricted.merge(total_cm_orders_by_part, on="part", how="left")
lunar_pos["total_cm_orders"] = lunar_pos["total_cm_orders"].fillna(0)
lunar_pos["uncommitted"] = lunar_pos["unrestricted"] - lunar_pos["total_cm_orders"]
lunar_pos["uncommitted"] = lunar_pos["uncommitted"].clip(lower=0)

print(f"  Total Lunar unrestricted: {lunar_pos['unrestricted'].sum():,.0f} units")
print(f"  Total CM orders from Lunar: {lunar_pos['total_cm_orders'].sum():,.0f} units")
print(f"  Total uncommitted: {lunar_pos['uncommitted'].sum():,.0f} units")

# Step 4: For validation, assume Scenario 1 (no shortages)
# This means Lunar on-hand allocation = uncommitted for all parts
print("\nStep 4: Calculate Lunar on-hand allocation...")
lunar_pos["lunar_on_hand_alloc"] = lunar_pos["uncommitted"]

# Step 5: Unit price already calculated above
print("\nStep 5: Using weighted average Lunar unit prices...")
lunar_pos = lunar_pos.merge(lunar_prices_calc, on="part", how="left")
lunar_pos.rename(columns={"weighted_price": "lunar_unit_price"}, inplace=True)
lunar_pos["lunar_unit_price"] = lunar_pos["lunar_unit_price"].fillna(0)

# Step 6: Calculate sumproduct
print("\nStep 6: Calculate sumproduct...")
lunar_pos["total_value"] = lunar_pos["lunar_on_hand_alloc"] * lunar_pos["lunar_unit_price"]

total_value = lunar_pos["total_value"].sum()
expected_value = 26_775_885.06

variance = total_value - expected_value
variance_pct = (variance / expected_value * 100) if expected_value != 0 else 0

print(f"\n  Total Lunar allocation value: ${total_value:,.2f}")
print(f"  Expected value:               ${expected_value:,.2f}")
print(f"  Variance:                     ${variance:,.2f} ({variance_pct:+.2f}%)")

# Step 7: Identify discrepancies
print("\nStep 7: Checking for discrepancies...")
if abs(variance) > 1.0:
    print(f"\n  ✗ MISMATCH DETECTED: ${variance:,.2f}")
    print(f"\n  Top 5 discrepant parts by allocation value:")

    top_5 = lunar_pos.nlargest(5, 'total_value')[
        ['part', 'lunar_on_hand_alloc', 'lunar_unit_price', 'total_value']
    ].copy()

    for idx, (_, row) in enumerate(top_5.iterrows(), 1):
        print(f"\n    {idx}. {row['part']}")
        print(f"       Allocation qty: {row['lunar_on_hand_alloc']:>15,.0f} units")
        print(f"       Unit price:    ${row['lunar_unit_price']:>14,.2f}")
        print(f"       Total value:   ${row['total_value']:>14,.2f}")

    print("\n  WARNING: The discrepancy may be due to:")
    print("  - Unit price data not being fully populated")
    print("  - 3-scenario allocation logic creating different allocation patterns")
    print("  - Missing inventory rows in the source data")

else:
    print(f"\n  ✓ VALIDATION PASSED: Variance is ${variance:,.2f} (within tolerance)")

print("\n" + "=" * 80)
print("Detailed Lunar Position Summary:")
print("=" * 80)

summary = lunar_pos[[
    'part', 'unrestricted', 'total_cm_orders', 'uncommitted',
    'lunar_on_hand_alloc', 'lunar_unit_price', 'total_value'
]].copy()
summary = summary.sort_values('total_value', ascending=False)

print(f"\n{len(summary)} parts with Lunar inventory\n")
print(summary.to_string(index=False, max_rows=20))

if len(summary) > 20:
    print(f"\n... and {len(summary) - 20} more parts")

print("\n" + "=" * 80)
