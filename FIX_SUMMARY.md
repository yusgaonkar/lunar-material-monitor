# CM Depletion Fix - Summary

## Problem
All CM PAB (Position Available to Build) columns were showing zero values, making the entire Inventory Depletion report useless.

## Root Cause
**CM name mismatch** between inventory data and engine PAB data:
- Inventory source_report had full CM names: `"Sienna GA"`, `"Qualitel WA"`
- Engine PAB had short CM names: `"Sienna"`, `"Qualitel"`
- The merge on `["cm", "part"]` found zero matches → all PAB values were NaN → filled with 0

## Solution Implemented

### 1. CM Name Normalization (Lines 2041-2050)
Added a mapping to convert inventory CM names to engine CM names:
```python
cm_name_map = {
    "Sienna GA": "Sienna",
    "Qualitel WA": "Qualitel",
    "Plexus": "Plexus",
    "Unigen": "Unigen",
    "Celestica MX": "Celestica",
    "Lunar": "Lunar"
}
```

### 2. Apply Mapping Before Merge (Line 2120)
Before merging `base_table` with `cm_pab`:
```python
base_table["cm"] = base_table["cm"].map(cm_name_map).fillna(base_table["cm"])
```

This ensures CM names match between inventory and engine PAB.

## What You'll See in Inventory Depletion Tab

### Regular Month Columns (e.g., "2026-07", "2026-08")
**CM PAB** - Position Available to Build for each CM
- Positive = surplus (can build more units)
- Negative = shortage (need more supply)
- Zero = exact match (demand = supply)

### Lunar_balance_XXXX-XX Columns (e.g., "Lunar_balance_2026-07")
**Lunar Allocated Balance** - How much allocated Lunar inventory is left for this CM after depletion
- Shows the operational Lunar inventory balance
- Floored at 0 (can't go negative in reporting)
- Reflects: `allocated_lunar_qty - (shortfalls_covered + orders_consumed)`

## Verification
To verify the fix works:
1. Run the app and go to "Inventory Depletion" tab
2. Filter for part **10-000551**
3. Check that PAB month columns show **non-zero values** (not all zeros)
4. Verify different CMs show different PAB values
5. Lunar_balance_XXXX-XX columns should also show actual values

## Files Modified
- `app_minimal.py`: Added CM name normalization before PAB merge
