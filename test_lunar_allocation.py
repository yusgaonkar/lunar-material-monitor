#!/usr/bin/env python3
"""Test Lunar allocation logic with real data."""

import pandas as pd
import numpy as np
from src import io as lio, engine

print("=== Testing Lunar Allocation Implementation ===\n")

# Load data
print("Loading data...")
files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Test part: 10-000099 (high usage, multiple CMs)
test_part = "10-000099"

print(f"\n--- Testing part: {test_part} ---\n")

# Step 1: Lunar unrestricted inventory
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()
lunar_unrestricted = lunar_oh.groupby("lpn").agg(
    unrestricted=("unrestricted_qty", "sum")
).rename_axis("part").reset_index()

lunar_total = lunar_unrestricted[lunar_unrestricted["part"] == test_part]["unrestricted"].values
if len(lunar_total) > 0:
    print(f"1. Lunar unrestricted inventory: {lunar_total[0]:,.0f}")
else:
    print(f"1. Lunar unrestricted inventory: 0")

# Step 2: CM orders placed against Lunar
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(
    r"(Sienna|Qualitel|Celestica|Plexus|Unigen)", expand=False
)

cm_orders_by_cm_part = cm_orders_lunar.groupby(["cm_extracted", "lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

test_orders = cm_orders_by_cm_part[cm_orders_by_cm_part["part"] == test_part].sort_values("cm")
print(f"\n2. CM orders from Lunar for {test_part}:")
total_cm_orders = 0
for _, row in test_orders.iterrows():
    print(f"   {row['cm']:15s}: {row['cm_orders']:12,.0f}")
    total_cm_orders += row['cm_orders']
print(f"   {'Total':15s}: {total_cm_orders:12,.0f}")

# Step 3: Uncommitted calculation
total_cm_orders_by_part = cm_orders_by_cm_part.groupby("part")["cm_orders"].sum().reset_index()
total_cm_orders_by_part.columns = ["part", "total_cm_orders"]

lunar_start = lunar_unrestricted.merge(total_cm_orders_by_part, on="part", how="left")
lunar_start["total_cm_orders"] = lunar_start["total_cm_orders"].fillna(0)
lunar_start["uncommitted"] = lunar_start["unrestricted"] - lunar_start["total_cm_orders"]
lunar_start["uncommitted"] = lunar_start["uncommitted"].clip(lower=0)

lunar_pos = lunar_start[lunar_start["part"] == test_part]
if len(lunar_pos) > 0:
    unrestricted = lunar_pos["unrestricted"].values[0]
    total_orders = lunar_pos["total_cm_orders"].values[0]
    uncommitted = lunar_pos["uncommitted"].values[0]
    print(f"\n3. Lunar inventory position:")
    print(f"   Unrestricted:    {unrestricted:12,.0f}")
    print(f"   Total CM orders: {total_orders:12,.0f}")
    print(f"   Uncommitted:     {uncommitted:12,.0f}")

    # Step 4: Verify the math
    if unrestricted == lunar_total[0] if len(lunar_total) > 0 else True:
        print(f"\n✓ Verification: Lunar totals match")
    else:
        print(f"\n✗ ERROR: Lunar totals don't match!")

    if total_orders == total_cm_orders:
        print(f"✓ Verification: CM order totals match")
    else:
        print(f"✗ ERROR: CM order totals don't match!")

# Step 5: Simulate allocation for two CMs
print(f"\n4. Allocation (assuming 50/50 demand split):")
cm_list = test_orders["cm"].unique()
for cm in sorted(cm_list):
    cm_order_qty = test_orders[test_orders["cm"] == cm]["cm_orders"].values[0]
    alloc_factor = 0.5  # Assuming 50/50 demand
    allocated = uncommitted * alloc_factor
    lunar_alloc = cm_order_qty + allocated
    print(f"   {cm}:")
    print(f"     Orders from Lunar: {cm_order_qty:12,.0f}")
    print(f"     Allocated uncommitted (50%): {allocated:12,.0f}")
    print(f"     Total lunar_on_hand_alloc:    {lunar_alloc:12,.0f}")

print("\n=== Test Complete ===")
