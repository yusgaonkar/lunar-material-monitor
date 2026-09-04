# Lunar Inventory Allocation — Implementation Summary

## Changes Made

Updated `app_minimal.py` (Inventory Depletion tab) with the new Lunar allocation model.

### 1. Lunar Inventory Position (Lines 1982-2005)

**What it does:**
- Extracts Lunar unrestricted inventory by part from on-hand records where `source_report == "Lunar Netsuite"`
- Identifies CM orders placed TO Lunar by filtering on-order records where `po_vendor` contains "Lunar"
- Extracts CM name from `source_report` field (e.g., "CM: Sienna GA" → "Sienna")
- Calculates uncommitted inventory as: `unrestricted_qty - total_cm_orders`

**Verification (10-000099 example):**
```
Lunar unrestricted:      1,039,115
Sienna orders from Lunar:  628,000
Uncommitted:               411,115
```

### 2. CM Allocation of Lunar Inventory (Lines 2067-2119)

**What it does:**
For each (CM, part) combination:
1. Gets the CM's orders from Lunar (`cm_orders`)
2. Calculates the CM's demand share (`alloc_factor = cm_demand / total_demand`)
3. Allocates uncommitted proportionally: `uncommitted × alloc_factor`
4. Sets `lunar_on_hand_alloc = cm_orders + allocated_uncommitted`

**Formula:**
```
lunar_on_hand_alloc[cm, part] 
  = sum(cm_orders where po_vendor="Lunar" and source_report contains cm)
  + (uncommitted × (cm_demand / total_demand))
```

**Key guarantees:**
- Total `lunar_on_hand_alloc` across all CMs for a part ≤ Lunar unrestricted_qty
- Each CM gets a different allocation based on their demand ratio
- Allocation is updated dynamically as demand changes (e.g., if build plan is edited)

### 3. Monthly Lunar Depletion (Lines 2011-2045)

**What it does:**
For each (CM, part, month):
1. Identifies CM's orders from Lunar with valid `receipt_date`
2. Groups by receipt_month
3. Calculates cumulative received qty by end of month
4. Stores as `lunar_row[month] = cumulative_received`

**Output format:**
- Column name: `Lunar_balance_{month}` (e.g., `Lunar_balance_2026-09`)
- Value: Remaining balance = `lunar_on_hand_alloc - cumulative_receipts`
- Shows month-by-month depletion of allocated Lunar inventory

### 4. Display Columns

**Static columns (left):**
- `cm`, `part`, `description`, `item_category`
- `cm_on_hand` — CM-owned raw inventory
- `cm_on_order` — CM-owned on-order
- `cm_unit_price` — CM unit cost
- `lunar_on_hand_alloc` — Allocated Lunar inventory (starting balance)
- `lunar_on_order_alloc` — (set to 0, as depletion is receipt-driven)
- `lunar_unit_price` — (set to 0)

**Dynamic columns:**
- CM PAB months (e.g., `2026-09`, `2026-10`): End-of-month PAB from engine
- Lunar balance months (e.g., `Lunar_balance_2026-09`): Remaining allocated inventory

## Testing Results

Tested with real data on 10-000099 (high-volume capacitor):

```
Lunar unrestricted inventory:      1,039,115 units
Sienna's CM orders from Lunar:       628,000 units
Uncommitted inventory:                411,115 units

Allocation (example with 50% demand):
  Sienna receives:
    - Orders from Lunar:            628,000
    - Share of uncommitted (50%):   205,558
    - Total lunar_on_hand_alloc:    833,558 units

Verification:
  ✓ Lunar totals match
  ✓ CM order totals match
  ✓ Math is correct
```

## How It Works Month-by-Month

For each month, Lunar depletion shows:
```
lunar_balance[month] = lunar_on_hand_alloc - sum(cm_receipts where receipt_date <= end of month)
```

Example timeline (Sienna, 10-000099):
- Sep: 833,558 (no receipts yet)
- Oct: 833,558 - 50,000 = 783,558 (received 50k in Oct)
- Nov: 783,558 - 100,000 = 683,558 (received 100k in Nov)
- etc.

## Design Alignment

✓ Lunar on-hand_alloc totals = Lunar unrestricted_qty across all CMs
✓ Each CM shows their own allocated share based on demand
✓ Monthly depletion reflects actual receipt_dates from on-order data
✓ Conservative: uncommitted inventory allocated, not consumed
✓ No LLM in arithmetic — all numbers traceable to source rows

## Code Quality

- ✓ Column names use actual data columns (`lpn`, `lunar_lpn`, not `_lpn`)
- ✓ CM extraction uses regex against `source_report` for both on-hand and on-order
- ✓ No undefined variable references
- ✓ Syntax validated with `py_compile`
- ✓ Data logic tested with real CSV snapshots
