#!/usr/bin/env python3
"""Run the app's validation test logic standalone."""

import pandas as pd
import numpy as np
import sys
import re
from src import io as lio, engine

print("=" * 80)
print("RUNNING APP VALIDATION TEST LOGIC")
print("=" * 80)

# Load data
print("\nLoading data...")
files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Run engine to get PAB
print("Running engine...")
cfg = engine.Config(snapshot=onhand['Updated at'].iloc[0])
result = engine.run(files, cfg)
pab = result['pab']

print(f"PAB data: {len(pab)} rows")
print(f"Onhand data: {len(onhand)} rows")
print(f"Onorder data: {len(onorder)} rows")

# Simplified version of the app's computation for validation
print("\nComputing Lunar allocation...")

# Get Lunar unrestricted inventory
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()
lunar_unrestricted = lunar_oh.groupby("lpn").agg(
    unrestricted=("unrestricted_qty", "sum")
).rename_axis("part").reset_index()

# Get CM orders from Lunar
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(
    r"(Sienna|Qualitel|Celestica|Plexus|Unigen)", expand=False
)

cm_orders_by_cm_part = cm_orders_lunar.groupby(["cm_extracted", "lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

# Calculate Lunar position
total_cm_orders_by_part = cm_orders_by_cm_part.groupby("part")["cm_orders"].sum().reset_index()
total_cm_orders_by_part.columns = ["part", "total_cm_orders"]

lunar_start = lunar_unrestricted.merge(total_cm_orders_by_part, on="part", how="left")
lunar_start["total_cm_orders"] = lunar_start["total_cm_orders"].fillna(0)
lunar_start["uncommitted"] = lunar_start["unrestricted"] - lunar_start["total_cm_orders"]
lunar_start["uncommitted"] = lunar_start["uncommitted"].clip(lower=0)

# Get Lunar unit prices using weighted average (like the app does)
def get_lunar_unit_prices(lunar_oh_data):
    """Calculate weighted average unit price for Lunar on-hand by part."""
    lunar_prices = {}

    for part in lunar_oh_data['lpn'].unique():
        part_data = lunar_oh_data[lunar_oh_data['lpn'] == part]

        # Filter to non-zero prices first
        non_zero = part_data[part_data['unit_price'] > 0]
        if len(non_zero) > 0:
            weighted_price = (non_zero['unrestricted_qty'] * non_zero['unit_price']).sum() / non_zero['unrestricted_qty'].sum()
        else:
            # Fall back to all prices if no non-zero
            if part_data['unrestricted_qty'].sum() > 0:
                weighted_price = (part_data['unrestricted_qty'] * part_data['unit_price']).sum() / part_data['unrestricted_qty'].sum()
            else:
                weighted_price = 0

        lunar_prices[part] = weighted_price

    return lunar_prices

lunar_unit_prices = get_lunar_unit_prices(lunar_oh)

print(f"\nLunar position summary:")
print(f"  Total Lunar unrestricted: {lunar_start['unrestricted'].sum():,.0f} units")
print(f"  Total CM orders: {lunar_start['total_cm_orders'].sum():,.0f} units")
print(f"  Total uncommitted: {lunar_start['uncommitted'].sum():,.0f} units")

# In Scenario 1 (no shortages), Lunar allocation = uncommitted
lunar_start["lunar_on_hand_alloc"] = lunar_start["uncommitted"]
lunar_start["lunar_unit_price"] = lunar_start["part"].map(lunar_unit_prices).fillna(0)

# Calculate validation sumproduct
lunar_start["allocation_value"] = lunar_start["lunar_on_hand_alloc"] * lunar_start["lunar_unit_price"]

total_allocation_value = lunar_start["allocation_value"].sum()
expected_value = 26_775_885.06
variance = total_allocation_value - expected_value
variance_pct = (variance / expected_value * 100) if expected_value != 0 else 0

print("\n" + "=" * 80)
print("VALIDATION TEST: Lunar Allocation Value")
print("=" * 80)
print(f"\nTotal Lunar allocation value: ${total_allocation_value:,.2f}")
print(f"Expected value:               ${expected_value:,.2f}")
print(f"Variance:                     ${variance:,.2f} ({variance_pct:+.2f}%)")

if abs(variance) > 1.0:
    print(f"\n✗ VARIANCE EXCEEDS $1")
    print("\nTop 5 parts by allocation value:")

    top_parts = lunar_start.nlargest(5, 'allocation_value')
    for idx, (_, row) in enumerate(top_parts.iterrows(), 1):
        print(f"  {idx}. {row['part']}")
        print(f"     Allocation qty: {row['lunar_on_hand_alloc']:>12,.0f} units")
        print(f"     Unit price:    ${row['lunar_unit_price']:>14,.2f}")
        print(f"     Allocation value: ${row['allocation_value']:>12,.2f}")
else:
    print(f"\n✓ VALIDATION PASSED: Variance within tolerance")

print("\n" + "=" * 80)
print("\nDEBUGGING: Checking if there's a pricing issue...")
print("=" * 80)

# Check for missing/zero prices in original data
missing_price_parts = lunar_start[lunar_start['lunar_unit_price'] == 0]
print(f"\nParts with $0 unit price: {len(missing_price_parts)}")
print(f"Total allocation for $0-price parts: {missing_price_parts['lunar_on_hand_alloc'].sum():,.0f} units")
print(f"Value of $0-price parts: ${missing_price_parts['allocation_value'].sum():,.2f}")

if len(missing_price_parts) > 0:
    print("\nTop 5 zero-price parts by allocation qty:")
    top_zero = missing_price_parts.nlargest(5, 'lunar_on_hand_alloc')
    for idx, (_, row) in enumerate(top_zero.iterrows(), 1):
        print(f"  {idx}. {row['part']}: {row['lunar_on_hand_alloc']:>12,.0f} units")
