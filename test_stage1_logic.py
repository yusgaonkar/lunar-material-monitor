#!/usr/bin/env python3
"""Test Stage 1 allocation logic in isolation."""

import pandas as pd
from src import io as lio

print("=" * 80)
print("TESTING STAGE 1 ALLOCATION LOGIC")
print("=" * 80)

files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Test part
test_part = "10-000099"

print(f"\nTest part: {test_part}\n")

# Step 1: Extract CM orders to Lunar (exactly as app does)
print("Step 1: Extract CM orders placed TO Lunar")
cm_orders_to_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
print(f"  Found {len(cm_orders_to_lunar)} rows with po_vendor containing 'Lunar'")

cm_orders_to_lunar["cm"] = cm_orders_to_lunar["source_report"].str.extract(r"(Sienna|Qualitel|Celestica|Plexus|Unigen)", expand=False)
print(f"  Extracted CM names: {cm_orders_to_lunar['cm'].value_counts().to_dict()}")

# Step 2: Group by (cm, part)
print("\nStep 2: Group by (cm, part)")
cm_orders_by_cm_part = cm_orders_to_lunar.groupby(["cm", "lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

print(f"  Total (cm, part) combinations: {len(cm_orders_by_cm_part)}")
print(f"  Sample rows:\n{cm_orders_by_cm_part.head(10).to_string()}")

# Step 3: Look up 10-000099 specifically
print(f"\nStep 3: Look up {test_part} in cm_orders_by_cm_part")
test_part_orders = cm_orders_by_cm_part[cm_orders_by_cm_part["part"] == test_part]
print(f"  Found {len(test_part_orders)} rows:")
print(test_part_orders.to_string())

# Step 4: Verify extraction works for each CM
print(f"\nStep 4: Verify extraction logic for {test_part}")
for cm in ["Sienna", "Qualitel", "Celestica"]:
    lookup = cm_orders_by_cm_part[
        (cm_orders_by_cm_part["cm"] == cm) &
        (cm_orders_by_cm_part["part"] == test_part)
    ]
    if len(lookup) > 0:
        qty = lookup["cm_orders"].values[0]
        print(f"  {cm}: {qty:,.0f} units ✓")
    else:
        print(f"  {cm}: NOT FOUND ✗")

print("\n" + "=" * 80)
