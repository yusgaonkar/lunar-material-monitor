# Data refresh

All files are same-day snapshots. Every file must carry the same snapshot date;
`validate.py` blocks the run if they disagree.

## Exported from Google Sheets

Export with **File → Download → Comma-separated values**, one tab at a time.

**Do not pull these through the Drive/Sheets API.** It truncates large tabs
silently and still returns a structurally valid final row, so nothing looks
wrong. The Stitched Indented BOM came back as 98 rows of 3,908 that way.

| File | Source workbook | Tab | Expected rows |
|---|---|---|---|
| `bom_stitched.csv` | Product BOM Master | Stitched Indented BOMs | 3908 |
| `bom_flat.csv`     | Product BOM Master | flat product-level tab  | TODO |
| `stitch_list.csv`  | Product BOM Master | Stitch List Input       | 18 |
| `onhand.csv`       | Inventory Master   | On Hand Inventory Master  | TODO |
| `onorder.csv`      | Inventory Master   | On Order Inventory Master | TODO |

Fill in every TODO by reading the row count off the sheet, then copy the numbers
into `EXPECTED_ROWS` in `src/io.py`. This is the truncation guard — without it a
short export reads as a stockout.

## Maintained by hand

| File | Grain | Notes |
|---|---|---|
| `build_plan.csv`   | product × period | Forward periods only |
| `plan_to_date.csv` | product          | Sets the backlog. Highest-risk input. |
| `in_transit.csv`   | product          | May be auto-derivable — see CLAUDE.md §6 |
| `exclusions.csv`   | component        | Parts that drop from the report |
| `golden_cases.csv` | component        | Regression tests. Hand-calculated. |

## Generated

| File | Notes |
|---|---|
| `_unmatched.csv` | Parts failing normalisation. Written every run. Watch it. |
| `notes.jsonl`    | Planner notes, append-only. **Never delete.** |
