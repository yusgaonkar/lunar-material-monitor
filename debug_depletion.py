#!/usr/bin/env python3

import sys
sys.path.insert(0, '/Users/yashusgaonkar/lunar-planner')

import pandas as pd
from src import io as lio

# Load the data
print("Loading data...")
frames = lio.load_all()
onhand = frames["onhand.csv"]
onorder = frames["onorder.csv"]

print(f"\nonhand shape: {onhand.shape}")
print(f"onorder shape: {onorder.shape}")

# Reproduce the depletion calculation
print("\n=== Depletion Calculation Debug ===\n")

# Step 1: Prepare CM orders from Lunar
print("Step 1: Extract CM orders where vendor is Lunar")
cm_orders_lunar = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
print(f"cm_orders_lunar shape: {cm_orders_lunar.shape}")
print(f"cm_orders_lunar columns: {cm_orders_lunar.columns.tolist()}")

# Add CM extraction
cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()
print(f"\nCMs in cm_orders_lunar: {cm_orders_lunar['cm_extracted'].unique()}")
print(f"cm_extracted nulls: {cm_orders_lunar['cm_extracted'].isna().sum()}")

# Step 2: Prepare dated orders
print("\n\nStep 2: Add dates to CM orders")
cm_orders_lunar["eta"] = cm_orders_lunar["receipt_date"].fillna(cm_orders_lunar["ship_date"])
print(f"Rows with eta: {cm_orders_lunar['eta'].notna().sum()}")

cm_orders_lunar_dated = cm_orders_lunar[cm_orders_lunar["eta"].notna()].copy()
print(f"cm_orders_lunar_dated shape after date filter: {cm_orders_lunar_dated.shape}")

if len(cm_orders_lunar_dated) > 0:
    cm_orders_lunar_dated["eta"] = pd.to_datetime(cm_orders_lunar_dated["eta"])
    cm_orders_lunar_dated["eta_month"] = cm_orders_lunar_dated["eta"].dt.to_period("M")
    print(f"Date range: {cm_orders_lunar_dated['eta'].min()} to {cm_orders_lunar_dated['eta'].max()}")
    print(f"Months: {sorted(cm_orders_lunar_dated['eta_month'].unique())}")

# Step 3: Check Lunar's own on-order
print("\n\nStep 3: Lunar's own on-order")
lunar_oo = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
print(f"lunar_oo shape: {lunar_oo.shape}")

lunar_oo_dated = lunar_oo[lunar_oo["receipt_date"].notna() | lunar_oo["ship_date"].notna()].copy()
print(f"lunar_oo_dated after date filter: {lunar_oo_dated.shape}")

if len(lunar_oo_dated) > 0:
    lunar_oo_dated["eta"] = lunar_oo_dated["receipt_date"].fillna(lunar_oo_dated["ship_date"])
    lunar_oo_dated["eta"] = pd.to_datetime(lunar_oo_dated["eta"])
    lunar_oo_dated["eta_month"] = lunar_oo_dated["eta"].dt.to_period("M")
    print(f"Lunar purchase order months: {sorted(lunar_oo_dated['eta_month'].unique())}")

# Step 4: Sample lookup for a specific part
if len(cm_orders_lunar_dated) > 0:
    print("\n\nStep 4: Sample lookup")
    sample_cm = "Sienna GA"
    sample_part = "10-000099"

    sample_rows = cm_orders_lunar_dated[
        (cm_orders_lunar_dated["cm_extracted"] == sample_cm) &
        (cm_orders_lunar_dated["lunar_lpn"] == sample_part)
    ]
    print(f"Rows for ({sample_cm}, {sample_part}): {len(sample_rows)}")
    if len(sample_rows) > 0:
        print(sample_rows[["cm_extracted", "lunar_lpn", "eta", "eta_month", "quantity_open"]].head())

# Step 5: Check what months are in the PAB calculation
print("\n\nStep 5: Check month string format")
# The app converts Period objects to strings using astype(str)
# So '2026-02' becomes the string '2026-02'
sample_month_period = cm_orders_lunar_dated["eta_month"].iloc[0]
sample_month_str = str(sample_month_period)
print(f"Sample month as Period: {sample_month_period} (type: {type(sample_month_period)})")
print(f"Sample month as string: {sample_month_str} (type: {type(sample_month_str)})")

# Try converting back
month_from_str = pd.Period(sample_month_str, freq="M")
print(f"Converting '{sample_month_str}' back to Period: {month_from_str}")
print(f"Do they match? {month_from_str == sample_month_period}")

# Step 6: Check lunar_start - Lunar unrestricted inventory by part
print("\n\nStep 6: Lunar unrestricted inventory")
lunar_onhand = onhand[onhand["source_report"] == "Lunar Netsuite"]
print(f"Lunar onhand rows: {len(lunar_onhand)}")

lunar_unrestricted = lunar_onhand.groupby("lpn").agg(unrestricted=("unrestricted_qty", "sum")).reset_index()
lunar_unrestricted.columns = ["part", "unrestricted"]
print(f"Parts with Lunar inventory: {len(lunar_unrestricted)}")
print(f"Sample: {lunar_unrestricted.head(3)}")

# Step 7: Check the CM order lookup issue
print("\n\nStep 7: Check if CM extraction from split matches CM extraction from regex")
# Get a sample source_report from onorder where po_vendor is Lunar
sample_oo_row = onorder[onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].iloc[0]
print(f"Sample source_report: '{sample_oo_row['source_report']}'")

# Test both extraction methods
source = sample_oo_row["source_report"]
split_result = source.split(":")[1].strip() if ":" in source else None
regex_result = pd.Series([source]).str.extract(r"CM:\s*(.+)$", expand=False).str.strip().values[0]
print(f"Split method: '{split_result}'")
print(f"Regex method: '{regex_result}'")
print(f"Match? {split_result == regex_result}")

# Step 7.5: Check if cm_orders_lunar and lunar_oo overlap
print("\n\nStep 7.5: Check for overlap between cm_orders_lunar and lunar_oo")
lunar_oo_test = onorder[onorder["source_report"] == "Lunar Netsuite"].copy()
print(f"lunar_oo (Lunar Netsuite only): {len(lunar_oo_test)} rows")
print(f"cm_orders_lunar (po_vendor contains Lunar): {len(cm_orders_lunar)} rows")

# Check overlap
overlap = pd.merge(
    cm_orders_lunar[["po_number", "po_line_item", "lunar_lpn"]],
    lunar_oo_test[["po_number", "po_line_item", "lunar_lpn"]],
    on=["po_number", "po_line_item", "lunar_lpn"],
    how="inner"
)
print(f"Rows that appear in both: {len(overlap)}")

# Check what source_reports are in cm_orders_lunar
print(f"\nSource reports in cm_orders_lunar:")
print(cm_orders_lunar["source_report"].value_counts())

# Check what po_vendors are in lunar_oo
print(f"\nPO vendors in lunar_oo (Lunar Netsuite source):")
print(lunar_oo_test["po_vendor"].value_counts().head(10))

# Step 8: Simulate the depletion calculation for one (cm, part) pair
print("\n\nStep 8: Simulate depletion calculation")

# For Sienna GA, 10-000099
test_cm = "Sienna GA"
test_part = "10-000099"

print(f"Testing depletion for ({test_cm}, {test_part}):")

# Get lunar starting inventory
lunar_data_part = lunar_unrestricted[lunar_unrestricted["part"] == test_part]
if len(lunar_data_part) > 0:
    lunar_unrestricted_val = lunar_data_part["unrestricted"].values[0]
    print(f"  Lunar unrestricted: {lunar_unrestricted_val}")
else:
    print(f"  Lunar unrestricted: NOT FOUND (0)")
    lunar_unrestricted_val = 0

# Get all months from the CM orders
months = sorted(cm_orders_lunar_dated["eta_month"].unique())
print(f"  Months: {months}")

cumulative_cm_consumed = 0
cumulative_lunar_received = 0

for month in months:
    month_period = pd.Period(month, freq="M")

    # Find CM consumption for this month
    month_cm_receipts = cm_orders_lunar_dated[
        (cm_orders_lunar_dated["cm_extracted"] == test_cm) &
        (cm_orders_lunar_dated["lunar_lpn"] == test_part) &
        (cm_orders_lunar_dated["eta_month"] == month_period)
    ]
    cm_month_qty = month_cm_receipts["quantity_open"].sum()
    cumulative_cm_consumed += cm_month_qty

    # Find Lunar supply for this month
    lunar_receipts = lunar_oo_dated[
        (lunar_oo_dated["lunar_lpn"] == test_part) &
        (lunar_oo_dated["eta_month"] == month_period)
    ]
    lunar_month_qty = lunar_receipts["quantity_open"].sum()
    cumulative_lunar_received += lunar_month_qty

    # Calculate balance
    net_balance = lunar_unrestricted_val + cumulative_lunar_received - cumulative_cm_consumed

    print(f"  {month}: CM qty={cm_month_qty}, Lunar qty={lunar_month_qty}, balance={net_balance}")

# Step 9: Check what's in lunar_oo_dated for this part
print("\n\nStep 9: Check lunar_oo_dated for 10-000099")
lunar_oo_for_part = lunar_oo_dated[lunar_oo_dated["lunar_lpn"] == test_part]
print(f"Rows for 10-000099 in lunar_oo_dated: {len(lunar_oo_for_part)}")
if len(lunar_oo_for_part) > 0:
    print(lunar_oo_for_part[["lunar_lpn", "eta", "eta_month", "quantity_open", "po_vendor"]].head())

# Step 10: Check what Lunar depletion rows get created
print("\n\nStep 10: Show what lunar_depletion_rows would look like for a few (cm, part) pairs")
from src import io as lio

# We need to reconstruct what cm_pab_full would look like
# For now, just show what would be added to lunar_depletion_rows for (Sienna GA, 10-000099)
lunar_row = {"cm": "Sienna GA", "part": "10-000099"}
for month in months:
    month_str = str(month)  # Convert Period to string like "2026-02"
    # This is what would be in the month columns of cm_pab_full
    lunar_row[month_str] = 1039115.0  # The balance we calculated

print(f"Sample lunar_row for (Sienna GA, 10-000099): {lunar_row}")

print("\n\nDone!")
