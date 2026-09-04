#!/usr/bin/env python3
"""Quick test: show CM-only runout for a few high-impact parts."""

import sys
import pandas as pd
from src import io as lio, engine, inventory_depletion

# Load data
files = lio.load_all()
cfg = engine.Config(snapshot=files["onhand.csv"]["Updated at"].iloc[0])

# Run engine
result = engine.run(files, cfg)

# Extract key outputs
demand_detail = result["demand_detail"]
pab = result["pab"]
onhand = files["onhand.csv"]
onorder = files["onorder.csv"]

print("\n=== DEMAND DETAIL (first 10 rows) ===")
print(demand_detail[["cm", "part", "period", "demand"]].head(10))

print("\n=== PAB (first 10 rows) ===")
print(pab[["cm", "part", "period", "pab"]].head(10))

print("\n=== CM RUNOUT (simple, top 5 parts by total demand) ===")
# Get top parts by demand
top_parts = demand_detail.groupby("part")["demand"].sum().nlargest(5).index.tolist()
filtered_demand = demand_detail[demand_detail["part"].isin(top_parts)]
filtered_pab = pab[pab["part"].isin(top_parts)]

try:
    cm_runout = inventory_depletion.build_cm_runout_only(
        filtered_demand,
        onhand,
        onorder,
        filtered_pab,
        cfg
    )
    print(cm_runout.to_string())
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
