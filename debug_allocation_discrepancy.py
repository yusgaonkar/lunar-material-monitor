#!/usr/bin/env python3
"""Debug the allocation discrepancy."""

import pandas as pd
from src import io as lio

print("=" * 80)
print("DEBUGGING ALLOCATION DISCREPANCY")
print("=" * 80)

files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Expected total from source
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()
expected_total = lunar_oh['unrestricted_value'].sum()
print(f"\nExpected total (from source unrestricted_value): ${expected_total:,.2f}")

# Get source unit prices
source_by_part = lunar_oh.groupby('lpn').agg({
    'unrestricted_qty': 'sum',
    'unrestricted_value': 'sum',
    'unit_price': lambda x: (x * lunar_oh.loc[x.index, 'unrestricted_qty']).sum() / lunar_oh.loc[x.index, 'unrestricted_qty'].sum()
}).rename_axis('part').reset_index()
source_by_part.columns = ['part', 'unrestricted_qty', 'unrestricted_value', 'weighted_unit_price']

print(f"\nSource data by part: {len(source_by_part)} parts")

# Get CM orders to Lunar
cm_orders_to_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_to_lunar["cm"] = cm_orders_to_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

cm_orders_by_cm_part = cm_orders_to_lunar.groupby(["cm", "lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

# Merge to get unit prices for Stage 1 allocations
cm_orders_with_price = cm_orders_by_cm_part.merge(source_by_part[['part', 'weighted_unit_price']], on='part', how='left')
cm_orders_with_price['allocation_value'] = cm_orders_with_price['cm_orders'] * cm_orders_with_price['weighted_unit_price']

# Calculate what should happen
print(f"\n" + "=" * 80)
print("EXPECTED ALLOCATION BREAKDOWN:")
print("=" * 80)

# Total CM Stage 1 allocation
total_cm_stage1_qty = cm_orders_with_price['cm_orders'].sum()
total_cm_stage1_value = cm_orders_with_price['allocation_value'].sum()

print(f"\nCM Stage 1 allocation:")
print(f"  Total qty: {total_cm_stage1_qty:,.0f} units")
print(f"  Total value: ${total_cm_stage1_value:,.2f}")

# What should Lunar row have?
lunar_row_expected = expected_total - total_cm_stage1_value

print(f"\nLunar row should have:")
print(f"  Expected total - CM Stage 1: ${expected_total:,.2f} - ${total_cm_stage1_value:,.2f}")
print(f"  = ${lunar_row_expected:,.2f}")

# Top discrepancies
print(f"\n" + "=" * 80)
print("TOP 10 CM STAGE 1 ALLOCATIONS (by value):")
print("=" * 80)

top_allocations = cm_orders_with_price.nlargest(10, 'allocation_value')[
    ['cm', 'part', 'cm_orders', 'weighted_unit_price', 'allocation_value']
]

for idx, (_, row) in enumerate(top_allocations.iterrows(), 1):
    print(f"\n{idx}. {row['part']} @ {row['cm']}")
    print(f"   Qty: {row['cm_orders']:>12,.0f}")
    print(f"   Price: ${row['weighted_unit_price']:>12,.2f}")
    print(f"   Value: ${row['allocation_value']:>12,.2f}")

# Check for parts that appear multiple times in Stage 1 (potential double-counting)
print(f"\n" + "=" * 80)
print("PARTS WITH MULTIPLE CM STAGE 1 ORDERS:")
print("=" * 80)

parts_with_multiple = cm_orders_by_cm_part.groupby('part').size()
parts_with_multiple = parts_with_multiple[parts_with_multiple > 1].sort_values(ascending=False)

if len(parts_with_multiple) > 0:
    print(f"\nFound {len(parts_with_multiple)} parts with multiple CMs ordering them:")
    for part, count in parts_with_multiple.head(10).items():
        part_data = cm_orders_by_cm_part[cm_orders_by_cm_part['part'] == part]
        part_price = source_by_part[source_by_part['part'] == part]['weighted_unit_price'].values
        price = part_price[0] if len(part_price) > 0 else 0

        print(f"\n  {part} ({count} CMs):")
        for _, row in part_data.iterrows():
            value = row['cm_orders'] * price
            print(f"    {row['cm']}: {row['cm_orders']:>10,.0f} units = ${value:>12,.2f}")

        total_qty = part_data['cm_orders'].sum()
        total_value = total_qty * price
        print(f"    TOTAL: {total_qty:>10,.0f} units = ${total_value:>12,.2f}")
else:
    print("\nNo parts with multiple CM orders (each part ordered by at most one CM)")

print("\n" + "=" * 80)
