#!/usr/bin/env python3
"""Test if Scenario 1 should allocate value proportionally."""

import pandas as pd
from src import io as lio

files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Get Lunar position
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()
lunar_by_part = lunar_oh.groupby("lpn").agg({
    'unrestricted_qty': 'sum',
    'unrestricted_value': 'sum'
}).rename_axis("part").reset_index()

# Get CM orders
cm_orders_to_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_to_lunar["cm"] = cm_orders_to_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()
cm_orders_by_cm_part = cm_orders_to_lunar.groupby(["cm", "lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

# Test with one part: 10-000099
test_part = "10-000099"

part_data = lunar_by_part[lunar_by_part["part"] == test_part].iloc[0]
unrestricted_qty = part_data['unrestricted_qty']
unrestricted_value = part_data['unrestricted_value']
unit_price = unrestricted_value / unrestricted_qty if unrestricted_qty > 0 else 0

print("=" * 80)
print(f"TESTING SCENARIO 1 ALLOCATION: {test_part}")
print("=" * 80)

print(f"\nSource data:")
print(f"  Unrestricted qty: {unrestricted_qty:,.0f} units")
print(f"  Unrestricted value: ${unrestricted_value:,.2f}")
print(f"  Unit price: ${unit_price:,.6f}")

# Get CM orders for this part
part_orders = cm_orders_by_cm_part[cm_orders_by_cm_part["part"] == test_part]
print(f"\nCM orders for {test_part}:")
total_cm_orders = 0
for _, row in part_orders.iterrows():
    print(f"  {row['cm']}: {row['cm_orders']:,.0f}")
    total_cm_orders += row['cm_orders']

print(f"  Total: {total_cm_orders:,.0f}")

uncommitted = unrestricted_qty - total_cm_orders

print(f"\nLunar position:")
print(f"  Uncommitted qty: {uncommitted:,.0f} units")
print(f"  Uncommitted value (qty × unit_price): ${uncommitted * unit_price:,.2f}")

print(f"\n" + "=" * 80)
print("ALLOCATION METHOD COMPARISON")
print("=" * 80)

# Method 1: Allocate by quantity (current approach)
print(f"\nMethod 1: Allocate by QUANTITY")
print(f"  Lunar row qty allocation: {uncommitted:,.0f} units")
print(f"  Lunar row value (qty × unit_price): ${uncommitted * unit_price:,.2f}")

# Method 2: Allocate by value percentage (alternative)
print(f"\nMethod 2: Allocate by VALUE percentage")
alloc_pct = uncommitted / unrestricted_qty if unrestricted_qty > 0 else 0
allocated_value = unrestricted_value * alloc_pct
print(f"  Allocation %: {alloc_pct*100:.1f}%")
print(f"  Lunar row value: ${allocated_value:,.2f}")

# Verify they're the same
diff = abs((uncommitted * unit_price) - allocated_value)
print(f"\nDifference: ${diff:,.2f}")
if diff < 0.01:
    print("  ✓ Methods are equivalent")
else:
    print("  ✗ Methods differ!")

print(f"\n" + "=" * 80)
print("CONCLUSION: Both methods should give the same result")
print("If they differ, it means unit_price calculation is inconsistent")
print("=" * 80)

