"""
WIP (Work-In-Progress) Inventory Calculator

For each component in the BOM, calculates WIP Consumed as:
  WIP Consumed = nearest Buy-ancestor's CM Raw Inventory × cumulative Flat Qty
                 from that ancestor down to the current component

This represents the component inventory physically present at the CM inside
higher-level assemblies (WIP) that haven't yet shipped to Lunar.
"""

import pandas as pd
import logging
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)


def load_bom_and_inventory(bom_path: str, onhand_path: str) -> Tuple[pd.DataFrame, Dict[str, float]]:
    """Load BOM and on-hand inventory, return BOM and CM raw inventory lookup."""
    bom = pd.read_csv(bom_path, dtype={'Parent Product LPN': str, 'item_number': str})
    onhand = pd.read_csv(onhand_path, dtype={'lpn': str})

    # Aggregate on-hand by CM and part (sum across all locations/storage for each CM-part combo)
    # Group by part and CM source
    cm_inventory = {}

    for _, row in onhand.iterrows():
        part = row['lpn']
        owned_by = row['owned_by']
        qty = row['unrestricted_qty']

        key = (part, owned_by)
        if key not in cm_inventory:
            cm_inventory[key] = 0
        cm_inventory[key] += qty

    return bom, cm_inventory


def build_parent_child_map(bom: pd.DataFrame) -> Dict[int, int]:
    """
    Build parent index lookup for indented BOM.

    In an indented BOM, parent of row at level N is the most recent row at level N-1.
    Returns: {child_index: parent_index}
    """
    parent_map = {}
    level_stack = {}  # level -> most recent row index at that level

    for idx, row in bom.iterrows():
        level = row['level']

        if level == 0:
            # Root node has no parent
            level_stack = {0: idx}
        elif level == 1:
            # Direct child of root
            parent_map[idx] = 0
            level_stack[1] = idx
        else:
            # Parent is the most recent row at level-1
            parent_idx = level_stack.get(level - 1)
            if parent_idx is not None:
                parent_map[idx] = parent_idx
            level_stack[level] = idx
            # Clear all deeper levels since we're at a new shallower level
            keys_to_remove = [k for k in level_stack if k > level]
            for k in keys_to_remove:
                del level_stack[k]

    return parent_map


def get_current_flat_qty(bom: pd.DataFrame, row_idx: int) -> float:
    """Get the current row's Flat Qty (the cumulative usage down the tree)."""
    row = bom.iloc[row_idx]
    flat_qty = row['Flat Qty']
    if pd.isna(flat_qty):
        return 0.0
    return float(flat_qty)


def find_nearest_buy_ancestor(bom: pd.DataFrame, parent_map: Dict[int, int],
                               row_idx: int) -> Tuple[int, str]:
    """
    Find the nearest Buy-tagged ancestor for a row.

    Returns: (ancestor_index, cm_source)
    """
    current_idx = row_idx

    while current_idx in parent_map:
        parent_idx = parent_map[current_idx]
        parent_row = bom.iloc[parent_idx]

        if parent_row['makebuy'] == 'Buy':
            # Found a Buy node; now determine which CM
            # Use the vendor or infer from the parent product and item
            return parent_idx, infer_cm_source(parent_row)

        current_idx = parent_idx

    # No Buy ancestor found (all Make or NA)
    return None, None


def infer_cm_source(row: pd.Series) -> str:
    """Infer the CM source from the product and vendor info."""
    # This will be replaced with actual CM mapping from stitch list
    # For now, use vendor field if available
    vendor = row.get('vendor', '')
    if pd.notna(vendor):
        return str(vendor)
    return 'Unknown'


def calculate_wip_consumed(bom: pd.DataFrame, cm_inventory: Dict[str, float] = None) -> pd.DataFrame:
    """
    Calculate WIP Consumed for each row in the BOM.

    WIP Consumed = nearest Buy-ancestor's CM Raw Inventory × current row's Flat Qty

    The CM Raw Inventory is already embedded in the BOM (from on-hand inventory aggregation).
    For each component, we find its nearest Buy-tagged ancestor, use that ancestor's
    CM Raw Inventory, and multiply by the current row's Flat Qty (cumulative usage).
    """
    bom = bom.copy()
    parent_map = build_parent_child_map(bom)

    wip_values = []

    for idx, row in bom.iterrows():
        # Skip root and level 1 (FG itself)
        if row['level'] <= 1:
            wip_values.append(0.0)
            continue

        # Find nearest Buy ancestor
        ancestor_idx, cm_source = find_nearest_buy_ancestor(bom, parent_map, idx)

        if ancestor_idx is None:
            # No Buy ancestor, WIP is 0
            wip_values.append(0.0)
            continue

        # Get ancestor's CM Raw Inventory from the BOM
        ancestor_row = bom.iloc[ancestor_idx]
        ancestor_inventory = ancestor_row['CM Raw Inventory']

        if pd.isna(ancestor_inventory):
            ancestor_inventory = 0.0
        else:
            ancestor_inventory = float(ancestor_inventory)

        # Get current row's Flat Qty (cumulative usage)
        current_flat_qty = get_current_flat_qty(bom, idx)

        # WIP = ancestor inventory × current row's Flat Qty
        wip = ancestor_inventory * current_flat_qty
        wip_values.append(wip)

    bom['WIP Consumed_calculated'] = wip_values
    return bom


def main():
    """Test the WIP calculator on sample data."""
    bom, cm_inventory = load_bom_and_inventory('data/bom_stitched.csv', 'data/onhand.csv')

    print("=== WIP Calculation Test ===\n")
    print(f"BOM rows: {len(bom)}")
    print(f"CM inventory entries: {len(cm_inventory)}\n")

    # Calculate WIP
    bom_with_wip = calculate_wip_consumed(bom, cm_inventory)

    # Compare with existing WIP Consumed
    comparison = bom_with_wip[['level', 'item_number', 'item_name', 'makebuy',
                                'Flat Qty', 'CM Raw Inventory',
                                'WIP Consumed', 'WIP Consumed_calculated']].copy()

    # Filter to rows where WIP > 0 for readability
    nonzero_wip = comparison[(comparison['WIP Consumed'] > 0) | (comparison['WIP Consumed_calculated'] > 0)]

    print("Sample rows with non-zero WIP:")
    print(nonzero_wip.head(20))

    # Summary stats
    print(f"\n\nWIP Comparison Statistics:")
    print(f"Existing WIP Consumed sum: {bom_with_wip['WIP Consumed'].sum():.2f}")
    print(f"Calculated WIP Consumed sum: {bom_with_wip['WIP Consumed_calculated'].sum():.2f}")

    return bom_with_wip


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = main()
