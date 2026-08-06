# 48-Hour Build Plan — Sequenced Prompts

Use with Claude Code, in a repo that has `CLAUDE.md` at the root. Each prompt is scoped
to one commit-sized chunk with its own acceptance test. **Do not paste them all at once.**

---

## Why sequenced, not one mega-prompt

A single giant prompt for a tool this shaped fails in a predictable way: the model builds
the UI first because that's the most concrete part of the request, wires the calculations
into Streamlit callbacks, and by the time you find a netting bug there is no way to test
it without clicking through the app. You then spend hour 30 of 48 doing surgery.

Sequencing forces the dependency order — data contract, then normalisation, then engine,
then UI — and gives you a working, testable artefact at each step. If you run out of time
at step 4 you still have a correct engine and a script that prints the report.

Three things make each prompt land:

1. **Real column names and real gotchas up front.** `CLAUDE.md` does this. The model
   cannot infer that `C` is a consignment prefix at Sienna only.
2. **An acceptance test stated before the code.** Give it the number you expect.
3. **An explicit "do not build" list.** Scope creep is what kills 48-hour targets.

---

## Hour 0 — before any prompting

Do these yourself. They are decisions, not code.

- [ ] Export every tab to CSV into `data/` (File → Download → CSV, per tab). **Not via
      the API** — it truncates silently. Stitched Indented BOM should be **3,908 rows**;
      if you get 98, the export is truncated.
- [ ] Record the true row count of each tab into `EXPECTED_ROWS` in `io.py`.
- [ ] Create `data/build_plan.csv`: `product_lpn,period_start,qty` — **forward periods
      only**. No program history needed under the pipeline-balance model.
- [ ] Create `data/plan_to_date.csv`: `product_lpn,plan_to_date_qty,units_received`.
      One row per product. This is the whole of the history you need.
- [ ] **Check whether in-transit can be auto-derived** (CLAUDE.md §6, "candidate
      auto-source"). Filter the On Order tab to `source_report = Lunar Netsuite` and
      `po_vendor` = a CM, then see whether `waybill` / `is_fully_shipped` are populated
      on partially-received lines. Ten minutes, and it may kill a standing manual input.
- [ ] If not derivable, create `data/in_transit.csv` with headers
      `product_lpn,cm,qty,asn_date`. Empty is fine to start.
- [ ] Pick 3 components you know the answer for. Hand-calculate their runout week into
      `data/golden_cases.csv`. These are your regression tests.
- [ ] Pick one part with committed Lunar stock (`10-000099` works — 948,000 committed
      against four Sienna POs) as the reconciliation test case.

---

## Prompt 1 — Data contract and loaders (target: 2h)

```
Read CLAUDE.md first.

Build src/io.py only. No engine, no UI.

src/io.py already exists with EXPECTED_ROWS populated and verified, plus assert_rows()
and a STR_COLS map. Build on it — do not replace those.

The real data is already in data/. Verified: bom_stitched 4,379 rows x 25 cols;
bom_flat 3,619 x 16; stitch_list 18 x 5; onhand 4,770 x 19; onorder 6,075 x 36.

Write one loader per CSV: load_bom_stitched, load_bom_flat, load_stitch_list,
load_onhand, load_onorder, load_build_plan, load_plan_to_date, load_in_transit,
load_exclusions. Each must:

- Use dtype=str for every ID / part-number column (STR_COLS already lists them).
  Never let pandas infer a part number — it mangles 10-08504A and coerces Wurth MPNs
  to scientific notation. Read numeric columns explicitly with pd.to_numeric.
- Call assert_rows(filename, len(df)) immediately after read. This is the truncation
  guard and it is not optional.
- Parse dates per column, never with one blanket parser:
    `Updated at`            MM-DD-YYYY
    po_created_date, receipt_date, ship_date, _db_created_at, last_modified
                            ISO YYYY-MM-DD
- Handle embedded double quotes in item names (18 rows contain characters like
  `.135" Hole Dia`). Use the default C parser with proper quoting; verify the row
  count assertion still passes, which is how you know quoting worked.
- Filter the one sentinel row from bom_stitched: item_number == 'Custom Parent List',
  level 0, blank Parent Product LPN. Log that you dropped exactly 1 row.
- Return verbatim column names from CLAUDE.md section 4. Do NOT rename anything —
  renaming belongs in normalize.py.

Note the column-name drift between files and do not "helpfully" harmonise it here:
  On Hand uses  raw_part_number / lpn / item_category
  On Order uses lunar_lpn_raw / lunar_lpn / item_category_
  BOM uses      item_number / category_name / procurement_type

Then tests/test_io.py: load every file, assert shape, assert part-number columns are
dtype object and that '10-08504A' survives intact, assert the sentinel row is gone.

Finally print a load summary table: file, rows, cols, snapshot date, distinct parts.
All five snapshot dates should read 08-03-2026 — flag it if they disagree.

Do not build: normalisation, the engine, or any UI.
```

---

## Prompt 2 — Normalisation (target: 1h — scope cut, see CLAUDE.md 5.1)

```
Read CLAUDE.md sections 5.1 and 5.6 before writing anything. The scope of this module
is much smaller than it looks: LunarDB has already normalised the part numbers and the
mapping is NOT reproducible by regex (814-4604 -> 10-06647B is a lookup table). We
validate the clean column; we do not re-derive it.

Build src/normalize.py.

1. clean_lpn(df, col) -> Series. Takes the ALREADY-CLEAN column (`lpn` on On Hand,
   `lunar_lpn` on On Order) and applies only the residual fixes:
     - strip trailing `_old` / `_old2`  (e.g. 10-005220_old -> 10-005220)
     - strip trailing `-I`              (10-06527A-I -> 10-06527A)
   Assert exactly 16 rows are touched across both files. If that count changes, the
   upstream rule changed — raise, do not silently absorb it.
   Never parse raw_part_number / lunar_lpn_raw. Keep them for drill-down only.

2. quarantine_non_lunar(df). Five Celestica rows carry a foreign numbering scheme
   (3480-0329, 3480-0330, 3508-2627, 8100-0352, 8105-0264). Exclude them and list them.
   Also handle the 5 On Order rows with a blank lunar_lpn — report, never guess.

3. resolve_cm(row) -> str. Use source_report as the primary key. Map the aliases:
   ATLN / Sienna Corporation - GA / ABV Electronics dba Sienna Corporation -> Sienna.
   Seattle, WA / Qualitel / Qualitel Corporation - ACH -> Qualitel.
   Monterrey Prod / Celestica (USA) Inc. dba Celestica LLC (ACH) -> Celestica.
   Never derive CM from `location` alone — it means three different things by feed.

4. unmatched_report(). Anti-join inventory against the 2,179 BOM item_numbers and write
   data/_unmatched.csv with part, description, source_report, row count and extended
   value, sorted by value descending. Expect roughly 426 distinct unmatched On Hand
   parts and 236 On Order. This is a real output, not a debug file — a part dropping
   out of the join looks exactly like a part with no demand.

4. Implement CLAUDE.md 5.2. Two separate pools, never summed into one:
   - cm_available(cm, part)   = SUM(unrestricted_qty) over CM-owned rows at that CM
   - lunar_allocatable(part)  = SUM(uncommitted_qty) over Lunar-owned rows, ALL locations
   Plus reconcile_committed(part) returning committed (= unrestricted - uncommitted,
   summed across locations) against SUM(quantity_open) on open CM POs where po_vendor
   resolves to Lunar Energy, with the variance.
   Assert on 10-000099: unrestricted 1,039,115, uncommitted 91,115, committed 948,000,
   reconciling to POs 5500086672 / 5500086673 / 5500086674 / 5500086685. Variance 0.
   This test is the canary for the whole supply side — if it drifts, something upstream
   broke.

Acceptance: pytest green, the 16-row assertion holds, the 10-000099 reconciliation
returns variance 0, and data/_unmatched.csv is written.

Do not build the engine or UI. Do not write regex normalisation of raw part numbers —
CLAUDE.md 5.1 explains why it cannot work.
```

---

## Prompt 3 — Validation gates (target: 1.5h)

```
Build src/validate.py.

One function per gate, each returning a list of structured findings:
{severity: 'block'|'warn', code: str, message: str, affected_rows: DataFrame}.

Blocking gates: unmatched part numbers after normalisation; Stitch List products with no
BOM rows; products with CM = 'TBD'; components with zero Sourcing Flat Qty across an
entire product; negative on-hand quantities; duplicate (po_number, po_line_item) without
a synthetic key; snapshot dates disagreeing across files; build plan rows referencing an
unknown product LPN.

Warning gates: undated open POs; receipt dates in the past; Celestica rows present;
parts matching the exclusion list.

run_all_validations() returns the findings list. The engine must refuse to run if any
'block' findings exist, unless override=True is passed.

Acceptance: run against the real data and print the findings table. I expect to see
blocks for TBD CMs and warnings for the 10 undated CM POs. Show me the output.
```

---

## Prompt 4 — The engine (target: 4h; the heart of it)

```
Build src/engine.py implementing CLAUDE.md section 6 exactly.

Hard rule: this module imports pandas and numpy only. No Streamlit. It must run from a
plain python script. Everything downstream depends on that.

Grain: (cm, component_lpn, period). Weekly periods. Keep the product-level detail in a
separate long dataframe for drill-down — do not throw it away in the rollup.

Implement in this order, testing each before moving on:
Implement the pipeline-balance model in CLAUDE.md 6 exactly. Read that whole section
before writing a line — the placement of WIP on the supply side is deliberate and the
worked example there is your first test.

1. compute_remaining_builds()  -- product space. backlog = plan_to_date - (received +
   in_transit), loaded into the FIRST period only. Then + forward plan per period.
   in_transit comes from data/in_transit.csv, defaults 0, stays a NAMED column all the
   way to the report — never silently folded in.
2. explode_demand()  -- remaining_builds * Sourcing_Flat_Qty, per product, rolled up by
   CM. Do NOT use Parent FG Consumed anywhere; it is derived and adds only risk.
3. build_supply()  -- CM-owned unrestricted_qty PLUS WIP Consumed as opening inventory.
   Lunar stock excluded (it already appears as CM open-order supply, CLAUDE.md 5.2).
   Scheduled receipts keyed on eta = COALESCE(receipt_date, ship_date) per CLAUDE.md 5.3
   — Sienna populates only ship_date, Qualitel only receipt_date. Carry eta_source.
   Past-due into a separate 'past_due' bucket. The 321 undated lines into an
   `undated_supply` column, reported but NEVER in the balance.
   Also cross-check CM Raw Inventory vs unrestricted_qty and warn on variance.

4. classify_part_state() -- per CLAUDE.md 5.7, every component resolves to exactly one
   of IN_PRODUCTION / ON_ORDER_ONLY / NOT_SOURCED. NOT_SOURCED is an NPI readiness gap,
   NOT a shortage, and must never appear in the shortage count. Default the report to
   products in production; NPI products behind a toggle.
4. compute_runout()  -- running PAB, first-negative period, shortage qty, runout date.
5. compute_coverage()  -- blocks_buildable = floor(available / per_unit_usage);
   flag components shared across products at the same CM where total demand exceeds
   supply, with the per-product demand split attached.

Every output row must carry the source row ids that produced it. I need drill-down.

Write tests/test_engine.py with:
- The worked example from CLAUDE.md 6: usage 2, plan 1,000 blocks, 300 received,
  50 in transit, 100 in WIP, raw on-hand 1,100. Demand must be 1,300 and supply 1,300.
  This one test pins the whole netting model — write it FIRST.
- A synthetic 3-part, 2-period case where I can verify PAB by hand.
- The three golden components from data/golden_cases.csv with their expected runout week.
- An assertion that undated supply never changes PAB.
- An assertion that a component with zero demand never appears as short.
- An assertion that no component with Sourcing Flat Qty = 0 carries demand (the
  load-bearing assumption in CLAUDE.md 6 — if this breaks, WIP double-counts).

Then write scripts/run_report.py that runs the whole thing end to end and prints the
runout table and the exec summary to stdout. I want to see correct numbers in a terminal
before any UI exists.

Acceptance: golden cases pass, and run_report.py prints a plausible report.
```

---

## Prompt 5 — Streamlit UI (target: 3h)

```
Build app.py. Thin. It calls src/ functions and renders — no calculation logic in this
file, and no logic that isn't also reachable from scripts/run_report.py.

Pages:
1. Run — validation findings first (blocks in red, warnings amber), then a Run Engine
   button, disabled while blocks exist with an override checkbox.
2. Runout report — the detail grid. Controls:
   - Time slider: show components with a shortage within x weeks (1-52)
   - Toggle: on-hand only vs on-hand + on-order
   - Toggle: include Celestica
   - Filters: CM, product, component category.
     CM filter is a straight narrowing of the grid. PRODUCT filter is not — a component
     can appear in several BOMs at one CM, and its shortage is driven by total demand
     across all of them. Filtering to a product shows components USED BY that product,
     but the demand, balance and runout stay CM-total, with a per-product demand split
     shown alongside. Never recompute runout against one product's demand in isolation —
     that would show a part as healthy while another product is consuming it.
   - Three visual states for supply: dated / undated / none. Undated must be visually
     distinct from no-supply — do not use the same blank cell for both.
   - Click a row to drill into the source rows that produced it.
3. Exec summary — sorted by severity. One line per shorted component: part, description,
   CM, products affected, first shortage date, shortage qty, weeks of cover, note.
4. Build plan editor — st.data_editor as a WIDE GRID: one row per product (18 rows,
   showing LPN + alias + CM), one column per month across the horizon. The planner types
   volumes straight into cells. Do NOT render it as a long product-by-month list.
   Pivot wide for editing, melt back to long (product_lpn, period_start, qty) on save
   to data/build_plan.csv. Validate on save: unknown product LPN, negative qty,
   non-numeric. Then a Re-run button that clears the cache and re-runs the engine.

   A second small editor for data/plan_to_date.csv — one row per product, columns
   plan_to_date_qty and units_received. This sets the backlog and is the highest-risk
   input in the tool, so label it clearly and show the derived backlog next to it so a
   planner can see the consequence of what they typed.
5. Exclusions — editable list, persisted to data/exclusions.csv.

Planner notes: append-only to data/notes.jsonl as
{timestamp, user, component_lpn, cm, note}. Render full history per component, newest
first. Never overwrite.

Cache loaders with st.cache_data; the Run button clears the cache.

Do not add: authentication, multi-user, a database, charts beyond a simple PAB line,
or export-to-Excel. Not in the pilot.
```

---

## Prompt 6 — Hardening (whatever time remains)

```
1. Add a data/README.md documenting how to refresh each CSV, with the exact
   File > Download > CSV path per tab and the expected row count.
2. Add scripts/check_snapshot.py that verifies all files share one snapshot date and
   that no row count has dropped more than 10% since last run.
3. Write a one-page docs/CALCULATION.md that a planner can read: plain English, one
   worked example from real data, from build plan through to runout date. This is what
   you will use to get sign-off from the material planning group, and it will surface
   spec disagreements faster than any demo.
```

---

## Where agents earn their place

**Build-time — this is the real win for a 48-hour target.**

Prompts 2, 3 and 5 have almost no shared surface. Once Prompt 1 fixes the data contract,
run three Claude Code subagents in parallel: one on normalisation + fixtures, one on
validation gates, one scaffolding the Streamlit shell against a stubbed engine interface.
Agree the function signatures first and put them in `CLAUDE.md` so the stubs match. That
compresses roughly 7 sequential hours into about 3.

Do **not** parallelise Prompt 4. The engine is where correctness lives and it needs your
attention on one thread.

**Runtime — three places where LLM judgment beats rules.**

1. **Validation triage.** Rules produce a list of 200 unmatched part numbers. An agent
   reads them alongside the item master and writes: *"58 of these are Sienna REV-1
   unpadded variants — the normaliser handles REV-01 but not REV-1. 12 are genuinely
   obsolete. 3 look like typos in the CM feed."* That is the difference between a
   findings dump and something a planner acts on.
2. **Exec narrative.** Turn the shortage table into five bullets, framed as *what changed
   since the last run*. Deltas are what an exec wants; a table of current state is not.
3. **Planner-note rollup.** You are capturing timestamped notes. An agent summarising
   "here is what the team said about this part over the last month" makes the note
   history worth keeping rather than write-only.

**The line you should not cross.**

No LLM anywhere in the arithmetic path. When someone asks why a part is short, the answer
has to be reproducible and traceable to a row — same inputs, same number, every time.
Agents explain, triage and draft. Deterministic pandas computes. Put this in `CLAUDE.md`
(it is in §8) so it survives future sessions where you have forgotten you decided it.

**One more, slightly outside the 48 hours.** Your context doc says the inventory feed is
CSVs emailed from CM ERPs into Box. That pipeline will break — a CM renames a column, a
file doesn't arrive, a format shifts. A scheduled agent that watches the Box folder,
diffs each new file against the expected schema, and messages you when something drifts
is a genuinely good use of an agent and maybe an hour of work. Consider it for week two.

---

## Realistic scope check

48 hours gets you: correct normalisation, validation gates, a working engine, a runout
report, an exec summary, and an editable build plan, across all products with a BOM.

It does not get you: live data connections, multi-user, or auth. The allocation
recommender is now cheap (the pool is just `uncommitted_qty`) so keep it in, but ship it
last.

**The two things most likely to cost you hours:**

1. **Part number normalisation.** Five encodings, and the `C`-prefix and `3MS` rules are
   both capable of silently corrupting a good part number. Budget the full 3 hours and
   keep the unmatched-parts report visible from day one.
2. **`plan_to_date` and `units_received` per product.** Only 18 numbers each, but they
   have no source of truth and they set the backlog for every component. Get them from
   the planners early and have someone sanity-check them — a wrong `plan_to_date` shifts
   every runout date for that product's whole BOM in the same direction, which is the
   hardest kind of error to spot because nothing looks anomalous.
