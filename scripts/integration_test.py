#!/usr/bin/env python3
"""End-to-end integration test: load, normalise, validate, run engine.

Run this to verify the whole pipeline works:
    python3 scripts/integration_test.py

Exit code 0 = success. Non-zero = blocking gates fired or an exception occurred.
"""

import sys
import logging

import pandas as pd

sys.path.insert(0, ".")
from src import io as lio, normalize as nz, validate as val, engine as eng

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def main():
    log.info("\n=== Integration Test ===\n")

    # --- Load ---
    log.info("Loading all inputs...")
    try:
        frames = lio.load_all()
    except Exception as e:
        log.error("LOAD FAILED: %s", e)
        return 1

    log.info(lio.summary(frames).to_string(index=False))

    # --- Pre-run gates ---
    log.info("\nRunning pre-run validation gates...")
    findings = val.run_all(frames)
    blocks = val.blocking(findings)

    if blocks:
        log.error("\n%d BLOCKING GATES FIRED:", len(blocks))
        for f in blocks:
            log.error("  [%s] %s — %d rows", f.code, f.message, f.n)
        return 1

    if findings:
        log.warning("\n%d warnings (not blocking):", len(findings))
        for f in findings:
            log.warning("  [%s] %s", f.code, f.message)

    # --- Normalise ---
    log.info("\nNormalising...")
    norm = nz.normalize_all(frames["onhand.csv"], frames["onorder.csv"],
                            frames["bom_stitched.csv"])
    log.info("  onhand: %d input → %d kept + %d quarantined", len(norm["onhand_all"]),
             len(norm["onhand"]), len(norm["quarantined"]))
    log.info("  onorder: %d input → %d kept", len(norm["onorder_all"]),
             len(norm["onorder"]))
    log.info("  unmatched: %d parts", len(norm["unmatched"]))

    # --- Run engine ---
    log.info("\nRunning engine...")
    try:
        result = eng.run(frames)
    except Exception as e:
        log.error("ENGINE FAILED: %s", e)
        import traceback
        traceback.print_exc()
        return 1

    log.info("  snapshot: %s", result["snapshot"])
    log.info("  week0: %s", result["config"].week0)
    log.info("  demand rows: %d", len(result["demand"]))
    log.info("  supply rows: %d", len(result["opening"]))
    log.info("  runout grid rows: %d", len(result["pab"]))
    log.info("  summary rows: %d", len(result["summary"]))

    s = result["summary"]
    log.info("\n  Parts by state:")
    log.info("    IN_PRODUCTION: %d", int((s["state"] == eng.IN_PRODUCTION).sum()))
    log.info("    ON_ORDER_ONLY: %d", int((s["state"] == eng.ON_ORDER_ONLY).sum()))
    log.info("    NOT_SOURCED: %d", int((s["state"] == eng.NOT_SOURCED).sum()))

    log.info("\n  Shortages (after NPI filter):")
    short = s[s["is_shortage"]]
    log.info("    Total: %d", len(short))
    if len(short):
        log.info("    First date: %s", short["first_shortage_date"].min())
        log.info("    Largest qty: %d", int(short["shortage_qty"].max()))
        log.info("\n  Top 5:")
        for i, row in short.head(5).iterrows():
            log.info("    %s@%s: %s — %d units short",
                     row["part"], row["cm"], row["description"],
                     int(row["shortage_qty"]))

    # --- Post-run gates ---
    log.info("\nRunning post-run validation gates...")
    post_findings = val.run_all(frames, result)
    post_blocks = val.blocking(post_findings)

    if post_blocks:
        log.error("\n%d POST-RUN BLOCKING GATES:", len(post_blocks))
        for f in post_blocks:
            log.error("  [%s] %s", f.code, f.message)
        return 1

    # --- Success ---
    log.info("\n✓ All gates passed. Engine output ready.")
    log.info("\nSummary table written to: data/_runout_summary.csv")
    cols = ["cm", "part", "description", "state", "first_shortage_date",
            "shortage_qty", "opening", "undated", "products"]
    cols = [c for c in cols if c in s.columns]
    s[cols].sort_values("first_shortage_date", na_position="last").to_csv(
        "data/_runout_summary.csv", index=False)

    log.info("\nNext steps:")
    log.info("  1. Review data/_runout_summary.csv")
    log.info("  2. Review data/_unmatched.csv")
    log.info("  3. Run: streamlit run app_minimal.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
