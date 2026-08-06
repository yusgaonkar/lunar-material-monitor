"""Normalisation tests. See CLAUDE.md 5.1, 5.2 and 5.6.

The reconciliation test on 10-000099 is the canary for the whole supply side. If
it drifts, something upstream broke and no number in the report can be trusted.
"""

import pandas as pd
import pytest

from src import io as lio
from src import normalize as nz


@pytest.fixture(scope="module")
def oh():
    return lio.load_onhand()


@pytest.fixture(scope="module")
def oo():
    return lio.load_onorder()


@pytest.fixture(scope="module")
def bom():
    return lio.load_bom_stitched()


@pytest.fixture(scope="module")
def norm(oh, oo, bom):
    return nz.normalize_all(oh, oo, bom)


@pytest.fixture(scope="module")
def pn_cases():
    path = lio.Path(__file__).parent / "fixtures" / "pn_cases.csv"
    skip = {
        i for i, ln in enumerate(path.read_text().splitlines())
        if not ln.strip() or ln.lstrip().startswith("#")
    }
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[],
                       skiprows=sorted(skip))


# --- clean_lpn ----------------------------------------------------------------

def test_pn_fixture_cases(pn_cases):
    """Every case in tests/fixtures/pn_cases.csv, driven off the file itself."""
    df = pd.DataFrame({"lpn": pn_cases["clean_in"]})
    got = nz.clean_lpn(df, "lpn")
    for i, row in pn_cases.iterrows():
        if row["action"].startswith("quarantine"):
            continue  # handled by quarantine_non_lunar, not clean_lpn
        assert got[i] == row["expected"], (
            f"{row['clean_in']!r} -> {got[i]!r}, expected {row['expected']!r} "
            f"({row['action']})"
        )


def test_fixture_covers_every_residual_action(pn_cases):
    """Guard against the fixture quietly losing a case."""
    actions = set(pn_cases["action"])
    assert "strip _old" in actions
    assert "strip _old2" in actions
    assert "strip -I" in actions
    assert any(a.startswith("quarantine") for a in actions)


def test_old2_stripped_whole_not_leaving_a_digit():
    """`_old2` must be tried before `_old`, else 10-005221_old2 -> 10-0052212."""
    df = pd.DataFrame({"lpn": ["10-005221_old2"]})
    assert nz.clean_lpn(df, "lpn").iloc[0] == "10-005221"


def test_trailing_letter_is_not_stripped():
    """10-08504A's trailing letter is part of the part number, not a suffix."""
    df = pd.DataFrame({"lpn": ["10-08504A", "10-06647B", "90-07675A", "10-06103C"]})
    out = nz.clean_lpn(df, "lpn")
    assert list(out) == ["10-08504A", "10-06647B", "90-07675A", "10-06103C"]


def test_suffix_only_stripped_at_the_end():
    df = pd.DataFrame({"lpn": ["10-005220_old_x", "10-Iold", "10-000054"]})
    out = nz.clean_lpn(df, "lpn")
    assert list(out) == ["10-005220_old_x", "10-Iold", "10-000054"]


@pytest.mark.parametrize("col", ["raw_part_number", "lunar_lpn_raw"])
def test_clean_lpn_refuses_raw_columns(col):
    """814-4604 -> 10-06647B is a lookup, not a rule (CLAUDE.md 5.1). Passing a
    raw column here has to fail loudly, not return plausible garbage."""
    df = pd.DataFrame({col: ["814-4604", "814L-10-000099 REV-02"]})
    with pytest.raises(ValueError, match="not derivable"):
        nz.clean_lpn(df, col)


def test_clean_lpn_rejects_missing_column():
    with pytest.raises(KeyError):
        nz.clean_lpn(pd.DataFrame({"lpn": ["10-000054"]}), "nope")


# --- the residual-fix tripwire ------------------------------------------------

def test_residual_fix_count(oh, oo):
    """10 rows on this snapshot, all On Hand. CLAUDE.md says 16; it is wrong.
    The value of the assertion is that it moves when upstream moves."""
    assert len(nz.residual_fixes(oh, "lpn")) == 10
    assert len(nz.residual_fixes(oo, "lunar_lpn")) == 0
    nz.assert_residual_fixes(10, 0)


def test_residual_fix_tripwire_fires():
    with pytest.raises(ValueError, match="upstream normalisation rule has changed"):
        nz.assert_residual_fixes(11)


def test_onorder_raw_still_dirty_but_clean_column_is_fixed(oo):
    """3,108 raw On Order values carry `_old` while the clean column carries
    none. This is the whole argument for not parsing raw, as data."""
    assert oo["lunar_lpn_raw"].str.contains(r"_old2?$", regex=True).sum() == 3108
    assert oo["lunar_lpn"].str.contains(r"_old2?$", regex=True).sum() == 0


# --- quarantine ---------------------------------------------------------------

def test_foreign_scheme_quarantined(norm):
    q = norm["quarantined"]
    foreign = q[q["_quarantine_reason"] == "foreign_scheme"]
    assert len(foreign) == 5
    assert set(foreign["_lpn"]) == set(nz.FOREIGN_PN)
    assert set(foreign["source_report"]) == {"CM: Celestica MX"}


def test_blank_lpn_quarantined_not_guessed(norm):
    q = norm["quarantined"]
    blank = q[q["_quarantine_reason"] == "blank"]
    assert len(blank) == 5
    assert set(blank["source_report"]) == {"CM: Qualitel WA"}


def test_quarantined_rows_are_removed_from_the_kept_frames(norm):
    assert len(norm["onhand"]) == 4770 - 5
    assert len(norm["onorder"]) == 6075 - 5
    assert not norm["onhand"]["_lpn"].isin(nz.FOREIGN_PN).any()
    assert (norm["onorder"]["_lpn"] == "").sum() == 0


def test_sixty_prefix_parts_are_not_quarantined(norm):
    """15 legitimate Lunar parts use a `60-` prefix that CLAUDE.md 2 does not
    document. A prefix-based rule would have dropped all of them."""
    sixty = {
        p for key in ("onhand", "onorder")
        for p in norm[key]["_lpn"] if p.startswith("60-")
    }
    assert len(sixty) == 15  # 14 on On Hand, 60-06855B only on On Order
    assert not norm["quarantined"]["_lpn"].str.startswith("60-").any()


# --- CM resolution ------------------------------------------------------------

@pytest.mark.parametrize("src,cm", [
    ("CM: Sienna GA", "Sienna"),
    ("CM: Qualitel WA", "Qualitel"),
    ("CM: Celestica MX", "Celestica"),
    ("Lunar Netsuite", "Lunar"),
])
def test_resolve_cm(src, cm):
    assert nz.resolve_cm({"source_report": src}) == cm


@pytest.mark.parametrize("value,party", [
    ("ATLN", "Sienna"),
    ("Sienna Corporation", "Sienna"),
    ("Sienna Corporation - GA", "Sienna"),
    ("ABV Electronics dba Sienna Corporation", "Sienna"),
    ("Seattle, WA", "Qualitel"),
    ("Qualitel", "Qualitel"),
    ("Qualitel Corporation - ACH", "Qualitel"),
    ("Monterrey Prod", "Celestica"),
    ("Celestica (USA) Inc. dba Celestica LLC (ACH)", "Celestica"),
    ("Lunar Energy", "Lunar"),
])
def test_resolve_party_aliases(value, party):
    assert nz.resolve_party(value) == party


def test_vendor_id_prefix_stripped():
    """NetSuite prefixes an internal vendor id. 273 of the 340 CM-PO-on-Lunar
    lines use the prefixed spelling, so missing this breaks the reconciliation."""
    assert nz.resolve_party("20004248 Lunar Energy") == "Lunar"
    assert nz.resolve_party("Lunar Energy Inc. - DNU") == "Lunar"


def test_resolve_party_returns_blank_for_suppliers():
    """Component distributors are not parties we resolve. '' is correct, not an
    error — most po_vendor values are suppliers."""
    for v in ["Avnet, Inc. (ACH)", "Arrow Electronics, Inc. (ACH) 2",
              "20002123 VENKEL LTD", "", "JIT - Reno"]:
        assert nz.resolve_party(v) == ""


def test_unmapped_source_report_raises(oh):
    """A CM resolving to '' would drop its entire inventory position."""
    broken = oh.copy()
    broken.loc[broken.index[0], "source_report"] = "CM: Plexus TX"
    with pytest.raises(ValueError, match="unmapped source_report"):
        nz.add_cm(broken)


def test_cm_is_not_inferred_from_location(norm):
    """NetSuite POs carry a CM as the ship-to `location`. Those rows must still
    resolve to Lunar — the material is on Lunar's book until it lands."""
    oo = norm["onorder"]
    shipto_cm = oo[oo["location"].isin(["Qualitel", "Unigen"])]
    assert len(shipto_cm) > 0
    assert set(shipto_cm["_cm"]) == {"Lunar"}


# --- synthetic PO row key -----------------------------------------------------

def test_po_row_key_is_unique(norm):
    """(po_number, po_line_item) is not unique — PO 139192 line 1 appears 4x."""
    oo = norm["onorder_all"]
    assert oo["_po_row_key"].is_unique
    dupes = oo[(oo["po_number"] == "139192") & (oo["po_line_item"] == "1")]
    assert len(dupes) == 4
    assert dupes["_po_row_key"].nunique() == 4


# --- the two pools, never summed ---------------------------------------------

def test_cm_available_excludes_lunar_stock(norm):
    """Adding Lunar's on-hand to a CM's opening inventory double-counts it — the
    same units are already an open CM PO (CLAUDE.md 5.2)."""
    ca = norm["cm_available"]
    assert "Lunar" not in set(ca["cm"])
    assert set(ca["cm"]) <= {"Sienna", "Qualitel", "Celestica"}


def test_cm_available_for_the_canary(norm):
    """10-000099 CM-owned on-hand: Sienna 404,000 + 565,573 + 172,937, Qualitel
    63,650, Celestica -14 (unfloored; the engine floors and reports it)."""
    ca = norm["cm_available"]
    got = ca[ca["part"] == "10-000099"].set_index("cm")["cm_available"]
    assert got["Sienna"] == pytest.approx(1_142_510)
    assert got["Qualitel"] == pytest.approx(63_650)
    assert got["Celestica"] == pytest.approx(-14)


def test_lunar_allocatable_is_uncommitted_across_all_locations(norm):
    """91,115 = 7,115 at Avnet + 84,000 at JIT - Reno."""
    la = norm["lunar_allocatable"]
    got = la.loc[la["part"] == "10-000099", "lunar_allocatable"].iloc[0]
    assert got == pytest.approx(91_115)


def test_the_two_pools_are_never_summed(norm):
    """Structural, not arithmetic: no part may appear in both pools under one
    total. The frames are separate and keyed differently on purpose."""
    assert "cm" in norm["cm_available"].columns
    assert "cm" not in norm["lunar_allocatable"].columns


# --- the reconciliation canary ------------------------------------------------

def test_canary_reconciles_to_zero(norm):
    """10-000099: unrestricted 1,039,115, uncommitted 91,115, committed 948,000,
    reconciling exactly to Sienna POs 5500086672/3/4 and 5500086685
    (320,000 + 308,000 + 108,885 + 211,115). Variance 0.

    If this test fails, stop and find out why before trusting any other number.
    """
    rec = norm["reconciliation"]
    row = rec[rec["part"] == "10-000099"].iloc[0]
    assert row["committed"] == pytest.approx(948_000)
    assert row["cm_po_open"] == pytest.approx(948_000)
    assert row["variance"] == pytest.approx(0)


def test_canary_components(oh, oo):
    """The inputs to the canary, so a failure above localises immediately."""
    lun = oh[(oh["lpn"] == "10-000099") & (oh["owned_by"] == "Lunar Energy")]
    assert lun["unrestricted_qty"].sum() == pytest.approx(1_039_115)
    assert lun["uncommitted_qty"].sum() == pytest.approx(91_115)

    pos = ["5500086672", "5500086673", "5500086674", "5500086685"]
    lines = oo[(oo["lunar_lpn"] == "10-000099") & oo["po_number"].isin(pos)]
    assert len(lines) == 4
    assert lines["quantity_open"].sum() == pytest.approx(948_000)


def test_reconciliation_covers_every_committed_part(norm):
    """A part with committed stock and no matching PO must appear with a
    variance, not fall out of the frame."""
    rec = norm["reconciliation"]
    assert rec["part"].is_unique
    assert (rec["variance"] == 0).sum() == 512
    assert (rec["variance"] != 0).sum() == 194


def test_reconciliation_ignores_lunar_purchases(norm):
    """10-000099 also has open NetSuite POs on Avnet totalling 1,144,000. Those
    are Lunar buying components, not a CM buying from Lunar, and counting them
    would swamp the gate."""
    oo = norm["onorder"]
    avnet = oo[(oo["_lpn"] == "10-000099") & (oo["_vendor"] == "")
               & (oo["quantity_open"] > 0)]
    assert len(avnet) > 0
    rec = norm["reconciliation"]
    assert rec.loc[rec["part"] == "10-000099", "cm_po_open"].iloc[0] == 948_000


# --- unmatched report ---------------------------------------------------------

def test_unmatched_written(norm, tmp_path):
    assert nz.UNMATCHED_PATH.exists()
    u = norm["unmatched"]
    assert list(u.columns) == ["part", "description", "source", "source_report",
                               "rows", "extended_value"]


def test_unmatched_counts(norm):
    """Matches CLAUDE.md 5.6 once residual cleanup and quarantine are applied:
    708 rows / 426 distinct On Hand before cleanup becomes 700 / 411 after."""
    u = norm["unmatched"]
    oh_u = u[u["source"] == "onhand"]
    oo_u = u[u["source"] == "onorder"]
    assert oh_u["rows"].sum() == 700
    assert oh_u["part"].nunique() == 411
    assert oo_u["rows"].sum() == 1094
    assert oo_u["part"].nunique() == 236


def test_unmatched_sorted_by_value_descending(norm):
    v = norm["unmatched"]["extended_value"]
    assert v.is_monotonic_decreasing


def test_join_coverage(norm, bom):
    """85% On Hand / 82% On Order, per CLAUDE.md 5.6. A sharp drop here means a
    broken join, which looks exactly like a demand-free part."""
    bom_parts = set(bom["item_number"])
    for key, expected in [("onhand", 0.852), ("onorder", 0.819)]:
        d = norm[key]
        hit = d["_lpn"].isin(bom_parts).sum()
        assert hit / len(d) == pytest.approx(expected, abs=0.005), key


def test_quarantined_parts_are_not_in_the_unmatched_report(norm):
    """Quarantine and unmatched are different findings. A foreign part number is
    not a join failure and must not pad the list a planner has to read."""
    u = set(norm["unmatched"]["part"])
    assert not (u & set(nz.FOREIGN_PN))
    assert "" not in u
