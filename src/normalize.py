"""Normalisation. Validate the clean part number; never re-derive it.

Read CLAUDE.md 5.1 before changing anything here. The scope of this module is
deliberately small. LunarDB has already normalised the part numbers and the
mapping is NOT reproducible by regex:

    814-4604  ->  10-06647B
    814-4641  ->  90-07675A

Qualitel numbers its own PCBAs. There is no rule that turns one into the other —
it is a lookup table and LunarDB already holds it. A normaliser written from
scratch would silently drop those rows, and they are top-level assemblies, the
highest-value items on the report.

So: trust `lpn` / `lunar_lpn`, apply the handful of residual fixes, quarantine
what is genuinely foreign, and report everything that fails to join.
`raw_part_number` / `lunar_lpn_raw` stay for the drill-down. They are never a
join key.

Added columns are prefixed `_` so nothing collides with a source column, and
nothing here mutates its argument.
"""

import logging
import re

import pandas as pd

from . import io as lio

log = logging.getLogger(__name__)

# --- residual part-number cleanup --------------------------------------------

# Upstream normalisation leaks these suffixes on a handful of rows. `_old2` must
# be tried before `_old` or it leaves a stray '2'.
RESIDUAL_RE = re.compile(r"(?:_old2|_old|-I)$")

# Measured on the 2026-08-04 snapshot: 10 rows, all On Hand — 9 Lunar NetSuite
# `_old`/`_old2` plus one Celestica `-I`. On Order needs none, even though 3,108
# of its RAW values still carry `_old`. That gap is the clearest demonstration
# available that the clean column is already fixed and the raw one must not be
# parsed.
#
# CLAUDE.md 5.1 and BUILD_PROMPTS Prompt 2 both say 16. That does not hold on
# this snapshot, and hardcoding 16 would raise on good data. The tripwire is what
# matters here, not the specific figure.
EXPECTED_RESIDUAL_FIXES = 10

# --- foreign numbering schemes ------------------------------------------------

# Five Celestica rows carry a different numbering scheme entirely. Exclude them;
# do not force-match.
#
# An explicit list, not a prefix rule, on purpose: 15 legitimate Lunar parts use
# a `60-` prefix (60-005955, 60-06569A, ... PCB and PCBA items on Lunar's own
# book) which CLAUDE.md 2 does not document. A regex admitting only 10-/20-/30-/
# 90- would quarantine all 15.
FOREIGN_PN = frozenset({
    "3480-0329", "3480-0330", "3508-2627", "8100-0352", "8105-0264",
})

# --- CM identity --------------------------------------------------------------

SIENNA, QUALITEL, CELESTICA, LUNAR = "Sienna", "Qualitel", "Celestica", "Lunar"

# `source_report` is the only reliable CM key (CLAUDE.md 5.4). `location` means
# three different things by feed and must never resolve a CM on its own.
SOURCE_REPORT_TO_CM = {
    "CM: Sienna GA": SIENNA,
    "CM: Qualitel WA": QUALITEL,
    "CM: Celestica MX": CELESTICA,
    "Lunar Netsuite": LUNAR,
}

# Every other observed spelling of the same four parties, across owned_by,
# po_vendor and location. Used to resolve ownership and vendor — NOT to infer a
# CM from location.
PARTY_ALIASES = {
    # owned_by
    "Sienna Corporation": SIENNA,
    "Qualitel": QUALITEL,
    "Celestica": CELESTICA,
    "Lunar Energy": LUNAR,
    # po_vendor
    "ABV Electronics dba Sienna Corporation": SIENNA,
    "Qualitel Corporation - ACH": QUALITEL,
    "Celestica (USA) Inc. dba Celestica LLC (ACH)": CELESTICA,
    "Lunar Energy Inc. - DNU": LUNAR,
    # location — for the drill-down label only
    "ATLN": SIENNA,
    "Sienna Corporation - GA": SIENNA,
    "Seattle, WA": QUALITEL,
    "Monterrey Prod": CELESTICA,
    "Celestica - Mexico": CELESTICA,
    "Celestica - TX": CELESTICA,
}

# NetSuite prefixes vendor names with an internal vendor id: '20004248 Lunar
# Energy'. Strip it before the alias lookup. This is not cosmetic — the
# reconciliation gate matches po_vendor to Lunar Energy, and 273 of the 340
# relevant lines use the prefixed spelling. Matching the bare string alone would
# quietly reconcile against a fifth of the POs and report the rest as variance.
VENDOR_ID_RE = re.compile(r"^\d{6,}\s+")

RAW_COLS = ("raw_part_number", "lunar_lpn_raw")


def clean_lpn(df: pd.DataFrame, col: str) -> pd.Series:
    """The already-clean part-number column with residual suffixes stripped.

    `col` is `lpn` on On Hand or `lunar_lpn` on On Order. Never a raw column.
    """
    if col in RAW_COLS:
        raise ValueError(
            f"{col!r} is a raw CM part number. It is not derivable to an LPN by "
            f"rule — see CLAUDE.md 5.1. Use the clean column."
        )
    if col not in df.columns:
        raise KeyError(f"{col!r} not in frame; columns are {list(df.columns)}")
    return df[col].str.replace(RESIDUAL_RE, "", regex=True)


def residual_fixes(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """The rows clean_lpn would change, for reporting."""
    cleaned = clean_lpn(df, col)
    changed = cleaned != df[col]
    out = pd.DataFrame({
        "lpn_in": df.loc[changed, col],
        "lpn_out": cleaned[changed],
    })
    if "source_report" in df.columns:
        out["source_report"] = df.loc[changed, "source_report"]
    return out.reset_index(drop=True)


def assert_residual_fixes(*counts: int) -> None:
    """Tripwire on the residual-cleanup count. Raises if upstream changed.

    A growing count means LunarDB's normalisation has regressed. A shrinking one
    means it improved, or that a feed is missing. Either way you want to know
    rather than absorb it silently.
    """
    total = sum(counts)
    if total != EXPECTED_RESIDUAL_FIXES:
        raise ValueError(
            f"residual part-number fixes: got {total}, expected "
            f"{EXPECTED_RESIDUAL_FIXES}. The upstream normalisation rule has "
            f"changed — look at the new cases before updating this number."
        )


def quarantine_non_lunar(
    df: pd.DataFrame, col: str = "_lpn"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split off rows whose part number is not a usable Lunar LPN.

    Returns (kept, quarantined). Two reasons are recorded separately:
      foreign_scheme — a different numbering system (the 5 Celestica rows)
      blank          — no clean part number at all; report, never guess
    """
    pn = df[col]
    blank = pn == ""
    foreign = pn.isin(FOREIGN_PN)
    drop = blank | foreign

    quarantined = df[drop].copy()
    quarantined["_quarantine_reason"] = ["blank" if b else "foreign_scheme"
                                        for b in blank[drop]]
    return df[~drop].copy(), quarantined


def resolve_party(value: str) -> str:
    """Canonical party for one owned_by / po_vendor / location string.

    Returns '' for anything unrecognised — a component supplier, a 3PL, a
    warehouse. That is the common case and is not an error.
    """
    if not isinstance(value, str) or not value:
        return ""
    return PARTY_ALIASES.get(VENDOR_ID_RE.sub("", value).strip(), "")


def resolve_cm(row) -> str:
    """Canonical CM for one inventory row, keyed on source_report.

    Returns 'Lunar' for NetSuite rows: they are Lunar's book, not a CM's, and per
    CLAUDE.md 5.2 they must never be added to a CM's on-hand.

    `location` is deliberately not consulted. On NetSuite POs it holds the ship-to
    ('Qualitel', 'Unigen'), on CM feeds the CM site, and on Lunar rows a warehouse
    or 3PL. Inferring a CM from it mixes all three.
    """
    src = row["source_report"]
    cm = SOURCE_REPORT_TO_CM.get(src, "")
    if not cm:
        log.warning("unmapped source_report %r — add it to SOURCE_REPORT_TO_CM", src)
    return cm


def add_cm(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorised resolve_cm, plus resolved owner and vendor where present."""
    out = df.copy()
    out["_cm"] = df["source_report"].map(SOURCE_REPORT_TO_CM).fillna("")
    unmapped = sorted(set(df.loc[out["_cm"] == "", "source_report"]))
    if unmapped:
        raise ValueError(
            f"unmapped source_report values: {unmapped}. Add them to "
            f"SOURCE_REPORT_TO_CM — a CM silently resolving to '' drops its "
            f"entire inventory position from the report."
        )
    if "owned_by" in df.columns:
        out["_owner"] = df["owned_by"].map(resolve_party)
    if "po_vendor" in df.columns:
        out["_vendor"] = df["po_vendor"].map(resolve_party)
    return out


def normalize_onhand(oh: pd.DataFrame) -> pd.DataFrame:
    """On Hand with `_lpn`, `_cm` and `_owner` added.

    Nothing renamed, nothing dropped — quarantine and exclusion are separate,
    explicit steps so that what left the report is always countable.
    """
    out = add_cm(oh)
    out["_lpn"] = clean_lpn(oh, "lpn")
    return out


def normalize_onorder(oo: pd.DataFrame) -> pd.DataFrame:
    """On Order with `_lpn`, `_cm`, `_vendor` and a synthetic row key.

    (po_number, po_line_item) is NOT unique — PO 139192 line 1 appears 4x as
    separate schedule lines (CLAUDE.md 5.4), so anything keyed on that pair
    silently collapses three of them.
    """
    out = add_cm(oo)
    out["_lpn"] = clean_lpn(oo, "lunar_lpn")
    out["_po_row_key"] = (
        out["po_number"] + "|" + out["po_line_item"] + "|"
        + out.groupby(["po_number", "po_line_item"]).cumcount().astype(str)
    )
    return out


# --- the two supply pools, never summed --------------------------------------

def cm_available(oh_norm: pd.DataFrame) -> pd.DataFrame:
    """CM-owned raw on-hand by (cm, part). Supply side of the CM runout.

    Lunar-owned stock is excluded by design: Lunar stock destined for a CM
    already appears as an open CM PO, so counting both double-counts it
    (CLAUDE.md 5.2). Negatives are NOT floored here — the engine floors them and
    has to report the count.
    """
    cm_rows = oh_norm[(oh_norm["_cm"] != LUNAR) & (oh_norm["_owner"] != LUNAR)]
    return (
        cm_rows.groupby(["_cm", "_lpn"], as_index=False)["unrestricted_qty"]
        .sum()
        .rename(columns={"_cm": "cm", "_lpn": "part",
                         "unrestricted_qty": "cm_available"})
    )


def lunar_allocatable(oh_norm: pd.DataFrame) -> pd.DataFrame:
    """`uncommitted_qty` on Lunar-owned rows, summed across ALL locations.

    What is genuinely free to promise. The committed remainder is already spoken
    for by an open CM PO, which is why the allocation recommender can draw on
    this pool without separately checking for existing POs.
    """
    lun = oh_norm[oh_norm["_owner"] == LUNAR]
    return (
        lun.groupby("_lpn", as_index=False)["uncommitted_qty"]
        .sum()
        .rename(columns={"_lpn": "part", "uncommitted_qty": "lunar_allocatable"})
    )


def reconcile_committed(
    oh_norm: pd.DataFrame, oo_norm: pd.DataFrame
) -> pd.DataFrame:
    """The CLAUDE.md 5.2 gate: committed Lunar stock vs open CM POs on Lunar.

        committed  = SUM(unrestricted_qty - uncommitted_qty) over Lunar-owned rows
        cm_po_open = SUM(quantity_open) over open CM POs whose vendor is Lunar

    They should agree per part. A variance means a missed PO, a stale snapshot or
    a broken part-number join. This is the canary for the whole supply side.

    Warn-only per CLAUDE.md 6 — it does not block a run.
    """
    lun = oh_norm[oh_norm["_owner"] == LUNAR]
    committed = (
        (lun["unrestricted_qty"] - lun["uncommitted_qty"])
        .groupby(lun["_lpn"]).sum().rename("committed")
    )

    cm_po = oo_norm[
        (oo_norm["_cm"] != LUNAR)
        & (oo_norm["_vendor"] == LUNAR)
        & (oo_norm["is_closed"] == "FALSE")
        & (oo_norm["quantity_open"] > 0)
    ]
    po_open = cm_po.groupby("_lpn")["quantity_open"].sum().rename("cm_po_open")

    rec = pd.concat([committed, po_open], axis=1).fillna(0.0)
    rec["variance"] = rec["committed"] - rec["cm_po_open"]
    return (
        rec.reset_index()
        .rename(columns={"_lpn": "part"})
        .sort_values("variance", key=lambda s: s.abs(), ascending=False)
        .reset_index(drop=True)
    )


# --- unmatched report ---------------------------------------------------------

UNMATCHED_PATH = lio.DATA / "_unmatched.csv"


def unmatched_report(
    oh_norm: pd.DataFrame,
    oo_norm: pd.DataFrame,
    bom: pd.DataFrame,
    path=UNMATCHED_PATH,
) -> pd.DataFrame:
    """Anti-join inventory against the BOM and write data/_unmatched.csv.

    A first-class output, not a debug artefact. A part quietly falling out of the
    join is indistinguishable from a part with no demand (CLAUDE.md 5.6). Most
    misses are legitimate — obsolete parts, other programmes, non-BOM consumables
    — which is exactly why a human has to read the list rather than trust a
    coverage percentage.
    """
    bom_parts = set(bom["item_number"])

    oh_kept, _ = quarantine_non_lunar(oh_norm)
    oo_kept, _ = quarantine_non_lunar(oo_norm)

    oh_miss = oh_kept[~oh_kept["_lpn"].isin(bom_parts)].assign(
        _value=lambda d: d["unrestricted_value"],
        _desc=lambda d: d["description"],
    )
    oo_miss = oo_kept[~oo_kept["_lpn"].isin(bom_parts)].assign(
        _value=lambda d: d["quantity_open"] * d["unit_price"].fillna(0.0),
        _desc=lambda d: d["item_description"],
    )

    frames = []
    for src, miss in [("onhand", oh_miss), ("onorder", oo_miss)]:
        if miss.empty:
            continue
        g = miss.groupby(["_lpn", "source_report"], as_index=False).agg(
            rows=("_lpn", "size"),
            extended_value=("_value", "sum"),
            description=("_desc", "first"),
        )
        g.insert(0, "source", src)
        frames.append(g)

    out = (
        pd.concat(frames, ignore_index=True)
        .rename(columns={"_lpn": "part"})
        .loc[:, ["part", "description", "source", "source_report", "rows",
                 "extended_value"]]
        .sort_values("extended_value", ascending=False)
        .reset_index(drop=True)
    )
    out.to_csv(path, index=False)
    log.info(
        "%s: %d rows, %d distinct parts, %.0f extended value",
        path.name, out["rows"].sum(), out["part"].nunique(),
        out["extended_value"].sum(),
    )
    return out


# --- one call that does the lot ----------------------------------------------

def normalize_all(oh: pd.DataFrame, oo: pd.DataFrame, bom: pd.DataFrame) -> dict:
    """Normalise both inventory feeds and return frames plus findings.

    Normalisation happens once, here, at load — CLAUDE.md 8 rule 4. `onhand` and
    `onorder` are the quarantined-out frames the engine should use; `*_all` keep
    every row for the drill-down.
    """
    oh_norm = normalize_onhand(oh)
    oo_norm = normalize_onorder(oo)

    fixes_oh = residual_fixes(oh, "lpn")
    fixes_oo = residual_fixes(oo, "lunar_lpn")
    assert_residual_fixes(len(fixes_oh), len(fixes_oo))

    oh_kept, oh_quar = quarantine_non_lunar(oh_norm)
    oo_kept, oo_quar = quarantine_non_lunar(oo_norm)

    return {
        "onhand": oh_kept,
        "onorder": oo_kept,
        "onhand_all": oh_norm,
        "onorder_all": oo_norm,
        "residual_fixes": pd.concat([fixes_oh, fixes_oo], ignore_index=True),
        "quarantined": pd.concat([oh_quar, oo_quar], ignore_index=True),
        "cm_available": cm_available(oh_norm),
        "lunar_allocatable": lunar_allocatable(oh_norm),
        "reconciliation": reconcile_committed(oh_norm, oo_norm),
        "unmatched": unmatched_report(oh_norm, oo_norm, bom),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    oh, oo, bom = lio.load_onhand(), lio.load_onorder(), lio.load_bom_stitched()
    r = normalize_all(oh, oo, bom)

    print(f"\nresidual fixes: {len(r['residual_fixes'])}")
    print(r["residual_fixes"].to_string(index=False))

    print(f"\nquarantined: {len(r['quarantined'])}")
    print(r["quarantined"]
          .groupby(["_quarantine_reason", "source_report"]).size().to_string())

    rec = r["reconciliation"]
    print(f"\nreconciliation: {len(rec)} parts, "
          f"{(rec['variance'] == 0).sum()} clean, "
          f"{(rec['variance'] != 0).sum()} with a variance")
    print(rec.head(8).to_string(index=False))
    print("\n10-000099 (the canary):")
    print(rec[rec["part"] == "10-000099"].to_string(index=False))

    u = r["unmatched"]
    print(f"\nunmatched: {u['rows'].sum()} rows, "
          f"{u['part'].nunique()} distinct parts")
    print(u.head(8).to_string(index=False))


if __name__ == "__main__":
    main()
