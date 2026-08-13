# Lunar Material Planning Tool — Complete Code Reference

**Purpose:** Self-contained reference. Paste entire file into Claude + your code questions.

**Last Updated:** August 14, 2026

---

## Quick Start for Asking Claude

Copy this entire file, paste into Claude, then ask:
- "Why does part 10-005080 show under both CMs?"
- "How does PAB calculation work?"
- "Are past-due receipts counted in the shortage?"

Claude can search the actual code and answer with line numbers.

---

## Architecture

```
lunar-planner/
├── src/
│   ├── engine.py              # Core calculations (pure pandas)
│   ├── io.py                  # CSV loaders, row-count assertions
│   ├── normalize.py           # Part number normalization
│   └── validate.py            # Validation gates
├── app_minimal.py             # Streamlit UI
└── data/                      # CSV inputs (gitignored)
```

**Non-negotiables:**
1. `engine.py` imports nothing from Streamlit
2. No LLM in arithmetic — every number traces to source rows
3. Every metric has drill-down, grain (day/week/month) is metadata

---

## Core Model

**Demand** = builds not yet delivered
**Supply** = material in pipeline  
Both as of snapshot date

```
PAB[t] = PAB[t-1] + receipts[t] - demand[t]
PAB[0] = opening_inventory
runout = first period where PAB < 0
```

### Key Concepts

- **Daily grain:** 389 daily periods (vs 13 weekly). Aggregate for reporting: PAB uses LAST, demand/supply use SUM
- **PCBA pull-forward:** Parts with Parent PCBA LPN shifted back 28 days for procurement lead time
- **Past-due receipts:** Included in supply, flagged is_past_due=True, displayed with original dates + [Past Due]
- **Product-filtered coverage:** blocks_buildable counts only usage from products with actual demand
- **Three part states:** IN_PRODUCTION, ON_ORDER_ONLY, NOT_SOURCED (NPI gap, never shortage)

---

## SOURCE CODE REFERENCE

### src/io.py (Loaders)

```python
"""Loaders with row-count truncation guard.

Part numbers load as str (never inferred). Dates parsed per column:
- "Updated at" = MM-DD-YYYY (US)  
- Everything else = YYYY-MM-DD (ISO)

Every load asserts row count. This is the truncation guard.
keep_default_na=False is deliberate: pandas' NA list contains literal 'NA',
and makebuy uses 'NA' as one of three not-applicable encodings.
"""

EXPECTED_ROWS = {
    "bom_stitched.csv": 4398,      # 4,397 data rows after dropping sentinel
    "bom_flat.csv": 3619,
    "stitch_list.csv": 18,          # Product master
    "onhand.csv": 4770,             # Inventory on-hand (join key: lpn)
    "onorder.csv": 6075,            # Open POs (join key: lunar_lpn)
}

def assert_rows(name: str, n: int) -> None:
    """Raises RowCountMismatch if export is truncated."""
    expected = EXPECTED_ROWS.get(name)
    if n != expected:
        raise RowCountMismatch(
            f"{name}: got {n:,} rows, expected {expected:,}. "
            f"Re-export via File > Download > CSV."
        )

def load_bom_stitched() -> pd.DataFrame:
    """4,397 data rows (raw export 4,398 minus sentinel).
    
    `item_number` is NOT unique — 11 items sit at multiple BOM positions.
    Key on (Parent Product LPN, guid) or aggregate.
    """
    df = _read_export("bom_stitched.csv")
    sentinel = (
        (df["item_number"] == "Custom Parent List") &
        (df["level"] == 0) &
        (df["Parent Product LPN"] == "")
    )
    return df[~sentinel].reset_index(drop=True)

def load_onhand() -> pd.DataFrame:
    """19 cols. Join key: lpn (clean part number)."""
    return _read_export("onhand.csv")

def load_onorder() -> pd.DataFrame:
    """36 cols. Join key: lunar_lpn (clean part number).
    
    (po_number, po_line_item) NOT unique — generate synthetic row key.
    ship_date and receipt_date mutually exclusive by feed.
    """
    return _read_export("onorder.csv")
```

### src/engine.py (Core Calculations)

```python
"""Demand explosion, netting, runout. Pipeline-balance model.

Pure pandas, no Streamlit. No LLM anywhere.

Model: demand = builds not yet delivered, supply = material in pipeline,
both as of snapshot date.
"""

@dataclass
class Config:
    """Run options. Everything UI toggles lives here."""
    snapshot: pd.Timestamp
    horizon_weeks: int = 52
    include_celestica: bool = False
    include_npi: bool = False
    use_on_order: bool = True
    count_in_transit: bool = True
    excluded_parts: frozenset = field(default_factory=frozenset)

IN_PRODUCTION = "IN_PRODUCTION"      # Has on-hand inventory
ON_ORDER_ONLY = "ON_ORDER_ONLY"      # No on-hand, open PO exists
NOT_SOURCED = "NOT_SOURCED"          # No on-hand, no PO (NPI gap)

def remaining_builds(build_plan, plan_to_date, in_transit, products, cfg):
    """Forward plan spread across daily working days, plus backlog.
    
    Monthly qty spread evenly across working days (Mon-Fri) in month.
    Backlog = plan_to_date_qty - (units_received + in_transit),
    loaded into period 0 (snapshot date).
    
    Returns (remaining, backlog_detail) at daily grain.
    """
    # Spread monthly qty across working days
    for row in bp.itertuples():
        month_end = (row.period_start + pd.offsets.MonthEnd(0)).normalize()
        all_days = pd.date_range(count_start, month_end, freq="D")
        working_days_count = (all_days.weekday < 5).sum()
        daily_rate = row.qty / working_days_count
        # Allocate daily_rate to each working day

def explode_demand(remaining, usage, products):
    """Component demand by (cm, part, period) and per-product split.
    
    Gross demand = SUM over products at CM of (build_plan_qty × Sourcing_Flat_Qty)
    
    The per-product split is critical. A shared component's shortage is caused
    by TOTAL demand; showing only one product makes a starved part look healthy.
    """
    detail = (
        remaining.merge(usage, on="product", how="inner")
        .merge(products[["product", "cm", "alias"]], on="product", how="left")
    )
    detail["demand"] = detail["qty"] * detail["usage"]
    total = detail.groupby(["cm", "part", "period"], as_index=False)["demand"].sum()
    return total, detail

def add_eta(oo_norm):
    """eta = COALESCE(receipt_date, ship_date), with eta_source.
    
    Sienna populates ship_date (optimistic, excludes transit time).
    Qualitel populates receipt_date. Dated coverage: 75% → 95%.
    """
    rd = pd.to_datetime(oo_norm["receipt_date"], errors="coerce")
    sd = pd.to_datetime(oo_norm["ship_date"], errors="coerce")
    out["_eta"] = rd.fillna(sd)
    out["_eta_source"] = np.where(rd.notna(), "receipt_date",
                                   np.where(sd.notna(), "ship_date", "none"))
    return out

def scheduled_receipts(oo_eta, cfg):
    """Open CM POs bucketed by eta. Returns (receipts, past_due_summary, undated).
    
    Three states:
    - Dated (ETA >= snapshot): aggregated by (cm, part, period)
    - Past-due (ETA < snapshot): moved to today for calc, flagged is_past_due=True,
                                  original date preserved for display
    - Undated (no ETA): not counted in balance, shown separately
    
    CHANGE: Daily bucketing. Past-due INCLUDED in supply (conservative).
    """
    past = dated[dated["_eta"] < cfg.snapshot].copy()
    future = dated[dated["_eta"] >= cfg.snapshot].copy()
    
    # Past-due: keep as individual lines, use today for calculation
    past["period"] = cfg.snapshot.normalize()
    past["_original_eta"] = past["_eta"]
    past["is_past_due"] = True
    
    # Future: aggregate by (cm, part, period)
    future["period"] = future["_eta"].dt.normalize()
    future["is_past_due"] = False
    
    return (receipts_df, past_due_summary, undated)

def wip_supply(bom, products):
    """WIP Consumed per (cm, part) — component embedded in higher assemblies.
    
    Supply, not negative demand. WIP = material CM physically holds,
    just built into something.
    """
    b = bom[["Parent Product LPN", "item_number", "WIP Consumed", "Sourcing Flat Qty"]].copy()
    for c in ("WIP Consumed", "Sourcing Flat Qty"):
        b[c] = pd.to_numeric(b[c], errors="coerce").fillna(0.0)
    b = b[b["Sourcing Flat Qty"] > 0]
    b = b.rename(columns={"Parent Product LPN": "product", "item_number": "part"})
    b = b.merge(products[["product", "cm"]], on="product", how="left")
    return b.groupby(["cm", "part"], as_index=False)["WIP Consumed"].sum()

def opening_inventory(cm_avail, wip, wip_fg=None):
    """CM-owned raw on-hand + WIP sub-assy + WIP FG.
    
    opening = cm_available + wip_consumed + wip_fg
    
    Lunar-owned stock NOT added: already appears as CM open PO
    (prevents double-count, CLAUDE.md 5.2).
    """
    opening = cm_avail.merge(wip, on=["cm", "part"], how="outer")
    opening[["cm_available", "wip"]] = opening[["cm_available", "wip"]].fillna(0.0)
    opening["opening"] = opening["cm_available"] + opening["wip"]
    return opening, negatives

def compute_runout(demand, opening, receipts, cfg):
    """Daily PAB calculation.
    
    PAB[t] = PAB[t-1] + receipts[t] - demand[t]
    PAB[0] = opening_inventory
    
    Returns (pab_grid, summary): pab is 389 daily periods; summary is 1 row per (cm, part).
    
    CHANGE: Computing daily (389 periods) not weekly (13 periods).
    """
    all_periods = [min_period + pd.Timedelta(days=i) for i in range(total_days)]
    periods = pd.DataFrame({"period": all_periods})
    
    keys = demand[["cm", "part"]].drop_duplicates()
    grid = keys.merge(periods, how="cross")
    grid = grid.merge(demand, on=["cm", "part", "period"], how="left")
    grid = grid.merge(receipts[["cm", "part", "period", "receipts"]], ...)
    grid = grid.merge(opening[["cm", "part", "opening"]], ...)
    
    grid[["demand", "receipts", "opening"]] = grid[...].fillna(0.0)
    grid = grid.sort_values(["cm", "part", "period"]).reset_index(drop=True)
    
    grid["net_flow"] = grid["receipts"] - grid["demand"]
    # Cumulative net flow per (cm, part)
    grid["pab"] = grid["opening"] + grid.groupby(
        ["cm", "part"], sort=False)["net_flow"].cumsum()
    
    short = grid[grid["pab"] < 0]
    first_shortage = short.groupby(["cm", "part"], as_index=False)["period"].min()
    worst_pab = grid.groupby(["cm", "part"], as_index=False)["pab"].min()
    
    summary["shortage_qty"] = (-summary["min_pab"]).clip(lower=0.0)
    summary["days_of_cover"] = (summary["first_shortage_date"] - cfg.snapshot).dt.days
    
    return grid, summary

def coverage(summary, usage, products, products_with_demand=None):
    """blocks_buildable = floor(opening / total_usage).
    
    How many complete units buildable before running out of this part.
    
    If products_with_demand specified, only count usage from products
    with actual demand (excludes phased-out products).
    """
    usage_with_cm = usage.merge(products[["product", "cm"]], on="product", how="left")
    
    if products_with_demand is not None:
        usage_with_cm = usage_with_cm[usage_with_cm["product"].isin(products_with_demand)]
    
    total_usage = usage_with_cm.groupby(["cm", "part"], as_index=False)["usage"].sum()
    out = summary.merge(total_usage, on=["cm", "part"], how="left")
    out["blocks_buildable"] = np.floor(out["opening"] / out["total_usage"].replace(0, np.nan))
    return out

def part_states(demand, oh_norm, oo_eta):
    """IN_PRODUCTION / ON_ORDER_ONLY / NOT_SOURCED per (cm, part).
    
    363 of 1,389 buy-parts have no On Hand row (NPI, new products).
    NOT_SOURCED is readiness gap, never a shortage.
    """
    has_oh = set(oh_norm.loc[oh_norm["unrestricted_qty"] > 0, "_lpn"])
    has_po = set(oo_eta.loc[oo_eta["quantity_open"] > 0, "_lpn"])
    out["state"] = np.where(
        out["part"].isin(has_oh), IN_PRODUCTION,
        np.where(out["part"].isin(has_po), ON_ORDER_ONLY, NOT_SOURCED))
    return out

def run(frames=None, cfg=None):
    """Load, normalize, explode, net, compute runout.
    
    Returns dict with every frame needed by UI.
    Nothing touches Streamlit.
    """
    frames = frames or lio.load_all()
    cfg = cfg or Config(snapshot=pd.Timestamp.today())
    
    products = product_master(frames["stitch_list.csv"])
    usage = sourcing_usage(frames["bom_stitched.csv"])
    
    remaining, backlog = remaining_builds(...)
    demand, demand_detail = explode_demand(remaining, usage, products)
    
    # PCBA pull-forward: shift demand 28 days earlier for parts with Parent PCBA LPN
    parts_with_pcba = set(bom[bom["Parent PCBA LPN"].notna()]["item_number"].unique())
    has_pcba_parent = demand["part"].isin(parts_with_pcba)
    demand.loc[has_pcba_parent, "period"] = demand.loc[has_pcba_parent, "period"] - pd.Timedelta(days=28)
    
    oo_eta = add_eta(norm["onorder"])
    receipts, past_due, undated = scheduled_receipts(oo_eta, cfg)
    opening, negatives = opening_inventory(...)
    
    pab, summary = compute_runout(demand, opening, receipts, cfg)
    products_with_demand = set(demand_detail["product"].unique())
    summary = coverage(summary, usage, products, products_with_demand)
    summary = classify_shortage_type(summary, pab, receipts)
    
    return {
        "config": cfg,
        "snapshot": snap,
        "products": products,
        "demand": demand,
        "demand_detail": demand_detail,
        "opening": opening,
        "receipts": receipts,
        "past_due": past_due,
        "undated": undated,
        "pab": pab,
        "summary": summary,
    }
```

### app_minimal.py (UI)

```python
"""Streamlit UI: filters, reports, drill-down.

@st.cache_data loads data once per session.
"""

@st.cache_data(show_spinner="Initializing engine...")
def load_and_run():
    """Cached: loads data and runs engine once per session."""
    frames = lio.load_all()
    result = eng.run(frames)
    return result, build_plan, bom_stitched

# Filters
cm_filter = st.selectbox("CM", ["All"] + sorted(s["cm"].unique()))
prod_filter = st.multiselect("Products", sorted(result["products"]["display_name"].unique()))
part_filter = st.multiselect("Part Number", sorted(s["part"].unique()))
weeks_window = st.slider("Planning Horizon (weeks)", min_value=1, max_value=52, value=12)

# Product filter: show only buy parts under selected products (also filter by CM)
if prod_filter:
    selected_products_df = products[products["display_name"].isin(prod_filter)][["product", "cm"]]
    selected_products = selected_products_df["product"].unique()
    selected_cms = set(selected_products_df["cm"].unique())
    
    parts_in_products = set()
    for product_lpn in selected_products:
        parts_in_products.update(get_buy_parts_under_product(product_lpn, bom_stitched))
    
    # Filter: parts in selected products AND in selected products' CMs
    filtered = filtered[
        (filtered["part"].isin(parts_in_products)) &
        (filtered["cm"].isin(selected_cms))
    ]

# Drill-down: 3 rows per part (Demand | Supply | Inventory)
def render_pab_drill_down(pab_to_show, filtered_parts, demand_detail, receipts):
    grain = st.selectbox("View by:", ["Day", "Week", "Month"])
    pab_aggregated = aggregate_pab_by_grain(pab_to_show, grain)
    
    grid_data = []
    for cm, part in filtered_parts.values:
        part_pab = pab_aggregated[(pab_aggregated["cm"] == cm) & ...].sort_values("period")
        
        # Demand row
        demand_row = {"CM": cm, "Part": part, "Metric": "Demand"}
        for _, pab_row in part_pab.iterrows():
            period_key = pab_row.get("period_key", ...)
            demand_row[period_key] = int(pab_row["demand"])
        grid_data.append(demand_row)
        
        # Supply row
        supply_row = {"CM": cm, "Part": part, "Metric": "Supply"}
        for _, pab_row in part_pab.iterrows():
            supply_row[period_key] = int(pab_row["receipts"])
        grid_data.append(supply_row)
        
        # Inventory row
        inv_row = {"CM": cm, "Part": part, "Metric": "Inventory"}
        for _, pab_row in part_pab.iterrows():
            inv_row[period_key] = int(pab_row["pab"])
        grid_data.append(inv_row)
    
    # Style inventory rows: green for positive, red for negative
    def color_inventory_row(row):
        if row["Metric"] != "Inventory":
            return [""] * len(row)
        colors = []
        for col in row.index:
            if col in ["CM", "Part", "Metric"]:
                colors.append("")
            else:
                val = row[col]
                if val < 0:
                    colors.append(f"background-color: rgba(255, 0, 0, 0.7)")  # Red
                else:
                    colors.append(f"background-color: rgba(0, 128, 0, 0.3)")  # Green
        return colors
    
    st.dataframe(grid_df.style.apply(color_inventory_row, axis=1))
```

---

## Data Hazards

### Part Number Normalization

Five encodings of same LPN — trust clean column, never re-derive:

| Source | Example |
|---|---|
| Lunar NetSuite | `10-000054` |
| Sienna (space rev) | `10-000099 REV-02` |
| Qualitel prefix | `814L-10-000099` |
| Sienna consignment | `C10-000253 REV-02` |
| Celestica suffix | `10-0000423MS` |

**Rule:** Use `lpn` (On Hand) and `lunar_lpn` (On Order) — already normalized on 99.7% of rows.

### Double-Count Prevention

**Problem:** Lunar stock allocated to CM appears twice — Lunar inventory AND CM open PO.

**Solution:**
```
CM runout = CM on-hand + CM open POs (never + Lunar on-hand)
Lunar pool = uncommitted_qty on Lunar-owned rows only
```

**Validation:**
```
SUM(unrestricted_qty − uncommitted_qty) over Lunar rows, by part
  == SUM(quantity_open) over CM POs where po_vendor = Lunar
```

### Three Part States

| State | Meaning | Action |
|---|---|---|
| IN_PRODUCTION | Has on-hand | Plan normally |
| ON_ORDER_ONLY | No on-hand, PO exists | Runout from PO dates |
| NOT_SOURCED | No on-hand, no PO | NPI gap (toggle to hide) |

### In-Transit Gap

`Parent FG Built` = goods receipt (confirmed), not shipment.

Between ASN and receipt, components sit unaccounted.

Result: engine over-demands conservatively (invents shortages, hides none).

Mitigation: `in_transit.csv` supplies true in-flight population.

---

## Asking Claude: Examples

### Example 1: Why does part show under two CMs?

> Part 10-005080 shows under Bridge (Sienna) but also under MSA-C (Qualitel). Why?

Claude will find:
- `get_buy_parts_under_product()` (line 262) finds parts under a product
- Product filter (line 393) checks `(part in parts_in_products) & (cm in selected_cms)`
- Filter should prevent cross-CM duplication

### Example 2: How does PAB work?

> Demand 1,000, supply 500. Why isn't PAB = -500?

Claude will find:
- `compute_runout()` (line 790-791): `pab = opening + cumsum(receipts - demand)`
- PAB depends on opening inventory, not just period flow

### Example 3: Are past-due included?

> Past-due receipts flagged [Past Due] — are they counted?

Claude will find:
- `scheduled_receipts()` (lines 585-592): past-due moved to today, `is_past_due=True`
- Line 619: included in receipts_df, so YES, counted in PAB

---

## Version

**v2.0 (Aug 14, 2026)**
- Daily grain (389 periods)
- PCBA 28-day pull-forward
- Past-due receipts included, flagged with original dates
- Product-filtered coverage
- Red highlighting for past-due

