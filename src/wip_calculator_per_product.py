"""
WIP Calculator - Per-Product with CM-Specific Inventory Join

Steps:
1. Load stitch list to map products → CMs
2. For each product's BOM:
   - Load BOM rows for that product
   - Join inventory from THAT CM only
   - Calculate WIP cumulatively
3. Compare to Product BOM file - must match EXACTLY
"""

import pandas as pd
import logging
from typing import Dict

log = logging.getLogger(__name__)


def load_stitch_list(path: str) -> Dict[str, str]:
    """Load product → CM mapping from stitch list."""
    stitch = pd.read_csv(path, dtype={'Parent Product LPN': str})
    product_cm_map = {}
    for _, row in stitch.iterrows():
        product_lpn = row['Parent Product LPN']
        cm = row['CM']
        product_cm_map[product_lpn] = cm
    return product_cm_map


def map_cm_name_to_inventory_source(cm_name: str) -> str:
    """Map stitch list CM name to inventory source_report value."""
    if pd.isna(cm_name) or cm_name == 'TBD':
        return None

    # Map known CMs
    cm_map = {
        'Sienna': 'CM: Sienna GA',
        'Qualitel': 'CM: Qualitel WA',
        'Celestica': 'CM: Celestica MX',
    }

    for key, source in cm_map.items():
        if key.lower() in str(cm_name).lower():
            return source

    # If not matched, return None
    return None


def load_inventory_for_cm(inventory_path: str, cm_source: str) -> Dict[str, float]:
    """Load on-hand inventory for a specific CM."""
    inventory = pd.read_csv(inventory_path, dtype={'lpn': str})

    # Filter to this CM and aggregate by part
    cm_inv = inventory[inventory['source_report'] == cm_source]
    cm_inv_agg = cm_inv.groupby('lpn')['unrestricted_qty'].sum().reset_index()
    cm_inv_agg.columns = ['item_number', 'CM_Raw_Inventory']

    # Convert to dict for lookup
    inv_dict = dict(zip(cm_inv_agg['item_number'], cm_inv_agg['CM_Raw_Inventory']))

    return inv_dict


def build_parent_child_map(bom_product: pd.DataFrame) -> Dict[int, int]:
    """Build parent map for a product's BOM rows."""
    parent_map = {}
    level_stack = {}

    for idx, row in bom_product.iterrows():
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


def calculate_wip_for_product(bom_product: pd.DataFrame,
                               cm_inventory: Dict[str, float],
                               parent_map: Dict[int, int]) -> pd.DataFrame:
    """Calculate WIP using nearest ancestor's CM Raw Inventory × current Flat Qty."""
    bom_product = bom_product.copy()

    # Add joined inventory to BOM
    bom_product['CM_Raw_Inventory_joined'] = bom_product['item_number'].map(
        lambda x: cm_inventory.get(str(x), 0.0)
    )

    wip_values = {}

    # Process rows in order
    for idx, row in bom_product.iterrows():
        level = row['level']

        # Skip root (FG) and level 1
        if level <= 1:
            wip_values[idx] = 0.0
            continue

        # For level 2, also WIP = 0 (these are direct children of FG, procured items)
        if level == 2:
            wip_values[idx] = 0.0
            continue

        # Use immediate parent (not nearest ancestor with inventory)
        parent_idx = parent_map.get(idx)

        if parent_idx is not None:
            parent_row = bom_product.iloc[parent_idx]
            parent_inventory = float(parent_row['CM_Raw_Inventory_joined'])
            parent_wip = wip_values.get(parent_idx, 0.0)
        else:
            parent_inventory = 0.0
            parent_wip = 0.0

        # Current row's Usage Qty (per-unit multiplier)
        usage_qty = float(row['Usage Qty']) if pd.notna(row['Usage Qty']) else 0.0

        # WIP = (parent_CM_Raw_Inventory + parent_WIP) × current_Usage_Qty
        wip = (parent_inventory + parent_wip) * usage_qty
        wip_values[idx] = wip

    bom_product['WIP_calculated'] = [wip_values[i] for i in bom_product.index]
    return bom_product


def main():
    """Calculate WIP per product and compare to Product BOM."""
    print("=== WIP Calculator: Per-Product with CM-Specific Joins ===\n")

    # Load data
    bom_full = pd.read_csv('data/bom_stitched.csv', dtype={'item_number': str, 'Parent Product LPN': str})
    product_cm_map = load_stitch_list('data/stitch_list.csv')
    product_bom = pd.read_csv('data/bom_stitched.csv', dtype={'item_number': str, 'Parent Product LPN': str})

    # Test with single product: HW Kit 90-001223
    test_product = '90-001223'

    print(f"Testing product: {test_product}\n")

    # Get CM for this product
    product_cm = product_cm_map.get(test_product)
    print(f"Product CM: {product_cm}")

    if pd.isna(product_cm) or product_cm == 'TBD':
        print(f"  → CM is {product_cm}, skipping\n")
        return

    cm_source = map_cm_name_to_inventory_source(product_cm)
    print(f"CM source in inventory: {cm_source}\n")

    # Load inventory for this CM
    cm_inventory = load_inventory_for_cm('data/onhand.csv', cm_source)
    print(f"Parts in {product_cm} inventory: {len(cm_inventory)}")
    print(f"Total on-hand at {product_cm}: {sum(cm_inventory.values()):.0f}\n")

    # Get BOM for this product
    bom_product = bom_full[bom_full['Parent Product LPN'] == test_product].copy()
    bom_product = bom_product.reset_index(drop=True)

    print(f"BOM rows for {test_product}: {len(bom_product)}")

    # Calculate WIP
    parent_map = build_parent_child_map(bom_product)
    bom_result = calculate_wip_for_product(bom_product, cm_inventory, parent_map)

    # Compare to Product BOM
    product_bom_product = product_bom[product_bom['Parent Product LPN'] == test_product].copy()
    product_bom_product = product_bom_product.reset_index(drop=True)

    # Compare CM Raw Inventory (CM-specific join)
    print("CM Raw Inventory Comparison (CM-specific):")
    calc_cm_raw = bom_result['CM_Raw_Inventory_joined'].sum()
    print(f"  Calculated (from {cm_source}): {calc_cm_raw:.0f}")
    print(f"  Note: Product BOM's CM Raw Inventory is global (across all CMs), so not directly comparable.\n")

    # Compare WIP (level >= 2)
    bom_non_fg = bom_result[bom_result['level'] >= 2]
    product_non_fg = product_bom_product[product_bom_product['level'] >= 2]

    print("WIP Consumed Comparison (level >= 2):")
    calc_wip = bom_non_fg['WIP_calculated'].sum()
    expected_wip = product_non_fg['WIP Consumed'].sum()
    print(f"  Calculated: {calc_wip:.0f}")
    print(f"  Expected:   {expected_wip:.0f}")
    print(f"  Match: {calc_wip == expected_wip}\n")

    # Check exact matches
    exact_matches = 0
    mismatches = []

    for idx in bom_non_fg.index:
        calc = bom_result.iloc[idx]['WIP_calculated']
        expected = product_bom_product.iloc[idx]['WIP Consumed']

        if calc == expected:
            exact_matches += 1
        else:
            mismatches.append({
                'idx': idx,
                'pn': bom_result.iloc[idx]['item_number'],
                'calc': calc,
                'expected': expected
            })

    print(f"Exact WIP matches: {exact_matches} / {len(bom_non_fg)} ({100*exact_matches/len(bom_non_fg):.1f}%)")

    if mismatches:
        print(f"\nAll mismatches ({len(mismatches)} total):")
        for m in mismatches:
            row_data = bom_result.iloc[m['idx']]
            print(f"  Row {m['idx']}: {m['pn']} | calc={m['calc']:.1f}, expected={m['expected']:.1f} | " +
                  f"Flat Qty={row_data['Flat Qty']}, CM Raw Inv={row_data['CM_Raw_Inventory_joined']:.0f}")

    # Show some sample rows
    print("\n\nSample rows (with joined inventory):")
    sample = bom_result[['level', 'item_number', 'CM_Raw_Inventory_joined', 'Usage Qty', 'WIP_calculated', 'WIP Consumed']].head(20)
    print(sample.to_string())

    return bom_result


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    result = main()
