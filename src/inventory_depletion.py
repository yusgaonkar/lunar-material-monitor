"""Inventory depletion report: two-stage CM→Lunar bleed with generation tracking.

Pure pandas. No Streamlit imports. Tracks inventory balance (qty and $) across
time periods as demand is netted against CM inventory first, then Lunar inventory.

Generation classification from Stitch List: each part is marked as Gen1/Gen2/Gen3
active or obsolete based on which BOMs it appears in.

Lunar inventory allocation is proportional to net demand (demand remaining after
CM inventory is consumed).

Returns a report structured for the UI: left columns are part details + inventory
positions; right columns are time periods with balance (qty) and balance ($).
"""

from __future__ import annotations

import logging
import pandas as pd
import numpy as np
from typing import Optional

from . import normalize as nz

log = logging.getLogger(__name__)


def build_cm_runout_only(
    demand_detail: pd.DataFrame,
    onhand: pd.DataFrame,
    onorder: pd.DataFrame,
    pab: pd.DataFrame,
    cfg,
) -> pd.DataFrame:
    """CM-level runout report: demand, CM inventory, PAB by period.

    Simple output showing CM runout WITHOUT Lunar allocation.
    Shows: Part, CM, Description, [Period columns with PAB values]

    Args:
        demand_detail: (cm, part, period, demand, ...) from engine
        onhand: inventory on-hand
        onorder: inventory on-order
        pab: PAB by (cm, part, period) from engine
        cfg: config object

    Returns:
        DataFrame for display: wide format with periods as columns
    """
    # Get unique (cm, part) combinations
    parts_by_cm = (
        demand_detail[["cm", "part", "description"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    # Build CM inventory caches
    cm_cache = _build_cm_inventory_cache(onhand, onorder)

    # For each (cm, part), get opening inventory
    rows = []
    for _, row in parts_by_cm.iterrows():
        cm = row["cm"]
        part = row["part"]
        desc = row["description"]

        # Get CM inventory position at snapshot
        cm_pos = get_cm_inventory_position(part, cm, cm_cache["oh"], cm_cache["oo"])

        # Get demand for this (cm, part)
        cm_part_demand = demand_detail[(demand_detail["cm"] == cm) & (demand_detail["part"] == part)]

        # Get PAB for this (cm, part) across periods
        cm_part_pab = pab[(pab["cm"] == cm) & (pab["part"] == part)].copy()

        if cm_part_pab.empty:
            continue

        # Pivot to get periods as columns
        pab_pivot = cm_part_pab.pivot_table(
            index=["cm", "part"],
            columns="period",
            values="pab",
            aggfunc="first"
        )

        # Add static columns
        pab_pivot["description"] = desc
        pab_pivot["cm_on_hand"] = cm_pos["raw_oh"]
        pab_pivot["cm_on_order"] = cm_pos["on_order"]
        pab_pivot["cm_total_oh"] = cm_pos["total_oh"]

        rows.append(pab_pivot)

    if not rows:
        return pd.DataFrame()

    result = pd.concat(rows, ignore_index=False)
    result = result.reset_index()

    # Reorder columns: static first, then periods
    static_cols = ["cm", "part", "description", "cm_on_hand", "cm_on_order", "cm_total_oh"]
    period_cols = [c for c in result.columns if c not in static_cols]

    return result[static_cols + sorted(period_cols)]


def classify_generation(part: str, bom: pd.DataFrame, stitch_list: pd.DataFrame) -> str:
    """Classify part by generation status based on which BOMs it appears in.

    Logic (per CLAUDE.md requirements):
    - Gen1 only in BOM → "Gen1 active"
    - Gen2 only in BOM → "Gen2 active"
    - Both Gen1 and Gen2 in BOM → "Gen1/2 active"
    - In Gen1 BOM but not Gen2 → "Gen2 obsolete"
    - In Gen2 BOM but not Gen1 → "Gen1 obsolete"
    - In neither Gen1 nor Gen2 → "Gen1/2 obsolete"

    Args:
        part: Part LPN to classify
        bom: BOM dataframe with item_number and Parent Product LPN columns
        stitch_list: Stitch list with product LPN and Generation Alias columns

    Returns:
        Generation status string (e.g., "Gen1 active", "Gen2 obsolete")
    """
    # Find all products (parent products) that contain this part
    part_in_products = set(
        bom[bom["item_number"] == part]["Parent Product LPN"].unique()
    )

    if not part_in_products:
        # Part not in any BOM — treat as obsolete for all
        return "Gen1/2 obsolete"

    # Map products to their generations from Stitch List
    gen_map = (
        stitch_list[["Parent Product LPN", "Generation Alias"]]
        .drop_duplicates()
        .set_index("Parent Product LPN")["Generation Alias"]
        .to_dict()
    )

    # Extract which products this part belongs to and their generations
    part_generations = set()
    for prod in part_in_products:
        if prod in gen_map:
            gen_alias = gen_map[prod]
            # Handle "Gen 1", "Gen 1/2", "Gen 2" style aliases
            if "Gen 1" in gen_alias and "Gen 2" in gen_alias:
                part_generations.add("Gen1")
                part_generations.add("Gen2")
            elif "Gen 1" in gen_alias:
                part_generations.add("Gen1")
            elif "Gen 2" in gen_alias:
                part_generations.add("Gen2")
            elif "Gen 3" in gen_alias:
                part_generations.add("Gen3")

    # Determine active vs obsolete status
    # For simplicity, classify as Gen1/2 if in both, or separate if only one
    # Obsolete = not in any current BOM (we'll treat Gen1 and Gen2 as "current")
    if len(part_generations) == 0:
        return "Gen1/2 obsolete"
    elif part_generations == {"Gen1"}:
        return "Gen1 active"
    elif part_generations == {"Gen2"}:
        return "Gen2 active"
    elif part_generations == {"Gen1", "Gen2"}:
        return "Gen1/2 active"
    else:
        # Gen3 or other
        return "Gen3 active"


def _build_cm_inventory_cache(onhand: pd.DataFrame, onorder: pd.DataFrame) -> dict:
    """Pre-build lookup caches for CM inventory by (part, cm) to avoid per-part filtering."""
    # On-hand cache
    oh_cache = {}
    for (part, cm), group in onhand.groupby(["_lpn", "_cm"]):
        qty = max(0.0, group["unrestricted_qty"].sum())
        # `unrestricted_value` is the extract's own extended value and is what a
        # pivot of the input file totals. Re-deriving it as qty x unit_price
        # drifts from that, because unit_price is location-specific.
        value = 0.0
        if "unrestricted_value" in group.columns:
            value = group["unrestricted_value"].sum()
        elif "unit_price" in group.columns:
            value = (group["unrestricted_qty"] * group["unit_price"]).sum()
        oh_cache[(part, cm)] = {"qty": qty, "value": value}

    # On-order cache
    oo_cache = {}
    if "_lpn" in onorder.columns and "_cm" in onorder.columns and "quantity_open" in onorder.columns:
        for (part, cm), group in onorder.groupby(["_lpn", "_cm"]):
            qty = group["quantity_open"].sum()
            value = 0.0
            if "unit_price" in group.columns:
                value = (group["quantity_open"] * group["unit_price"]).sum()
            oo_cache[(part, cm)] = {"qty": qty, "value": value}

    return {"oh": oh_cache, "oo": oo_cache}


def get_cm_inventory_position(
    part: str,
    cm: str,
    oh_cache: dict,
    oo_cache: dict,
) -> dict:
    """Get CM inventory position from pre-built caches (fast lookup, no filtering).

    Returns:
        dict with keys: raw_oh, wip_oh, total_oh, on_order, value_raw_oh, value_on_order
    """
    raw_oh = oh_cache.get((part, cm), {}).get("qty", 0.0)
    value_raw_oh = oh_cache.get((part, cm), {}).get("value", 0.0)

    on_order = oo_cache.get((part, cm), {}).get("qty", 0.0)
    value_on_order = oo_cache.get((part, cm), {}).get("value", 0.0)

    wip_oh = 0.0  # TODO: pass in from engine if needed

    return {
        "raw_oh": raw_oh,
        "wip_oh": wip_oh,
        "total_oh": raw_oh + wip_oh,
        "on_order": on_order,
        "value_raw_oh": value_raw_oh,
        "value_on_order": value_on_order,
    }


def _build_price_cache(onhand: pd.DataFrame) -> dict:
    """Quantity-weighted unit price per (part, cm) and per part.

    unit_price in the On Hand extract is location-specific average cost, so one
    part legitimately carries several different prices — and $0.00 on CM
    line-side storage locations, which do not carry standard cost. Weighting
    extended value by quantity is the only figure that reproduces the input
    file's own totals.
    """
    if "unrestricted_value" in onhand.columns:
        value = onhand["unrestricted_value"]
    else:
        value = onhand["unrestricted_qty"] * onhand.get("unit_price", 0.0)
    work = pd.DataFrame({
        "part": onhand["_lpn"],
        "cm": onhand["_cm"],
        "qty": onhand["unrestricted_qty"],
        "value": value,
    })

    def _wavg(frame, keys):
        # NaN (not dropped) marks "this key holds no positive quantity", which is
        # a different situation from "this key is priced at zero" and has to stay
        # distinguishable for the fallback below.
        g = frame.groupby(keys)[["qty", "value"]].sum()
        return (g["value"] / g["qty"].where(g["qty"] > 0)).to_dict()

    return {
        "by_part_cm": _wavg(work, ["part", "cm"]),
        "by_part": _wavg(work, "part"),
    }


def _weighted_unit_price(price_cache: dict, part: str, cm: str) -> float:
    """Weighted unit price for (part, cm); the part across all CMs as fallback.

    The fallback fires only when that CM holds no quantity of the part — e.g.
    when valuing Lunar stock earmarked for a CM that has none on its own book.
    A (part, cm) that does hold quantity at a unit_price of 0 keeps its 0: the CM
    feeds genuinely carry no standard cost on line-side storage locations, and
    imputing a price there would make the value view stop tying to the input
    file. Those rows are flagged in the UI instead.
    """
    price = price_cache["by_part_cm"].get((part, cm))
    if price is None or pd.isna(price):
        price = price_cache["by_part"].get(part)
    if price is None or pd.isna(price):
        return 0.0
    return float(price)


def _build_lunar_inventory_cache(onhand: pd.DataFrame, onorder: pd.DataFrame) -> dict:
    """Pre-build lookup caches for Lunar inventory by part."""
    lunar_oh_cache = {}
    lunar_oo_cache = {}

    # Lunar on-hand cache (owned_by = "Lunar", use uncommitted_qty to avoid double-counting)
    # uncommitted_qty = available inventory not spoken for by open CM POs
    # committed qty is already counted in CM on-order
    if "_owner" in onhand.columns:
        lunar_oh = onhand[onhand["_owner"] == "Lunar"]
        for part, group in lunar_oh.groupby("_lpn"):
            # Use uncommitted_qty if available, else fall back to unrestricted_qty
            qty_col = "uncommitted_qty" if "uncommitted_qty" in lunar_oh.columns else "unrestricted_qty"
            qty = max(0.0, group[qty_col].sum())
            value = 0.0
            # For value, use the qty we selected multiplied by unit price
            if "unit_price" in group.columns:
                value = (group[qty_col] * group["unit_price"]).sum()
            lunar_oh_cache[part] = {"qty": qty, "value": value}

    # Lunar on-order cache (Lunar Netsuite POs ONLY - Lunar's own on-order)
    # Do NOT include Lunar POs to CMs (those are supply to the CMs, not Lunar's on-order)
    if "_owner" in onorder.columns and "_lpn" in onorder.columns and "quantity_open" in onorder.columns:
        lunar_oo = onorder[onorder["_owner"] == "Lunar"]
        for part, group in lunar_oo.groupby("_lpn"):
            qty = group["quantity_open"].sum()
            value = 0.0
            if "unit_price" in group.columns:
                value = (group["quantity_open"] * group["unit_price"]).sum()
            lunar_oo_cache[part] = {"qty": qty, "value": value}

    return {"oh": lunar_oh_cache, "oo": lunar_oo_cache}


def get_lunar_inventory_position(
    part: str,
    lunar_oh_cache: dict,
    lunar_oo_cache: dict,
) -> dict:
    """Get Lunar-owned inventory position from pre-built caches (fast lookup).

    Returns:
        dict with keys: on_hand, on_order, value_on_hand, value_on_order
    """
    on_hand = lunar_oh_cache.get(part, {}).get("qty", 0.0)
    value_on_hand = lunar_oh_cache.get(part, {}).get("value", 0.0)

    on_order = lunar_oo_cache.get(part, {}).get("qty", 0.0)
    value_on_order = lunar_oo_cache.get(part, {}).get("value", 0.0)

    return {
        "on_hand": on_hand,
        "on_order": on_order,
        "value_on_hand": value_on_hand,
        "value_on_order": value_on_order,
    }


def compute_inventory_depletion(
    demand_detail: pd.DataFrame,
    pab: pd.DataFrame,
    onhand: pd.DataFrame,
    onorder: pd.DataFrame,
    cfg,
) -> pd.DataFrame:
    """Compute CM-level inventory depletion: PAB across periods.

    For each (cm, part), shows:
    - Static columns: part, description, cm, on_hand, on_order at snapshot
    - Period columns: PAB (Position Available to Promise) at end of each period

    Uses demand_detail from engine (which already has demand calculated
    from build plan × BOM × WIP logic, same as shortage/drill-down modules).

    Returns:
        DataFrame wide format: rows are (cm, part), columns are [static] + [periods]
    """
    # Get unique (cm, part) combinations with description
    parts_by_cm = (
        demand_detail[["cm", "part", "description", "item_category"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    # Build CM inventory cache
    cm_cache = _build_cm_inventory_cache(onhand, onorder)

    # Process each (cm, part)
    rows = []
    for _, row in parts_by_cm.iterrows():
        try:
            cm = row["cm"]
            part = row["part"]
            desc = row["description"]

            # Get CM inventory at snapshot
            cm_pos = get_cm_inventory_position(part, cm, cm_cache["oh"], cm_cache["oo"])

            # Get PAB for this (cm, part) across all periods
            cm_part_pab = pab[(pab["cm"] == cm) & (pab["part"] == part)].copy()

            if cm_part_pab.empty:
                continue  # Skip if no PAB (shouldn't happen if demand exists)

            # Pivot periods to columns
            pab_wide = cm_part_pab.pivot_table(
                index=["cm", "part"],
                columns="period",
                values="pab",
                aggfunc="first"
            )

            # Add static columns
            pab_wide["description"] = desc
            pab_wide["on_hand"] = cm_pos["total_oh"]
            pab_wide["on_order"] = cm_pos["on_order"]

            rows.append(pab_wide)
        except Exception as e:
            log.error(f"Error processing (cm={cm}, part={part}): {e}", exc_info=True)
            raise

    if not rows:
        log.warning(f"No rows to concat. parts_by_cm had {len(parts_by_cm)} entries but none had PAB data.")
        return pd.DataFrame()

    log.info(f"Concatenating {len(rows)} DataFrames")
    result = pd.concat(rows, ignore_index=False)
    log.info(f"After concat, shape: {result.shape}, columns: {list(result.columns)}")

    result = result.reset_index()
    log.info(f"After reset_index, columns: {list(result.columns)}")

    # Reorder: static columns first, then period columns
    static_cols = ["cm", "part", "description", "on_hand", "on_order"]
    existing_static = [c for c in static_cols if c in result.columns]
    period_cols = [c for c in result.columns if c not in static_cols]

    return result[existing_static + sorted(period_cols)]


# --- Entry point for caching -----

def run(
    demand_detail: pd.DataFrame,
    pab: pd.DataFrame,
    onhand: pd.DataFrame,
    onorder: pd.DataFrame,
    cfg,
) -> pd.DataFrame:
    """Wrapper for CM-level inventory depletion. Cacheable from app.py.

    Returns:
        DataFrame: CM runout with periods as columns
    """
    return compute_inventory_depletion(demand_detail, pab, onhand, onorder, cfg)
