"""Loader tests. See CLAUDE.md sections 3, 4 and 5.

The point of most of these is not that the loaders work today — it is that a
silently truncated or dtype-mangled export fails loudly tomorrow. A short export
reads as a stockout and nothing about it looks wrong (CLAUDE.md 5.5).
"""

import pandas as pd
import pytest

from src import io as lio


# --- shape and the truncation guard ------------------------------------------

EXPECTED_SHAPES = {
    # file: (rows in the returned frame, cols)
    "bom_stitched.csv": (4378, 25),  # 4,379 raw less the one sentinel row
    "bom_flat.csv": (3619, 16),
    "stitch_list.csv": (18, 5),
    "onhand.csv": (4770, 19),
    "onorder.csv": (6075, 36),
}


@pytest.fixture(scope="module")
def frames():
    return lio.load_all()


@pytest.mark.parametrize("name,shape", EXPECTED_SHAPES.items())
def test_shape(frames, name, shape):
    assert frames[name].shape == shape


def test_expected_rows_covers_every_export():
    assert set(lio.EXPECTED_ROWS) == set(EXPECTED_SHAPES)


def test_columns_are_verbatim(frames):
    """Renaming belongs in normalize.py. The drift between files is deliberate."""
    assert lio.PART_COL["onhand.csv"] == "lpn"
    assert lio.PART_COL["onorder.csv"] == "lunar_lpn"
    assert "item_category" in frames["onhand.csv"].columns
    assert "item_category_" in frames["onorder.csv"].columns  # trailing underscore
    assert "raw_part_number" in frames["onhand.csv"].columns
    assert "lunar_lpn_raw" in frames["onorder.csv"].columns
    assert "procurement_type" in frames["bom_stitched.csv"].columns
    assert "procurementtype" in frames["onhand.csv"].columns


def test_assert_rows_rejects_wrong_count():
    with pytest.raises(lio.RowCountMismatch, match="truncated"):
        lio.assert_rows("onhand.csv", 4769)


def test_assert_rows_rejects_unknown_file():
    with pytest.raises(lio.RowCountMismatch, match="no expected row count"):
        lio.assert_rows("something_new.csv", 100)


def test_truncated_export_raises(tmp_path, monkeypatch):
    """The guard has to fire on a real short file, not just on a bad integer.

    This is the 98-rows-of-4,379 failure from CLAUDE.md 5.5: the payload was
    structurally valid and every completeness heuristic passed.
    """
    src = (lio.DATA / "onhand.csv").read_text().splitlines()
    (tmp_path / "onhand.csv").write_text("\n".join(src[:99]) + "\n")
    monkeypatch.setattr(lio, "DATA", tmp_path)
    with pytest.raises(lio.RowCountMismatch):
        lio.load_onhand()


# --- part numbers -------------------------------------------------------------

def test_part_columns_are_strings(frames):
    """Not `object` — pandas 3 gives these dtype `str`. What matters is that
    nothing numeric-looking was inferred."""
    for name, col in lio.PART_COL.items():
        s = frames[name][col]
        assert pd.api.types.is_string_dtype(s), f"{name}:{col} is {s.dtype}"


@pytest.mark.parametrize("name", list(lio.STR_COLS))
def test_declared_str_cols_are_strings(frames, name):
    for col in lio.STR_COLS[name]:
        s = frames[name][col]
        assert pd.api.types.is_string_dtype(s), f"{name}:{col} is {s.dtype}"


def test_trailing_letter_part_number_survives(frames):
    """10-08504A must not become 10-08504 or 1.08504e+07.

    It appears only on On Order — 4 lines, the confirmed Qualitel 139131 /
    NetSuite PO-US-09229 duplicate buy from CLAUDE.md 5.2.
    """
    oo = frames["onorder.csv"]
    assert (oo["lunar_lpn"] == "10-08504A").sum() == 4


def test_all_digit_mfg_pn_not_coerced(frames):
    """Wurth MPNs are long digit strings. Inferred as numbers they become floats
    in scientific notation and never join again."""
    mfg = frames["onorder.csv"]["mfg_pn"]
    assert (mfg == "7490220120").any()
    assert not mfg.str.contains(r"E\+", case=False, regex=True).any()


def test_leading_zero_part_numbers_keep_their_zeros(frames):
    lpn = frames["onhand.csv"]["lpn"]
    assert (lpn == "10-000054").any()


def test_blank_lunar_lpn_is_preserved_not_guessed(frames):
    """5 On Order rows have no clean part number. Report them, never guess
    (CLAUDE.md 5.1). Missing text is '' here, not NaN — see the io module docstring.
    """
    assert (frames["onorder.csv"]["lunar_lpn"] == "").sum() == 5


# --- the sentinel row ---------------------------------------------------------

def test_sentinel_row_dropped(frames):
    b = frames["bom_stitched.csv"]
    assert not (b["item_number"] == lio.SENTINEL_ITEM).any()
    assert not (b["Parent Product LPN"] == "").any()
    assert (b["level"] == 0).sum() == 0


def test_expected_rows_still_describes_the_file():
    """EXPECTED_ROWS measures the export, not the frame. Do not 'fix' it to
    4,378 to make the shapes line up."""
    assert lio.EXPECTED_ROWS["bom_stitched.csv"] == 4379


def test_all_18_products_present(frames):
    """No product may lose its BOM to the sentinel filter. CLAUDE.md 5.5."""
    b = frames["bom_stitched.csv"]
    stitch = frames["stitch_list.csv"]
    assert b["Parent Product LPN"].nunique() == 18
    assert set(stitch["Parent Product LPN"]) == set(b["Parent Product LPN"].unique())


# --- dates --------------------------------------------------------------------

def test_updated_at_is_us_format(frames):
    """08-04-2026 is 4 August, not 8 April. A blanket parser gets this wrong on
    the first twelve days of every month and never raises."""
    for name in ["bom_stitched.csv", "bom_flat.csv", "onhand.csv", "onorder.csv"]:
        snap = lio.snapshot_date(frames[name])
        assert snap == pd.Timestamp("2026-08-04"), name


def test_iso_date_columns_parsed(frames):
    oo = frames["onorder.csv"]
    for col in ["po_created_date", "ship_date", "receipt_date",
                "_db_created_at", "last_modified"]:
        assert pd.api.types.is_datetime64_any_dtype(oo[col]), col


def test_snapshot_dates_agree_across_files(frames):
    """A mixed-date snapshot reconciles to nothing. validate.py blocks on this;
    this test catches it at refresh time instead."""
    snaps = {
        lio.snapshot_date(df) for df in frames.values()
        if "Updated at" in df.columns
    }
    assert len(snaps) == 1


def test_snapshot_date_rejects_mixed_dates(frames):
    mixed = frames["onhand.csv"].copy()
    mixed.loc[mixed.index[0], "Updated at"] = pd.Timestamp("2026-08-03")
    with pytest.raises(ValueError, match="more than one snapshot date"):
        lio.snapshot_date(mixed)


def test_stitch_list_has_no_snapshot_date(frames):
    assert lio.snapshot_date(frames["stitch_list.csv"]) is None


# --- numerics and NA handling -------------------------------------------------

def test_fractional_quantities_survive(frames):
    """Flat Qty carries values like 23.3. Rounded to int the demand is wrong."""
    b = frames["bom_stitched.csv"]
    assert pd.api.types.is_float_dtype(b["Flat Qty"])
    assert ((b["Flat Qty"] % 1) != 0).any()


def test_makebuy_na_is_a_string_not_nan(frames):
    """pandas' default NA list contains the literal 'NA', and makebuy uses 'NA'
    as a distinct not-applicable encoding (CLAUDE.md 5.4). Folding it into NaN
    would erase the distinction from '-'."""
    mb = frames["bom_stitched.csv"]["makebuy"]
    assert (mb == "NA").sum() == 70
    assert (mb == "-").sum() == 1476
    assert mb.isna().sum() == 0


def test_approval_status_null_is_a_literal_string(frames):
    """CM rows carry the four-character string '[null]', not SQL NULL."""
    assert (frames["onorder.csv"]["approval_status"] == "[null]").sum() == 2106


def test_negatives_are_not_floored_at_load(frames):
    """140 negative on-hand rows are real (backflush lag). Flooring belongs in
    the engine, which also has to count and report them."""
    assert (frames["onhand.csv"]["unrestricted_qty"] < 0).sum() == 140


def test_dead_columns_are_still_read(frames):
    """waybill and lead_time_days are 100% empty. Load them anyway so the day
    they start arriving is visible rather than silently ignored."""
    oo = frames["onorder.csv"]
    assert (oo["waybill"] != "").sum() == 0
    assert oo["lead_time_days"].notna().sum() == 0


# --- embedded quotes ----------------------------------------------------------

def test_embedded_double_quotes_parsed(frames):
    """Item names contain characters like .135" Hole Dia. If quoting broke,
    fields would shift and the row count assertion would already have failed —
    this pins the actual values.

    Measured: 41 rows / 27 distinct names in bom_stitched. CLAUDE.md 5.4 says 18,
    which is understated.
    """
    b = frames["bom_stitched.csv"]
    names = b["item_name"]
    quoted = names[names.str.contains('"', regex=False)]
    assert len(quoted) == 41
    assert quoted.nunique() == 27
    assert (names == 'Ball Stud Receiver, 8lb, .135" Hole Dia, 0.17" Thk, SS').any()


# --- hand-maintained inputs ---------------------------------------------------

def test_comment_lines_skipped(frames):
    """plan_to_date.csv opens with three # lines of documentation."""
    p = frames["plan_to_date.csv"]
    assert list(p.columns) == ["product_lpn", "plan_to_date_qty", "units_received"]
    assert (p["product_lpn"] == "90-001223").any()
    assert p.loc[p["product_lpn"] == "90-001223", "plan_to_date_qty"].iloc[0] == 3040


def test_empty_hand_files_load_with_columns(frames):
    """in_transit and exclusions are header-only today. They must still come back
    as typed, empty frames — in_transit defaults to 0 (CLAUDE.md 6)."""
    it = frames["in_transit.csv"]
    assert len(it) == 0
    assert list(it.columns) == ["product_lpn", "cm", "qty", "asn_date"]
    ex = frames["exclusions.csv"]
    assert len(ex) == 0
    assert list(ex.columns) == ["component_lpn", "reason", "added_by", "added_date"]


def test_build_plan_typed(frames):
    bp = frames["build_plan.csv"]
    assert pd.api.types.is_datetime64_any_dtype(bp["period_start"])
    assert pd.api.types.is_string_dtype(bp["product_lpn"])
    assert bp["qty"].sum() == 7400


def test_build_plan_products_exist(frames):
    """A plan row naming an unknown product is a blocking gate in validate.py.
    Catch the obvious case here."""
    known = set(frames["stitch_list.csv"]["Parent Product LPN"])
    assert set(frames["build_plan.csv"]["product_lpn"]) <= known
