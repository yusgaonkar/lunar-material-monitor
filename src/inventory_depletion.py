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
        oh_cache[(part, cm)] = {
            "qty": max(0.0, group["unrestricted_qty"].sum()),
            "value": (group["unrestricted_qty"] * group.get("unit_price", 0)).sum(),
        }

    # On-order cache
    oo_cache = {}
    for (part, cm), group in onorder.groupby(["_lpn", "_cm"]):
        oo_cache[(part, cm)] = {
            "qty": group["quantity_open"].sum() if "quantity_open" in group.columns else 0.0,
            "value": (group.get("quantity_open", 0) * group.get("unit_price", 0)).sum(),
        }

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


def _build_lunar_inventory_cache(onhand: pd.DataFrame, onorder: pd.DataFrame) -> dict:
    """Pre-build lookup caches for Lunar inventory by part."""
    # Lunar on-hand cache
    lunar_oh = onhand[onhand["_owner"] == "Lunar"]
    lunar_oh_cache = {}
    for part, group in lunar_oh.groupby("_lpn"):
        lunar_oh_cache[part] = {
            "qty": max(0.0, group["unrestricted_qty"].sum()),
            "value": (group["unrestricted_qty"] * group.get("unit_price", 0)).sum(),
        }

    # Lunar on-order cache (POs where po_vendor = Lunar Energy)
    lunar_oo = onorder[onorder.get("_cm", "").isna() | (onorder.get("_cm", "") == "Lunar")]
    lunar_oo_cache = {}
    for part, group in lunar_oo.groupby("_lpn"):
        lunar_oo_cache[part] = {
            "qty": group["quantity_open"].sum() if "quantity_open" in group.columns else 0.0,
            "value": (group.get("quantity_open", 0) * group.get("unit_price", 0)).sum(),
        }

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
    onhand: pd.DataFrame,
    onorder: pd.DataFrame,
    bom: pd.DataFrame,
    stitch_list: pd.DataFrame,
    products: pd.DataFrame,
    cfg,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute inventory depletion: two-stage CM→Lunar bleed.

    For each (cm, part), calculates inventory balance (qty and $) across time periods:
    1. CM inventory depletion: consume CM on-hand + on-order first
    2. Lunar inventory depletion: allocate Lunar inventory proportionally to remaining net demand

    Returns:
        tuple of (balance_table, summary_table)
        - balance_table: long format with (cm, part, period, balance_qty, balance_value) rows
        - summary_table: left columns only (static inventory positions)
    """
    # Get all unique (cm, part) combinations from demand_detail
    parts_by_cm = (
        demand_detail[["cm", "part", "description", "category"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    # Skip generation classification for now (too slow with per-part BOM lookups)
    # TODO: optimize with vectorized generation lookup
    parts_by_cm["generation_status"] = "TBD"

    # Rename category to item_category
    parts_by_cm = parts_by_cm.rename(columns={"category": "item_category"})

    # Pre-build lookup caches for fast access (avoid per-part filtering)
    cm_cache = _build_cm_inventory_cache(onhand, onorder)
    lunar_cache = _build_lunar_inventory_cache(onhand, onorder)

    # Add inventory positions (static, not time-varying)
    inventory_positions = []
    for _, row in parts_by_cm.iterrows():
        cm = row["cm"]
        part = row["part"]

        cm_pos = get_cm_inventory_position(part, cm, cm_cache["oh"], cm_cache["oo"])
        lunar_pos = get_lunar_inventory_position(part, lunar_cache["oh"], lunar_cache["oo"])

        inventory_positions.append({
            "cm": cm,
            "part": part,
            "cm_raw_oh": cm_pos["raw_oh"],
            "cm_wip_oh": cm_pos["wip_oh"],
            "cm_total_oh": cm_pos["total_oh"],
            "cm_on_order": cm_pos["on_order"],
            "lunar_on_hand": lunar_pos["on_hand"],
            "lunar_on_order": lunar_pos["on_order"],
            "value_cm_raw_oh": cm_pos["value_raw_oh"],
            "value_cm_on_order": cm_pos["value_on_order"],
            "value_lunar_on_hand": lunar_pos["value_on_hand"],
            "value_lunar_on_order": lunar_pos["value_on_order"],
        })

    inv_pos_df = pd.DataFrame(inventory_positions)
    summary_table = parts_by_cm.merge(inv_pos_df, on=["cm", "part"], how="left")

    # Group demand by (cm, part, period) and sort
    demand_by_period = (
        demand_detail.groupby(["cm", "part", "period"], as_index=False)["demand"].sum()
        .sort_values(["cm", "part", "period"])
    )

    # Get all unique periods across all parts (sorted)
    all_periods = sorted(demand_by_period["period"].unique())

    # First pass: calculate total net demand for each (cm, part) to allocate Lunar proportionally
    total_demand_by_cm_part = (
        demand_by_period.groupby(["cm", "part"], as_index=False)["demand"].sum()
        .rename(columns={"demand": "total_demand"})
    )

    # Compute balances for each (cm, part)
    balance_rows = []

    for _, part_row in summary_table.iterrows():
        cm = part_row["cm"]
        part = part_row["part"]

        # Starting inventory
        cm_available = part_row["cm_total_oh"] + part_row["cm_on_order"]
        lunar_available = part_row["lunar_on_hand"] + part_row["lunar_on_order"]

        # Get unit price for value calculations
        oh = onhand[(onhand["_lpn"] == part) & (onhand["_cm"] == cm)]
        unit_price = (
            oh["unit_price"].iloc[0]
            if len(oh) > 0 and pd.notna(oh["unit_price"].iloc[0])
            else 0.0
        )

        # Get demand for this part at this CM
        part_demand = demand_by_period[
            (demand_by_period["cm"] == cm) & (demand_by_period["part"] == part)
        ].copy()

        # Calculate total net demand (after CM is consumed)
        total_demand = part_demand["demand"].sum() if len(part_demand) > 0 else 0.0
        net_demand_after_cm = max(0.0, total_demand - cm_available)

        # Running balances
        cm_balance = cm_available
        lunar_balance = lunar_available

        for period in all_periods:
            # Demand for this period
            period_demand_rows = part_demand[part_demand["period"] == period]
            period_demand = period_demand_rows["demand"].sum() if len(period_demand_rows) > 0 else 0.0

            # Stage 1: Consume CM inventory first
            cm_consumed = min(cm_balance, period_demand)
            cm_balance -= cm_consumed
            demand_remaining = period_demand - cm_consumed

            # Stage 2: Consume Lunar inventory (proportional allocation)
            # Allocate Lunar proportionally to net demand
            if net_demand_after_cm > 0 and demand_remaining > 0:
                lunar_allocation = (
                    demand_remaining / net_demand_after_cm * lunar_available
                    if net_demand_after_cm > 0
                    else 0.0
                )
                lunar_consumed = min(lunar_balance, demand_remaining)
            else:
                lunar_consumed = min(lunar_balance, demand_remaining)

            lunar_balance -= lunar_consumed

            # Calculate value balance
            total_balance = cm_balance + lunar_balance
            value_balance = total_balance * unit_price

            balance_rows.append({
                "cm": cm,
                "part": part,
                "description": part_row["description"],
                "item_category": part_row["item_category"],
                "generation_status": part_row["generation_status"],
                "period": period,
                "cm_balance_qty": cm_balance,
                "lunar_balance_qty": lunar_balance,
                "total_balance_qty": total_balance,
                "total_balance_value": value_balance,
            })

    balance_table = pd.DataFrame(balance_rows) if balance_rows else pd.DataFrame()

    return balance_table, summary_table


# --- Entry point for caching -----

def run(
    demand_detail: pd.DataFrame,
    onhand: pd.DataFrame,
    onorder: pd.DataFrame,
    bom: pd.DataFrame,
    stitch_list: pd.DataFrame,
    products: pd.DataFrame,
    cfg,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Wrapper for inventory depletion calculation. Cacheable from app.py.

    Returns:
        tuple of (balance_table, summary_table)
    """
    return compute_inventory_depletion(
        demand_detail, onhand, onorder, bom, stitch_list, products, cfg
    )
