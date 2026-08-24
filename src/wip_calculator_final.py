"""
WIP Calculator - Final Correct Version

Formula: WIP[row] = (WIP[parent] + CM_Raw_Inventory[row]) × Usage_Qty[row]

This is a cumulative calculation where each row inherits WIP from its parent,
adds its own inventory, and multiplies by usage to propagate to children.
"""

import pandas as pd
import logging
from typing import Dict

log = logging.getLogger(__name__)


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


def calculate_wip_cumulative(bom: pd.DataFrame, parent_map: Dict[int, int]) -> pd.DataFrame:
    """
    Calculate WIP using cumulative bottom-up propagation.

    Formula:
    WIP[row] = (WIP[parent] + CM_Raw_Inventory[row]) × Usage_Qty[row]

    Each row's WIP is the cumulative inventory (from parent's WIP + own inventory)
    scaled by this row's usage to propagate to its children.
    """
    bom = bom.copy()
    wip_values = {}

    # Process in order (parent indices are always smaller than child indices in indented BOM)
    for idx in range(len(bom)):
        row = bom.iloc[idx]

        # Skip root (level 0)
        if row['level'] == 0:
            wip_values[idx] = 0.0
            continue

        # Get parent's WIP
        parent_idx = parent_map.get(idx)
        if parent_idx is None:
            parent_wip = 0.0
        else:
            parent_wip = wip_values.get(parent_idx, 0.0)

        # Own inventory from BOM
        own_inventory = row['CM Raw Inventory']
        if pd.isna(own_inventory):
            own_inventory = 0.0
        else:
            own_inventory = float(own_inventory)

        # Usage Qty from BOM
        usage_qty = row['Usage Qty']
        if pd.isna(usage_qty):
            usage_qty = 0.0
        else:
            usage_qty = float(usage_qty)

        # WIP = (parent WIP + own inventory) × usage qty
        wip = (parent_wip + own_inventory) * usage_qty
        wip_values[idx] = wip

    # Add to DataFrame
    bom['WIP Consumed_calculated'] = [wip_values[i] for i in range(len(bom))]
    return bom


def main():
    """Test the WIP calculator."""
    bom = pd.read_csv('data/bom_stitched.csv', dtype={'item_number': str})

    print("=== WIP Calculator (Cumulative Formula) ===\n")
    print(f"Total rows: {len(bom)}")

    parent_map = build_parent_child_map(bom)
    bom_result = calculate_wip_cumulative(bom, parent_map)

    # Exclude FG (level 0 and 1)
    bom_non_fg = bom_result[bom_result['level'] >= 2].copy()

    print(f"Rows to compare (level >= 2): {len(bom_non_fg)}")

    # Check exact matches
    exact_match = (bom_non_fg['WIP Consumed'] == bom_non_fg['WIP Consumed_calculated']).sum()
    print(f"Exact matches: {exact_match} / {len(bom_non_fg)} ({100*exact_match/len(bom_non_fg):.1f}%)\n")

    # Check totals
    print(f"Expected WIP sum: {bom_non_fg['WIP Consumed'].sum():.2f}")
    print(f"Calculated WIP sum: {bom_non_fg['WIP Consumed_calculated'].sum():.2f}")

    # Find mismatches
    mismatches = bom_non_fg[bom_non_fg['WIP Consumed'] != bom_non_fg['WIP Consumed_calculated']]
    print(f"\nRemaining mismatches: {len(mismatches)}")

    if len(mismatches) > 0 and len(mismatches) <= 20:
        print("\nMismatched rows:")
        print(mismatches[['level', 'item_number', 'CM Raw Inventory', 'Usage Qty',
                          'WIP Consumed', 'WIP Consumed_calculated']])

    return bom_result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = main()
