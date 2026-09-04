#!/usr/bin/env python3
"""
Debug script to show what the lunar depletion calculation should produce
"""

import sys
sys.path.insert(0, '/Users/yashusgaonkar/lunar-planner')

import pandas as pd
from src import io as lio, engine as eng

# Load all data
print("Loading data...")
frames = lio.load_all()
build_plan = lio.load_build_plan()

# Run engine to get PAB
print("Running engine...")
frames['build_plan.csv'] = build_plan
result = eng.run(frames)
pab = result["pab"]

# Get the inventories
onhand = frames["onhand.csv"]
onorder = frames["onorder.csv"]

print("\n" + "="*80)
print("LUNAR DEPLETION CALCULATION FOR 10-000099")
print("="*80)

# Get CM orders to Lunar
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

# Filter for 10-000099
part = "10-000099"
cm_orders_part = cm_orders_lunar[cm_orders_lunar["lunar_lpn"] == part]

print(f"\nCM Orders to Lunar for {part}:")
print(f"Total rows: {len(cm_orders_part)}")
if len(cm_orders_part) > 0:
    by_cm = cm_orders_part.groupby("cm_extracted")["quantity_open"].sum()
    for cm, qty in by_cm.items():
        print(f"  {cm}: {qty}")

# Get Lunar on-order for this part
lunar_oo = onorder[onorder["source_report"] == "Lunar Netsuite"].copy()
lunar_oo_part = lunar_oo[lunar_oo["lunar_lpn"] == part]
lunar_total_oo = lunar_oo_part["quantity_open"].sum()
print(f"\nLunar On-Order for {part}: {lunar_total_oo}")

# Get Lunar unrestricted for this part
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()
lunar_oh_part = lunar_oh[lunar_oh["lpn"] == part]
lunar_unrestricted = lunar_oh_part["unrestricted_qty"].sum()
print(f"Lunar Unrestricted for {part}: {lunar_unrestricted}")

# Get CM on-hand for this part
print(f"\nCM On-Hand for {part}:")
cm_oh = onhand[onhand["source_report"] != "Lunar Netsuite"].copy()
cm_oh["cm_extracted"] = cm_oh["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()
cm_oh_part = cm_oh[cm_oh["lpn"] == part]
if len(cm_oh_part) > 0:
    by_cm = cm_oh_part.groupby("cm_extracted")["unrestricted_qty"].sum()
    for cm, qty in by_cm.items():
        print(f"  {cm}: {qty}")

# Now show what the allocation should be
print(f"\n" + "="*80)
print("EXPECTED ALLOCATIONS")
print("="*80)
print(f"Lunar unrestricted: {lunar_unrestricted}")
print(f"Sienna order to Lunar: 628000 (from earlier)")
print(f"Qualitel order to Lunar: ? (need to check)")
print(f"Stage 1: Allocate min(628000, {lunar_unrestricted}) = 628000 to Sienna")
print(f"Remaining for Lunar: {lunar_unrestricted} - 628000 = {lunar_unrestricted - 628000}")
print(f"Lunar on-order: {lunar_total_oo}")
print(f"Lunar total available: {lunar_unrestricted - 628000} + {lunar_total_oo} = {lunar_unrestricted - 628000 + lunar_total_oo}")

# Now simulate the depletion calculation
print(f"\n" + "="*80)
print("LUNAR DEPLETION SIMULATION")
print("="*80)

# Prepare dated orders
cm_orders_lunar_dated = cm_orders_lunar.copy()
cm_orders_lunar_dated["eta"] = cm_orders_lunar_dated["receipt_date"].fillna(cm_orders_lunar_dated["ship_date"])
cm_orders_lunar_dated = cm_orders_lunar_dated[cm_orders_lunar_dated["eta"].notna()].copy()
cm_orders_lunar_dated["eta"] = pd.to_datetime(cm_orders_lunar_dated["eta"])
cm_orders_lunar_dated["eta_month"] = cm_orders_lunar_dated["eta"].dt.to_period("M")

lunar_oo_dated = lunar_oo[lunar_oo["receipt_date"].notna() | lunar_oo["ship_date"].notna()].copy()
lunar_oo_dated["eta"] = lunar_oo_dated["receipt_date"].fillna(lunar_oo_dated["ship_date"])
lunar_oo_dated["eta"] = pd.to_datetime(lunar_oo_dated["eta"])
lunar_oo_dated["eta_month"] = lunar_oo_dated["eta"].dt.to_period("M")

# Get months
pab_monthly = pab[pab["part"] == part].copy()
if len(pab_monthly) > 0:
    pab_monthly["period_date"] = pd.to_datetime(pab_monthly["period"])
    pab_monthly["month"] = pab_monthly["period_date"].dt.to_period("M")
    months = sorted(pab_monthly["month"].unique())

    print(f"\nMonths in projection: {months}")

    # Simulate for Sienna
    print(f"\n--- SIENNA Row (allocated 628,000) ---")
    sienna_alloc = 628000
    cumulative_consumed = 0

    for month in months:
        month_str = str(month)
        month_cm_receipts = cm_orders_lunar_dated[
            (cm_orders_lunar_dated["cm_extracted"] == "Sienna GA") &
            (cm_orders_lunar_dated["lunar_lpn"] == part) &
            (cm_orders_lunar_dated["eta_month"] == month)
        ]
        cm_month_qty = month_cm_receipts["quantity_open"].sum()
        cumulative_consumed += cm_month_qty
        balance = sienna_alloc - cumulative_consumed
        print(f"  {month_str}: received={cm_month_qty}, cumulative_consumed={cumulative_consumed}, balance={balance}")

    # Simulate for Lunar
    print(f"\n--- LUNAR Row (allocated {lunar_unrestricted - 628000}, on-order {lunar_total_oo}) ---")
    lunar_alloc = lunar_unrestricted - 628000
    cumulative_cm_consumed = 0
    cumulative_lunar_received = 0

    for month in months:
        month_str = str(month)
        # Total CM consumption this month
        month_cm_receipts = cm_orders_lunar_dated[
            (cm_orders_lunar_dated["lunar_lpn"] == part) &
            (cm_orders_lunar_dated["eta_month"] == month)
        ]
        cm_month_qty = month_cm_receipts["quantity_open"].sum()
        cumulative_cm_consumed += cm_month_qty

        # Lunar receipts this month
        lunar_receipts = lunar_oo_dated[
            (lunar_oo_dated["lunar_lpn"] == part) &
            (lunar_oo_dated["eta_month"] == month)
        ]
        lunar_month_qty = lunar_receipts["quantity_open"].sum()
        cumulative_lunar_received += lunar_month_qty

        balance = lunar_alloc + lunar_total_oo + cumulative_lunar_received - cumulative_cm_consumed
        print(f"  {month_str}: cm_qty={cm_month_qty}, lunar_qty={lunar_month_qty}, cumulative_cm_consumed={cumulative_cm_consumed}, cumulative_lunar_received={cumulative_lunar_received}, balance={balance}")

print("\n" + "="*80)
