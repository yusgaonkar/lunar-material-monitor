"""Demand explosion, netting and runout. The pipeline-balance model, CLAUDE.md 6.

Pure pandas. Imports nothing from Streamlit and runs from a plain script — that
rule is load-bearing (CLAUDE.md 8 rule 1). No LLM anywhere in this file.

The model in one line: demand is builds not yet delivered, supply is material
still in the pipeline, both measured as of the snapshot date.

    DEMAND   backlog[product]  = plan_to_date - (received + in_transit)
             remaining[p, per] = forward_plan + backlog (first period only)
             gross[c, per]     = SUM over products at that CM of
                                 remaining * Sourcing Flat Qty

    SUPPLY   available[cm, c]  = CM-owned unrestricted_qty      raw at the CM
                               + WIP Consumed                   embedded in assemblies
                               + dated open POs                 by eta

WIP sits on the SUPPLY side deliberately. It is material the CM physically holds,
just built into something. It cannot be collapsed into a product-level netting
figure because WIP is uneven across BOM levels (CLAUDE.md 6).

`Parent FG Consumed` is deliberately unused: it is `Parent FG Built x Flat Qty`,
a derived column carrying nothing the product-level number does not already have,
and netting it in component space multiplies the mapping risk 4,378-fold.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import io as lio
from . import normalize as nz

log = logging.getLogger(__name__)

# Part states, CLAUDE.md 5.7. NOT_SOURCED is an NPI readiness gap, never a shortage.
IN_PRODUCTION = "IN_PRODUCTION"
ON_ORDER_ONLY = "ON_ORDER_ONLY"
NOT_SOURCED = "NOT_SOURCED"


@dataclass
class Config:
    """Run options. Everything the UI toggles lives here, nothing else."""

    snapshot: pd.Timestamp
    horizon_weeks: int = 52
    include_celestica: bool = False
    include_npi: bool = False
    use_on_order: bool = True
    count_in_transit: bool = True
    excluded_parts: frozenset = field(default_factory=frozenset)

    @property
    def week0(self) -> pd.Timestamp:
        """Monday of the snapshot week. Period 0 of the horizon."""
        s = pd.Timestamp(self.snapshot)
        return (s - pd.Timedelta(days=s.weekday())).normalize()

    def periods(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(
            [self.week0 + pd.Timedelta(weeks=i) for i in range(self.horizon_weeks)]
        )


def _week(s: pd.Series) -> pd.Series:
    """Monday of the week containing each date."""
    d = pd.to_datetime(s, errors="coerce")
    return (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.normalize()


def _count_working_days(start_date: pd.Timestamp, end_date: pd.Timestamp) -> int:
    """Count working days (Mon-Fri) between start and end dates, inclusive."""
    dates = pd.date_range(start_date, end_date, freq='D')
    return (dates.weekday < 5).sum()


def _count_remaining_working_days_in_month(snapshot: pd.Timestamp) -> int:
    """Count remaining working days (Mon-Fri) in the month from snapshot onward."""
    month_end = (snapshot + pd.offsets.MonthEnd(0)).normalize()
    return _count_working_days(snapshot, month_end)


# --- product master -----------------------------------------------------------

def product_master(stitch_list: pd.DataFrame) -> pd.DataFrame:
    """One row per product: lpn, cm, alias. The CM join key for the whole engine.

    The stitched BOM has no CM column — CM assignment lives only here, so every
    component's CM is inherited from its parent product (CLAUDE.md 4).
    """
    out = stitch_list.rename(
        columns={
            "Parent Product LPN": "product",
            "CM": "cm",
            "Product Alias": "alias",
            "Description": "description",
        }
    )[["product", "cm", "alias", "description"]].copy()
    out["cm"] = [nz.resolve_party(v) or v for v in out["cm"]]
    # Add display_name as "PN - Alias" for UI
    out["display_name"] = out["product"] + " - " + out["alias"]
    return out


def identify_pcba_parts(bom: pd.DataFrame) -> dict:
    """Map each PCBA part (any make part with buy children) to its parent products.

    A PCBA is any part where:
    - Sourcing_Flat_Qty = 0 (it's not a buy part)
    - It has children where Sourcing_Flat_Qty > 0 (it has buy parts as descendants)

    Returns:
        dict: {pcba_lpn: [parent_product_lpn, ...]}
        Example: {'30-005747': ['90-06801A'], '10-06105C': ['90-06889C']}
    """
    # Find all parts with Sourcing_Flat_Qty = 0 (make parts)
    make_parts = bom[pd.to_numeric(bom['Sourcing Flat Qty'], errors='coerce') == 0]['item_number'].unique()

    # Filter to those that have buy children
    pcbas = []
    for part in make_parts:
        children = bom[bom['Parent Product LPN'] == part]
        if len(children) > 0:
            has_buy_child = any(pd.to_numeric(children['Sourcing Flat Qty'], errors='coerce') > 0)
            if has_buy_child:
                pcbas.append(part)

    # For each PCBA, find all parent products that contain it (direct parent only)
    pcba_map = {}
    for pcba_lpn in pcbas:
        # Get all products where this PCBA appears
        parents = bom[bom['item_number'] == pcba_lpn]['Parent Product LPN'].unique()
        # Keep only 90- (top-level) products
        top_level = [p for p in parents if isinstance(p, str) and p.startswith('90-')]
        if top_level:
            pcba_map[pcba_lpn] = top_level

    return pcba_map


# --- demand, in product space -------------------------------------------------

def remaining_builds(
    build_plan: pd.DataFrame,
    plan_to_date: pd.DataFrame,
    in_transit: pd.DataFrame,
    products: pd.DataFrame,
    cfg: Config,
):
    """Forward plan spread across daily working days, plus backlog loaded into snapshot day.

    Returns (remaining, backlog_detail). `remaining` is now at daily grain instead of weekly.
    `backlog_detail` is 18 rows a human can check — plan, received, in transit, backlog —
    and it should be shown in the UI next to the build plan editor. A wrong plan_to_date
    shifts every runout date in that product's BOM the same way and nothing looks anomalous
    (CLAUDE.md 6).

    A monthly plan row is spread evenly over the days of its month. No weekly bucketing.
    """
    bp = build_plan.copy()
    bp["period_start"] = pd.to_datetime(bp["period_start"], errors="coerce")
    bp["qty"] = pd.to_numeric(bp["qty"], errors="coerce").fillna(0.0)
    bp = bp[(bp["qty"] != 0) & bp["period_start"].notna()]

    # Working days logic: allocate monthly qty across remaining working days in month from snapshot
    snapshot = cfg.snapshot
    daily = []

    for row in bp.itertuples(index=False):
        if row.qty == 0:
            continue

        period_start = row.period_start
        month_end = (period_start + pd.offsets.MonthEnd(0)).normalize()

        # For the snapshot month, count working days from ASN end date (if available) or snapshot onward
        # For other months, count working days from the 1st of the month
        if period_start.month == snapshot.month and period_start.year == snapshot.year:
            # Check if asn_end_date exists; if so, start disaggregation from the day after ASN period
            if hasattr(row, 'asn_end_date') and pd.notna(row.asn_end_date):
                asn_end = pd.to_datetime(row.asn_end_date, errors='coerce')
                if pd.notna(asn_end):
                    count_start = asn_end
                else:
                    count_start = snapshot
            else:
                count_start = snapshot
        else:
            count_start = period_start

        # Count working days (Mon-Fri) from count_start to month end
        remaining_working_days = _count_working_days(count_start, month_end)

        # Distribute qty evenly across working days, accounting for remainder
        # to avoid truncation issues that lose units
        if remaining_working_days > 0:
            base_daily = int(row.qty / remaining_working_days)
            remainder = row.qty - (base_daily * remaining_working_days)
        else:
            base_daily = 0
            remainder = 0

        # Allocate to each working day from count_start to month end
        # First 'remainder' days get base_daily + 1, rest get base_daily
        all_days = pd.date_range(count_start, month_end, freq="D")
        working_day_count = 0
        for day in all_days:
            if day.weekday() < 5:  # Mon-Fri only
                # Add the remainder to the first few days to preserve total
                daily_qty = base_daily + (1 if working_day_count < remainder else 0)
                daily.append({
                    "product": row.product_lpn,
                    "day": day,
                    "qty": daily_qty,
                })
                working_day_count += 1

    if daily:
        fwd = pd.DataFrame(daily)
        # CHANGE: Keep daily grain instead of weekly bucketing
        fwd = fwd.rename(columns={"day": "period"})
    else:
        fwd = pd.DataFrame(columns=["product", "period", "qty"])

    ptd = plan_to_date.copy()
    for c in ("plan_to_date_qty", "units_received"):
        ptd[c] = pd.to_numeric(ptd.get(c), errors="coerce").fillna(0.0)

    it = in_transit.copy()
    if len(it):
        it["qty"] = pd.to_numeric(it["qty"], errors="coerce").fillna(0.0)
        it = it.groupby("product_lpn", as_index=False)["qty"].sum()
    else:
        it = pd.DataFrame(columns=["product_lpn", "qty"])

    b = (
        products[["product", "cm", "alias"]]
        .merge(ptd.rename(columns={"product_lpn": "product"}), on="product", how="left")
        .merge(it.rename(columns={"product_lpn": "product", "qty": "in_transit"}),
               on="product", how="left")
    )
    cols = ["plan_to_date_qty", "units_received", "in_transit"]
    for c in cols:
        b[c] = pd.to_numeric(b.get(c), errors="coerce").fillna(0.0)

    if not cfg.count_in_transit:
        b["in_transit"] = 0.0

    b["backlog_raw"] = b["plan_to_date_qty"] - (b["units_received"] + b["in_transit"])
    # Negative backlog means built ahead of plan. Those units are finished goods;
    # they do not offset future builds. Floor at zero and report it separately.
    b["ahead_of_plan"] = (-b["backlog_raw"]).clip(lower=0.0)
    b["backlog"] = b["backlog_raw"].clip(lower=0.0)

    # CHANGE: Load backlog into snapshot date (actual day), not week0 (Monday)
    first = b.loc[b["backlog"] > 0, ["product", "backlog"]].copy()
    first["period"] = cfg.snapshot.normalize()
    first = first.rename(columns={"backlog": "qty"})

    # No groupby needed; daily frame already has (product, period) uniqueness by day
    remaining = pd.concat([fwd, first[["product", "period", "qty"]]], ignore_index=True)
    return remaining, b


# --- demand, exploded into component space ------------------------------------

def sourcing_usage(bom: pd.DataFrame) -> pd.DataFrame:
    """Sourcing Flat Qty per (product, component), summed over BOM positions.

    `item_number` is not unique — 11 items sit at up to 4 positions in one BOM
    (CLAUDE.md 5.4) — so positions must be summed, not deduped.

    Only rows with Sourcing Flat Qty > 0 survive. That is the procurement
    boundary: what the CM actually buys. Planning anything else would count a
    bought sub-assembly on the On Hand report AND its children again through
    WIP Consumed (CLAUDE.md 6, load-bearing assumption).
    """
    b = bom[["Parent Product LPN", "item_number", "item_name",
             "category_name", "Sourcing Flat Qty", "unit_of_measure"]].copy()
    b["Sourcing Flat Qty"] = pd.to_numeric(
        b["Sourcing Flat Qty"], errors="coerce").fillna(0.0)
    b = b[b["Sourcing Flat Qty"] > 0]
    return (
        b.groupby(["Parent Product LPN", "item_number"], as_index=False)
        .agg(usage=("Sourcing Flat Qty", "sum"),
             description=("item_name", "first"),
             category=("category_name", "first"),
             uom=("unit_of_measure", "first"))
        .rename(columns={"Parent Product LPN": "product", "item_number": "part"})
    )


def explode_demand(remaining, usage, products):
    """Component demand by (cm, part, period), and the per-product split.

    The split is not decoration. A component shared across products at one CM is
    short because of TOTAL demand; showing only one product's contribution makes
    a starved part look healthy. The UI needs both.

    Preserves demand_source column if present (for PCBA pull-forward tracking).
    """
    detail = (
        remaining.merge(usage, on="product", how="inner")
        .merge(products[["product", "cm", "alias"]], on="product", how="left")
    )
    detail["demand"] = detail["qty"] * detail["usage"]

    cols_to_keep = ["cm", "part", "product", "alias", "period",
                    "qty", "usage", "demand", "description", "category"]

    detail = detail[cols_to_keep]
    total = detail.groupby(["cm", "part", "period"], as_index=False)["demand"].sum()
    return total, detail


def build_pcba_pull_forward_plan(remaining: pd.DataFrame, bom: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Create pull-forward build plan for PCBAs before explosion.

    Handles TWO paths:
    1. 30- PCBAs under 90- products: Creates demand at 30- level from 90- build plans
    2. 10- PCBAs with direct build plans: Creates demand at 10- level (e.g., 10-06103C, 10-06105C)

    Transformation: shift demand back 4 weeks for PCBA-level procurement.
    All original weekly periods are mapped to (period - 4 weeks), with earliest dates clamped to 4 weeks before snapshot.
    """
    if len(remaining) == 0:
        return pd.DataFrame()

    # Separate into 90- and non-90- products
    top_level_products = remaining[remaining["product"].str.startswith("90-", na=False)].copy()
    pcba_products = remaining[~remaining["product"].str.startswith("90-", na=False)].copy()

    print(f"DEBUG: build_pcba_pull_forward_plan called")
    print(f"  Top-level (90-) products: {len(top_level_products)}")
    print(f"  PCBA (non-90-) products: {len(pcba_products)}")
    print(f"  Top-level product LPNs: {top_level_products['product'].unique().tolist()}")
    print(f"  PCBA product LPNs: {pcba_products['product'].unique().tolist()}")

    pf_rows = []

    # PATH 1: 30- PCBAs from 90- products
    if len(top_level_products) > 0:
        for product_lpn in top_level_products["product"].unique():
            prod_plan = top_level_products[top_level_products["product"] == product_lpn]

            # Find 30- PCBAs under this product using LEVEL-based hierarchy
            prod_bom = bom[bom["Parent Product LPN"] == product_lpn]
            pcbas_30 = prod_bom[prod_bom["item_number"].str.startswith("30-", na=False)][
                ["item_number", "Flat Qty"]
            ].drop_duplicates("item_number")

            print(f"  PATH 1 - Product {product_lpn}: found {len(pcbas_30)} PCBAs")
            if len(pcbas_30) > 0:
                print(f"    PCBAs: {pcbas_30['item_number'].tolist()}")

            for _, pcba_row in pcbas_30.iterrows():
                pcba_lpn = pcba_row["item_number"]
                flat_qty = pd.to_numeric(pcba_row["Flat Qty"], errors="coerce") or 1.0

                # Find all buy parts (Sourcing Flat Qty > 0) that are descendants of this PCBA
                # Use Parent PCBA LPN to find children, since 30-PCBAs have Sourcing Flat Qty = 0
                pcba_bom = bom[bom["Parent PCBA LPN"] == pcba_lpn]
                buy_parts = pcba_bom[
                    (pd.to_numeric(pcba_bom["Sourcing Flat Qty"], errors="coerce").fillna(0) > 0)
                ][["item_number", "Sourcing Flat Qty"]].drop_duplicates("item_number")

                # For each buy part under the PCBA, create pull-forward demand
                for _, buy_row in buy_parts.iterrows():
                    buy_part_lpn = buy_row["item_number"]
                    buy_part_usage = pd.to_numeric(buy_row["Sourcing Flat Qty"], errors="coerce") or 1.0
                    combined_usage = flat_qty * buy_part_usage

                    for _, plan_row in prod_plan.iterrows():
                        pf_demand = plan_row["qty"] * combined_usage
                        # Shift demand back 4 weeks for PCBA-level procurement
                        pf_period = plan_row["period"] - pd.Timedelta(weeks=4)

                        pf_rows.append({
                            "product": buy_part_lpn,
                            "period": pf_period,
                            "qty": pf_demand,
                            "demand_source": "PCBA_PullForward"
                        })

    # PATH 2: 10- PCBAs with direct build plans (e.g., 10-06103C, 10-06105C at Qualitel)
    if len(pcba_products) > 0:
        for product_lpn in pcba_products["product"].unique():
            prod_plan = pcba_products[pcba_products["product"] == product_lpn]

            for _, plan_row in prod_plan.iterrows():
                pf_demand = plan_row["qty"]
                # Shift demand back 4 weeks for coated PCBA-level procurement
                pf_period = plan_row["period"] - pd.Timedelta(weeks=4)

                pf_rows.append({
                    "product": product_lpn,
                    "period": pf_period,
                    "qty": pf_demand,
                    "demand_source": "PCBA_PullForward"
                })

    print(f"  Total PF rows created: {len(pf_rows)}")

    if not pf_rows:
        return pd.DataFrame()

    pf_df = pd.DataFrame(pf_rows)
    print(f"  PF DataFrame columns: {pf_df.columns.tolist()}")
    print(f"  PF demand_source values: {pf_df['demand_source'].unique().tolist()}")
    return pf_df


def get_pcba_descendants(pcba_lpn: str, bom: pd.DataFrame) -> set:
    """Find all buy parts (Sourcing_Flat_Qty > 0) that are descendants of a PCBA.

    Traverses the BOM tree recursively to find all parts at any level under the PCBA.
    Only returns parts that are buy parts (Sourcing_Flat_Qty > 0).
    """
    descendants = set()
    visited = set()

    def traverse(parent_lpn):
        if parent_lpn in visited:
            return
        visited.add(parent_lpn)

        # Find direct children where parent_lpn matches "Parent Product LPN"
        children = bom[bom["Parent Product LPN"] == parent_lpn]

        for _, child_row in children.iterrows():
            child_lpn = child_row["item_number"]
            sourcing_qty = pd.to_numeric(child_row["Sourcing Flat Qty"], errors="coerce") or 0

            # If this child is a buy part, add it
            if sourcing_qty > 0:
                descendants.add(child_lpn)

            # Recurse to find children of this child (even if not a buy part)
            traverse(child_lpn)

    traverse(pcba_lpn)
    return descendants


def apply_pcba_pull_forward(demand_detail: pd.DataFrame, pcba_map: dict, bom: pd.DataFrame) -> pd.DataFrame:
    """Generate 4-week pull-forward demand for descendants of PCBAs.

    For each PCBA in a product:
    - Find all buy parts that are descendants of the PCBA in the BOM
    - Shift their demand back 4 weeks
    - Mark with demand_source='PCBA_PullForward'
    - Keep original rows for non-PCBA descendants as 'Build Plan'

    Inputs:
        demand_detail: from explode_demand(), has columns
                      [cm, part, product, alias, period, qty, usage, demand, ...]
        pcba_map: {pcba_lpn: [parent_product_lpn, ...]}
        bom: BOM dataframe with Parent Product LPN, item_number, Sourcing Flat Qty

    Returns:
        demand_detail with demand_source column, PCBA descendants shifted back 4 weeks
    """
    out = demand_detail.copy()
    out['demand_source'] = 'Build Plan'

    # Get all unique periods across all data
    all_periods = sorted(out['period'].unique())
    if len(all_periods) == 0:
        return out
    min_period = all_periods[0]
    shift_days = 28  # 4 weeks

    # For each PCBA, find its descendants and shift them
    shifted_rows = []
    rows_to_remove = []

    for pcba_lpn, parent_products in pcba_map.items():
        # Get all buy parts that are descendants of this PCBA
        descendants = get_pcba_descendants(pcba_lpn, bom)

        if not descendants:
            continue

        # For each parent product that uses this PCBA
        for parent_product in parent_products:
            # Find rows in demand_detail that are:
            # - for this product
            # - for a part that is a descendant of this PCBA
            product_demand = out[
                (out['product'] == parent_product) &
                (out['part'].isin(descendants))
            ].copy()

            if len(product_demand) == 0:
                continue

            # Shift each row back 4 weeks
            for idx, row in product_demand.iterrows():
                # Mark for removal (we'll create shifted version)
                rows_to_remove.append(idx)

                # Shift period back 4 weeks
                shifted_period = pd.to_datetime(row['period']) - pd.Timedelta(days=shift_days)

                # Clamp to min_period if needed
                if shifted_period < min_period:
                    shifted_period = min_period

                shifted_rows.append({
                    'cm': row['cm'],
                    'part': row['part'],
                    'product': row['product'],
                    'alias': row['alias'],
                    'period': shifted_period,
                    'qty': row['qty'],
                    'usage': row['usage'],
                    'demand': row['demand'],
                    'description': row.get('description', ''),
                    'category': row.get('category', ''),
                    'demand_source': 'PCBA_PullForward'
                })

    # Remove original rows for PCBA descendants
    if rows_to_remove:
        out = out.drop(rows_to_remove, errors='ignore')

    # Add shifted rows
    if shifted_rows:
        shifted_df = pd.DataFrame(shifted_rows)
        out = pd.concat([out, shifted_df], ignore_index=True)

    return out


# --- supply -------------------------------------------------------------------

def wip_supply(bom: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """`WIP Consumed` per (cm, part) — the component embedded in higher assemblies.

    Supply, not negative demand (CLAUDE.md 6). Summed over BOM positions, then
    over products at the same CM.
    """
    b = bom[["Parent Product LPN", "item_number", "WIP Consumed",
             "Sourcing Flat Qty"]].copy()
    for c in ("WIP Consumed", "Sourcing Flat Qty"):
        b[c] = pd.to_numeric(b[c], errors="coerce").fillna(0.0)
    b = b[b["Sourcing Flat Qty"] > 0]
    b = b.rename(columns={"Parent Product LPN": "product", "item_number": "part"})
    b = b.merge(products[["product", "cm"]], on="product", how="left")
    return (
        b.groupby(["cm", "part"], as_index=False)["WIP Consumed"]
        .sum().rename(columns={"WIP Consumed": "wip"})
    )


def add_eta(oo_norm: pd.DataFrame) -> pd.DataFrame:
    """eta = COALESCE(receipt_date, ship_date), with eta_source carried through.

    The two columns are mutually exclusive by feed: Sienna populates only
    ship_date (1,223 of 1,506), Qualitel only receipt_date (CLAUDE.md 5.3). The
    coalesce takes dated coverage from 75% to 95%.

    A Sienna date is a SHIP date and therefore optimistic by whatever transit
    takes. `_eta_source` must survive to the report so a planner can see which
    kind of date they are looking at.
    """
    out = oo_norm.copy()
    rd = pd.to_datetime(out["receipt_date"], errors="coerce")
    sd = pd.to_datetime(out["ship_date"], errors="coerce")
    out["_eta"] = rd.fillna(sd)
    out["_eta_source"] = np.where(
        rd.notna(), "receipt_date", np.where(sd.notna(), "ship_date", "none"))
    return out


def scheduled_receipts(oo_eta: pd.DataFrame, cfg: Config):
    """Open CM POs bucketed by eta. Returns (receipts, past_due_summary, undated).

    Three buckets, three visual states. Undated supply is real material that we
    refuse to count because we cannot place it in time — never added to the
    balance, and never rendered the same as "no supply exists" (CLAUDE.md 5.3).

    Past-due receipts are INCLUDED in supply calculation (period = today) but kept
    as individual lines (not aggregated) with original dates preserved for display.
    They are flagged with is_past_due=True and show as "Past due receipt" in reports.

    CHANGE: Now bucketing to daily grain instead of weekly.
    CHANGE: Past-due receipts moved to cfg.snapshot (today) for calculation but
            displayed with original receipt_date and individual lines (no aggregation).
    """
    oo = oo_eta[
        (oo_eta["quantity_open"] > 0)
        & (oo_eta["is_closed"].astype(str).str.upper() != "TRUE")
    ].copy()

    undated = oo[oo["_eta"].isna()]
    dated = oo[oo["_eta"].notna()]

    past = dated[dated["_eta"] < cfg.snapshot].copy()
    future = dated[dated["_eta"] >= cfg.snapshot].copy()

    # Past-due receipts: keep as individual lines with original date for display
    # but use today's period for PAB calculation
    past["period"] = cfg.snapshot.normalize()  # For calculation
    past["_original_eta"] = past["_eta"]  # Preserve original date for display
    past["is_past_due"] = True
    past["_note"] = "Past due receipt"

    # Future receipts: aggregate by (cm, part, period)
    future["period"] = future["_eta"].dt.normalize()
    future["is_past_due"] = False
    future["_note"] = None

    # For receipts dataframe: keep past-due as individual lines, aggregate future
    past_receipts = past[[
        "_cm", "_lpn", "period", "quantity_open", "_original_eta", "is_past_due", "_note"
    ]].rename(columns={
        "_cm": "cm", "_lpn": "part", "quantity_open": "receipts", "_note": "note"
    })

    future_agg = (
        future.groupby(["_cm", "_lpn", "period"], as_index=False)["quantity_open"]
        .sum()
        .rename(columns={
            "_cm": "cm", "_lpn": "part", "quantity_open": "receipts"
        })
    )
    future_agg["_original_eta"] = None
    future_agg["is_past_due"] = False
    future_agg["note"] = None

    # Combine: past-due as individual lines, future aggregated
    receipts_df = pd.concat([past_receipts, future_agg], ignore_index=True)

    # past_due summary for reporting (qty count per part, aggregated)
    def g(d, *keys):
        if len(d) == 0:
            return pd.DataFrame(columns=list(keys) + ["quantity_open"])
        return d.groupby(list(keys), as_index=False)["quantity_open"].sum()

    past_due_summary = g(past, "_cm", "_lpn").rename(
        columns={"_cm": "cm", "_lpn": "part", "quantity_open": "past_due"})

    return (
        receipts_df,
        past_due_summary,
        g(undated, "_cm", "_lpn").rename(
            columns={"_cm": "cm", "_lpn": "part", "quantity_open": "undated"}),
    )


def calc_wip_fg(bom: pd.DataFrame, cm_avail: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Calculate WIP finished goods: completed assemblies at CM not yet ASNd.

    For each part, find all top-level products that use it.
    Extended usage = total usage path through BOM tree.
    WIP_FG = (On-hand qty of top-level product at CM) × (Extended usage in product)

    Returns: (cm, part) → wip_fg_qty
    """
    # Get top-level products only; products DF has columns: product, cm, alias, description
    top_level = products[products['cm'].notna()].copy()

    wip_fg_list = []

    for _, part_row in cm_avail[['cm', 'part']].drop_duplicates().iterrows():
        part = part_row['part']
        cm = part_row['cm']

        total_wip_fg = 0.0

        # Find all top-level products at this CM
        for _, prod_row in top_level[top_level['cm'] == cm].iterrows():
            prod_lpn = prod_row['product']  # product column (renamed from Parent Product LPN)

            # Find all occurrences of this part in this product's BOM
            part_in_prod = bom[
                (bom['item_number'] == part) &
                (bom['Parent Product LPN'] == prod_lpn)
            ]

            if len(part_in_prod) == 0:
                continue

            # Extended usage = sum of Usage Qty across all BOM paths
            extended_usage = part_in_prod['Usage Qty'].sum()

            # Get on-hand qty of the top-level product at this CM
            prod_onhand = cm_avail[
                (cm_avail['part'] == prod_lpn) &
                (cm_avail['cm'] == cm)
            ]

            if len(prod_onhand) > 0:
                prod_qty = prod_onhand.iloc[0]['cm_available']
                wip_fg_contribution = prod_qty * extended_usage
                total_wip_fg += wip_fg_contribution

        wip_fg_list.append({
            'cm': cm,
            'part': part,
            'wip_fg': total_wip_fg
        })

    return pd.DataFrame(wip_fg_list) if wip_fg_list else pd.DataFrame(columns=['cm', 'part', 'wip_fg'])


def opening_inventory(cm_avail: pd.DataFrame, wip: pd.DataFrame, wip_fg: pd.DataFrame = None):
    """CM-owned raw on-hand + WIP sub-assy + WIP finished goods.

    opening = cm_available (raw) + wip_consumed (sub-assy WIP) + wip_fg (FG at CM)

    Lunar-owned stock is NOT added: it already appears as an open CM PO, and
    counting both inflates availability (CLAUDE.md 5.2). Lunar inventory reaches
    the report only through the allocation recommender.
    """
    opening = cm_avail.merge(wip, on=["cm", "part"], how="outer")
    opening[["cm_available", "wip"]] = opening[["cm_available", "wip"]].fillna(0.0)

    # Add WIP FG if provided
    if wip_fg is not None and len(wip_fg) > 0:
        opening = opening.merge(wip_fg[['cm', 'part', 'wip_fg']], on=['cm', 'part'], how='left')
        opening['wip_fg'] = opening['wip_fg'].fillna(0.0)
    else:
        opening['wip_fg'] = 0.0

    # Store breakdown for audit trail
    opening['raw_inventory'] = opening['cm_available']
    opening['wip_inventory'] = opening['wip'] + opening['wip_fg']

    negatives = opening[opening["cm_available"] < 0].copy()
    opening["cm_available"] = opening["cm_available"].clip(lower=0.0)
    opening["opening"] = opening["cm_available"] + opening["wip"] + opening["wip_fg"]

    return opening, negatives


# --- part state ---------------------------------------------------------------

def part_states(demand, oh_norm, oo_eta) -> pd.DataFrame:
    """IN_PRODUCTION / ON_ORDER_ONLY / NOT_SOURCED per (cm, part), CLAUDE.md 5.7.

    363 of 1,389 buy-parts have no On Hand row and they are overwhelmingly the new
    products — 56% of the 11kW Inverter, 54% of Link. If NOT_SOURCED renders as a
    shortage, 166 parts fire on day one and the report is dismissed as noise. It is
    an NPI readiness gap, reported separately, never counted as a shortage.
    """
    has_oh = set(oh_norm.loc[oh_norm["unrestricted_qty"] > 0, "_lpn"])
    has_po = set(oo_eta.loc[oo_eta["quantity_open"] > 0, "_lpn"])
    out = demand[["cm", "part"]].drop_duplicates().copy()
    out["state"] = np.where(
        out["part"].isin(has_oh), IN_PRODUCTION,
        np.where(out["part"].isin(has_po), ON_ORDER_ONLY, NOT_SOURCED))
    return out


# --- runout -------------------------------------------------------------------

def compute_runout(demand, opening, receipts, cfg: Config):
    """Projected available balance per day, and the per-part summary.

        PAB[t] = PAB[t-1] + receipts[t] - demand[t]
        PAB[0] = opening inventory

    Returns (pab, summary). `pab` is the drill-down grid; `summary` is one row per
    (cm, part) with the runout date and shortage quantity.

    CHANGE: Now computing daily PAB instead of weekly.
    """
    # Include pulled-forward demand periods (may go before cfg.snapshot)
    min_demand_period = demand["period"].min() if len(demand) > 0 else cfg.snapshot
    min_receipts_period = receipts["period"].min() if len(receipts) > 0 else cfg.snapshot
    min_period = min(min_demand_period, min_receipts_period)

    # CHANGE: Create daily period list. Horizon is in weeks but we need daily dates.
    # Start from min_period, go forward horizon_weeks * 5 working days
    total_days = cfg.horizon_weeks * 7  # 7 days per week to be conservative
    all_periods = [min_period + pd.Timedelta(days=i) for i in range(total_days)]
    periods = pd.DataFrame({"period": all_periods})

    # Only parts that carry demand. A part with supply and no demand is not a
    # planning object — including it would bury 21 real shortages under ~2,000
    # rows of inventory that nothing consumes.
    keys = demand[["cm", "part"]].drop_duplicates().reset_index(drop=True)

    grid = keys.merge(periods, how="cross")
    grid = grid.merge(demand, on=["cm", "part", "period"], how="left")
    # Only merge receipts quantity; keep _original_eta, is_past_due, note separate for drill-down metadata
    receipts_for_calc = receipts[["cm", "part", "period", "receipts"]].copy()
    grid = grid.merge(
        receipts_for_calc if cfg.use_on_order else receipts_for_calc.iloc[0:0],
        on=["cm", "part", "period"], how="left")
    grid = grid.merge(
        opening[["cm", "part", "opening"]], on=["cm", "part"], how="left")

    for c in ("demand", "receipts", "opening"):
        if c not in grid:
            grid[c] = 0.0
    grid[["demand", "receipts", "opening"]] = grid[
        ["demand", "receipts", "opening"]].fillna(0.0)

    grid = grid.sort_values(["cm", "part", "period"]).reset_index(drop=True)
    grid["net_flow"] = grid["receipts"] - grid["demand"]
    grid["pab"] = grid["opening"] + grid.groupby(
        ["cm", "part"], sort=False)["net_flow"].cumsum()

    short = grid[grid["pab"] < 0]
    first = (short.groupby(["cm", "part"], as_index=False)["period"].min()
             .rename(columns={"period": "first_shortage_date"}))
    worst = (grid.groupby(["cm", "part"], as_index=False)["pab"].min()
             .rename(columns={"pab": "min_pab"}))

    summary = (
        keys.merge(first, on=["cm", "part"], how="left")
        .merge(worst, on=["cm", "part"], how="left")
        .merge(opening[["cm", "part", "opening", "cm_available", "wip", "raw_inventory", "wip_inventory"]],
               on=["cm", "part"], how="left")
    )
    summary["shortage_qty"] = (-summary["min_pab"]).clip(lower=0.0)
    summary["is_short"] = summary["first_shortage_date"].notna()
    # CHANGE: Calculate days_of_cover instead of weeks (will convert to weeks in aggregation)
    summary["days_of_cover"] = (
        (summary["first_shortage_date"] - cfg.snapshot).dt.days)
    summary["weeks_of_cover"] = (summary["days_of_cover"] / 7).round(1)

    # Calculate days_of_supply: on-hand only, no on-order
    # For each part at each CM, find how many days on-hand lasts based on daily demand
    summary["days_of_supply"] = 999.0  # Default: infinite supply

    for idx, row in summary.iterrows():
        cm = row["cm"]
        part = row["part"]
        on_hand = row["cm_available"]

        # Handle zero on-hand
        if on_hand <= 0:
            summary.loc[idx, "days_of_supply"] = 0.0
            continue

        # Get demand for this part at this CM, from snapshot onwards
        part_demand = grid[(grid["cm"] == cm) & (grid["part"] == part) & (grid["period"] >= cfg.snapshot)].copy()

        if len(part_demand) == 0:
            # No demand → infinite supply
            summary.loc[idx, "days_of_supply"] = 999.0
            continue

        # Calculate cumulative demand from snapshot
        part_demand = part_demand.sort_values("period").reset_index(drop=True)
        part_demand["cumsum_demand"] = part_demand["demand"].cumsum()

        # Find first date where cumsum >= on_hand
        exhausted = part_demand[part_demand["cumsum_demand"] >= on_hand]

        if len(exhausted) == 0:
            # On-hand never runs out
            summary.loc[idx, "days_of_supply"] = 999.0
        else:
            # Days from snapshot to exhaustion date
            exhaustion_date = exhausted.iloc[0]["period"]
            days = (exhaustion_date - cfg.snapshot).days
            summary.loc[idx, "days_of_supply"] = max(0, days)

    return grid, summary


def coverage(summary: pd.DataFrame, usage: pd.DataFrame, products: pd.DataFrame,
             products_with_demand: set[str] | None = None) -> pd.DataFrame:
    """blocks_buildable = floor(opening / total_usage_at_cm).

    For a given CM, calculates how many complete units can be built before
    running out of this part. Uses total usage from products at that CM.

    If products_with_demand is specified (e.g., from demand_detail), only
    counts usage from products that actually have demand in the build plan.
    This excludes products with BOM entries but no current/future demand.

    Takes the BOM usage table (product, part, usage), merges with products
    to get CM, then sums unique usage values per (cm, part).
    """
    # Start with usage table (product, part, sourcing_flat_qty)
    # Merge with products to get CM
    usage_with_cm = usage.merge(products[["product", "cm"]], on="product", how="left")

    # Filter to products with demand if specified
    if products_with_demand is not None:
        usage_with_cm = usage_with_cm[usage_with_cm["product"].isin(products_with_demand)]

    # Sum unique usage values per (cm, part)
    # Group by (cm, part) and sum the usage (each product appears once)
    total_usage = (
        usage_with_cm.groupby(["cm", "part"], as_index=False)["usage"]
        .sum()
        .rename(columns={"usage": "total_usage"})
    )

    out = summary.merge(total_usage, on=["cm", "part"], how="left")
    out["blocks_buildable"] = np.floor(
        out["opening"].fillna(0) / out["total_usage"].replace(0, np.nan))
    return out


def classify_shortage_type(summary: pd.DataFrame, pab: pd.DataFrame, receipts: pd.DataFrame) -> pd.DataFrame:
    """Classify shortage types based on on-hand coverage and supply position.

    New columns added:
    - on_hand_short: True if cm_available (no WIP) < total future demand
    - shortage_type: Why it's short (if on_hand_short=True)
      * "On-hand covers" — on-hand alone sufficient (no shortage)
      * "Incoming supply covers" — on-hand short but in first demand period, PAB never negative
      * "Supply gap" — on-hand + all on-order insufficient; PAB goes negative
      * "Delivery timing gap" — PAB recovers after going negative (supply arrives late)

    Logic:
    - on_hand_short = cm_available < shortage_qty (first shortage period demand)
    - shortage_type = check first shortage period PAB vs later recovery
    - Existing PAB/is_short logic unchanged; this is additive
    """
    out = summary.copy()

    shortage_types = []
    on_hand_short_list = []

    for idx, row in out.iterrows():
        cm = row["cm"]
        part = row["part"]
        cm_available = row["cm_available"]  # Raw on-hand, no WIP
        first_shortage_date = row.get("first_shortage_date")

        # on_hand_short: Does RAW ON-HAND (cm_available, no WIP) cause a shortage anywhere in horizon?
        part_pab = pab[(pab["cm"] == cm) & (pab["part"] == part)].sort_values("period")

        # If PAB ever goes negative, on-hand is insufficient
        on_hand_short = False
        if not part_pab.empty:
            min_pab_overall = part_pab["pab"].min()
            on_hand_short = min_pab_overall < 0

        on_hand_short_list.append(on_hand_short)
        part_pab = pab[(pab["cm"] == cm) & (pab["part"] == part)].sort_values("period")  # Re-fetch for classification

        # Classify based on on_hand_short + supply timing
        if not on_hand_short:
            # No shortage at any point in horizon
            shortage_types.append("On-hand covers")
            continue

        # on_hand_short = True: on-hand insufficient at some point
        # Check if incoming supply arrives BEFORE first shortage
        if part_pab.empty or pd.isna(first_shortage_date):
            shortage_types.append("Supply gap")
            continue

        # Find first dated receipt
        part_receipts = receipts[(receipts["cm"] == cm) & (receipts["part"] == part)].sort_values("period")
        dated_receipts = part_receipts[part_receipts["period"].notna()]

        if dated_receipts.empty:
            # No incoming supply
            shortage_types.append("Supply gap")
        elif dated_receipts.iloc[0]["period"] <= first_shortage_date:
            # Incoming supply arrives BEFORE or ON the shortage date
            shortage_types.append("Incoming supply covers")
        else:
            # Incoming supply arrives AFTER shortage date (too late)
            shortage_types.append("Supply gap")

    out["on_hand_short"] = on_hand_short_list
    out["shortage_type"] = shortage_types

    return out


# --- allocation scenario (what-if: assume Lunar inventory is pulled) --------

def apply_allocation_scenario(result: dict, lunar_allocatable: pd.DataFrame,
                              usage: pd.DataFrame, cfg: Config = None) -> dict:
    """Recalculate PAB assuming shortages are covered by Lunar allocations.

    For each shortage:
      - Calculate qty needed from Lunar: shortage_qty / max_usage
      - Check if Lunar has uncommitted inventory for that part
      - If yes: inject as virtual receipt in period 0, recalculate PAB, hide shortage
      - If no: leave as-is

    Returns modified result dict with recalculated pab and summary.
    Shortages that are covered disappear; those that aren't remain.
    """
    cfg = result["config"]
    demand = result["demand"].copy()
    receipts = result["receipts"].copy()
    summary = result["summary"].copy()
    pab = result["pab"].copy()

    # Only process parts with shortages
    short_parts = summary[summary["is_shortage"]].copy()
    if len(short_parts) == 0:
        result["summary_with_allocation"] = summary
        result["pab_with_allocation"] = pab
        result["allocation_recommendations"] = pd.DataFrame()
        return result

    # Get max usage per part
    u = usage.groupby("part", as_index=False)["usage"].max()
    short_parts = short_parts.merge(u, on="part", how="left", suffixes=("", "_usage"))
    short_parts = short_parts.merge(lunar_allocatable, on="part", how="left")

    # Calculate allocation per part: how much to pull from Lunar
    # shortage_qty is already in units of the part, not in blocks
    short_parts["lunar_available"] = short_parts["lunar_available"].fillna(0.0)
    short_parts["qty_needed_from_lunar"] = short_parts["shortage_qty"].fillna(0.0).clip(lower=0)
    short_parts["qty_to_allocate"] = short_parts[[
        "qty_needed_from_lunar", "lunar_available"
    ]].min(axis=1)
    short_parts["fully_covered"] = (
        short_parts["qty_to_allocate"] >= short_parts["qty_needed_from_lunar"]
    )

    # Build virtual receipts for period 0
    allocations = short_parts[short_parts["qty_to_allocate"] > 0][[
        "cm", "part", "qty_to_allocate", "fully_covered"
    ]].copy()
    allocations["period"] = cfg.week0
    allocations["receipts"] = allocations["qty_to_allocate"]

    # Inject virtual receipts into receipts dataframe
    receipts_with_alloc = receipts.copy()
    for _, alloc in allocations.iterrows():
        mask = (receipts_with_alloc["cm"] == alloc["cm"]) & \
               (receipts_with_alloc["part"] == alloc["part"]) & \
               (receipts_with_alloc["period"] == cfg.week0)
        if mask.any():
            receipts_with_alloc.loc[mask, "receipts"] += alloc["receipts"]
        else:
            receipts_with_alloc = pd.concat([
                receipts_with_alloc,
                pd.DataFrame([{
                    "cm": alloc["cm"],
                    "part": alloc["part"],
                    "period": cfg.week0,
                    "receipts": alloc["receipts"]
                }])
            ], ignore_index=True)

    # Recalculate PAB with allocated receipts
    pab_alloc, summary_alloc = compute_runout(demand, result["opening"],
                                               receipts_with_alloc, cfg)

    # Merge allocation metadata and product info from original summary
    summary_alloc = summary_alloc.merge(
        allocations[["cm", "part", "qty_to_allocate", "fully_covered"]],
        on=["cm", "part"], how="left"
    )
    # Copy over product-level columns from original summary
    prod_cols = summary[["cm", "part", "products", "uom", "description", "category", "state"]].drop_duplicates("part", keep="first")
    summary_alloc = summary_alloc.merge(
        prod_cols, on=["cm", "part"], how="left", suffixes=("", "_orig")
    )
    # Use original columns if present
    for col in ["products", "uom", "description", "category", "state"]:
        if f"{col}_orig" in summary_alloc.columns:
            summary_alloc[col] = summary_alloc[f"{col}_orig"]
            summary_alloc = summary_alloc.drop(columns=[f"{col}_orig"])

    summary_alloc["qty_to_allocate"] = summary_alloc["qty_to_allocate"].fillna(0.0)
    summary_alloc["fully_covered"] = summary_alloc["fully_covered"].fillna(False)

    # Add coverage metrics (blocks_buildable)
    # Note: in allocation scenario, we use the original products from result
    products_full = result.get("products", pd.DataFrame())
    # Use the same products_with_demand as the original calculation
    demand_detail = result.get("demand_detail", pd.DataFrame())
    products_with_demand = set(demand_detail["product"].unique()) if not demand_detail.empty else None
    summary_alloc = coverage(summary_alloc, usage, products_full, products_with_demand)
    summary_alloc = classify_shortage_type(summary_alloc, pab_alloc, receipts_with_alloc)

    # For parts that are fully covered by allocation, keep them as "shortages" but mark status
    # For parts that still have shortages after allocation, show the new shortage info
    summary_alloc["allocation_status"] = ""
    for idx in summary_alloc.index:
        if summary_alloc.loc[idx, "fully_covered"]:
            summary_alloc.loc[idx, "allocation_status"] = "RESOLVED_BY_ALLOCATION"
            summary_alloc.loc[idx, "is_shortage"] = True  # Keep visible
        else:
            # Check if there's still a shortage after allocation
            summary_alloc.loc[idx, "is_shortage"] = (
                summary_alloc.loc[idx, "first_shortage_date"] is not pd.NaT and
                summary_alloc.loc[idx, "state"] != "NOT_SOURCED"
            )

    # Apply same filtering as original summary: exclude NOT_SOURCED if configured
    if cfg and not cfg.include_npi:
        summary_alloc = summary_alloc[summary_alloc["state"] != NOT_SOURCED]

    result["summary_with_allocation"] = summary_alloc
    result["pab_with_allocation"] = pab_alloc
    result["allocation_recommendations"] = allocations

    return result


# --- excess monitoring --------------------------------------------------------

def compute_excess(demand_detail: pd.DataFrame, onorder_with_eta: pd.DataFrame,
                   products: pd.DataFrame) -> pd.DataFrame:
    """Identify parts with supply scheduled beyond end of demand.

    For each (cm, part): find max demand period. Flag onorder receipts after that
    as excess and suggest cancellations. Group by PO line for actionability.

    Returns DataFrame with columns:
      cm, part, description, products, last_demand_period,
      po_number, po_line_item, receipt_date, quantity_open, unit_price,
      qty_to_cancel, cost_to_save, action_text
    """
    if len(demand_detail) == 0 or len(onorder_with_eta) == 0:
        return pd.DataFrame()

    # Step 1: Find last demand period for each (cm, part)
    last_demand = (demand_detail.groupby(["cm", "part"])["period"]
                   .max().reset_index())
    last_demand.columns = ["cm", "part", "last_demand_period"]

    # Step 2: Filter onorder: only open lines with quantity
    oo = onorder_with_eta[
        (onorder_with_eta["quantity_open"] > 0) &
        (onorder_with_eta["_eta"].notna())
    ].copy()

    # Step 3: Rename _cm and _lpn to cm and part for merge
    oo.rename(columns={"_cm": "cm", "_lpn": "part"}, inplace=True)

    # Step 4: Merge onorder with last_demand_period to find excess
    oo_excess = oo.merge(
        last_demand, on=["cm", "part"], how="inner"
    )

    # Step 5: Filter for ETAs after last demand period
    oo_excess = oo_excess[oo_excess["_eta"] > oo_excess["last_demand_period"]].copy()

    if len(oo_excess) == 0:
        return pd.DataFrame()

    # Step 6: Calculate cost to save
    oo_excess["qty_to_cancel"] = oo_excess["quantity_open"]
    oo_excess["cost_to_save"] = (oo_excess["qty_to_cancel"] *
                                  oo_excess["unit_price"].fillna(0))

    # Step 7: Get product names and description for this part per CM
    prod_names = (demand_detail.groupby(["cm", "part"])[["alias", "description"]]
                  .agg({"alias": lambda s: ", ".join(sorted(set(s))),
                        "description": "first"})
                  .reset_index()
                  .rename(columns={"alias": "products", "description": "desc_from_bom"}))

    # Step 8: Build output
    output = oo_excess[[
        "cm", "part", "last_demand_period",
        "po_number", "po_line_item", "_eta", "quantity_open",
        "qty_to_cancel", "unit_price", "cost_to_save"
    ]].copy()

    output = output.merge(prod_names, on=["cm", "part"], how="left")
    output["description"] = output["desc_from_bom"].fillna("—")
    output = output.drop(columns=["desc_from_bom"])
    output = output[[
        "cm", "part", "description", "last_demand_period",
        "po_number", "po_line_item", "_eta", "quantity_open",
        "qty_to_cancel", "unit_price", "cost_to_save", "products"
    ]]
    output.columns = [
        "cm", "part", "description", "last_demand_period",
        "po_number", "po_line_item", "receipt_date", "quantity_open",
        "qty_to_cancel", "unit_price", "cost_to_save", "products"
    ]

    # Step 9: Format action text
    cost_str = ""
    if output["cost_to_save"].notna().any():
        output["cost_str"] = output["cost_to_save"].apply(
            lambda x: f" (${x:,.0f})" if pd.notna(x) and x > 0 else ""
        )
    else:
        output["cost_str"] = ""

    output["action_text"] = (
        "Cancel PO " + output["po_number"].astype(str) + " line " +
        output["po_line_item"].astype(str) + ": " +
        output["qty_to_cancel"].astype(int).astype(str) + " units" +
        output["cost_str"]
    )

    output = output.sort_values(["cm", "part", "receipt_date"])

    return output


# --- daily to weekly aggregation for reporting ---

def weekly_demand(demand_daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily demand to weekly (by Monday of each week)."""
    if len(demand_daily) == 0:
        return demand_daily.copy()

    out = demand_daily.copy()
    out["week_start"] = _week(out["period"])
    return (
        out.groupby(["cm", "part", "week_start"], as_index=False)["demand"].sum()
        .rename(columns={"week_start": "period"})
    )


def weekly_pab(pab_daily: pd.DataFrame) -> pd.DataFrame:
    """Extract PAB values at week boundaries (Mondays) from daily PAB."""
    if len(pab_daily) == 0:
        return pab_daily.copy()

    out = pab_daily.copy()
    out["week_start"] = _week(out["period"])
    out["is_week_boundary"] = out["period"] == out["week_start"]

    # Keep only week boundaries (Mondays), and for each week, take the last day's PAB
    weekly = out[out["is_week_boundary"]].copy()
    return weekly[["cm", "part", "period", "demand", "receipts", "pab"]].rename(
        columns={"period": "period"})


def weekly_receipts(receipts_daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily receipts to weekly (by Monday of each week)."""
    if len(receipts_daily) == 0:
        return receipts_daily.copy()

    out = receipts_daily.copy()
    out["week_start"] = _week(out["period"])
    return (
        out.groupby(["cm", "part", "week_start"], as_index=False)["receipts"].sum()
        .rename(columns={"week_start": "period"})
    )


# --- orchestration ------------------------------------------------------------

def run(frames: dict | None = None, cfg: Config | None = None) -> dict:
    """Load, normalise, explode, net, and compute runout. Returns every frame.

    Everything the report and the drill-down need is in the returned dict.
    Nothing here touches Streamlit.
    """
    frames = frames or lio.load_all()
    bom = frames["bom_stitched.csv"]
    snap = lio.snapshot_date(frames["onhand.csv"])
    snap = pd.Timestamp(snap) if snap is not None else pd.Timestamp.today()
    cfg = cfg or Config(snapshot=snap)

    norm = nz.normalize_all(frames["onhand.csv"], frames["onorder.csv"], bom)

    # CRITICAL: Calculate WIP from ALL products before filtering, so opening inventory
    # reflects the true stock constraint regardless of which products are in demand.
    products_full = product_master(frames["stitch_list.csv"])
    wip_all = wip_supply(bom, products_full)

    # Now filter products for demand calculations (e.g., Celestica exclusion)
    products = products_full.copy()
    if not cfg.include_celestica:
        products = products[products["cm"] != "Celestica"]

    remaining, backlog = remaining_builds(
        frames["build_plan.csv"], frames["plan_to_date.csv"],
        frames["in_transit.csv"], products, cfg)

    # Regular demand explosion (no pull-forward yet)
    remaining_with_source = remaining.assign(demand_source="Build Plan")
    usage = sourcing_usage(bom)
    demand, demand_detail = explode_demand(remaining_with_source, usage, products)

    # Apply PCBA pull-forward: shift demand 28 days (4 weeks) earlier for parts with Parent PCBA LPN
    # Identify parts that have ANY non-empty Parent PCBA LPN (use set to avoid duplicate rows)
    parts_with_pcba = set(
        bom[(bom["Parent PCBA LPN"].notna()) & (bom["Parent PCBA LPN"] != "")]["item_number"].unique()
    )

    # CHANGE: Shift demand 28 days (4 weeks) earlier for parts with Parent PCBA LPN (daily grain)
    has_pcba_parent = demand["part"].isin(parts_with_pcba)
    demand.loc[has_pcba_parent, "period"] = demand.loc[has_pcba_parent, "period"] - pd.Timedelta(days=28)

    # Same for demand_detail
    has_pcba_parent_detail = demand_detail["part"].isin(parts_with_pcba)
    demand_detail.loc[has_pcba_parent_detail, "period"] = demand_detail.loc[has_pcba_parent_detail, "period"] - pd.Timedelta(days=28)

    oo_eta = add_eta(norm["onorder"])
    receipts, past_due, undated = scheduled_receipts(oo_eta, cfg)
    # Calculate WIP finished goods: completed top-level assemblies at CM not yet ASNd
    wip_fg_df = calc_wip_fg(bom, norm["cm_available"], products_full)
    # Use wip_all (from ALL products) for opening inventory, not just filtered demand
    opening, negatives = opening_inventory(
        norm["cm_available"], wip_all, wip_fg_df)
    states = part_states(demand, norm["onhand"], oo_eta)

    keep = set(products["cm"])
    demand = demand[demand["cm"].isin(keep)]
    opening = opening[opening["cm"].isin(keep)]
    receipts = receipts[receipts["cm"].isin(keep)]

    if cfg.excluded_parts:
        drop = set(cfg.excluded_parts)
        demand = demand[~demand["part"].isin(drop)]
        opening = opening[~opening["part"].isin(drop)]
        receipts = receipts[~receipts["part"].isin(drop)]

    pab, summary = compute_runout(demand, opening, receipts, cfg)
    # Only count usage from products that have actual demand in the build plan
    products_with_demand = set(demand_detail["product"].unique())
    summary = coverage(summary, usage, products_full, products_with_demand)
    summary = classify_shortage_type(summary, pab, receipts)
    summary = (
        summary.merge(states, on=["cm", "part"], how="left")
        .merge(past_due, on=["cm", "part"], how="left")
        .merge(undated, on=["cm", "part"], how="left")
        .merge(usage[["part", "description", "category", "uom"]].drop_duplicates("part"),
               on="part", how="left")
    )
    summary[["past_due", "undated"]] = summary[["past_due", "undated"]].fillna(0.0)
    summary["uom"] = summary["uom"].fillna("each")

    # NOT_SOURCED is an NPI readiness gap, never a shortage (CLAUDE.md 5.7).
    summary["is_shortage"] = summary["is_short"] & (summary["state"] != NOT_SOURCED)
    if not cfg.include_npi:
        summary = summary[summary["state"] != NOT_SOURCED]

    per_part = (demand_detail.groupby(["cm", "part"])["alias"]
                .agg(lambda s: ", ".join(sorted(set(s)))).rename("products"))
    summary = summary.merge(per_part, on=["cm", "part"], how="left")
    summary["shared"] = summary["products"].fillna("").str.contains(",")

    # Compute excess supply
    excess = compute_excess(demand_detail, oo_eta, products)

    # Compute Lunar allocatable per part (uncommitted Lunar-owned inventory)
    # Use onhand data: Lunar-owned rows have uncommitted qty available for allocation
    onhand = norm["onhand"].copy()
    lunar_alloc = onhand[onhand["_owner"] == "Lunar"].copy()
    lunar_alloc = lunar_alloc.groupby("_lpn", as_index=False)["uncommitted_qty"].sum()
    lunar_alloc.columns = ["part", "lunar_available"]

    # Create the result dict
    result = {
        "config": cfg, "snapshot": snap, "products": products,
        "backlog": backlog, "remaining_builds": remaining, "usage": usage,
        "demand": demand, "demand_detail": demand_detail,
        "opening": opening, "negatives": negatives,
        "receipts": receipts, "past_due": past_due, "undated": undated,
        "states": states, "pab": pab,
        "summary": summary.sort_values(
            ["is_shortage", "first_shortage_date", "shortage_qty"],
            ascending=[False, True, False]).reset_index(drop=True),
        "excess": excess,
        "reconciliation": norm["reconciliation"],
        "unmatched": norm["unmatched"],
        "lunar_allocatable": norm["lunar_allocatable"],
    }

    # Apply allocation scenario for the "what-if" case
    result = apply_allocation_scenario(result, lunar_alloc, usage, cfg)

    return result
