#!/usr/bin/env python3
"""Validate final sumproduct against source on-hand inventory."""

import pandas as pd
from src import io as lio

print("=" * 80)
print("FINAL VALIDATION: App Lunar Allocation vs Source Inventory")
print("=" * 80)

files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# SOURCE DATA: Total Lunar on-hand value
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()
source_total_value = lunar_oh['unrestricted_value'].sum()
print(f"\nSOURCE: Lunar on-hand unrestricted_value")
print(f"  Total value: ${source_total_value:,.2f}")

# APP CALCULATION: Simulate exact app logic
print(f"\nAPP CALCULATION:")

# Step 1: Get Lunar position (unrestricted and CM orders)
lunar_unrestricted = lunar_oh.groupby("lpn").agg(
    unrestricted=("unrestricted_qty", "sum"),
    unrestricted_value=("unrestricted_value", "sum")
).rename_axis("part").reset_index()

# Step 2: Get CM orders to Lunar (with deduplication for Stage 1)
cm_orders_to_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_to_lunar["cm"] = cm_orders_to_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

# Deduplicate for Stage 1
stage1_orders = cm_orders_to_lunar.drop_duplicates(
    subset=["cm", "po_number", "po_line_item", "lunar_lpn"],
    keep="first"
)

cm_orders_by_cm_part = stage1_orders.groupby(["cm", "lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

# Step 3: Calculate uncommitted and unit prices
total_cm_orders_by_part = cm_orders_by_cm_part.groupby("part")["cm_orders"].sum().reset_index()
total_cm_orders_by_part.columns = ["part", "total_cm_orders"]

lunar_pos = lunar_unrestricted.merge(total_cm_orders_by_part, on="part", how="left")
lunar_pos["total_cm_orders"] = lunar_pos["total_cm_orders"].fillna(0)
lunar_pos["uncommitted"] = (lunar_pos["unrestricted"] - lunar_pos["total_cm_orders"]).clip(lower=0)

# Calculate unit prices (weighted average from Lunar on-hand)
lunar_pos["unit_price"] = lunar_pos["unrestricted_value"] / lunar_pos["unrestricted"]

print(f"  Parts with Lunar inventory: {len(lunar_pos)}")
print(f"  Total unrestricted qty: {lunar_pos['unrestricted'].sum():,.0f}")
print(f"  Total Stage 1 orders (before dedup): {onorder[onorder['po_vendor'].str.contains('Lunar', case=False, na=False)].groupby('lunar_lpn')['quantity_open'].sum().sum():,.0f}")
print(f"  Total Stage 1 orders (after dedup): {lunar_pos['total_cm_orders'].sum():,.0f}")
print(f"  Total uncommitted qty: {lunar_pos['uncommitted'].sum():,.0f}")

# Step 4: Allocate per Scenario 1 with SEQUENTIAL Stage 1 allocation
# Stage 1: CMs get MIN(their_order, remaining_lunar_available)
# Lunar row: gets what's left after all Stage 1 allocations
lunar_pos["stage1_allocation"] = 0  # Will be calculated per CM
lunar_pos["lunar_allocation"] = 0    # Will be calculated as remainder

# For each part, allocate Stage 1 sequentially
for idx, row in lunar_pos.iterrows():
    part = row['part']
    lunar_avail = row['unrestricted']
    remaining = lunar_avail

    # Get all CM orders for this part (if any)
    cm_orders_for_part = cm_orders_by_cm_part[cm_orders_by_cm_part['part'] == part]

    # Allocate sequentially to CMs
    total_stage1 = 0
    for _, cm_row in cm_orders_for_part.iterrows():
        cm_order = cm_row['cm_orders']
        allocated = min(cm_order, remaining)
        total_stage1 += allocated
        remaining -= allocated

    lunar_pos.at[idx, 'stage1_allocation'] = total_stage1
    lunar_pos.at[idx, 'lunar_allocation'] = max(0, remaining)

# Step 5: Calculate values
lunar_pos["stage1_value"] = lunar_pos["stage1_allocation"] * lunar_pos["unit_price"]
lunar_pos["lunar_value"] = lunar_pos["lunar_allocation"] * lunar_pos["unit_price"]
lunar_pos["total_allocation_value"] = lunar_pos["stage1_value"] + lunar_pos["lunar_value"]

# Step 6: Sum across all parts
total_stage1_value = lunar_pos["stage1_value"].sum()
total_lunar_value = lunar_pos["lunar_value"].sum()
total_app_value = lunar_pos["total_allocation_value"].sum()

print(f"\nALLOCATION VALUES:")
print(f"  Stage 1 (CM allocations): ${total_stage1_value:,.2f}")
print(f"  Lunar row (uncommitted):  ${total_lunar_value:,.2f}")
print(f"  TOTAL APP VALUE:          ${total_app_value:,.2f}")

print(f"\nCOMPARISON:")
print(f"  Source on-hand value: ${source_total_value:,.2f}")
print(f"  App allocation value: ${total_app_value:,.2f}")

variance = total_app_value - source_total_value
variance_pct = (variance / source_total_value * 100) if source_total_value != 0 else 0

print(f"  Variance: ${variance:,.2f} ({variance_pct:+.2f}%)")

if abs(variance) < 1.0:
    print(f"\n✓ VALIDATION PASSED")
else:
    print(f"\n✗ MISMATCH DETECTED")

    # Identify top discrepancies
    lunar_pos["abs_diff"] = abs(lunar_pos["total_allocation_value"] - (lunar_pos["unrestricted_value"]))
    top_disc = lunar_pos.nlargest(5, "abs_diff")[
        ["part", "unrestricted", "unrestricted_value", "total_allocation_value", "abs_diff"]
    ]

    print(f"\nTop 5 discrepant parts:")
    for idx, (_, row) in enumerate(top_disc.iterrows(), 1):
        print(f"  {idx}. {row['part']}")
        print(f"     Source value: ${row['unrestricted_value']:>12,.2f}")
        print(f"     App value:    ${row['total_allocation_value']:>12,.2f}")
        print(f"     Diff:         ${row['abs_diff']:>12,.2f}")

print("\n" + "=" * 80)
