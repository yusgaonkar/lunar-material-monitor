"""Loaders. One per input file.

Rules (see CLAUDE.md sections 3 and 5):
  - Part numbers load as str. Never let pandas infer them — it mangles
    10-08504A and coerces Wurth MPNs to scientific notation.
  - Dates are parsed per column. `Updated at` is MM-DD-YYYY; everything else
    is ISO YYYY-MM-DD. One parser across all columns will misread them.
  - Every load asserts its row count. This is the truncation guard.
  - No renaming here. Normalisation happens in normalize.py.

Two conventions worth knowing before you use these frames:

  - Every field is read as text and only the columns named in NUM_COLS / DATE_COLS
    are converted. Nothing is inferred, so no part number can be mangled by a
    dtype guess, whether or not it appears in STR_COLS.

  - Missing text reads as the empty string, not NaN. `keep_default_na=False` is
    deliberate: pandas' default NA list contains the literal 'NA', and `makebuy`
    uses 'NA' as one of its three distinct encodings for not-applicable, the
    others being '-' and blank (CLAUDE.md 5.4). Letting pandas fold 'NA' into NaN
    would erase a distinction we are required to keep. So test text columns with
    `== ''`, not `.isna()`. Numeric and date columns do use NaN/NaT for blanks.
"""

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parent.parent / "data"

# DATA ROWS, excluding the header. Verified against the exports of 2026-08-13.
# Re-check these every time the snapshots are refreshed.
#
# Sheets shows a last-row number that INCLUDES the header, so these are each one
# less than what you read off the sheet:
#   bom_stitched 4399 | bom_flat 3620 | stitch_list 19 | onhand 4771 | onorder 6076
EXPECTED_ROWS = {
    "bom_stitched.csv": 4398,
    "bom_flat.csv": 3619,
    "stitch_list.csv": 18,
    "onhand.csv": 4770,
    "onorder.csv": 6075,
}

# Columns that must load as str, by file.
STR_COLS = {
    "bom_stitched.csv": ["Parent Product LPN", "item_number", "guid", "revision"],
    "bom_flat.csv": ["Parent Product LPN", "item_number", "revision"],
    "stitch_list.csv": ["Parent Product LPN"],
    "onhand.csv": ["raw_part_number", "lpn", "revision_number"],
    "onorder.csv": ["lunar_lpn_raw", "lunar_lpn", "po_number",
                    "po_line_item", "mfg_pn", "waybill"],
}

# Columns converted with pd.to_numeric. Everything not listed here stays text.
# `Flat Qty` and `Usage Qty` carry fractional values (e.g. 23.3), so these are
# floats, not ints.
NUM_COLS = {
    "bom_stitched.csv": ["level", "Product System Usage", "Flat Qty", "Usage Qty",
                         "Sourcing Flat Qty", "Sourcing Usage Qty", "Parent FG Built",
                         "Parent FG Consumed", "WIP Consumed", "CM Raw Inventory"],
    "bom_flat.csv": ["Product Usage per ESS", "Flat Qty", "Sourcing Flat Qty"],
    "stitch_list.csv": ["Product Usage per ESS"],
    "onhand.csv": ["unrestricted_qty", "restricted_qty", "unit_price",
                   "unrestricted_value", "restricted_value", "uncommitted_qty"],
    "onorder.csv": ["unit_price", "quantity", "quantity_billed", "quantity_received",
                    "quantity_open", "lead_time_days"],
}

# Date columns and their format. `Updated at` is the odd one out — MM-DD-YYYY
# where every other date column is ISO. Parsing these with a single blanket
# parser silently transposes day and month on the first twelve of each month.
ISO = "%Y-%m-%d"
US = "%m-%d-%Y"
DATE_COLS = {
    "bom_stitched.csv": {"Updated at": US},
    "bom_flat.csv": {"Updated at": US},
    "stitch_list.csv": {},  # no date column on this tab
    "onhand.csv": {"Updated at": US},
    "onorder.csv": {"Updated at": US, "po_created_date": ISO, "ship_date": ISO,
                    "receipt_date": ISO, "_db_created_at": ISO,
                    "last_modified": ISO},
}

# Hand-maintained inputs. Row counts vary by design, so these get no truncation
# assertion — the guard exists for the machine exports, which have a known size.
# Leading `#` lines in these files are documentation and are skipped on read.
HAND_COLS = {
    "build_plan.csv": {
        "str": ["product_lpn"], "num": ["qty"], "date": {"period_start": ISO},
    },
    "plan_to_date.csv": {
        "str": ["product_lpn"], "num": ["plan_to_date_qty", "units_received"],
        "date": {},
    },
    "in_transit.csv": {
        "str": ["product_lpn", "cm"], "num": ["qty"], "date": {"asn_date": ISO},
    },
    "exclusions.csv": {
        "str": ["component_lpn", "reason", "added_by"], "num": [],
        "date": {"added_date": ISO},
    },
}

# The indented BOM carries one sentinel row: level 0, blank parent, this
# item_number. It is not a component. CLAUDE.md 5.4.
SENTINEL_ITEM = "Custom Parent List"


class RowCountMismatch(AssertionError):
    """Raised when a file's row count differs from EXPECTED_ROWS.

    Almost always a truncated export. Re-export via File > Download > CSV.
    """


def assert_rows(name: str, n: int) -> None:
    expected = EXPECTED_ROWS.get(name)
    if expected is None:
        raise RowCountMismatch(
            f"{name}: no expected row count set. Read it off the sheet and put "
            f"it in EXPECTED_ROWS before loading this file."
        )
    if n != expected:
        raise RowCountMismatch(
            f"{name}: got {n:,} rows, expected {expected:,}. "
            f"Likely a truncated export — re-export via File > Download > CSV."
        )


def _to_num(s: pd.Series) -> pd.Series:
    """Blank -> NaN, then strict numeric. Raises on anything unparseable.

    errors='coerce' is wrong here: it would turn a corrupted quantity into NaN
    and the runout would just quietly read low.
    """
    return pd.to_numeric(s.replace("", None), errors="raise")


def _to_date(s: pd.Series, fmt: str) -> pd.Series:
    """Blank -> NaT, then strict parse against one explicit format."""
    return pd.to_datetime(s.replace("", None), format=fmt, errors="raise")


def _read_export(name: str) -> pd.DataFrame:
    """Read one machine export: all text, assert row count, then convert.

    The assertion runs on the raw row count, before any filtering, so it is
    measuring the export and not our handling of it.
    """
    df = pd.read_csv(DATA / name, dtype=str, keep_default_na=False, na_values=[])
    assert_rows(name, len(df))

    missing = [c for c in STR_COLS[name] if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: expected columns absent: {missing}")

    for col in NUM_COLS[name]:
        df[col] = _to_num(df[col])
    for col, fmt in DATE_COLS[name].items():
        df[col] = _to_date(df[col], fmt)

    log.info("%s: %d rows x %d cols", name, len(df), df.shape[1])
    return df


def _read_hand(name: str) -> pd.DataFrame:
    """Read a hand-maintained file, skipping whole-line `#` comments.

    Skipping by row index rather than passing comment='#' to pandas keeps a '#'
    inside a description or a note from truncating the field. This module is
    itself called io, so it also avoids needing to import the stdlib io — which
    resolves back to this file when it is run as a script.
    """
    path = DATA / name
    spec = HAND_COLS[name]
    comments = {
        i for i, ln in enumerate(path.read_text().splitlines())
        if not ln.strip() or ln.lstrip().startswith("#")
    }
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[],
                     skiprows=sorted(comments))

    expected = spec["str"] + spec["num"] + list(spec["date"])
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: expected columns absent: {missing}")

    for col in spec["num"]:
        df[col] = _to_num(df[col])
    for col, fmt in spec["date"].items():
        df[col] = _to_date(df[col], fmt)

    log.info("%s: %d rows x %d cols", name, len(df), df.shape[1])
    return df


# --- machine exports ---------------------------------------------------------

def load_bom_stitched() -> pd.DataFrame:
    """Stitched Indented BOMs — 25 cols, one row per BOM position.

    Returns 4,397 rows: the raw export is 4,398 and the sentinel row is dropped.
    EXPECTED_ROWS stays at 4,398 on purpose; it describes the file, not the frame.

    `item_number` is NOT unique — 11 items sit at several BOM positions. Key on
    (Parent Product LPN, guid) or aggregate. CLAUDE.md 5.4.
    """
    df = _read_export("bom_stitched.csv")

    sentinel = (
        (df["item_number"] == SENTINEL_ITEM)
        & (df["level"] == 0)
        & (df["Parent Product LPN"] == "")
    )
    n = int(sentinel.sum())
    if n != 1:
        raise ValueError(
            f"bom_stitched.csv: expected exactly 1 sentinel row "
            f"(item_number={SENTINEL_ITEM!r}, level 0, blank parent), found {n}. "
            f"The export layout has changed — look at it before going further."
        )
    log.info("bom_stitched.csv: dropped 1 sentinel row, %d remain", len(df) - 1)
    return df[~sentinel].reset_index(drop=True)


def load_bom_flat() -> pd.DataFrame:
    """Flat product-level BOM — 16 cols. Covers 15 of 18 products."""
    return _read_export("bom_flat.csv")


def load_stitch_list() -> pd.DataFrame:
    """Stitch List Input — the product master. 18 rows, one per product.

    `Product Usage per ESS` is not used in planning; the build plan is already
    stated at product level. CLAUDE.md 4.
    """
    return _read_export("stitch_list.csv")


def load_onhand() -> pd.DataFrame:
    """On Hand — 19 cols.

    `lpn` is the clean part number and the join key. `raw_part_number` is for the
    drill-down only, never a join key (CLAUDE.md 5.1). Contains negative
    quantities and consigned rows; both are real and handled downstream.
    """
    return _read_export("onhand.csv")


def load_onorder() -> pd.DataFrame:
    """On Order — 36 cols.

    `lunar_lpn` is the clean part number. (po_number, po_line_item) is NOT
    unique — one PO line appears as several schedule lines, so a synthetic row
    key is needed (CLAUDE.md 5.4). `ship_date` and `receipt_date` are mutually
    exclusive by feed; the eta coalesce belongs in normalize.py, not here.
    """
    return _read_export("onorder.csv")


# --- hand-maintained inputs --------------------------------------------------

def load_build_plan() -> pd.DataFrame:
    """Forward build plan, product x period. Editable in the UI."""
    return _read_hand("build_plan.csv")


def load_plan_to_date() -> pd.DataFrame:
    """Sets the backlog: plan_to_date_qty - (units_received + in_transit).

    Highest-risk input in the tool. A wrong value moves every runout date in
    that product's BOM the same way and nothing looks anomalous.
    """
    return _read_hand("plan_to_date.csv")


def load_in_transit() -> pd.DataFrame:
    """TLAs with an ASN submitted but not yet received. Defaults to empty.

    Not auto-derivable: `waybill` is populated on 0 of 6,075 on-order rows and
    `is_fully_shipped` failed a recency check. CLAUDE.md 6.
    """
    return _read_hand("in_transit.csv")


def load_exclusions() -> pd.DataFrame:
    """Parts that drop from the report. Editable in the UI, persisted here."""
    return _read_hand("exclusions.csv")


# --- summary -----------------------------------------------------------------

def load_all() -> dict[str, pd.DataFrame]:
    """Every input, keyed by filename. Order matches the summary table.

    Note: exclusions are now loaded from Supabase in app_minimal.py, not from CSV.
    """
    return {
        "bom_stitched.csv": load_bom_stitched(),
        "bom_flat.csv": load_bom_flat(),
        "stitch_list.csv": load_stitch_list(),
        "onhand.csv": load_onhand(),
        "onorder.csv": load_onorder(),
        "build_plan.csv": load_build_plan(),
        "plan_to_date.csv": load_plan_to_date(),
        "in_transit.csv": load_in_transit(),
    }


# The column holding the clean part number, per file. Differs by file and that
# drift is deliberate — see CLAUDE.md 4.
PART_COL = {
    "bom_stitched.csv": "item_number",
    "bom_flat.csv": "item_number",
    "stitch_list.csv": "Parent Product LPN",
    "onhand.csv": "lpn",
    "onorder.csv": "lunar_lpn",
    "build_plan.csv": "product_lpn",
    "plan_to_date.csv": "product_lpn",
    "in_transit.csv": "product_lpn",
    "exclusions.csv": "component_lpn",
}


def snapshot_date(df: pd.DataFrame) -> pd.Timestamp | None:
    """The single `Updated at` value, or None if the frame has no such column.

    Raises if a file carries more than one — a mixed-date export means someone
    pasted two refreshes into one tab.
    """
    if "Updated at" not in df.columns:
        return None
    vals = df["Updated at"].dropna().unique()
    if len(vals) > 1:
        raise ValueError(
            f"more than one snapshot date in a single file: "
            f"{[str(v)[:10] for v in sorted(vals)]}"
        )
    return pd.Timestamp(vals[0]) if len(vals) else None


def summary(frames: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """file / rows / cols / snapshot date / distinct parts, one row per input."""
    frames = load_all() if frames is None else frames
    rows = []
    for name, df in frames.items():
        snap = snapshot_date(df)
        rows.append({
            "file": name,
            "rows": len(df),
            "cols": df.shape[1],
            "snapshot": snap.date().isoformat() if snap is not None else "—",
            "distinct_parts": df[PART_COL[name]].replace("", None).nunique(),
        })
    return pd.DataFrame(rows)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    frames = load_all()
    tbl = summary(frames)
    print()
    print(tbl.to_string(index=False))

    snaps = {s for s in tbl["snapshot"] if s != "—"}
    print()
    if len(snaps) == 1:
        print(f"Snapshot date: {snaps.pop()} — consistent across all dated files.")
    else:
        print(
            f"WARNING: snapshot dates disagree across files: {sorted(snaps)}. "
            f"validate.py must block the run — a mixed-date snapshot reconciles "
            f"to nothing."
        )


if __name__ == "__main__":
    main()
