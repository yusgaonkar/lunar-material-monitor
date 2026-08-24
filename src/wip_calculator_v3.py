"""
WIP Calculator v3 - Correct Logic

WIP = inventory embedded in Make assemblies

For each component:
1. Find nearest ancestor with makebuy = 'Make'
2. Use that ancestor's CM Raw Inventory
3. Multiply by cumulative Flat Qty from ancestor to current row
"""

import pandas as pd
import logging
from typing import Dict, Tuple

log = logging.getLogger(__name__)


def load_and_join_inventory(bom_path: str, onhand_path: str) -> pd.DataFrame:
    """Load BOM and join with on-hand inventory."""
    bom = pd.read_csv(bom_path, dtype={'Parent Product LPN': str, 'item_number': str})
    onhand = pd.read_csv(onhand_path, dtype={'lpn': str})

    # Aggregate on-hand by part
    inventory_by_part = onhand.groupby('lpn')['unrestricted_qty'].sum().reset_index()
    inventory_by_part.columns = ['item_number', 'CM_Raw_Inventory_calculated']

    # Join with BOM
    bom = bom.merge(inventory_by_part, on='item_number', how='left')
    bom['CM_Raw_Inventory_calculated'] = bom['CM_Raw_Inventory_calculated'].fillna(0)

    return bom


def build_parent_child_map(bom: pd.DataFrame) -> Dict[int, int]:
    """Build parent index map for indented BOM."""
    parent_map = {}
    level_stack = {}

    for idx, row in bom.iterrows():
        level = row['level']

        if level == 0:
            level_stack = {0: idx}
        elif level == 1:
            parent_map[idx] = 0
            level_stack[1] = idx
        else:
            parent_idx = level_stack.get(level - 1)
            if parent_idx is not None:
                parent_map[idx] = parent_idx
            level_stack[level] = idx
            keys_to_remove = [k for k in level_stack if k > level]
            for k in keys_to_remove:
                del level_stack[k]

    return parent_map


def sum_all_ancestor_inventory(bom: pd.DataFrame, parent_map: Dict[int, int],
                                row_idx: int) -> float:
    """Sum CM Raw Inventory from all ancestors in the parent chain."""
    total_inventory = 0.0
    current_idx = row_idx

    while current_idx in parent_map:
        parent_idx = parent_map[current_idx]
        parent_row = bom.iloc[parent_idx]

        # Add parent's inventory to total
        inventory = parent_row['CM_Raw_Inventory_calculated']
        if pd.notna(inventory):
            total_inventory += float(inventory)

        current_idx = parent_idx

    return total_inventory


def calculate_wip_from_parent_inventory(bom: pd.DataFrame, parent_map: Dict[int, int]) -> pd.DataFrame:
    """
    Calculate WIP as inventory propagating down the assembly tree.

    Formula:
    WIP Consumed = (SUM of all ancestor CM Raw Inventory) × (current row's Flat Qty)

    Each component's WIP represents the cumulative inventory from all parent assemblies
    in its chain, scaled by its usage (Flat Qty).
    """
    bom = bom.copy()
    wip_values = []

    for idx, row in bom.iterrows():
        # Skip root (level 0)
        if row['level'] == 0:
            wip_values.append(0.0)
            continue

        # Sum inventory from all ancestors
        ancestor_inventory_sum = sum_all_ancestor_inventory(bom, parent_map, idx)

        # Current row's Flat Qty
        current_flat_qty = row['Flat Qty']
        if pd.isna(current_flat_qty):
            current_flat_qty = 0.0
        else:
            current_flat_qty = float(current_flat_qty)

        # WIP = sum of ancestor inventory × current flat qty
        wip = ancestor_inventory_sum * current_flat_qty
        wip_values.append(wip)

    bom['WIP Consumed_calculated'] = wip_values
    return bom


def main():
    """Test the WIP calculator."""
    print("=== WIP Calculator v3 (Make assemblies) ===\n")

    bom = load_and_join_inventory('data/bom_stitched.csv', 'data/onhand.csv')

    print(f"BOM rows: {len(bom)}")
    print(f"Make rows: {(bom['makebuy'] == 'Make').sum()}")
    print(f"Buy rows: {(bom['makebuy'] == 'Buy').sum()}")
    print(f"Parts with calculated inventory: {(bom['CM_Raw_Inventory_calculated'] > 0).sum()}\n")

    parent_map = build_parent_child_map(bom)
    bom_result = calculate_wip_from_parent_inventory(bom, parent_map)

    # Compare
    print("WIP Comparison:")
    print(f"Existing WIP sum: {bom_result['WIP Consumed'].sum():.2f}")
    print(f"Calculated WIP sum: {bom_result['WIP Consumed_calculated'].sum():.2f}")

    # Check accuracy
    exact_match = (bom_result['WIP Consumed'] == bom_result['WIP Consumed_calculated']).sum()
    print(f"Exact matches: {exact_match} / {len(bom_result)}")

    # Find mismatches
    mismatch = bom_result[bom_result['WIP Consumed'] != bom_result['WIP Consumed_calculated']]
    print(f"Mismatches: {len(mismatch)}")

    print("\nSample comparison:")
    sample = bom_result[bom_result['WIP Consumed'] > 0].head(30)
    print(sample[['level', 'item_number', 'makebuy', 'Flat Qty',
                  'CM_Raw_Inventory_calculated', 'WIP Consumed', 'WIP Consumed_calculated']])

    return bom_result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = main()
