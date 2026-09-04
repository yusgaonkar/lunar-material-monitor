# CM-Level Runout Report Format

## What it shows
For each **(CM, Part)** combination, displays **PAB (Position Available to Promise)** across monthly periods.

## Calculation (from engine.py — same as shortage/drill-down)

```
For each (cm, part, period):

  DEMAND
    gross_demand[cm, part, period]
      = SUM over products at that CM of:
        remaining_builds[product, period] × Sourcing Flat Qty[product, part]
    
    (remaining_builds = forward_plan + backlog, loaded into period 1)

  SUPPLY
    cm_on_hand[cm, part]      = unrestricted_qty (CM-owned)
    cm_wip[cm, part]          = WIP Consumed
    scheduled_receipts[cm, part, period]  = on-order with eta in that period

  RUNOUT (Period-by-period balance)
    PAB[period] = PAB[period-1] + scheduled_receipts[period] - demand[period]
    PAB[0]      = cm_on_hand + cm_wip
```

## Output Table Structure

```
Part      | Desc        | CM        | OnHand | OnOrder | 2026-09 | 2026-10 | 2026-11 | ...
----------|-------------|-----------|--------|---------|---------|---------|---------|-----
10-000099 | CAP...      | Sienna    | 1,100  | 0       | 500     | 200     | -300    |
10-000099 | CAP...      | Qualitel  | 50     | 100     | 80      | -20     |         |
10-000054 | RES...      | Sienna    | 500    | 200     | 300     | 100     | 50      |
10-000054 | RES...      | Qualitel  | 200    | 0       | 100     | 0       | -50     |
```

## Key Points
- **One row per (CM, Part)** — two CMs, same part = two rows
- **Static columns:** Part, Description, CM, OnHand, OnOrder at snapshot
- **Period columns:** End-of-period PAB for each month
- **Negative PAB** = shortage; shows quantity short
- **Uses demand_detail** already calculated by engine (no new calculation)
- **No Lunar allocation yet** — just CM inventory vs. demand

## Next Step
Layer in Lunar inventory as a separate column group:
```
Part | ... | CM | CM_OnHand | CM_OnOrder | 2026-09_CM | 2026-10_CM | ... | 
                                                                    | 2026-09_Lunar | 2026-10_Lunar | ...
```
