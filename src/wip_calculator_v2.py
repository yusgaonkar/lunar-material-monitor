"""
WIP Calculator v2 - Correct approach

Steps:
1. Load BOM structure
2. Join with on-hand inventory to populate CM Raw Inventory per part
3. Calculate WIP bottom-up through the tree
   WIP = own inventory + sum of children's WIP

Then scale by Flat Qty for usage multiplier.
"""

import pandas as pd
import logging
from typing import Dict, Tuple

log = logging.getLogger(__name__)


def load_and_join_inventory(bom_path: str, onhand_path: str) -> pd.DataFrame:
    """
    Load BOM and join with on-hand inventory.

    Returns BOM with CM Raw Inventory populated for each part.
    """
    bom = pd.read_csv(bom_path, dtype={'Parent Product LPN': str, 'item_number': str})
    onhand = pd.read_csv(onhand_path, dtype={'lpn': str})

    # Aggregate on-hand by part (sum across all locations, CMs, etc.)
    # Group by lpn to get total on-hand per part
    inventory_by_part = onhand.groupby('lpn')['unrestricted_qty'].sum().reset_index()
    inventory_by_part.columns = ['item_number', 'CM_Raw_Inventory_from_onhand']

    # Join with BOM
    bom = bom.merge(inventory_by_part, on='item_number', how='left')
    bom['CM_Raw_Inventory_from_onhand'] = bom['CM_Raw_Inventory_from_onhand'].fillna(0)

    return bom, inventory_by_part


def build_parent_child_map(bom: pd.DataFrame) -> Tuple[Dict[int, int], Dict[int, list]]:
    """
    Build parent->child and child->parent maps for the indented BOM.

    Returns:
        parent_map: {child_idx: parent_idx}
        children_map: {parent_idx: [child_idx, ...]}
    """
    parent_map = {}
    children_map = {}
    level_stack = {}  # level -> most recent row index at that level

    for idx, row in bom.iterrows():
        level = row['level']

        if level == 0:
            level_stack = {0: idx}
            children_map[idx] = []
        elif level == 1:
            parent_map[idx] = 0
            if 0 not in children_map:
                children_map[0] = []
            children_map[0].append(idx)
            level_stack[1] = idx
        else:
            parent_idx = level_stack.get(level - 1)
            if parent_idx is not None:
                parent_map[idx] = parent_idx
                if parent_idx not in children_map:
                    children_map[parent_idx] = []
                children_map[parent_idx].append(idx)
            level_stack[level] = idx
            # Clear deeper levels
            keys_to_remove = [k for k in level_stack if k > level]
            for k in keys_to_remove:
                del level_stack[k]

    return parent_map, children_map


def calculate_wip_bottomup(bom: pd.DataFrame, parent_map: Dict[int, int],
                            children_map: Dict[int, list]) -> pd.DataFrame:
    """
    Calculate WIP bottom-up through the tree.

    For each row:
      wip_consumed = own_cm_raw_inventory + sum(children's wip_consumed)

    Then scale by Flat Qty to account for usage multipliers.
    """
    bom = bom.copy()
    wip_values = [0.0] * len(bom)

    # Process in reverse order (children before parents)
    for idx in range(len(bom) - 1, -1, -1):
        row = bom.iloc[idx]
        own_inventory = row['CM_Raw_Inventory_from_onhand']
        if pd.isna(own_inventory):
            own_inventory = 0.0

        # Sum WIP from all children
        children_wip = sum(wip_values[child_idx] for child_idx in children_map.get(idx, []))

        # WIP = own inventory + children's WIP
        wip_base = own_inventory + children_wip

        # Scale by Flat Qty
        flat_qty = row['Flat Qty']
        if pd.isna(flat_qty):
            flat_qty = 1.0

        wip_values[idx] = wip_base * flat_qty

    bom['WIP Consumed_calculated'] = wip_values
    return bom


def main():
    """Test the WIP calculator."""
    print("=== WIP Calculator v2 (with inventory join) ===\n")

    bom, inv_agg = load_and_join_inventory('data/bom_stitched.csv', 'data/onhand.csv')

    print(f"BOM rows: {len(bom)}")
    print(f"Parts with on-hand inventory: {(inv_agg['CM_Raw_Inventory_from_onhand'] > 0).sum()}\n")

    # Check if inventory joined correctly
    print("Sample CM Raw Inventory values:")
    print(bom[bom['CM_Raw_Inventory_from_onhand'] > 0][['item_number', 'item_name', 'CM_Raw_Inventory_from_onhand']].head(10))

    parent_map, children_map = build_parent_child_map(bom)
    bom_result = calculate_wip_bottomup(bom, parent_map, children_map)

    # Compare
    print("\n\nWIP Comparison:")
    print(f"Existing WIP sum: {bom_result['WIP Consumed'].sum():.2f}")
    print(f"Calculated WIP sum: {bom_result['WIP Consumed_calculated'].sum():.2f}")

    print("\nSample comparison (first 20 rows with non-zero WIP):")
    nonzero = bom_result[bom_result['WIP Consumed'] > 0]
    print(nonzero[['level', 'item_number', 'makebuy', 'Flat Qty',
                   'CM_Raw_Inventory_from_onhand', 'WIP Consumed', 'WIP Consumed_calculated']].head(20))

    return bom_result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = main()
