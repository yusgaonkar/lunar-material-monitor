"""Process ASN (Advanced Shipping Notice) data from CM exports.

Aggregates shipments by product and date range.
"""

import pandas as pd
from datetime import datetime


def process_asn_file(filepath: str, start_date: str, end_date: str, cm: str = 'sienna') -> pd.DataFrame:
    """
    Process ASN file and aggregate by product.

    Args:
        filepath: Path to ASN CSV
        start_date: YYYY-MM-DD format
        end_date: YYYY-MM-DD format
        cm: 'sienna' or 'qualitel' (schema differs)

    Returns:
        DataFrame with columns: product_lpn, asn_qty (aggregated by product)
    """
    df = pd.read_csv(filepath)

    if cm.lower() == 'sienna':
        date_col = 'shipped_date'
        part_col = 'customer_part_number'
        qty_col = 'quantity'
    else:  # qualitel
        date_col = 'ship_date'
        part_col = 'lunar_part_number'
        qty_col = 'qty'

    # Parse date
    df[date_col] = pd.to_datetime(df[date_col])

    # Filter to date range
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    df = df[(df[date_col] >= start) & (df[date_col] <= end)]

    # Group by product and sum quantities
    asn_agg = df.groupby(part_col).agg({
        qty_col: 'sum'
    }).reset_index()

    asn_agg.columns = ['product_lpn', 'asn_qty']

    return asn_agg


def create_build_plan_pivot(build_plan_df: pd.DataFrame, asn_sienna: pd.DataFrame,
                            asn_qualitel: pd.DataFrame, month: str = 'Aug') -> tuple:
    """
    Update build plan with ASN deductions.

    Inserts new column "Aug shipped to date" before Aug column,
    shows ASN qty from 8/1 to 8/13.
    Reduces Aug month qty by ASN shipped amount.

    Args:
        build_plan_df: Current build plan
        asn_sienna: Sienna ASN aggregated data
        asn_qualitel: Qualitel ASN aggregated data
        month: Month to adjust (e.g., 'Aug')

    Returns:
        Updated build plan with new columns
    """
    result = build_plan_df.copy()

    # Combine ASN data from both CMs
    asn_combined = pd.concat([asn_sienna, asn_qualitel], ignore_index=True)
    asn_combined = asn_combined.groupby('product_lpn')['asn_qty'].sum().reset_index()

    # Merge with build plan
    result = result.merge(asn_combined, left_on='product_lpn', right_on='product_lpn', how='left')
    result['asn_qty'] = result['asn_qty'].fillna(0).astype(int)

    # Find position of month column
    month_col = month
    if month_col in result.columns:
        col_index = result.columns.get_loc(month_col)

        # Create new column name
        new_col = f"{month} shipped to date"

        # Move asn_qty to the right position and rename
        result.insert(col_index, new_col, result.pop('asn_qty'))

        # Reduce month qty by ASN shipped amount
        result[month_col] = result[month_col] - result[new_col]
        result[month_col] = result[month_col].clip(lower=0)  # Don't go negative

    return result


if __name__ == "__main__":
    # Test
    sienna = process_asn_file(
        "/sessions/fervent-keen-albattani/mnt/Downloads/Sienna ASN-data-2026-08-17 13_24_29.csv",
        "2026-08-01",
        "2026-08-13",
        cm='sienna'
    )
    qualitel = process_asn_file(
        "/sessions/fervent-keen-albattani/mnt/Downloads/QTL ASN-data-2026-08-17 13_25_13.csv",
        "2026-08-01",
        "2026-08-13",
        cm='qualitel'
    )

    print("Sienna ASN (8/1-8/13):")
    print(sienna)
    print(f"\nTotal Sienna ASN: {sienna['asn_qty'].sum()}")

    print("\nQualitel ASN (8/1-8/13):")
    print(qualitel)
    print(f"\nTotal Qualitel ASN: {qualitel['asn_qty'].sum()}")
