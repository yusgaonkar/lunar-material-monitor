#!/usr/bin/env python3
"""Correct validation: sum(lunar allocation value) should equal source unrestricted_value."""

import pandas as pd
from src import io as lio

print("=" * 80)
print("LUNAR ALLOCATION VALUE VALIDATION (CORRECT METHOD)")
print("=" * 80)

files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Get source Lunar unrestricted position
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()

# Group by part to get aggregated position
lunar_by_part = lunar_oh.groupby("lpn").agg({
    'unrestricted_qty': 'sum',
    'unrestricted_value': 'sum'  # This is the expected total
}).rename_axis("part").reset_index()

print(f"\nSource data (Lunar Netsuite, 8/26 snapshot):")
print(f"  Total unrestricted qty: {lunar_by_part['unrestricted_qty'].sum():,.0f} units")
print(f"  Total unrestricted value: ${lunar_by_part['unrestricted_value'].sum():,.2f}")

# Get CM orders from Lunar
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_by_part = cm_orders_lunar.groupby("lunar_lpn").agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_part.columns = ["part", "cm_orders"]

# Calculate uncommitted
lunar_by_part = lunar_by_part.merge(cm_orders_by_part, on="part", how="left")
lunar_by_part["cm_orders"] = lunar_by_part["cm_orders"].fillna(0)
lunar_by_part["uncommitted"] = (lunar_by_part["unrestricted_qty"] - lunar_by_part["cm_orders"]).clip(lower=0)

print(f"\nLunar position:")
print(f"  Total unrestricted: {lunar_by_part['unrestricted_qty'].sum():,.0f} units")
print(f"  Total CM orders: {lunar_by_part['cm_orders'].sum():,.0f} units")
print(f"  Total uncommitted: {lunar_by_part['uncommitted'].sum():,.0f} units")

# Scenario 1: Full allocation (no shortages)
# Allocation = uncommitted, so we need to proportionally allocate the unrestricted_value
lunar_by_part["allocation_pct"] = lunar_by_part["uncommitted"] / lunar_by_part["unrestricted_qty"]
lunar_by_part["allocation_pct"] = lunar_by_part["allocation_pct"].fillna(0)
lunar_by_part["allocated_value"] = lunar_by_part["unrestricted_value"] * lunar_by_part["allocation_pct"]

total_allocated_value = lunar_by_part["allocated_value"].sum()
source_total = lunar_by_part["unrestricted_value"].sum()

print(f"\n" + "=" * 80)
print("SCENARIO 1 (No shortages - allocation = uncommitted):")
print("=" * 80)
print(f"\nTotal allocated value: ${total_allocated_value:,.2f}")
print(f"Source total value:    ${source_total:,.2f}")
print(f"Variance:              ${total_allocated_value - source_total:,.2f}")

if abs(total_allocated_value - source_total) < 1.0:
    print("\n✓ VALIDATION PASSED")
else:
    print("\n✗ MISMATCH")

# Top 5 parts by allocated value
print(f"\nTop 5 parts by allocated value:")
top_5 = lunar_by_part.nlargest(5, 'allocated_value')
for idx, (_, row) in enumerate(top_5.iterrows(), 1):
    print(f"  {idx}. {row['part']}")
    print(f"     Unrestricted: {row['unrestricted_qty']:>12,.0f} units → ${row['unrestricted_value']:>12,.2f}")
    print(f"     Allocated:    {row['uncommitted']:>12,.0f} units ({row['allocation_pct']*100:>5.1f}%) → ${row['allocated_value']:>12,.2f}")

# Check what causes the discrepancy if there is one
if abs(total_allocated_value - source_total) > 1.0:
    print(f"\n" + "=" * 80)
    print("DEBUGGING: Parts where allocation < unrestricted")
    print("=" * 80)

    reduced = lunar_by_part[lunar_by_part["allocation_pct"] < 1.0]
    print(f"\nParts with CM orders reducing allocation: {len(reduced)}")
    print(f"Total qty reduction: {(reduced['unrestricted_qty'] - reduced['uncommitted']).sum():,.0f} units")
    print(f"Total value reduction: ${(reduced['unrestricted_value'] - reduced['allocated_value']).sum():,.2f}")

    # This value reduction is expected - it goes to CM Stage 1 allocations
    print(f"\nThis reduction represents Stage 1 CM PO allocations (not in Lunar row)")
