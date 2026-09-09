"""Process ASN (Advanced Shipping Notice) data from CM exports.

Handles two formats:
1. Pivot table (monthly grid) from Google Sheet
2. Legacy raw ASN files from CMs
"""

import pandas as pd
from datetime import datetime


def process_asn_pivot(filepath: str) -> pd.DataFrame:
    """
    Process ASN pivot table from Google Sheet (monthly grid format).

    Format:
      LPN | Description | Sep-26 | Oct-26 | Nov-26 | ...
      90-06948B | Non Compliant BB | 10 | 5 | ...

    Extracts current month's column and returns product LPN + shipped qty.

    Args:
        filepath: Path to asn_latest.csv (from Google Sheet sync)

    Returns:
        DataFrame with columns: [product_lpn, asn_qty] for current month
    """
    df = pd.read_csv(filepath)

    # Columns: LPN, Description, then month columns (Sep-26, Oct-26, etc.)
    if 'LPN' not in df.columns or 'Description' not in df.columns:
        raise ValueError("ASN sheet must have 'LPN' and 'Description' columns")

    # Find month columns (all except LPN and Description)
    month_cols = [c for c in df.columns if c not in ['LPN', 'Description']]

    if not month_cols:
        return pd.DataFrame(columns=['product_lpn', 'asn_qty'])

    # Get current month in format "Sep-26" (MMM-YY)
    today = datetime.now()
    current_month = today.strftime('%b-%y')  # e.g., "Sep-26"

    # Try to find exact month column match, case-insensitive
    current_col = None
    for col in month_cols:
        if col.lower() == current_month.lower():
            current_col = col
            break

    if current_col is None:
        # If exact match not found, use the first month column as fallback
        print(f"Warning: Current month '{current_month}' not found. Available: {month_cols[:5]}")
        current_col = month_cols[0]

    # Extract LPN and current month's shipped qty
    result = df[['LPN', current_col]].copy()
    result.columns = ['product_lpn', 'asn_qty']

    # Fill NaN with 0
    result['asn_qty'] = result['asn_qty'].fillna(0).astype(int)

    # Remove zero rows for clarity
    result = result[result['asn_qty'] > 0].reset_index(drop=True)

    return result


def process_asn_file(filepath: str, start_date: str, end_date: str, cm: str = 'unified') -> pd.DataFrame:
    """
    Process legacy ASN file and aggregate by product.

    Args:
        filepath: Path to ASN CSV
        start_date: YYYY-MM-DD format
        end_date: YYYY-MM-DD format
        cm: 'unified' (new format), 'sienna', or 'qualitel' (legacy formats)

    Returns:
        DataFrame with columns: product_lpn, asn_qty (aggregated by product)
    """
    df = pd.read_csv(filepath)

    if cm.lower() == 'unified':
        # New unified ASN format (asn_aug18.csv)
        date_col = 'shipped_date'
        part_col = 'customer_part_number'
        qty_col = 'quantity'
    elif cm.lower() == 'sienna':
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
