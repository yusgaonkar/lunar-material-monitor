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
    return out


# --- demand, in product space -------------------------------------------------

def remaining_builds(
    build_plan: pd.DataFrame,
    plan_to_date: pd.DataFrame,
    in_transit: pd.DataFrame,
    products: pd.DataFrame,
    cfg: Config,
):
    """Forward plan bucketed to weeks, plus backlog loaded into the first period.

    Returns (remaining, backlog_detail). `backlog_detail` is 18 rows a human can
    check — plan, received, in transit, backlog — and it should be shown in the UI
    next to the build plan editor. A wrong plan_to_date shifts every runout date in
    that product's BOM the same way and nothing looks anomalous (CLAUDE.md 6).

    A monthly plan row is spread evenly over the days of its month, then summed
    into weekly buckets, so a month boundary falling mid-week apportions correctly.
    """
    bp = build_plan.copy()
    bp["period_start"] = pd.to_datetime(bp["period_start"], errors="coerce")
    bp["qty"] = pd.to_numeric(bp["qty"], errors="coerce").fillna(0.0)
    bp = bp[(bp["qty"] != 0) & bp["period_start"].notna()]

    daily = []
    for row in bp.itertuples(index=False):
        start = row.period_start
        days = pd.date_range(start, start + pd.offsets.MonthEnd(0), freq="D")
        if len(days) == 0:
            continue
        daily.append(pd.DataFrame({
            "product": row.product_lpn, "day": days, "qty": row.qty / len(days),
        }))

    if daily:
        d = pd.concat(daily, ignore_index=True)
        d["period"] = _week(d["day"])
        fwd = d.groupby(["product", "period"], as_index=False)["qty"].sum()
        fwd = fwd[fwd["period"] >= cfg.week0]
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

    first = b.loc[b["backlog"] > 0, ["product", "backlog"]].copy()
    first["period"] = cfg.week0
    first = first.rename(columns={"backlog": "qty"})

    remaining = (
        pd.concat([fwd, first[["product", "period", "qty"]]], ignore_index=True)
        .groupby(["product", "period"], as_index=False)["qty"].sum()
    )
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
    """
    detail = (
        remaining.merge(usage, on="product", how="inner")
        .merge(products[["product", "cm", "alias"]], on="product", how="left")
    )
    detail["demand"] = detail["qty"] * detail["usage"]
    detail = detail[["cm", "part", "product", "alias", "period",
                     "qty", "usage", "demand", "description", "category"]]
    total = detail.groupby(["cm", "part", "period"], as_index=False)["demand"].sum()
    return total, detail


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
    """Open CM POs bucketed by eta. Returns (dated, past_due, undated).

    Three buckets, three visual states. Undated supply is real material that we
    refuse to count because we cannot place it in time — never added to the
    balance, and never rendered the same as "no supply exists" (CLAUDE.md 5.3).
    """
    oo = oo_eta[
        (oo_eta["quantity_open"] > 0)
        & (oo_eta["is_closed"].astype(str).str.upper() != "TRUE")
    ].copy()

    undated = oo[oo["_eta"].isna()]
    dated = oo[oo["_eta"].notna()]
    past = dated[dated["_eta"] < cfg.week0]
    future = dated[dated["_eta"] >= cfg.week0].copy()
    future["period"] = _week(future["_eta"])

    def g(d, *keys):
        if len(d) == 0:
            return pd.DataFrame(columns=list(keys) + ["quantity_open"])
        return d.groupby(list(keys), as_index=False)["quantity_open"].sum()

    return (
        g(future, "_cm", "_lpn", "period").rename(
            columns={"_cm": "cm", "_lpn": "part", "quantity_open": "receipts"}),
        g(past, "_cm", "_lpn").rename(
            columns={"_cm": "cm", "_lpn": "part", "quantity_open": "past_due"}),
        g(undated, "_cm", "_lpn").rename(
            columns={"_cm": "cm", "_lpn": "part", "quantity_open": "undated"}),
    )


def opening_inventory(cm_avail: pd.DataFrame, wip: pd.DataFrame):
    """CM-owned raw on-hand + WIP. Negatives floored at zero but counted.

    Lunar-owned stock is NOT added: it already appears as an open CM PO, and
    counting both inflates availability (CLAUDE.md 5.2). Lunar inventory reaches
    the report only through the allocation recommender.
    """
    opening = cm_avail.merge(wip, on=["cm", "part"], how="outer")
    opening[["cm_available", "wip"]] = opening[["cm_available", "wip"]].fillna(0.0)
    negatives = opening[opening["cm_available"] < 0].copy()
    opening["cm_available"] = opening["cm_available"].clip(lower=0.0)
    opening["opening"] = opening["cm_available"] + opening["wip"]
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
    """Projected available balance per period, and the per-part summary.

        PAB[t] = PAB[t-1] + receipts[t] - demand[t]
        PAB[0] = opening inventory

    Returns (pab, summary). `pab` is the drill-down grid; `summary` is one row per
    (cm, part) with the runout date and shortage quantity.
    """
    periods = pd.DataFrame({"period": cfg.periods()})
    # Only parts that carry demand. A part with supply and no demand is not a
    # planning object — including it would bury 21 real shortages under ~2,000
    # rows of inventory that nothing consumes.
    keys = demand[["cm", "part"]].drop_duplicates().reset_index(drop=True)

    grid = keys.merge(periods, how="cross")
    grid = grid.merge(demand, on=["cm", "part", "period"], how="left")
    grid = grid.merge(
        receipts if cfg.use_on_order else receipts.iloc[0:0],
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
        .merge(opening[["cm", "part", "opening", "cm_available", "wip"]],
               on=["cm", "part"], how="left")
    )
    summary["shortage_qty"] = (-summary["min_pab"]).clip(lower=0.0)
    summary["is_short"] = summary["first_shortage_date"].notna()
    summary["weeks_of_cover"] = (
        (summary["first_shortage_date"] - cfg.week0).dt.days // 7)
    return grid, summary


def coverage(summary: pd.DataFrame, usage: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """blocks_buildable = floor(opening / total_usage_at_cm).

    For a given CM, calculates how many complete units can be built before
    running out of this part. Uses total usage from all products at that CM,
    not maximum usage (which would be a bottleneck analysis).

    Takes the BOM usage table (product, part, usage), merges with products
    to get CM, then sums unique usage values per (cm, part).
    """
    # Start with usage table (product, part, sourcing_flat_qty)
    # Merge with products to get CM
    usage_with_cm = usage.merge(products[["product", "cm"]], on="product", how="left")

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
    summary_alloc = coverage(summary_alloc, usage, products_full)
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

    usage = sourcing_usage(bom)
    demand, demand_detail = explode_demand(remaining, usage, products)

    oo_eta = add_eta(norm["onorder"])
    receipts, past_due, undated = scheduled_receipts(oo_eta, cfg)
    # Use wip_all (from ALL products) for opening inventory, not just filtered demand
    opening, negatives = opening_inventory(
        norm["cm_available"], wip_all)
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
    summary = coverage(summary, usage, products_full)
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
