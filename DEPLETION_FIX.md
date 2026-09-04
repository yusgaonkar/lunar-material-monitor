# CM Depletion Calculation Fix

## Problem
The Lunar inventory depletion columns were showing zero for all parts instead of displaying the actual Lunar inventory balance by month for each CM.

## Root Cause
The `lunar_depletion_temp` DataFrame was being calculated correctly but was **never being merged into the final `balance_table`**. This caused all the Lunar depletion data to be lost before the results were displayed.

## Solution
Added code to merge `lunar_depletion_temp` into `balance_table` after line 2439 in `app_minimal.py`:

```python
# Add Lunar depletion columns
# Rename month columns to Lunar_XXXX format to distinguish from CM PAB columns
if len(lunar_depletion_temp) > 0:
    lunar_depletion_renamed = lunar_depletion_temp.copy()
    month_cols = [col for col in lunar_depletion_renamed.columns if col not in ["cm", "part"]]
    for col in month_cols:
        lunar_depletion_renamed[f"Lunar_{col}"] = lunar_depletion_renamed[col]
        lunar_depletion_renamed = lunar_depletion_renamed.drop(columns=[col])

    balance_table = balance_table.merge(lunar_depletion_renamed, on=["cm", "part"], how="left")
```

## Key Changes
1. **Skipped "Lunar" row** in the depletion calculation (line 2183) - we only track depletion for real CMs
2. **Renamed month columns** with "Lunar_" prefix to distinguish them from CM PAB columns
3. **Merged** `lunar_depletion_temp` into `balance_table` so the data is included in the output

## How It Works
The Lunar depletion calculation tracks:
- **CM consumption**: Quantities that CMs are ordering from Lunar (from on-order with po_vendor="Lunar")
- **Lunar supply**: Quantities that Lunar receives from vendors (Lunar Netsuite on-order)
- **Net balance**: `opening_inventory + cumulative_lunar_receipts - cumulative_cm_consumed`

For each month, if Lunar receives exactly what the CMs consume, the balance stays constant. If CMs consume more than Lunar receives, the balance declines (showing a depletion).

## Verification
The calculation was tested with debug output and confirmed to be producing correct values:
- Part 10-000099 at Sienna GA starts with 1,039,115 units
- Oct 2026: CM orders 308,000, Lunar receives 308,000 → balance stays 1,039,115
- Nov 2026: CM orders 320,000, Lunar receives 320,000 → balance stays 1,039,115

This is correct behavior - Lunar inventory is being replenished as CMs consume it.
