#!/usr/bin/env python3
"""
Validate PAB calculation for 10-000551
Shows what the PAB should be for each (CM, month) combination
"""

import sys
sys.path.insert(0, '/Users/yashusgaonkar/lunar-planner')

import pandas as pd
from src import io as lio, engine as eng

# Load all data
print("Loading data...")
frames = lio.load_all()
build_plan = lio.load_build_plan()

# Run engine to get PAB
print("Running engine...")
frames['build_plan.csv'] = build_plan
result = eng.run(frames)
pab = result["pab"]

# Filter for part 10-000551
print("\n" + "="*80)
print("PAB for part 10-000551 (all CMs, by month)")
print("="*80)

pab_551 = pab[pab["part"] == "10-000551"].copy()
pab_551["period_date"] = pd.to_datetime(pab_551["period"])
pab_551 = pab_551.sort_values(["cm", "period_date"])

if len(pab_551) == 0:
    print("No PAB data found for 10-000551")
else:
    # Show by CM and period
    for cm in sorted(pab_551["cm"].unique()):
        cm_data = pab_551[pab_551["cm"] == cm].sort_values("period_date")
        print(f"\n{cm}:")
        print("-" * 60)
        for _, row in cm_data.iterrows():
            print(f"  {row['period']}: demand={row['demand']:>10.0f}, "
                  f"opening={row['opening']:>10.0f}, receipts={row['receipts']:>10.0f}, "
                  f"PAB={row['pab']:>10.0f}")

# Show end-of-month PAB summary
print("\n" + "="*80)
print("End-of-Month PAB Summary for 10-000551")
print("="*80)

pab_551["month"] = pab_551["period_date"].dt.to_period("M")
pab_eom = pab_551.loc[pab_551.groupby(["cm", "month"])["period_date"].idxmax()]

for cm in sorted(pab_eom["cm"].unique()):
    cm_eom = pab_eom[pab_eom["cm"] == cm].sort_values("month")
    print(f"\n{cm}: ", end="")
    pab_values = [f"{row['month']}={row['pab']:.0f}" for _, row in cm_eom.iterrows()]
    print(", ".join(pab_values))

print("\n" + "="*80)
print("Copy-paste this summary into the app and verify it matches!")
print("="*80)
