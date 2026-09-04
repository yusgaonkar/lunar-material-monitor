#!/usr/bin/env python3
"""Test just the unit pricing logic without running the engine."""

import pandas as pd
from src import io as lio

print("=" * 80)
print("UNIT PRICING ANALYSIS")
print("=" * 80)

files = lio.load_all()
onhand = files['onhand.csv']
onorder = files['onorder.csv']

# Get Lunar unrestricted inventory
lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()
lunar_unrestricted = lunar_oh.groupby("lpn").agg(
    unrestricted=("unrestricted_qty", "sum")
).rename_axis("part").reset_index()

# Get CM orders from Lunar
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
cm_orders_by_cm_part = cm_orders_lunar.groupby(["lunar_lpn"]).agg(
    cm_orders=("quantity_open", "sum")
).reset_index()
cm_orders_by_cm_part.columns = ["part", "cm_orders"]

# Calculate Lunar position
lunar_unrestricted = lunar_unrestricted.merge(cm_orders_by_cm_part, on="part", how="left")
lunar_unrestricted["cm_orders"] = lunar_unrestricted["cm_orders"].fillna(0)
lunar_unrestricted["uncommitted"] = (lunar_unrestricted["unrestricted"] - lunar_unrestricted["cm_orders"]).clip(lower=0)

print(f"\nLunar position:")
print(f"  Total unrestricted: {lunar_unrestricted['unrestricted'].sum():,.0f} units")
print(f"  Total CM orders: {lunar_unrestricted['cm_orders'].sum():,.0f} units")
print(f"  Total uncommitted: {lunar_unrestricted['uncommitted'].sum():,.0f} units")

# Method 1: Simple weighted average (all prices including zeros)
def weighted_price_all(group):
    if group['unrestricted_qty'].sum() > 0:
        return (group['unrestricted_qty'] * group['unit_price']).sum() / group['unrestricted_qty'].sum()
    return 0

lunar_prices_m1 = lunar_oh.groupby("lpn").apply(weighted_price_all, include_groups=False).reset_index()
lunar_prices_m1.columns = ["part", "price_m1"]

lunar_unrestricted = lunar_unrestricted.merge(lunar_prices_m1, on="part", how="left")
lunar_unrestricted["value_m1"] = lunar_unrestricted["uncommitted"] * lunar_unrestricted["price_m1"]

total_m1 = lunar_unrestricted["value_m1"].sum()

print(f"\nMethod 1 (weighted avg including zeros):")
print(f"  Total value: ${total_m1:,.2f}")

# Method 2: Weighted average excluding zero prices
def weighted_price_nonzero(group):
    non_zero = group[group['unit_price'] > 0]
    if len(non_zero) > 0:
        return (non_zero['unrestricted_qty'] * non_zero['unit_price']).sum() / non_zero['unrestricted_qty'].sum()
    else:
        if group['unrestricted_qty'].sum() > 0:
            return (group['unrestricted_qty'] * group['unit_price']).sum() / group['unrestricted_qty'].sum()
        return 0

lunar_prices_m2 = lunar_oh.groupby("lpn").apply(weighted_price_nonzero, include_groups=False).reset_index()
lunar_prices_m2.columns = ["part", "price_m2"]

lunar_unrestricted = lunar_unrestricted.merge(lunar_prices_m2, on="part", how="left")
lunar_unrestricted["value_m2"] = lunar_unrestricted["uncommitted"] * lunar_unrestricted["price_m2"]

total_m2 = lunar_unrestricted["value_m2"].sum()

print(f"\nMethod 2 (weighted avg excluding zeros first):")
print(f"  Total value: ${total_m2:,.2f}")

# Method 3: Maximum price per part
def max_price(group):
    return group['unit_price'].max()

lunar_prices_m3 = lunar_oh.groupby("lpn").apply(max_price, include_groups=False).reset_index()
lunar_prices_m3.columns = ["part", "price_m3"]

lunar_unrestricted = lunar_unrestricted.merge(lunar_prices_m3, on="part", how="left")
lunar_unrestricted["value_m3"] = lunar_unrestricted["uncommitted"] * lunar_unrestricted["price_m3"]

total_m3 = lunar_unrestricted["value_m3"].sum()

print(f"\nMethod 3 (maximum price per part):")
print(f"  Total value: ${total_m3:,.2f}")

# Expected value
expected = 26_775_885.06

print(f"\nExpected value: ${expected:,.2f}")
print(f"\nVariances:")
print(f"  Method 1 vs expected: ${total_m1 - expected:,.2f} ({(total_m1 - expected) / expected * 100:+.2f}%)")
print(f"  Method 2 vs expected: ${total_m2 - expected:,.2f} ({(total_m2 - expected) / expected * 100:+.2f}%)")
print(f"  Method 3 vs expected: ${total_m3 - expected:,.2f} ({(total_m3 - expected) / expected * 100:+.2f}%)")

# Check if parts with CM orders have different treatment
print(f"\n" + "=" * 80)
print("PARTS WITH CM ORDERS FROM LUNAR:")
print("=" * 80)

parts_with_orders = lunar_unrestricted[lunar_unrestricted["cm_orders"] > 0].copy()
print(f"\nParts with CM POs to Lunar: {len(parts_with_orders)}")

if len(parts_with_orders) > 0:
    print(f"Total CM orders allocated: {parts_with_orders['cm_orders'].sum():,.0f} units")
    print(f"Total uncommitted for these parts: {parts_with_orders['uncommitted'].sum():,.0f} units")
    print(f"\nTop 5 parts with CM orders:")
    top_order_parts = parts_with_orders.nlargest(5, 'cm_orders')
    for idx, (_, row) in enumerate(top_order_parts.iterrows(), 1):
        print(f"  {idx}. {row['part']}: {row['cm_orders']:>10,.0f} units ordered, {row['unrestricted']:>10,.0f} unrestricted")
