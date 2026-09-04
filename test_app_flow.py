#!/usr/bin/env python3
"""Test the exact flow in the app."""

import pandas as pd
from src import io as lio, engine

print("=" * 80)
print("TESTING APP FLOW: Stage 1 Allocation")
print("=" * 80)

files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Skip engine for this test - just focus on the lookup logic
print("\nSkipping engine (just testing lookup logic)...")
pab = pd.DataFrame()

# Test part
test_part = "10-000099"
print(f"\nTest part: {test_part}")

# Step 1: Create cm_orders_by_cm_part (Stage 1)
print("\nStep 1: Create cm_orders_by_cm_part")
cm_orders_to_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_to_lunar["cm"] = cm_orders_to_lunar["source_report"].str.extract(r"(Sienna|Qualitel|Celestica|Plexus|Unigen)", expand=False)

cm_orders_by_cm_part = cm_orders_to_lunar.groupby(["cm", "lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

print(f"  Created cm_orders_by_cm_part with {len(cm_orders_by_cm_part)} rows")

# Step 2: Create balance_table (simplified - just for testing)
print("\nStep 2: Create balance_table")
# For testing, create a simple balance table with just our test part and all CMs
balance_table = pd.DataFrame({
    'cm': ['Celestica MX', 'Sienna GA', 'Qualitel WA', 'Lunar'],
    'part': [test_part, test_part, test_part, test_part]
})
print(f"  Created balance_table:\n{balance_table.to_string()}")

# Step 3: Calculate stage1_by_part (the key logic)
print(f"\nStep 3: Calculate stage1_by_part")
stage1_by_part = {}
stage1_by_part[test_part] = {}

part_rows = balance_table[balance_table["part"] == test_part]
print(f"  Processing {len(part_rows)} rows for {test_part}")

for _, row in part_rows.iterrows():
    cm = row["cm"]
    if cm == "Lunar":
        print(f"    Skipping Lunar row")
        continue

    # This is the critical lookup
    cm_lunar_orders = cm_orders_by_cm_part[
        (cm_orders_by_cm_part["cm"] == cm) &
        (cm_orders_by_cm_part["part"] == test_part)
    ]

    cm_orders = cm_lunar_orders["cm_orders"].values[0] if len(cm_lunar_orders) > 0 else 0
    stage1_by_part[test_part][cm] = cm_orders

    print(f"    {cm}: looked up in cm_orders_by_cm_part, found {cm_orders:,.0f} units")

# Step 4: Check what we got
print(f"\nStep 4: Results")
print(f"  stage1_by_part[{test_part}] = {stage1_by_part[test_part]}")

# Step 5: Verify the lookup by CM name
print(f"\nStep 5: Debug: What CM names appear in cm_orders_by_cm_part?")
print(f"  Unique CMs: {cm_orders_by_cm_part['cm'].unique().tolist()}")
print(f"  For {test_part}:")
for cm in cm_orders_by_cm_part['cm'].unique():
    rows = cm_orders_by_cm_part[(cm_orders_by_cm_part['cm'] == cm) & (cm_orders_by_cm_part['part'] == test_part)]
    if len(rows) > 0:
        print(f"    {cm}: {rows['cm_orders'].values[0]:,.0f}")

print("\n" + "=" * 80)
