#!/usr/bin/env python3
"""
Verify the depletion logic for a few parts before implementing in code
"""

import sys
sys.path.insert(0, '/Users/yashusgaonkar/lunar-planner')

import pandas as pd
from src import io as lio, engine as eng

# Load data
print("Loading data...")
frames = lio.load_all()
build_plan = lio.load_build_plan()

# Run engine to get PAB
print("Running engine...")
frames['build_plan.csv'] = build_plan
result = eng.run(frames)
pab = result["pab"]

onhand = frames["onhand.csv"]
onorder = frames["onorder.csv"]

# Test parts
test_parts = ["10-000099", "10-000551"]

for part in test_parts:
    print("\n" + "="*80)
    print(f"PART: {part}")
    print("="*80)

    # Step 1: Lunar unrestricted
    lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"]
    lunar_part = lunar_oh[lunar_oh["lpn"] == part]
    lunar_unrestricted = lunar_part["unrestricted_qty"].sum()
    print(f"\n1. Lunar unrestricted: {lunar_unrestricted:,}")

    # Step 2: CM POs to Lunar
    cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
    cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

    cm_orders_part = cm_orders_lunar[cm_orders_lunar["lunar_lpn"] == part]
    cm_pos_by_cm = cm_orders_part.groupby("cm_extracted")["quantity_open"].sum()

    print(f"\n2. CM POs to Lunar:")
    total_cm_pos = 0
    for cm, qty in cm_pos_by_cm.items():
        print(f"   {cm}: {qty:,}")
        total_cm_pos += qty
    print(f"   Total CM POs: {total_cm_pos:,}")

    # Step 3: Uncommitted
    uncommitted = lunar_unrestricted - total_cm_pos
    if uncommitted < 0:
        uncommitted = 0
        print(f"\n3. Uncommitted: 0 (clamped from {lunar_unrestricted - total_cm_pos:,})")
        print(f"   WARNING: Total CM POs exceed Lunar inventory!")
    else:
        print(f"\n3. Uncommitted: {lunar_unrestricted:,} - {total_cm_pos:,} = {uncommitted:,}")

    # Step 4: Get worst PAB for this part by CM
    pab_part = pab[pab["part"] == part].copy()
    pab_part["period_date"] = pd.to_datetime(pab_part["period"])

    print(f"\n4. CM Worst PAB (shortages):")
    worst_pab_by_cm = pab_part.groupby("cm")["pab"].min()
    total_shortage = 0
    cm_shortages = {}
    for cm, worst in worst_pab_by_cm.items():
        shortage = abs(worst) if worst < 0 else 0
        cm_shortages[cm] = shortage
        total_shortage += shortage
        if shortage > 0:
            print(f"   {cm}: {shortage:,}")

    if total_shortage == 0:
        print(f"   No shortages")

    # Step 5: Determine scenario
    print(f"\n5. Allocation Scenario:")
    print(f"   Total shortage: {total_shortage:,}")
    print(f"   Uncommitted: {uncommitted:,}")

    if total_shortage == 0:
        scenario = 1
        print(f"   → Scenario 1: No shortage (uncommitted stays in Lunar row)")
    elif total_shortage <= uncommitted:
        scenario = 2
        print(f"   → Scenario 2: Partial shortage (allocate exact shortage, balance remains)")
    else:
        scenario = 3
        print(f"   → Scenario 3: Full shortage (split uncommitted proportionally)")

    # Step 6: Build allocations
    print(f"\n6. Allocations (lunar_on_hand_alloc column):")
    cm_allocations = {}

    if scenario == 1:
        for cm, po_qty in cm_pos_by_cm.items():
            cm_allocations[cm] = po_qty
            print(f"   {cm}: {po_qty:,} (PO only)")
        lunar_alloc = uncommitted
        print(f"   Lunar: {lunar_alloc:,} (uncommitted)")

    elif scenario == 2:
        for cm, po_qty in cm_pos_by_cm.items():
            shortage_share = cm_shortages.get(cm, 0)
            alloc = po_qty + shortage_share
            cm_allocations[cm] = alloc
            print(f"   {cm}: {po_qty:,} (PO) + {shortage_share:,} (shortage coverage) = {alloc:,}")
        lunar_alloc = uncommitted - total_shortage
        print(f"   Lunar: {lunar_alloc:,} (uncommitted - total shortage)")

    else:  # scenario 3
        remaining_uncommitted = uncommitted
        for cm, po_qty in cm_pos_by_cm.items():
            shortage = cm_shortages.get(cm, 0)
            if total_shortage > 0:
                shortage_share = shortage * (uncommitted / total_shortage)
            else:
                shortage_share = 0
            alloc = po_qty + shortage_share
            cm_allocations[cm] = alloc
            print(f"   {cm}: {po_qty:,} (PO) + {shortage_share:,.0f} (proportional) = {alloc:,.0f}")
        lunar_alloc = 0
        print(f"   Lunar: 0 (fully allocated proportionally)")

    # Step 7: Show depletion by month
    print(f"\n7. Monthly Depletion:")

    # Get receipt dates
    cm_orders_dated = cm_orders_part.copy()
    cm_orders_dated["eta"] = cm_orders_dated["receipt_date"].fillna(cm_orders_dated["ship_date"])
    cm_orders_dated["eta"] = pd.to_datetime(cm_orders_dated["eta"])
    cm_orders_dated["eta_month"] = cm_orders_dated["eta"].dt.to_period("M")

    pab_part["month"] = pab_part["period_date"].dt.to_period("M")
    months = sorted(pab_part["month"].unique())
    months_str = [str(m) for m in months]

    # For each CM
    for cm in sorted(cm_allocations.keys()):
        print(f"\n   {cm} row (allocated {cm_allocations[cm]:,.0f}):")
        cumulative_po = 0
        cumulative_shortage = 0

        for month in months:
            # PO receipts this month
            po_this_month = cm_orders_dated[
                (cm_orders_dated["cm_extracted"] == cm) &
                (cm_orders_dated["eta_month"] == month)
            ]["quantity_open"].sum()
            cumulative_po += po_this_month

            # Shortage coverage this month (abs of min PAB)
            pab_this_month = pab_part[
                (pab_part["cm"] == cm) &
                (pab_part["month"] <= month)
            ]["pab"].min()
            shortage_this_month = abs(pab_this_month) if pab_this_month and pab_this_month < 0 else 0
            cumulative_shortage = shortage_this_month  # Use the minimum (worst) value

            balance = cm_allocations[cm] - cumulative_po - cumulative_shortage
            print(f"      {month}: po_recv={po_this_month:,.0f}, cumul_po={cumulative_po:,.0f}, shortage={cumulative_shortage:,.0f}, balance={max(0, balance):,.0f}")

    # Lunar row
    print(f"\n   Lunar row (allocated {lunar_alloc:,.0f}):")

    lunar_oo = onorder[onorder["source_report"] == "Lunar Netsuite"]
    lunar_oo_part = lunar_oo[lunar_oo["lunar_lpn"] == part]
    lunar_oo_part["eta"] = lunar_oo_part["receipt_date"].fillna(lunar_oo_part["ship_date"])
    lunar_oo_part["eta"] = pd.to_datetime(lunar_oo_part["eta"])
    lunar_oo_part["eta_month"] = lunar_oo_part["eta"].dt.to_period("M")

    cumulative_lunar_replenish = 0
    for month in months:
        replenish_this_month = lunar_oo_part[
            (lunar_oo_part["eta_month"] == month)
        ]["quantity_open"].sum()
        cumulative_lunar_replenish += replenish_this_month

        balance = lunar_alloc + cumulative_lunar_replenish
        print(f"      {month}: replenish={replenish_this_month:,.0f}, cumul_replenish={cumulative_lunar_replenish:,.0f}, balance={balance:,.0f}")

print("\n" + "="*80)
