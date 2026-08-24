"""
WIP Calculator - All Products with CM-Specific Inventory Joins

Formula: WIP[current] = (parent_CM_Raw_Inventory + parent_WIP) × current_Usage_Qty
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

    cm_map = {
        'Sienna': 'CM: Sienna GA',
        'Qualitel': 'CM: Qualitel WA',
        'Celestica': 'CM: Celestica MX',
    }

    for key, source in cm_map.items():
        if key.lower() in str(cm_name).lower():
            return source

    return None


def load_inventory_for_cm(inventory_path: str, cm_source: str, cm_name: str) -> Dict[str, float]:
    """Load CM-owned on-hand inventory for a specific CM."""
    if cm_source is None:
        return {}

    inventory = pd.read_csv(inventory_path, dtype={'lpn': str})

    # Map CM name to owned_by value
    owned_by_map = {
        'CM: Sienna GA': 'Sienna Corporation',
        'CM: Qualitel WA': 'Qualitel',
        'CM: Celestica MX': 'Celestica',
    }
    owned_by = owned_by_map.get(cm_source)

    # Filter by location AND ownership
    if owned_by:
        cm_inv = inventory[
            (inventory['source_report'] == cm_source) &
            (inventory['owned_by'] == owned_by)
        ]
    else:
        cm_inv = inventory[inventory['source_report'] == cm_source]

    cm_inv_agg = cm_inv.groupby('lpn')['unrestricted_qty'].sum().reset_index()
    cm_inv_agg.columns = ['item_number', 'CM_Raw_Inventory']

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
    """Calculate WIP: (parent_CM_Raw_Inventory + parent_WIP) × current_Usage_Qty."""
    bom_product = bom_product.copy()

    # Join inventory
    bom_product['CM_Raw_Inventory_joined'] = bom_product['item_number'].map(
        lambda x: cm_inventory.get(str(x), 0.0)
    )

    wip_values = {}

    # Process rows in order
    for idx, row in bom_product.iterrows():
        level = row['level']

        # Skip root and level 1
        if level <= 1:
            wip_values[idx] = 0.0
            continue

        # Skip level 2
        if level == 2:
            wip_values[idx] = 0.0
            continue

        # For level >= 3: use immediate parent
        parent_idx = parent_map.get(idx)

        if parent_idx is not None:
            parent_row = bom_product.iloc[parent_idx]
            parent_inventory = float(parent_row['CM_Raw_Inventory_joined'])
            parent_wip = wip_values.get(parent_idx, 0.0)
        else:
            parent_inventory = 0.0
            parent_wip = 0.0

        # Current row's Usage Qty
        usage_qty = float(row['Usage Qty']) if pd.notna(row['Usage Qty']) else 0.0

        # WIP = (parent_CM_Raw_Inventory + parent_WIP) × current_Usage_Qty
        wip = (parent_inventory + parent_wip) * usage_qty
        wip_values[idx] = wip

    bom_product['WIP_calculated'] = [wip_values[i] for i in bom_product.index]
    return bom_product


def main():
    """Calculate WIP for all products and report statistics."""
    print("=== WIP Calculator: All Products ===\n")

    # Load data
    bom_full = pd.read_csv('data/bom_stitched.csv', dtype={'item_number': str, 'Parent Product LPN': str})
    product_cm_map = load_stitch_list('data/stitch_list.csv')
    product_bom = pd.read_csv('data/bom_stitched.csv', dtype={'item_number': str, 'Parent Product LPN': str})

    # Get all products
    all_products = [p for p in bom_full['Parent Product LPN'].unique() if pd.notna(p) and p != '']
    all_products = sorted(all_products)

    results = []

    for product_lpn in all_products:
        # Get CM for this product
        product_cm = product_cm_map.get(product_lpn)

        if pd.isna(product_cm) or product_cm == 'TBD':
            print(f"⊘ {product_lpn}: CM = {product_cm}, skipping")
            continue

        cm_source = map_cm_name_to_inventory_source(product_cm)
        if cm_source is None:
            print(f"⊘ {product_lpn}: CM source not found for {product_cm}, skipping")
            continue

        # Load inventory for this CM (CM-owned only)
        cm_inventory = load_inventory_for_cm('data/onhand.csv', cm_source, product_cm)

        # Get BOM for this product
        bom_product = bom_full[bom_full['Parent Product LPN'] == product_lpn].copy()
        bom_product = bom_product.reset_index(drop=True)

        if len(bom_product) == 0:
            continue

        # Calculate WIP
        parent_map = build_parent_child_map(bom_product)
        bom_result = calculate_wip_for_product(bom_product, cm_inventory, parent_map)

        # Compare to Product BOM
        product_bom_product = product_bom[product_bom['Parent Product LPN'] == product_lpn].copy()
        product_bom_product = product_bom_product.reset_index(drop=True)

        # Compare WIP (level >= 2)
        bom_non_fg = bom_result[bom_result['level'] >= 2]
        product_non_fg = product_bom_product[product_bom_product['level'] >= 2]

        if len(bom_non_fg) == 0:
            continue

        calc_wip = bom_non_fg['WIP_calculated'].sum()
        expected_wip = product_non_fg['WIP Consumed'].sum()

        # Count exact matches (with tolerance for floating point)
        exact_matches = 0
        for idx in bom_non_fg.index:
            calc = bom_result.iloc[idx]['WIP_calculated']
            expected = product_bom_product.iloc[idx]['WIP Consumed']
            # Use tolerance for floating point comparison
            if abs(calc - expected) < 1e-6:
                exact_matches += 1

        match_pct = 100 * exact_matches / len(bom_non_fg) if len(bom_non_fg) > 0 else 100
        wip_diff_pct = abs(calc_wip - expected_wip) / expected_wip * 100 if expected_wip > 0 else 0

        results.append({
            'product': product_lpn,
            'cm': product_cm,
            'rows': len(bom_product),
            'exact_matches': exact_matches,
            'total_rows': len(bom_non_fg),
            'match_pct': match_pct,
            'wip_diff_pct': wip_diff_pct,
            'calc_wip': calc_wip,
            'expected_wip': expected_wip
        })

        status = "✓" if wip_diff_pct < 0.01 else "⚠" if wip_diff_pct < 1.0 else "✗"
        print(f"{status} {product_lpn}: {exact_matches}/{len(bom_non_fg)} matches ({match_pct:.1f}%), WIP diff {wip_diff_pct:.4f}%")

    # Summary table
    print("\n\n=== Summary Statistics ===\n")
    if results:
        results_df = pd.DataFrame(results)
        print(f"{'Product':<15} {'CM':<12} {'Rows':<6} {'Matches':<10} {'Match %':<10} {'WIP Diff %':<12}")
        print("-" * 75)
        for _, row in results_df.iterrows():
            print(f"{row['product']:<15} {row['cm']:<12} {row['rows']:<6} {row['exact_matches']}/{row['total_rows']:<8} "
                  f"{row['match_pct']:<10.1f} {row['wip_diff_pct']:<12.6f}")

        # Overall stats
        print("\n" + "=" * 75)
        total_rows = results_df['total_rows'].sum()
        total_matches = results_df['exact_matches'].sum()
        overall_match_pct = 100 * total_matches / total_rows if total_rows > 0 else 0

        total_calc_wip = results_df['calc_wip'].sum()
        total_expected_wip = results_df['expected_wip'].sum()
        overall_wip_diff_pct = abs(total_calc_wip - total_expected_wip) / total_expected_wip * 100 if total_expected_wip > 0 else 0

        print(f"OVERALL: {total_matches}/{total_rows} exact matches ({overall_match_pct:.1f}%)")
        print(f"OVERALL WIP: {total_calc_wip:.0f} calculated vs {total_expected_wip:.0f} expected (diff {overall_wip_diff_pct:.4f}%)")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
