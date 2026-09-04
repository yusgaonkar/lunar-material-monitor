#!/usr/bin/env python3
"""
Verify the depletion logic (simplified, no engine)
"""

import sys
sys.path.insert(0, '/Users/yashusgaonkar/lunar-planner')

import pandas as pd
from src import io as lio

# Load data
print("Loading data...")
frames = lio.load_all()
onhand = frames["onhand.csv"]
onorder = frames["onorder.csv"]

part = "10-000099"

print("="*80)
print(f"DEPLETION LOGIC VERIFICATION FOR {part}")
print("="*80)

# Step 1: Lunar unrestricted
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"]
lunar_part = lunar_oh[lunar_oh["lpn"] == part]
lunar_unrestricted = lunar_part["unrestricted_qty"].sum()
print(f"\n1. Lunar unrestricted on-hand: {lunar_unrestricted:,}")

# Step 2: CM POs to Lunar
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

cm_orders_part = cm_orders_lunar[cm_orders_lunar["lunar_lpn"] == part]
cm_pos_by_cm = cm_orders_part.groupby("cm_extracted")["quantity_open"].sum()

print(f"\n2. CM POs to Lunar for {part}:")
total_cm_pos = 0
for cm, qty in sorted(cm_pos_by_cm.items()):
    print(f"   {cm}: {qty:,}")
    total_cm_pos += qty
print(f"   Total: {total_cm_pos:,}")

# Step 3: Uncommitted
uncommitted = max(0, lunar_unrestricted - total_cm_pos)
print(f"\n3. Uncommitted = {lunar_unrestricted:,} - {total_cm_pos:,} = {uncommitted:,}")

# Step 4: Scenario detection (assume Scenario 1 - no shortage for this simple case)
print(f"\n4. Scenario: 1 (No shortage - this is a simple case)")

# Step 5: Allocations
print(f"\n5. Stage 1 Allocations (lunar_on_hand_alloc):")
for cm, qty in sorted(cm_pos_by_cm.items()):
    print(f"   {cm}: {qty:,} (their PO)")
print(f"   Lunar: {uncommitted:,} (uncommitted, remaining)")

# Step 6: Depletion month-by-month
print(f"\n6. Monthly Depletion Calculation:")

cm_orders_dated = cm_orders_part.copy()
cm_orders_dated["eta"] = cm_orders_dated["receipt_date"].fillna(cm_orders_dated["ship_date"])
cm_orders_dated["eta"] = pd.to_datetime(cm_orders_dated["eta"])
cm_orders_dated["eta_month"] = cm_orders_dated["eta"].dt.to_period("M")

months = sorted(cm_orders_dated[cm_orders_dated["eta"].notna()]["eta_month"].unique())
print(f"   Months: {[str(m) for m in months]}")

# Sienna
print(f"\n   Sienna row (allocated {cm_pos_by_cm.get('Sienna GA', 0):,.0f}):")
sienna_alloc = cm_pos_by_cm.get('Sienna GA', 0)
cumulative_po = 0
for month in months:
    po_this_month = cm_orders_dated[
        (cm_orders_dated["cm_extracted"] == "Sienna GA") &
        (cm_orders_dated["eta_month"] == month)
    ]["quantity_open"].sum()
    cumulative_po += po_this_month
    balance = sienna_alloc - cumulative_po
    print(f"      {month}: PO received={po_this_month:,.0f}, cumulative={cumulative_po:,.0f}, balance={max(0, balance):,.0f}")

# Qualitel
if "Qualitel WA" in cm_pos_by_cm.index:
    print(f"\n   Qualitel row (allocated {cm_pos_by_cm.get('Qualitel WA', 0):,.0f}):")
    qualitel_alloc = cm_pos_by_cm.get('Qualitel WA', 0)
    cumulative_po = 0
    for month in months:
        po_this_month = cm_orders_dated[
            (cm_orders_dated["cm_extracted"] == "Qualitel WA") &
            (cm_orders_dated["eta_month"] == month)
        ]["quantity_open"].sum()
        cumulative_po += po_this_month
        balance = qualitel_alloc - cumulative_po
        print(f"      {month}: PO received={po_this_month:,.0f}, cumulative={cumulative_po:,.0f}, balance={max(0, balance):,.0f}")

# Lunar row
print(f"\n   Lunar row (allocated {uncommitted:,}):")
lunar_oo = onorder[onorder["source_report"] == "Lunar Netsuite"]
lunar_oo_part = lunar_oo[lunar_oo["lunar_lpn"] == part]
lunar_oo_part["eta"] = lunar_oo_part["receipt_date"].fillna(lunar_oo_part["ship_date"])
lunar_oo_part["eta"] = pd.to_datetime(lunar_oo_part["eta"])
lunar_oo_part["eta_month"] = lunar_oo_part["eta"].dt.to_period("M")

cumulative_lunar = 0
for month in months:
    lunar_this_month = lunar_oo_part[
        (lunar_oo_part["eta_month"] == month)
    ]["quantity_open"].sum()
    cumulative_lunar += lunar_this_month
    balance = uncommitted + cumulative_lunar
    print(f"      {month}: Lunar replenish={lunar_this_month:,.0f}, cumulative={cumulative_lunar:,.0f}, balance={balance:,.0f}")

print("\n" + "="*80)
print("EXPECTED APP OUTPUT")
print("="*80)

# Build expected table
output = []
for cm in sorted(cm_pos_by_cm.keys()):
    row = {"cm": cm, "part": part}
    cumulative_po = 0
    alloc = cm_pos_by_cm[cm]
    for month in months:
        po_this_month = cm_orders_dated[
            (cm_orders_dated["cm_extracted"] == cm) &
            (cm_orders_dated["eta_month"] == month)
        ]["quantity_open"].sum()
        cumulative_po += po_this_month
        balance = max(0, alloc - cumulative_po)
        row[f"Lunar_balance_{str(month)}"] = int(balance)
    output.append(row)

# Lunar row
lunar_row = {"cm": "Lunar", "part": part}
cumulative_lunar = 0
for month in months:
    lunar_this_month = lunar_oo_part[
        (lunar_oo_part["eta_month"] == month)
    ]["quantity_open"].sum()
    cumulative_lunar += lunar_this_month
    balance = uncommitted + cumulative_lunar
    lunar_row[f"Lunar_balance_{str(month)}"] = int(balance)
output.append(lunar_row)

df = pd.DataFrame(output)
print(df.to_string(index=False))

print("\n" + "="*80)
