"""Pre-run validation gates. CLAUDE.md 6, "Validation gates".

Every gate returns Findings. Blocking findings stop the engine unless the caller
passes override=True — that escape hatch exists because a planner mid-crisis
needs a number more than they need a clean bill of health, but the override has
to be a deliberate act, visible in the UI.

The gates encode failures that have actually been observed in this data, not
hypothetical ones. Read CLAUDE.md 5 before adding to them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import io as lio
from . import normalize as nz

BLOCK = "block"
WARN = "warn"


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    rows: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def n(self) -> int:
        return len(self.rows)


def _f(sev, code, msg, rows=None) -> Finding:
    return Finding(sev, code, msg, rows if rows is not None else pd.DataFrame())


# --- gates --------------------------------------------------------------------

def gate_snapshot_dates(frames: dict) -> list[Finding]:
    """Every export must be the same snapshot. Mixing dates silently misstates
    the balance — old demand against new supply, or the reverse."""
    dates = {}
    for name, df in frames.items():
        d = lio.snapshot_date(df)
        if d is not None:
            dates[name] = pd.Timestamp(d).date()
    if len(set(dates.values())) > 1:
        return [_f(BLOCK, "snapshot_mismatch",
                   f"Exports carry different snapshot dates: {dates}. "
                   f"Re-export them all from the same day.",
                   pd.DataFrame({"file": list(dates), "snapshot": list(dates.values())}))]
    return []


def gate_products_without_bom(frames: dict) -> list[Finding]:
    sl = frames["stitch_list.csv"]
    bom = frames["bom_stitched.csv"]
    have = set(bom["Parent Product LPN"])
    miss = sl[~sl["Parent Product LPN"].isin(have)]
    if len(miss):
        return [_f(BLOCK, "product_without_bom",
                   f"{len(miss)} product(s) in the Stitch List have no BOM rows. "
                   f"Their demand would silently vanish.", miss)]
    return []


def gate_cm_tbd(frames: dict) -> list[Finding]:
    sl = frames["stitch_list.csv"]
    tbd = sl[sl["CM"].str.strip().str.upper().isin({"TBD", "", "NA"})]
    if len(tbd):
        return [_f(BLOCK, "cm_tbd",
                   f"{len(tbd)} product(s) have no CM assigned "
                   f"({', '.join(tbd['Parent Product LPN'])}). They cannot be "
                   f"planned — a CM is the grain of the whole report.", tbd)]
    return []


def gate_build_plan_products(frames: dict) -> list[Finding]:
    bp, sl = frames["build_plan.csv"], frames["stitch_list.csv"]
    if not len(bp):
        return [_f(BLOCK, "build_plan_empty",
                   "data/build_plan.csv is empty. Nothing to plan against.")]
    known = set(sl["Parent Product LPN"])
    bad = bp[~bp["product_lpn"].isin(known)]
    out = []
    if len(bad):
        # Changed to WARN instead of BLOCK to allow evolving product list
        out.append(_f(WARN, "build_plan_unknown_product",
                      f"{len(bad)} build plan row(s) name a product that is not in "
                      f"the Stitch List (skipped from planning).", bad))
    covered = set(bp["product_lpn"])
    gap = sl[~sl["Parent Product LPN"].isin(covered)]
    if len(gap):
        out.append(_f(WARN, "build_plan_missing_product",
                      f"{len(gap)} product(s) have no build plan and will show zero "
                      f"demand.", gap[["Parent Product LPN", "Description", "CM"]]))
    return out


def gate_zero_sourcing(frames: dict) -> list[Finding]:
    """No product may have zero Sourcing Flat Qty across its whole BOM."""
    bom = frames["bom_stitched.csv"].copy()
    bom["_sfq"] = pd.to_numeric(
        bom["Sourcing Flat Qty"], errors="coerce").fillna(0)
    per = bom.groupby("Parent Product LPN", as_index=False)["_sfq"].sum()
    bad = per[per["_sfq"] <= 0]
    if len(bad):
        return [_f(BLOCK, "zero_sourcing_qty",
                   f"{len(bad)} product(s) have no buy-parts.", bad)]
    return []


def gate_negative_onhand(oh_norm: pd.DataFrame) -> list[Finding]:
    """140 rows are negative, mostly Celestica backflush lag. Real and floored."""
    neg = oh_norm[oh_norm["unrestricted_qty"] < 0]
    if not len(neg):
        return []
    by = neg.groupby("source_report").size().to_dict()
    return [_f(WARN, "negative_onhand",
               f"{len(neg)} negative on-hand rows {by}. Floored at zero.",
               neg[["source_report", "_lpn", "location", "unrestricted_qty"]].head(50))]


def gate_duplicate_po_lines(oo_norm: pd.DataFrame) -> list[Finding]:
    """(po_number, po_line_item) is not unique."""
    if "_po_row_key" not in oo_norm.columns:
        return [_f(BLOCK, "no_po_row_key",
                   "On Order missing synthetic row key.")]
    dup = oo_norm.duplicated(["po_number", "po_line_item"], keep=False)
    if dup.any():
        return [_f(WARN, "duplicate_po_lines",
                   f"{int(dup.sum())} rows share a (po_number, po_line_item) pair.",
                   oo_norm.loc[dup, ["po_number", "po_line_item", "_lpn"]].head(50))]
    return []


def gate_reconciliation(rec: pd.DataFrame, tol: float = 1.0) -> list[Finding]:
    """Committed Lunar stock vs open CM POs on Lunar. Warn-only."""
    bad = rec[rec["variance"].abs() > tol]
    if not len(bad):
        return []
    return [_f(WARN, "committed_vs_cm_po",
               f"{len(bad)} parts with variance >±{tol}. "
               f"Largest: {bad['variance'].abs().max():,.0f}.",
               bad.head(50))]


def gate_undated_supply(undated: pd.DataFrame) -> list[Finding]:
    if not len(undated):
        return []
    tot = undated["undated"].sum()
    return [_f(WARN, "undated_supply",
               f"{len(undated)} (cm, part) positions with {tot:,.0f} units "
               f"of open PO and no ETA. Not counted.",
               undated.sort_values("undated", ascending=False).head(50))]


def gate_past_due(past_due: pd.DataFrame) -> list[Finding]:
    if not len(past_due):
        return []
    return [_f(WARN, "past_due_receipts",
               f"{len(past_due)} (cm, part) positions with past-due PO.",
               past_due.sort_values("past_due", ascending=False).head(50))]


def gate_unmatched(unmatched: pd.DataFrame) -> list[Finding]:
    if not len(unmatched):
        return []
    return [_f(WARN, "unmatched_parts",
               f"{len(unmatched)} inventory/PO parts don't join to BOM.",
               unmatched.head(50))]


def gate_not_sourced(summary: pd.DataFrame) -> list[Finding]:
    """NPI readiness gaps, not shortages. CLAUDE.md 5.7."""
    if "state" not in summary.columns:
        return []
    ns = summary[summary["state"] == "NOT_SOURCED"]
    if not len(ns):
        return []
    return [_f(WARN, "not_sourced",
               f"{len(ns)} component(s) have demand but no on-hand or PO. "
               f"NPI gaps, excluded from shortage count.",
               ns[["cm", "part", "description"]].head(50))]


def gate_false_shortage(backlog: pd.DataFrame) -> list[Finding]:
    """In-transit unknowns. CLAUDE.md 6."""
    if not len(backlog) or backlog["in_transit"].sum() > 0:
        return []
    return [_f(WARN, "in_transit_unknown",
               "data/in_transit.csv is empty. Backlog is overstated by whatever "
               "has shipped but not been received. Populate to sharpen shortages.")]


# --- orchestration ------------------------------------------------------------

def run_all(frames: dict | None = None, result: dict | None = None) -> list[Finding]:
    """Every gate. Pass `result` from engine.run() to include post-run gates."""
    frames = frames or lio.load_all()
    out: list[Finding] = []
    out += gate_snapshot_dates(frames)
    out += gate_products_without_bom(frames)
    out += gate_cm_tbd(frames)
    out += gate_build_plan_products(frames)
    out += gate_zero_sourcing(frames)

    norm = nz.normalize_all(
        frames["onhand.csv"], frames["onorder.csv"], frames["bom_stitched.csv"])
    out += gate_negative_onhand(norm["onhand_all"])
    out += gate_duplicate_po_lines(norm["onorder_all"])
    out += gate_reconciliation(norm["reconciliation"])
    out += gate_unmatched(norm["unmatched"])

    if result:
        out += gate_undated_supply(result["undated"])
        out += gate_past_due(result["past_due"])
        out += gate_not_sourced(result["summary"])
        out += gate_false_shortage(result["backlog"])
    return out


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == BLOCK]


def to_frame(findings: list[Finding]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"severity": f.severity, "code": f.code, "rows": f.n, "message": f.message}
         for f in findings]
    )
