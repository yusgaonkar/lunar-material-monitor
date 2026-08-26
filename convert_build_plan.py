#!/usr/bin/env python3
"""Convert pivot-format build plan to long format for the engine."""

import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

# Read pivot format (skip first decorative row)
pivot = pd.read_csv(DATA / "build_plan_2026-08-26.csv", skiprows=1)

# Months are in columns Aug-26, Sep-26, etc.
month_cols = [c for c in pivot.columns if c and isinstance(c, str) and (c.startswith(('Aug-', 'Sep-', 'Oct-', 'Nov-', 'Dec-', 'Jan-', 'Feb-', 'Mar-', 'Apr-', 'May-', 'Jun-', 'Jul-')))]

rows = []
for _, row in pivot.iterrows():
    product_lpn = row.get('LPN') or row.get('product_lpn')
    if not product_lpn or pd.isna(product_lpn):
        continue

    # Extract all month columns (Aug-26, Sep-26, Oct-26, etc.)
    month_cols = [c for c in pivot.columns if c and isinstance(c, str) and '-' in c and c[0].isalpha()]

    for month_col in month_cols:
        qty = row[month_col]
        if pd.isna(qty) or qty == '' or qty == 0:
            continue

        try:
            if isinstance(qty, str):
                qty = int(qty.replace(',', ''))
            else:
                qty = int(qty)

            # Parse month_col like "Aug-26" -> 2026-08-01
            month_str, year_str = month_col.split('-')
            month_map = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                        'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
            month_num = month_map.get(month_str)
            if not month_num:
                continue
            year_num = 2000 + int(year_str)

            period_start = f"{year_num}-{month_num:02d}-01"

            rows.append({
                'product_lpn': str(product_lpn).strip(),
                'period_start': period_start,
                'qty': qty
            })
        except Exception as e:
            continue

result = pd.DataFrame(rows)
result.to_csv(DATA / "build_plan.csv", index=False)
print(f"✓ Converted: {len(result)} rows written to build_plan.csv")
