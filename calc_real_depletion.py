#!/usr/bin/env python3
"""
Calculate actual lunar depletion for 10-000099 using real receipt dates
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
print(f"REAL LUNAR DEPLETION CALCULATION FOR {part}")
print("="*80)

# Get CM orders to Lunar with receipt dates
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

part_cm_orders = cm_orders_lunar[cm_orders_lunar["lunar_lpn"] == part].copy()
part_cm_orders["eta"] = part_cm_orders["receipt_date"].fillna(part_cm_orders["ship_date"])
part_cm_orders["eta"] = pd.to_datetime(part_cm_orders["eta"])
part_cm_orders["eta_month"] = part_cm_orders["eta"].dt.to_period("M")

print(f"\nCM Orders to Lunar for {part}:")
print(f"Total rows: {len(part_cm_orders)}")
if len(part_cm_orders) > 0:
    print(f"\nBy CM and month:")
    for cm in sorted(part_cm_orders["cm_extracted"].unique()):
        cm_data = part_cm_orders[part_cm_orders["cm_extracted"] == cm]
        print(f"\n  {cm}:")
        for month in sorted(cm_data["eta_month"].unique()):
            month_data = cm_data[cm_data["eta_month"] == month]
            qty = month_data["quantity_open"].sum()
            print(f"    {month}: {qty:,}")

# Get Lunar on-order for this part
lunar_oo = onorder[onorder["source_report"] == "Lunar Netsuite"].copy()
lunar_oo_part = lunar_oo[lunar_oo["lunar_lpn"] == part].copy()
lunar_oo_part["eta"] = lunar_oo_part["receipt_date"].fillna(lunar_oo_part["ship_date"])
lunar_oo_part["eta"] = pd.to_datetime(lunar_oo_part["eta"])
lunar_oo_part["eta_month"] = lunar_oo_part["eta"].dt.to_period("M")

print(f"\n\nLunar On-Order for {part}:")
lunar_total_oo = lunar_oo_part["quantity_open"].sum()
print(f"Total: {lunar_total_oo:,}")
if len(lunar_oo_part) > 0:
    print(f"\nBy month:")
    for month in sorted(lunar_oo_part["eta_month"].dropna().unique()):
        month_data = lunar_oo_part[lunar_oo_part["eta_month"] == month]
        qty = month_data["quantity_open"].sum()
        print(f"  {month}: {qty:,}")

# Get Lunar on-hand
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()
lunar_oh_part = lunar_oh[lunar_oh["lpn"] == part]
lunar_unrestricted = lunar_oh_part["unrestricted_qty"].sum()

print(f"\n\nLunar Position for {part}:")
print(f"Unrestricted on-hand: {lunar_unrestricted:,}")

# Calculate allocations (from earlier analysis)
sienna_allocated = 628000
lunar_allocated = lunar_unrestricted - sienna_allocated
lunar_total_start = lunar_allocated + lunar_total_oo

print(f"\n\nALLOCATIONS:")
print(f"Sienna allocated: {sienna_allocated:,}")
print(f"Lunar allocated on-hand: {lunar_allocated:,}")
print(f"Lunar on-order (replenishment): {lunar_total_oo:,}")
print(f"Lunar total starting balance: {lunar_total_start:,}")

# Now calculate month-by-month depletion
print(f"\n\n" + "="*80)
print("MONTH-BY-MONTH DEPLETION CALCULATION")
print("="*80)

# Get all months
if len(part_cm_orders) > 0:
    all_months = sorted(part_cm_orders["eta_month"].unique())
else:
    all_months = []

if len(lunar_oo_part) > 0:
    lunar_months = sorted(lunar_oo_part["eta_month"].dropna().unique())
    all_months = sorted(set(all_months) | set(lunar_months))

all_months_str = [str(m) for m in all_months]

print(f"\nMonths in projection: {all_months_str}")

# SIENNA depletion
print(f"\n\n--- SIENNA ROW ---")
print(f"Starting allocation: {sienna_allocated:,}")
sienna_cumulative = 0
sienna_results = {"cm": "Sienna", "part": part}

for month in all_months:
    sienna_month_qty = part_cm_orders[
        (part_cm_orders["cm_extracted"] == "Sienna GA") &
        (part_cm_orders["eta_month"] == month)
    ]["quantity_open"].sum()

    sienna_cumulative += sienna_month_qty
    balance = sienna_allocated - sienna_cumulative
    month_str = str(month)
    sienna_results[f"Lunar_balance_{month_str}"] = max(0, int(balance))

    print(f"{month_str}: received={sienna_month_qty:,}, cumulative={sienna_cumulative:,}, balance={max(0, balance):,}")

# LUNAR depletion
print(f"\n\n--- LUNAR ROW ---")
print(f"Starting: allocated {lunar_allocated:,} + on-order {lunar_total_oo:,} = {lunar_total_start:,}")
lunar_cumulative_cm = 0
lunar_cumulative_replenish = 0
lunar_results = {"cm": "Lunar", "part": part}

for month in all_months:
    # Total CM consumption this month
    month_cm_qty = part_cm_orders[
        (part_cm_orders["eta_month"] == month)
    ]["quantity_open"].sum()
    lunar_cumulative_cm += month_cm_qty

    # Lunar replenishment this month
    month_lunar_qty = lunar_oo_part[
        (lunar_oo_part["eta_month"] == month)
    ]["quantity_open"].sum()
    lunar_cumulative_replenish += month_lunar_qty

    balance = lunar_total_start + lunar_cumulative_replenish - lunar_cumulative_cm
    month_str = str(month)
    lunar_results[f"Lunar_balance_{month_str}"] = max(0, int(balance))

    print(f"{month_str}: cm_qty={month_cm_qty:,}, lunar_qty={month_lunar_qty:,}, cumulative_cm={lunar_cumulative_cm:,}, cumulative_lunar={lunar_cumulative_replenish:,}, balance={max(0, balance):,}")

print(f"\n\n" + "="*80)
print("EXPECTED OUTPUT TABLE")
print("="*80)

# Show as table
output_rows = [sienna_results, lunar_results]
output_df = pd.DataFrame(output_rows)

# Reorder columns
static_cols = ["cm", "part"]
month_cols = [col for col in output_df.columns if col.startswith("Lunar_balance_")]
col_order = static_cols + sorted(month_cols)
output_df = output_df[col_order]

print(output_df.to_string(index=False))

print(f"\n\n" + "="*80)
print("KEY VALUES FOR VERIFICATION")
print("="*80)
print(f"Sienna final balance: {sienna_results[f'Lunar_balance_{all_months_str[-1]}']}")
print(f"Lunar final balance: {lunar_results[f'Lunar_balance_{all_months_str[-1]}']}")
