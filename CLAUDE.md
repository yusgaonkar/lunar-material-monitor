# Lunar Material Planning Tool — Project Context

## 0. TOKEN DISCIPLINE — read this first, every session

**Never open a file in `data/` with Read, cat, head, or grep.** `onorder.csv` is 2 MB,
`bom_stitched.csv` is 1 MB. Reading even a fraction of one burns an enormous amount of
context for no benefit.

Every column name, row count, distinct value and data hazard you need is already
documented in sections 4 and 5 below. If you need a fact that is not there, get it by
writing a small pandas script, running it, and reading only the printed summary:

```bash
.venv/bin/python -c "import pandas as pd; df=pd.read_csv('data/onhand.csv'); print(df['source_report'].value_counts())"
```

Print aggregates. Never print rows.

Same rule for `data/_unmatched.csv` — it is an output for the planner, not for you.

> Put this file at the repo root. Claude Code loads it automatically on every session,
> so you never re-explain the domain. Update it as facts change.

---

## 1. What this is

A component-level demand & supply planning tool for Lunar Energy's material planning
group. Two outputs:

1. **Runout report** — detailed, per component, per CM, per period.
2. **Exec shortage summary** — rolled up, prioritised, narrative.

Pilot target: running locally via Streamlit, 48 hours. Not production. Not multi-user.

## 2. Domain model

**Products.** Lunar sells a BESS. It is assembled from ~10 separately-built products
(battery block, inverter, maximizer, bridge, link, hub, hardware kit, etc). Each product
is one BOM. **The build plan is defined at the product level**, not the ESS level.

**Contract manufacturers.** Production happens at CMs, who also buy most components.

| CM | Status | Notes |
|---|---|---|
| Sienna | Active | 10 of 18 products |
| Qualitel | Active | 6 of 18 products; took over Battery Blocks from Celestica |
| Plexus | Upcoming | No data yet |
| Unigen | Upcoming | Appears as a ship-to on 2 NetSuite POs |
| Celestica | **Sunset** | Still holds residual inventory. Exclude by default. |

Two products (`90-08096A` Lunar 3 Inverter, `30-07320F` Maximizer 2p0 PCBA) have
`CM = TBD`. They cannot be planned until assigned.

**Inventory ownership.** Two books for one physical world:
- **Lunar-owned** (NetSuite) — including stock physically at a CM but on Lunar's books.
- **CM-owned** (SAP) — Qualitel, Sienna, Celestica.
- Lunar normally **sells inventory to the CM at cost before production**. A few very
  high-value items (cell modules) are truly consigned at zero cost.

**Part nomenclature.**
| Prefix | Meaning |
|---|---|
| `90-` | Boxed top-level assembly (product) |
| `10-` | Component or assembly; also a PCBA *after* conformal coating |
| `30-` | PCBA post-SMT |
| `20-` | PCB (raw fab) |

## 3. Data sources

| Source | System | Path today |
|---|---|---|
| BOM | Arena → LunarDB (AWS) | Google Sheet: Product BOM Master |
| On-hand / on-order | CM SAP + Lunar NetSuite → email CSV → Box → LunarDB | Google Sheet: Inventory Master |
| Build plan | **No source of truth exists** | To be created as an input sheet |

For the pilot: **local CSV snapshots in `/data`. Do not connect to LunarDB, NetSuite or
Box.** Live connections are the single biggest threat to the 48-hour target.

### Export the CSVs directly from Sheets (File → Download → CSV), per tab.
Do **not** pull them through the Drive API. The API's document reader silently truncates
large sheets — the Inventory Master On Hand tab came back cut off at part `10-000575`
(41 distinct parts) when the real tab is far larger. Truncated inventory fails *silently*
and looks exactly like a stockout.

**Always print row counts on load and assert against expected.**

---

## 4. Schemas (verbatim column names — they are inconsistent, do not "tidy" them in your head)

### Product BOM Master

**`Stitched Indented BOMs`** — 25 cols
```
Updated at | Parent Product LPN | Parent Product Name | level | guid | item_number |
item_name | revision | procurement_type | unit_of_measure | category_name |
lifecycle_phase | vendor | makebuy | Parent PCBA LPN | Parent PCBA Name |
Product System Usage | Flat Qty | Usage Qty | Sourcing Flat Qty | Sourcing Usage Qty |
Parent FG Built | Parent FG Consumed | WIP Consumed | CM Raw Inventory
```

**`Stitch List Input`** — 5 cols. One row per product. This is the product master.
```
Parent Product LPN | Description | CM | Product Alias | Product Usage per ESS
```
18 products. `Product Usage per ESS` is qty in a typical 20kWh install (battery blocks
are 5kWh so qty 4). **Not used in planning** — the build plan is already stated at
product level. It is for a future take-rate layer.

**Flat product-level tab** — 16 cols. Covers 15 of 18 products.
```
Updated at | item_number | item_name | revision | procurement_type | unit_of_measure |
category_name | lifecycle_phase | vendor | Product Alias | Parent Product LPN |
Parent Product Name | CM | Product Usage per ESS | Flat Qty | Sourcing Flat Qty
```

Also present: an item/LPN master (498 rows), a vendor replacement list, and a BOM change
log that is stale (all dates Feb–Apr 2024). Ignore the last two for the pilot.

### Inventory Master

**`On Hand`** — 19 cols
```
Updated at | source_report | raw_part_number | lpn | description | item_category |
procurementtype | location | storage_location | is_consigned | owned_by |
unrestricted_qty | restricted_qty | unit_price | unrestricted_value | restricted_value |
revision_number | row_type | uncommitted_qty
```

**`On Order`** — 36 cols
```
Updated at | source_report | po_created_date | po_number | location | po_vendor |
po_line_item | lunar_lpn_raw | lunar_lpn | item_description | procurementtype |
item_category_ | mfg_pn | unit_price | quantity | quantity_billed | quantity_received |
quantity_open | ship_date | receipt_date | notes | is_fully_received | line_item_type |
approval_status | is_closed | billing_completed | custom_status | po_vendor_email |
department | classification | is_fully_shipped | is_cleared | waybill | lead_time_days |
_db_created_at | last_modified
```

**Column naming drifts between tabs — alias both:**
| Concept | On Hand | On Order | BOM |
|---|---|---|---|
| Raw CM part no. | `raw_part_number` | `lunar_lpn_raw` | — |
| Clean part no. | `lpn` | `lunar_lpn` | `item_number` |
| Category | `item_category` | `item_category_` | `category_name` |
| Procurement | `procurementtype` | `procurementtype` | `procurement_type` |

---

## 5. Known data hazards — these are verified, not hypothetical

### 5.1 Part numbers — VALIDATE, do not re-derive

**LunarDB has already normalised these.** The `lpn` (On Hand) and `lunar_lpn` (On Order)
columns are populated and correct on 99.7% of the 10,845 inventory rows. Re-deriving them
from `raw_part_number` / `lunar_lpn_raw` is not just wasted work — **it is impossible**.

Proof that a regex cannot do this job:

```
814-4604  ->  10-06647B     PCBA DCDC/BMS, with Conformal Coat
814-4641  ->  90-07675A     LFP Battery Block with Wall Bracket
814-4656  ->  10-06103C     PCBA, Terminal Board, Type C
814-4657  ->  10-06105C     PCBA, Power Board, Type C
```

Qualitel uses its own internal numbering for PCBAs and assemblies. There is no rule that
turns `814-4604` into `10-06647B` — it is a lookup table, and LunarDB already holds it.
Any normaliser written from scratch would silently drop these rows, and they are
top-level assemblies, i.e. the highest-value items on the report.

**So the rule is: trust the clean column, then validate it.** This converts the riskiest
module in the build into a cheap check.

#### Observed raw encodings (reference only — for the exception report, not for parsing)

| Pattern | Source | Rows | Example |
|---|---|---|---|
| Identity | Lunar NetSuite | 1,871 | `10-000054` |
| ` REV-nn` suffix | Sienna GA | 4,203 | `10-000099 REV-02` |
| `814L-` prefix | Qualitel WA | 1,109 | `814L-10-000099` |
| `814L-` + `@Rev4` | Qualitel OO | 168 | `814L-10-06572A@Rev4` |
| `C` prefix + ` REV-nn` | Sienna GA, consigned | 163 | `C10-000253 REV-02` |
| `3MS` suffix | Celestica MX | 200 | `10-0000423MS` |
| `_old` suffix | NetSuite OO | ~260 | `10-005899_old` → `10-005899` |
| `815L-` prefix | Qualitel WA | 2 | `815L-10-003368` |
| `3MX` suffix | Celestica MX | 1 | `10-06527A3MX` |
| `YJS` suffix | Celestica MX | 5 | `3480-0329YJS` → `3480-0329` (not a Lunar LPN) |
| ` REV-A` / ` REV-.01` | Sienna GA | 3 | malformed revisions |
| `814-nnnn` **lookup** | Qualitel WA | 7 | **not derivable — see above** |

#### What normalize.py should actually do

1. **Use the provided clean column as the key.** Do not parse the raw column.
2. **Clean up the 16 rows where upstream normalisation leaked**, all in one place:
   `10-005220_old`, `10-005221_old2`, `10-06949C_old`, `90-001221_old` (strip `_old`,
   `_old2`), and `10-06527A-I` (strip `-I`). Assert the count is 16; if it grows, the
   upstream rule has changed and you want to know.
3. **Quarantine non-Lunar part numbers.** Five Celestica rows carry `3480-0329`,
   `8100-0352` etc. — a different numbering scheme entirely. Exclude, do not force-match.
4. **Handle 5 On Order rows with a blank `lunar_lpn`.** Report them; never guess.
5. **Emit `data/_unmatched.csv` every run** — see §5.6.

`raw_part_number` stays in the drill-down so a planner can see what the CM called it.
It is never a join key.

### 5.2 What `uncommitted_qty` means, and the double-count it prevents

**Resolved.** `committed = unrestricted_qty − uncommitted_qty`, and the committed portion
is stock **allocated against open POs the CM has issued to Lunar**. It aggregates across
locations, not per row.

Worked example, `10-000099` (CAP CER 1UF 35V X7R 0603), Lunar-owned across all locations:

| | Qty |
|---|---|
| `unrestricted_qty` total | 1,039,115 |
| `uncommitted_qty` total | 91,115 |
| **Committed** | **948,000** |

That 948,000 reconciles exactly to four open Sienna→Lunar POs in the on-order report:
`5500086672`, `5500086673`, `5500086674`, `5500086685`.

So the flow is: Lunar holds the stock → the CM raises a PO on Lunar → the units become
committed on Lunar's book while still physically at a Lunar location → they ship and
land as CM on-hand.

**The double-count:** those units appear twice — once as Lunar `unrestricted_qty`, once
as CM open-order supply. Counting both inflates availability.

**Rules:**
- **CM runout:** count CM on-hand (`unrestricted_qty`, CM-owned) **plus** CM open POs,
  including POs where `po_vendor` is Lunar Energy. Never add Lunar's on-hand on top.
- **Lunar allocation pool** (what is genuinely free to promise): `uncommitted_qty` on
  Lunar-owned rows, summed across locations.

**Validation gate — a strong reconciliation test:**
```
SUM(unrestricted_qty − uncommitted_qty) over Lunar-owned rows, by part
  ==  SUM(quantity_open) over open CM POs where po_vendor resolves to Lunar Energy
```
Any part where these disagree indicates a missed PO, a stale snapshot, or a broken part
number join. Report the variance per part — this is one of the highest-value checks in
the tool.

Separately, a confirmed same-part duplicate across feeds: Qualitel PO `139131` mirrors
NetSuite `PO-US-09229` for `10-08504A` (2,500 + 1,696 units). Two systems recording one
buy. De-dup on (clean part number, quantity, approximate date) and flag rather than
silently dropping.

### 5.6 BOM-to-inventory join coverage — build the exception report first

Measured on the full snapshot against the 2,179 distinct `item_number` values in the
stitched BOM:

| | Rows | Joins to BOM | Misses |
|---|---|---|---|
| On Hand | 4,770 | 4,062 (85.2%) | 708 rows / 426 distinct parts |
| On Order | 6,075 | 4,976 (82.0%) | 1,094 rows / 236 distinct parts |

Most misses are legitimate — obsolete parts, other programmes, non-BOM consumables. But
**the unmatched list is a first-class output, not a debug artefact.** Write it to
`data/_unmatched.csv` on every run with part, description, source, row count and value,
and surface a summary count in the UI. A part quietly falling out of the join is
indistinguishable from a part with no demand.

### 5.7 The 363 buy-parts with no inventory record — this is NPI, not a data fault

363 of 1,389 buy-parts have no On Hand row. They are **not scattered** — they are almost
entirely the new products:

| Product | Alias | CM | Buy parts | No inventory | % |
|---|---|---|---|---|---|
| 90-08096A | 11 KW Inverter | **TBD** | 426 | 238 | **56%** |
| 30-07519A | Link | Sienna | 91 | 49 | **54%** |
| 90-08373A | 2.1 LFP Block | Qualitel | 185 | 68 | **37%** |
| 90-001223 | HW Kit | Sienna | 14 | 4 | 29% |
| 30-07320F | Max 2.0 PCBA | **TBD** | 36 | 8 | 22% |
| 90-06889C | 9.6 KW Inverter | Sienna | 548 | 10 | 2% |
| 90-06801A | Bridge | Sienna | 256 | 4 | 2% |
| 90-06948C | 2.0 LFP - EVT | Qualitel | 203 | 7 | 3% |
| *(all other mature products)* | | | | 0–3% | |

Mature products in rate production sit at 0–3%. Everything above 20% is a product that
has not started buying yet — and the two worst are the two with `CM = TBD`.

Of the 363: **197 already have open POs** (procurement under way, will land), **166 have
neither on-hand nor on-order** (nothing bought yet).

By category: 283 Electrical Components, 60 Part, 15 Assembly, 3 PCB.

**Design consequence — three states, not two.** A component must render as one of:

| State | Meaning | Treatment |
|---|---|---|
| `IN_PRODUCTION` | has an inventory record | normal runout |
| `ON_ORDER_ONLY` | no on-hand, open PO exists | runout from PO dates only; flag |
| `NOT_SOURCED` | no on-hand, no PO | **NPI readiness gap, not a shortage** |

If `NOT_SOURCED` renders as a shortage, 166 parts fire on day one, the exec summary is
swamped by the 11kW Inverter, and the report gets dismissed as noise in week one.

Default the runout view to products in production. Put NPI products behind a toggle, and
report them against sourcing readiness — which is exactly what the CTB / OK2Buy tracker
in the L2.1 launch workbook was doing by hand. That gate model was the strongest idea in
the original spreadsheet and it earns its place here.

Worked examples (highest usage first — these would scream loudest if mishandled):

```
10-006782   usage 65   on order  1,330   11 KW Inverter   RES 680K OHM 1% 3/4W 1210
10-07398A   usage 46   on order      0   2.1 LFP Block    CAP CER 1UF 250V X7R 2220
10-006656   usage 44   on order 30,000   11 KW Inverter   CAP CER 1UF 16V X5R 0201
10-08083A   usage 22   on order      0   11 KW Inverter   BN 6404 hexalobular socket button head
10-006373   usage 22   on order      0   Link             CAP CER 0.022UF 50V X7R 0402
10-004131   usage 20   on order      0   Maximizer Box    Tape, Polyimide, 0.165mm, 2" Wide
10-005406   usage 20   on order  9,800   11 KW Inverter   RES SMD 1K OHM 1% 1/16W 0402
10-007001   usage 20   on order      0   11 KW Inverter   RES 750K OHM 0.1% 0.52W 1206
10-005459   usage 19   on order      0   2.1 LFP Block    CAP CER 0.47UF 25V X7R 0603
10-006761   usage 18   on order  3,555   11 KW Inverter   RES 10K OHM 5% 1/2W 1206
```

### 5.3 On-order dates — each feed uses a different column

**The two date columns are mutually exclusive by source.** Neither one alone is the ETA.

| Source | Rows | `ship_date` | `receipt_date` | Both | Neither |
|---|---|---|---|---|---|
| CM: Sienna GA | 1,506 | **1,223** | 0 | 0 | 283 |
| CM: Qualitel WA | 600 | 0 | **569** | 0 | 31 |
| Lunar Netsuite | 3,969 | 3,033 | **3,962** | 3,026 | 0 |

Sienna populates `ship_date` and never `receipt_date`; Qualitel does the exact opposite.
So define one derived field and use it everywhere:

```
eta = COALESCE(receipt_date, ship_date)
eta_source = 'receipt_date' | 'ship_date' | 'none'
```

Keep `eta_source` on the row — a Sienna date is a *ship* date, so it is optimistic by
whatever transit takes, and a planner should be able to see which kind they are looking at.

**Effect:** dated coverage goes from 4,531/6,075 (75%) to **5,754/6,075 (95%)**, and
Sienna goes from completely blind to 81% covered. This is a large accuracy win.

Residual undated: 283 Sienna + 31 Qualitel + 7 NetSuite = **321 lines**. Still not
counted in the balance, still rendered distinctly from "no supply exists" — a third
visual state.

Sienna `eta` spans 2025-09-19 to 2028-07-10; 266 of 1,223 (22%) are already in the past.
Past-due goes to its own bucket, never silently into period 1.

> **Data question worth raising with the CMs.** On the 3,026 NetSuite lines carrying both
> dates, `receipt_date` is *earlier* than `ship_date` on 2,538 of them. That is
> physically impossible if the labels mean what they say. Most likely `receipt_date` is a
> need-by or promise date and `ship_date` is the actual/planned vendor ship. It does not
> block the pilot — but do not build a lead-time model on these two columns until
> someone confirms what they mean.

### 5.4 Other verified issues

- **140 negative on-hand rows** — 138 Celestica MX (backflush lag), 2 Qualitel WA.
  Another reason to exclude Celestica by default. The 2 Qualitel rows need a look.
- **166 rows are `is_consigned = TRUE`**, not a handful. Consignment is material and the
  `C`-prefix convention at Sienna is load-bearing.
- **`(po_number, po_line_item)` is not unique.** PO `139192` line `1` appears 4× as
  separate schedule lines. Generate a synthetic row key.
- **`approval_status` is the literal string `[null]`** on CM rows, not SQL NULL.
- **`makebuy` uses three encodings for "not applicable":** `-`, `NA`, and blank.
  Two rows carry CM Raw Inventory while `makebuy = '-'` (`10-000778`, `10-005259`), so
  filtering buy-parts on `makebuy == 'Buy'` alone will miss stock.
- **`item_number` is not unique in the indented BOM** — 11 items appear at multiple BOM
  positions (up to 4×). Legitimate multi-position use, but joins must aggregate or key on
  `(Parent Product LPN, guid)`.
- **One sentinel row** in the indented BOM: `item_number = 'Custom Parent List'`, level 0,
  blank parent. Filter it out.
- **`location` has three different meanings** by feed: CM site (`ATLN`, `Seattle, WA`),
  Lunar warehouse/3PL (`JIT - Reno`, `Avnet`, `NexGen Digital Inc`), and ship-to on
  NetSuite POs (`Qualitel`, `Unigen`). **`source_report` is the only reliable CM key.**
- Same CM under three names: `ATLN` / `Sienna Corporation - GA` / `ABV Electronics dba
  Sienna Corporation`. Needs an explicit alias map.
- `Updated at` is `MM-DD-YYYY`; every other date column is ISO `YYYY-MM-DD`.
- 18 item names contain embedded `"` characters (`.135" Hole Dia`). Break naive parsing.
- `Parent PCBA LPN` / `Parent PCBA Name` are **empty on every row**.
- `Parent FG Built` is a **constant per product** (3040 for `90-001223`), not per-line.

### 5.5 BOM coverage — resolved, and a cautionary tale

**Verified against the real export.** The Stitched Indented BOM has **4,379 data rows**
covering **all 18 products** (19 distinct `Parent Product LPN` values = 18 products plus
the blank sentinel row). No product is missing a BOM. Multi-product planning is viable.

Coverage is very uneven, which matters for testing — pick cases from both ends:

| Product | Rows | | Product | Rows |
|---|---|---|---|---|
| 90-06889C (9.6kW Inverter) | 1,138 | | 90-001223 (20kWh HW Kit) | 100 |
| 90-08096A (Lunar 3 Inverter) | 524 | | 90-07290A (LFP HW Kit) | 100 |
| 90-06801A (Bridge) | 512 | | 30-07519A (Link PCBA) | 93 |
| 90-06948C (LFP DC ESS) | 369 | | 30-07320F (Max 2p0 PCBA) | 40 |
| 90-08373A (2.1 LFP Block) | 339 | | 90-005336 (Lifting Tool) | 20 |
| 90-07675A (LFP Block) | 324 | | 90-06789A (Rogowski Coil) | 9 |
| 10-06103C (Terminal PCBA) | 256 | | 90-005375 (Ground Mount) | **2** |

`90-005375` at 2 rows and `90-06789A` at 9 are thin enough to be worth a sanity check.

Verified row counts, all now in `EXPECTED_ROWS`. Note Sheets shows a last-row number that
includes the header, so each is one less than what you read off the sheet:

| File | Data rows | Cols |
|---|---|---|
| `bom_stitched.csv` | 4,379 | 25 |
| `bom_flat.csv` | 3,619 | 16 |
| `stitch_list.csv` | 18 | 5 |
| `onhand.csv` | 4,770 | 19 |
| `onorder.csv` | 6,075 | 36 |

An earlier read of this tab via the Google Drive document API returned 98 rows for a
single product (`90-001223`) and reported no truncation. It was wrong. The API silently
cut the response at ~98 rows and the payload still terminated with a structurally valid
final row, so every completeness heuristic passed.

**This is exactly the failure mode described at the top of §3, and it is the reason for
the row-count assertions in `io.py`.** Truncated BOM or inventory data does not throw —
it produces a clean, plausible, wrong report. Do not trust any extract that has not had
its row count asserted against a number you obtained by looking at the sheet directly.

Export via **File → Download → CSV**, per tab. Record every expected row count in
`EXPECTED_ROWS` in `io.py`.

---

## 6. Calculation spec

Grain: **(CM, component, period)**, with product-level detail retained for drill-down.
Periods: **weekly buckets**. Store the build plan daily; bucket to weekly for reporting.

### Demand
```
gross_demand[product, component, period]
    = build_plan_qty[product, period] * Sourcing_Flat_Qty[product, component]
```
Use `Sourcing Flat Qty`, not `Flat Qty` — it is non-zero only at the procurement boundary
(what the CM actually buys) and correctly zeroes out both make-levels above and
sub-components below.

Roll up across products **within a CM** to get total component demand at that CM.

### Netting — the pipeline balance model (DECIDED)

**Go-forward, not cumulative.** Demand is builds not yet delivered; supply is material
still in the pipeline. Both measured as of the snapshot date.

#### Why not cumulative-from-program-start

Cumulative and go-forward are algebraically identical, so the choice is about which one a
human can check. Batch ordering made cumulative natural — "we bought for 3,040 units,
built 2,800, 240 left" is a discrete fact with a boundary. Rate production at 5k/month
has no boundary. Eighteen months in, cumulative demand is ~90,000 blocks while the live
quarter is 15,000; a 2% error in historical build records is 1,800 blocks, or 12% of the
signal anyone cares about. **The cumulative error window never closes.** Go-forward
truncates it at today, and removes the need for build history back to program start.

#### Material states

| # | State | Where counted | Side |
|---|---|---|---|
| 1 | Raw component at the CM | `unrestricted_qty` (CM-owned on-hand) | Supply |
| 2 | Embedded in a higher-level assembly at the CM, pre-ASN | `WIP Consumed` | **Supply** |
| 3 | ASN submitted; TLA in transit to Lunar | *nowhere — see gap* | Demand reduction |
| 4 | TLA received by Lunar against Lunar's PO to the CM | `Parent FG Built` | Demand reduction |

**WIP is inventory, not negative demand.** It is material the CM physically holds, just
embedded in something. `WIP Consumed` cascades as
`(parent's WIP Consumed + parent's CM Raw Inventory) × Usage Qty`, which makes it exactly
"quantity of this component present at the CM inside higher-level assemblies."

This placement is not cosmetic. WIP is uneven across BOM levels — 500 sub-assemblies
built against 200 top-level units means a component consumed at sub-assembly level has
500 committed while one added at final assembly has 200. It therefore **cannot** be
collapsed into a product-level netting figure, whereas states 3 and 4 can.

#### The calculation

```
DEMAND  (product space — 18 numbers, human-checkable)
  backlog[product]          = plan_to_date - (units_received + units_in_transit)
  remaining_builds[product, period]
                            = forward_plan[product, period]
                            + backlog  (loaded into the first period only)

  gross_demand[component, period]
                            = SUM over products at that CM of
                              remaining_builds[product, period] * Sourcing_Flat_Qty

SUPPLY  (component space)
  available[cm, component]  = unrestricted_qty      # CM-owned raw on-hand
                            + WIP Consumed          # embedded in higher assemblies
                            + dated open POs        # scheduled receipts by period
```

Worked check — component at usage 2, plan 1,000 blocks; 300 received, 50 in transit,
100 in WIP, 550 not started, raw on-hand 1,100:
- Demand = (1,000 − 350) × 2 = **1,300**
- Supply = 1,100 + (100 × 2) = **1,300** → balanced. Correct.

`Parent FG Consumed` is **not used**. It is a derived column
(`Parent FG Built × Flat Qty`) carrying no information the product-level figure does not
already have, and netting it in component space multiplies the mapping risk 3,908-fold.

#### Load-bearing assumption

This model plans **only at the `Sourcing Flat Qty` boundary — buy parts**. Otherwise a
bought sub-assembly would be counted on the On Hand report *and* its children counted
again via `WIP Consumed`. Assert this in `validate.py`: no component with
`Sourcing Flat Qty = 0` may carry demand.

#### The in-transit gap (CONFIRMED REAL — placeholder required)

**`Parent FG Built` = cumulative received under Lunar's PO to the CM, i.e. goods
receipt — not shipment confirmation.** Confirmed with Yash.

So `WIP Consumed` drops units at ASN submission while `Parent FG Built` only picks them
up at goods receipt. Between those events the TLA's components sit in neither bucket.

Consequence: `backlog` is overstated, so **the engine over-demands** by
`units_in_transit × Sourcing_Flat_Qty`. Conservative — it invents shortages rather than
hiding them — but it will fire on high-runner parts, which is how a new tool loses
credibility in week one.

**Sizing the exposure.** Both active CMs ship domestically (Qualitel → Seattle, Sienna →
Atlanta), so transit is days, not weeks. At a 5,000/month rate and ~5 days transit the
in-flight population is roughly 1,150 blocks — small against a monthly bucket, but at a
usage of 9 that is ~10,000 units of a single resistor. Not negligible on high-usage
components. Worth quantifying per product before deciding how much effort to spend.

`units_in_transit` comes from `data/in_transit.csv`
(`product_lpn, cm, qty, asn_date`), defaults to 0, and must stay a **named column all
the way to the report**. Flag any component whose shortage clears when in-transit is
counted as *"possible false shortage — check in-transit."*

#### Auto-sourcing in-transit — TESTED AND REJECTED

The idea was to derive in-transit from `waybill` / `is_fully_shipped` on Lunar's POs to
the CMs. Tested against the full snapshot. It does not work:

- `waybill` is **populated on 0 of 6,075 rows.** Dead column.
- Only **86** NetSuite PO lines have a CM as `po_vendor` (61 Sienna, 14 Qualitel,
  11 Celestica) — far too few to represent TLA receipts at rate production.
- Of the 45 lines flagged `is_fully_shipped = TRUE` with `quantity_received <
  quantity`, **41 have a ship_date more than 60 days old and none is under 14 days.**
  Examples: `10-005226` shipped 2026-04-03, received 0; `10-00522D` shipped 2026-04-30
  against a 2026-12-31 receipt date. These are stale or mis-flagged lines, not goods
  in transit.
- `is_fully_shipped = TRUE` on 3,150 of 6,075 rows overall — implausibly high for a
  genuine shipment flag.

**Conclusion: keep the manual `data/in_transit.csv`.** Revisit only if a real ASN feed
appears. Do not let anyone reconnect this to `is_fully_shipped` without re-running the
recency check above.

#### Two rules that keep this unconfusing

1. State the time origin in the report header: *"Nets against builds not yet delivered
   as of <snapshot_date>."*
2. Never display a number that requires the reader to add back something that already
   happened. The moment a planner has to mentally re-add consumption, the report has
   lost them.

### Supply
```
opening_inventory[CM, component] = SUM(unrestricted_qty) WHERE CM-owned at that CM
                                 + WIP Consumed
```
**Do not add Lunar-owned stock to a CM's opening inventory.** Per §5.2, Lunar stock
destined for a CM already appears as an open CM PO; adding both double-counts. Lunar
inventory enters the picture only through the allocation recommender below.

Exclude `restricted_qty` entirely. Exclude Celestica unless toggled on. Floor negatives
at zero but count and report them.

Cross-check `CM Raw Inventory` (BOM tab) against `unrestricted_qty` (On Hand tab) per
component. They should agree; the On Hand tab is the source of truth. Report the variance
as a warning — a disagreement means one of the two extracts is stale.

```
eta = COALESCE(receipt_date, ship_date)          -- see 5.3; feeds differ by source
scheduled_receipts[CM, component, period]
    = SUM(quantity_open) WHERE eta falls in period AND eta IS NOT NULL
```
ETAs before today collapse into a "past due" bucket, shown separately. The 321 lines with
no `eta` go into an `undated_supply` column — never added to the balance. Carry
`eta_source` through so a planner can see that a Sienna date is a ship date, not a
receipt date.

### Runout
```
PAB[period] = PAB[period-1] + scheduled_receipts[period] - net_demand[period]
PAB[0]      = opening_inventory
runout_period  = first period where PAB < 0
shortage_qty   = abs(min(PAB)) over the horizon
first_shortage_date = start date of runout_period
```

### Coverage
```
blocks_buildable[component] = floor(available_qty / per_unit_usage)
```
Where a component is shared across products at the same CM and total demand exceeds
supply, flag it for **prioritisation** and show the per-product split so a planner can
decide allocation.

### Inventory allocation (recommendation only)
The allocatable pool is **`uncommitted_qty` on Lunar-owned rows**, summed across
locations — committed stock is already spoken for by an open CM PO (§5.2).

Where that pool could cover a CM shortage, emit: *"Have Qualitel place a PO for 4,500
units of 10-000099 — 91,115 uncommitted at Lunar."* Allocate by a configurable location
hierarchy. Never auto-commit.

Because the pool is already net of open CM POs, the "CM already has a PO out" case
resolves itself: those units are excluded from `uncommitted_qty` by construction. Still
show existing open CM POs alongside each recommendation so the planner sees the full
position before acting.

### Validation gates — must run before any engine run

**Blocking:** row counts not matching `EXPECTED_ROWS` (the truncation guard — see §5.5);
unmatched part numbers after normalisation; products in the Stitch List with no BOM;
products with `CM = TBD`; components with zero `Sourcing Flat Qty` across an entire
product; negative on-hand; duplicate PO schedule lines; snapshot dates disagreeing across
files; build plan referencing an unknown product LPN; build plan not starting at program
start (the cumulative burn-down is wrong without history).

**Warn but allow:** the committed-vs-CM-PO reconciliation variance from §5.2; undated open
POs; past-due receipt dates; Celestica rows; parts on the exclusion list; any component
where a shortage would disappear if in-transit qty were counted.

---

## 7. Reporting requirements

- Time slider: show parts with a shortage within *x* weeks.
- Toggle: on-hand only **vs** on-hand + on-order.
- Toggle: include/exclude Celestica.
- Planner notes per component, **timestamped and appended as history** (never overwritten).
- Exclusion list — parts that drop from the report (in-house printed labels, consumables).
  Editable in the UI, persisted to `data/exclusions.csv`.
- Blocks-buildable per component; shared-component shortages flagged for prioritisation.
- Build plan editable in-app with a **re-run** button.
- Exec summary: sorted by severity, one line per shorted component, with owner and date.

---

## 8. Architecture rules

```
lunar-planner/
  CLAUDE.md
  data/               # CSV snapshots, gitignored; notes.jsonl and exclusions.csv persist
  src/
    io.py             # loaders, row-count assertions
    normalize.py      # PN normalisation, CM alias map, dedup
    validate.py       # gates -> structured findings
    engine.py         # pure pandas: explode, net, runout. NO Streamlit imports.
    allocate.py       # Lunar inventory allocation recommendations
  tests/
    fixtures/pn_cases.csv
    test_normalize.py
    test_engine.py    # golden scenarios with hand-checked numbers
  app.py              # thin UI
```

**Non-negotiables:**
1. `engine.py` imports nothing from Streamlit and is callable from a plain script.
   Everything downstream depends on this.
2. **No LLM in the arithmetic path.** Every number must be reproducible and traceable to
   a row. An LLM may explain, triage or draft narrative — never compute.
3. Every derived number must be traceable to source rows. Keep a drill-down.
4. Normalisation happens once, at load, in one place.
5. `st.cache_data` on loaders; the run button clears cache.
