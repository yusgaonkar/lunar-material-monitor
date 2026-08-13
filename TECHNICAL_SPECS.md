# Lunar Material Planning Tool — Technical Specifications

## Overview

Component-level demand & supply planning engine for contract manufacturer (CM) procurement. Calculates projected available balance (PAB) at daily granularity, identifies shortages, and recommends inventory allocation from Lunar's uncommitted stock.

**Key metrics:** runout date, shortage quantity, build coverage, days of cover.

---

## 1. Grain: Daily (not weekly)

All calculations operate at **daily granularity** (389 working days in planning horizon). Demand, supply, and inventory balances are computed per day, then aggregated for reporting.

### Period definition
- `period` = date (YYYY-MM-DD)
- Horizon = `cfg.horizon_weeks × 7` days from min(demand, receipts) forward
- No calendar skipping; weekends included in calculations

### Aggregation for reporting
When collapsing to weekly or monthly grain:
- **Demand, receipts, net_flow:** `SUM` across the period
- **PAB (inventory):** `LAST` value (end-of-period balance only, never summed)
- **Period key:** Monday for Week, 1st for Month

---

## 2. Demand Calculation

### Gross demand
```
gross_demand[cm, part, period] 
    = SUM over products at cm of (build_plan_qty[product, period] × Sourcing_Flat_Qty[product, part])
```

**Sourcing_Flat_Qty:** Non-zero only at procurement boundary (what CM actually buys). Zeroes out make-levels and sub-components to prevent double-counting.

### PCBA pull-forward (28-day shift)
Parts with non-empty `Parent PCBA LPN` in BOM have demand shifted back 28 days, daily disaggregated and reaggregated:

```
1. Explode monthly build plan to daily
2. For each day's demand: shift period back 28 days
3. Reaggregate to shifted monthly buckets
4. Use Sourcing_Flat_Qty in calculation (not Flat_Qty)
```

**Per-product filtering:** Apply time-phasing ONLY to selected product's parts to prevent cross-product demand inflation.

### Net demand (backlog loading)
```
backlog[product] = plan_to_date_qty - (units_received + units_in_transit)
remaining_builds[product, period]:
    = forward_plan[product, period]
    + backlog  (loaded into period 0 only)
```

---

## 3. Supply Calculation

### Opening inventory
```
opening[cm, part] = cm_raw_inventory[cm, part] + wip_consumed[cm, part]
```

**CM raw inventory:** `unrestricted_qty` (CM-owned on-hand) from inventory master, summed across locations.

**WIP Consumed:** Cascades as `(parent's WIP Consumed + parent's CM Raw Inventory) × Usage Qty`, representing components embedded in higher-level assemblies at the CM.

### Scheduled receipts (dated supply)
```
scheduled_receipts[cm, part, period] = SUM(quantity_open) 
    WHERE eta falls in period AND eta IS NOT NULL
```

**ETA coalesce:** `COALESCE(receipt_date, ship_date)` per row (Sienna ships, Qualitel receipts are mutually exclusive by feed).

**Past-due handling:**
- Receipt date < snapshot → move to `cfg.snapshot` (today) for calculation
- Keep original date in `_original_eta` for display
- Flag as `is_past_due = True`; shown with "[Past Due]" note in reports
- **Included in supply**, not excluded

**Undated supply:** No ETA → not counted in balance, shown separately.

---

## 4. PAB (Projected Available Balance)

### Daily balance
```
PAB[period] = PAB[period-1] + receipts[period] - demand[period]
PAB[0] = opening_inventory

runout_period = first period where PAB < 0
shortage_qty = abs(min(PAB)) over horizon
days_of_cover = (first_shortage_date - snapshot).days
```

### Load-bearing assumption
Plan only at `Sourcing_Flat_Qty > 0` boundary. Components with `Sourcing_Flat_Qty = 0` carry zero demand (make-parts, not buy-parts).

### In-transit gap (conservative)
`Parent FG Built` = cumulative received (goods receipt), not shipment confirmation. Between ASN and receipt, TLA components sit unaccounted. 

Consequence: backlog overstates demand → engine over-demands → invents shortages conservatively rather than hiding them.

**Mitigation:** `data/in_transit.csv` supplies true in-flight population; flag parts where shortage clears when in-transit counted.

---

## 5. Coverage (Blocks Buildable)

### Calculation
```
blocks_buildable[cm, part] = floor(opening_inventory[cm, part] / total_usage[cm, part])
```

### Product filtering
`total_usage` sums across **products with actual demand in the build plan only** (not all products in BOM). 

Example: if part is used in 90-07675A (demand: Aug–Dec) and 90-08373A (demand: Feb–Mar) but only 90-07675A has current demand, `total_usage` = usage from 90-07675A only.

This reflects **temporal reality:** shared components have different coverage at different times as products are phased in/out.

---

## 6. Shortage Classification

Three states per part (CLAUDE.md 5.7):

| State | Meaning | Action |
|---|---|---|
| `IN_PRODUCTION` | Has on-hand inventory record | Plan normally |
| `ON_ORDER_ONLY` | No on-hand, open PO exists | Runout from PO dates; flag |
| `NOT_SOURCED` | No on-hand, no PO | NPI readiness gap (toggle to show) |

**Shortage type:** Determined by whether on-hand covers total future demand:
- "On-hand covers" — sufficient without PO
- "Incoming supply covers" — PO arrives before shortage
- "Supply gap" — on-hand insufficient, no dated supply before runout

---

## 7. Inventory Allocation Recommendation

### Lunar pool
```
allocatable[part] = SUM(uncommitted_qty) over Lunar-owned rows
```

Committed stock (allocated to CM open POs) excluded by construction.

### Recommendation
For shortages where `shortage_qty ≤ allocatable[part]`:
> "Have [CM] place a PO for [qty] units of [part] — [qty] uncommitted at Lunar."

Never auto-commit; show existing CM POs alongside recommendation.

### Double-count prevention
Per CLAUDE.md 5.2: Lunar stock allocated to CM already appears as CM open PO. Counting both inflates supply. **Rule:** CM runout counts CM on-hand + CM POs, never adds Lunar on-hand on top.

**Validation gate:**
```
SUM(unrestricted_qty − uncommitted_qty) over Lunar-owned, by part
  ≟  SUM(quantity_open) over CM POs where po_vendor = Lunar
```
Variance indicates missed PO, stale snapshot, or join issue.

---

## 8. Part Number Normalization

**Trust the clean column.** LunarDB has normalized `lpn` (On Hand) and `lunar_lpn` (On Order) on 99.7% of rows.

**Rule:** Use clean column; validate, never re-derive. Five Celestica rows carry non-Lunar numbers (3480-0329). Exclude, do not force-match.

**Upstream leaks (16 rows):** `10-005220_old`, `10-06527A-I` → strip suffix once at load. Assert count = 16; if grows, upstream rule changed.

---

## 9. Validation Gates

**Blocking:**
- Row counts match `EXPECTED_ROWS` (truncation guard)
- All products in Stitch List have BOM
- No products with `CM = TBD`
- No components with zero `Sourcing Flat Qty` across entire product
- No duplicate PO schedule lines (synthetic row key generated)
- Build plan references known products only
- Build plan starts at program start (cumulative burn-down depends on history)

**Warn but allow:**
- Committed vs CM-PO reconciliation variance > 0
- Undated supply lines (321 today)
- Past-due receipt dates
- Celestica rows (excluded by default)
- Parts on exclusion list
- Components where shortage clears if in-transit counted

---

## 10. Architecture

```
src/
  io.py             # Loaders, row-count assertions
  normalize.py      # PN normalization, CM alias map
  validate.py       # Validation gates → findings
  engine.py         # Pure pandas: demand, supply, runout. NO Streamlit.
  allocate.py       # Lunar allocation recommendations
tests/
  fixtures/         # PN test cases, golden scenarios
  test_engine.py
app_minimal.py      # Streamlit UI (thin wrapper)
```

**Non-negotiables:**
1. `engine.py` callable from plain script. No Streamlit imports.
2. **No LLM in arithmetic path.** Every number reproducible, traceable to row.
3. Every derived metric has drill-down. Keep grain (day/week/month) as metadata.

---

## 11. Data Flow

```
CSV imports
  ↓ (io.py)
Normalize: PN, CM alias, ETA coalesce
  ↓ (normalize.py)
Validate: row counts, part joins, business rules
  ↓ (validate.py)
Explode demand by product-component, apply PCBA pull-forward
  ↓ (engine.py explode_demand)
Calculate opening inventory: on-hand + WIP
  ↓ (engine.py opening_inventory)
Bucket receipts to daily periods (move past-due to today, flag)
  ↓ (engine.py scheduled_receipts)
Compute daily PAB, identify shortages
  ↓ (engine.py compute_runout)
Calculate blocks_buildable (product-filtered usage sum)
  ↓ (engine.py coverage)
Classify shortage types, add allocation recommendations
  ↓ (allocate.py + engine.py)
Result dict (demand, supply, receipts, PAB, summary) → UI
  ↓ (app_minimal.py)
Render reports: Shortage, Drill-down, Excess
  Grain toggle: aggregate daily to week/month (PAB uses LAST, others sum)
```

---

## 12. Key Differences from Weekly Baseline

| Aspect | Weekly | Daily |
|---|---|---|
| **Grain** | Monday-bucketed | Date |
| **Horizon** | ~13 weeks | ~389 days (same span) |
| **PCBA shift** | Weeks | 28 days (exact) |
| **PAB periods** | 13–20 | 389 |
| **Aggregation** | All sum | PAB uses LAST |
| **Past-due** | Excluded | Included (flagged) |
| **Coverage** | All products | Products with demand only |
| **Performance** | N/A | Caching + aggregation on-demand |

---

## 13. Reports & Outputs

### Shortage Report
Per-part row: CM | Part | Description | Products (with usage) | Inventory | Build Coverage | First Short Date | Shortage Type | Incoming Supply (original dates, [Past Due] flagged, red highlight).

### Drill-Down (Demand/Supply/Inventory)
3-row grid per part:
- Row 1: Demand (daily or bucketed)
- Row 2: Supply (receipts by date, undated separate)
- Row 3: PAB (color-coded: green positive, red negative)

**Grain toggle:** Day, Week, Month. Demand/Supply sum; PAB shows end-of-period.

### Excess Monitor
Parts with PAB > demand for full horizon. Quantity in excess. Recommend reduce, consume, or sell.

---

## Last Updated
August 13, 2026

**Core engine:** v2 (daily grain, past-due included, product-filtered coverage)
