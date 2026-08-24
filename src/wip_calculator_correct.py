"""
WIP and CM Raw Inventory Calculator - CORRECT VERSION

Steps:
1. Load BOM (ignore existing CM Raw Inventory and WIP Consumed)
2. Join with inventory file to populate CM Raw Inventory per part
3. Calculate WIP using cumulative formula: WIP = (parent_WIP + own_CM_Raw_Inventory) × Usage_Qty
4. Compare to Product BOM file values - must match EXACTLY
"""

import pandas as pd
import logging
from typing import Dict

log = logging.getLogger(__name__)


def load_and_prepare(bom_path: str, inventory_path: str) -> pd.DataFrame:
    """Load BOM and join with inventory to populate CM Raw Inventory."""
    bom = pd.read_csv(bom_path, dtype={'item_number': str})
    inventory = pd.read_csv(inventory_path, dtype={'lpn': str})

    # Aggregate inventory by part (sum across all locations/CMs)
    inventory_agg = inventory.groupby('lpn')['unrestricted_qty'].sum().reset_index()
    inventory_agg.columns = ['item_number', 'CM_Raw_Inventory_from_inventory']

    # Join with BOM
    bom = bom.merge(inventory_agg, on='item_number', how='left')
    bom['CM_Raw_Inventory_from_inventory'] = bom['CM_Raw_Inventory_from_inventory'].fillna(0)

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


def calculate_wip_cumulative(bom: pd.DataFrame, parent_map: Dict[int, int]) -> pd.DataFrame:
    """Calculate WIP cumulatively using inventory from joined file."""
    bom = bom.copy()
    wip_values = {}

    # Process rows in order (parents before children)
    for idx in range(len(bom)):
        row = bom.iloc[idx]

        # Skip root
        if row['level'] == 0:
            wip_values[idx] = 0.0
            continue

        # Get parent's WIP
        parent_idx = parent_map.get(idx)
        parent_wip = wip_values.get(parent_idx, 0.0) if parent_idx is not None else 0.0

        # Own inventory from joined inventory file
        own_inventory = float(row['CM_Raw_Inventory_from_inventory']) if pd.notna(row['CM_Raw_Inventory_from_inventory']) else 0.0

        # Usage Qty from BOM
        usage_qty = float(row['Usage Qty']) if pd.notna(row['Usage Qty']) else 0.0

        # WIP = (parent WIP + own inventory) × usage qty
        wip = (parent_wip + own_inventory) * usage_qty
        wip_values[idx] = wip

    bom['WIP_Consumed_calculated'] = [wip_values[i] for i in range(len(bom))]
    return bom


def main():
    """Calculate WIP and compare to Product BOM."""
    print("=== WIP Calculator: Inventory Join + Cumulative Calculation ===\n")

    # Load and join
    bom = load_and_prepare('data/bom_stitched.csv', 'data/onhand.csv')
    print(f"BOM rows: {len(bom)}")
    print(f"Parts with inventory from join: {(bom['CM_Raw_Inventory_from_inventory'] > 0).sum()}\n")

    # Calculate WIP
    parent_map = build_parent_child_map(bom)
    bom_result = calculate_wip_cumulative(bom, parent_map)

    # Load Product BOM to compare
    product_bom = pd.read_csv('data/bom_stitched.csv', dtype={'item_number': str})

    # Compare CM Raw Inventory
    print("CM Raw Inventory Comparison:")
    print(f"  My CM Raw Inv sum: {bom_result['CM_Raw_Inventory_from_inventory'].sum():.0f}")
    print(f"  Product BOM CM Raw Inv sum: {product_bom['CM Raw Inventory'].sum():.0f}")

    # Compare WIP (level >= 2 only)
    bom_non_fg = bom_result[bom_result['level'] >= 2]
    product_non_fg = product_bom[product_bom['level'] >= 2]

    print(f"\nWIP Consumed Comparison (level >= 2):")
    print(f"  My WIP sum: {bom_non_fg['WIP_Consumed_calculated'].sum():.0f}")
    print(f"  Product BOM WIP sum: {product_non_fg['WIP Consumed'].sum():.0f}")

    # Check exact matches by row index
    wip_exact = 0
    wip_total = 0
    wip_mismatches = []

    for idx in bom_non_fg.index:
        calc_wip = bom_result.iloc[idx]['WIP_Consumed_calculated']
        exp_wip = product_bom.iloc[idx]['WIP Consumed']

        if calc_wip == exp_wip:
            wip_exact += 1
        else:
            wip_mismatches.append({
                'row': idx,
                'pn': bom_result.iloc[idx]['item_number'],
                'calc': calc_wip,
                'expected': exp_wip
            })
        wip_total += 1

    print(f"\n  Exact WIP matches: {wip_exact} / {wip_total} ({100*wip_exact/wip_total:.1f}%)")

    if wip_mismatches and len(wip_mismatches) <= 10:
        print(f"\n  Sample mismatches (first 10):")
        for m in wip_mismatches[:10]:
            print(f"    Row {m['row']}: {m['pn']} - calc={m['calc']:.0f}, expected={m['expected']:.0f}")

    return bom_result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = main()
