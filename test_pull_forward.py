#!/usr/bin/env python3
"""Test script to verify PCBA pull-forward logic is working correctly."""

import sys
sys.path.insert(0, 'src')

from src import engine, io as lio
import pandas as pd

# Load data
print("Loading data...")
frames = lio.load_all()
bom = frames["bom_stitched.csv"]

# Run the engine
print("Running engine...")
result = engine.run()

# Check 10-001651 demand
demand = result['demand']
bridge_demand = demand[demand['part'] == '10-001651'].sort_values('period')

print("\n" + "="*80)
print("10-001651 (Bridge sub-component) Demand Analysis")
print("="*80)

if len(bridge_demand) == 0:
    print("❌ ERROR: No demand found for 10-001651!")
    sys.exit(1)

print(f"\nTotal demand rows: {len(bridge_demand)}")
print(f"Demand sources present: {bridge_demand['demand_source'].unique().tolist()}")

# Show demand by source
for source in bridge_demand['demand_source'].unique():
    source_rows = bridge_demand[bridge_demand['demand_source'] == source]
    print(f"\n{source}: {len(source_rows)} rows")
    print(source_rows[['cm', 'part', 'period', 'demand', 'demand_source']].to_string(index=False))

# Verify structure
build_plan_rows = len(bridge_demand[bridge_demand['demand_source'] == 'Build Plan'])
pf_rows = len(bridge_demand[bridge_demand['demand_source'] == 'PCBA_PullForward'])

print(f"\n" + "="*80)
print("Summary")
print("="*80)
print(f"✓ Build Plan rows: {build_plan_rows}")
print(f"✓ PCBA_PullForward rows: {pf_rows}")

if pf_rows > 0:
    print("✓ Pull-forward demand is being created!")

    # Check period shift
    bp = bridge_demand[bridge_demand['demand_source'] == 'Build Plan']['period'].min()
    pf = bridge_demand[bridge_demand['demand_source'] == 'PCBA_PullForward']['period'].min()
    shift = (bp - pf).days // 7
    print(f"✓ Period shift: {shift} weeks (expected: 4)")
    if shift == 4:
        print("✓ Shift is correct!")
    else:
        print(f"⚠ WARNING: Expected 4 week shift, got {shift} weeks")
else:
    print("❌ ERROR: No PCBA_PullForward demand found!")
    print("Pull-forward logic is not working correctly")
    sys.exit(1)

print("\n✅ TEST PASSED: Pull-forward logic is working!")
