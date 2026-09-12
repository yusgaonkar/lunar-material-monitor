#!/usr/bin/env python3
"""Download Google Sheets tabs to data/cloud/ as CSVs.

Requires GSHEETS_KEY env var (service account JSON).

Note on filters: the Sheets API `values.get` (which gspread's get_all_values()
wraps) returns the underlying cell values for the range. Basic filters and
filter views are a *display* feature and do not hide rows from the API, so a
filter left on in the UI does not truncate this export. Row-count floors below
are the guard against real truncation.

Writes atomically (temp file + replace) so a partial download can never leave a
half-written CSV that the app would happily load.
"""
import json
import os
import random
import sys
import time
from pathlib import Path

import gspread
import pandas as pd


def with_retry(fn, what, attempts=4, base_delay=2.0):
    """Run fn(), retrying transient network/API failures with backoff.

    Google intermittently drops connections mid-handshake (RemoteDisconnected)
    and returns 429/5xx under load. Without retries a single blip leaves a
    partial sync, which is the failure mode this whole script exists to avoid.
    """
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            msg = str(e)
            transient = any(
                s in msg
                for s in (
                    "RemoteDisconnected", "Connection aborted", "Connection reset",
                    "timed out", "Read timed out", "[429]", "[500]", "[502]",
                    "[503]", "[504]", "ServiceUnavailable", "Broken pipe",
                )
            )
            if not transient or i == attempts - 1:
                raise
            delay = base_delay * (2 ** i) + random.uniform(0, 1)
            print(f"   retry {i + 1}/{attempts - 1} for {what} in {delay:.1f}s ({type(e).__name__})")
            time.sleep(delay)
    raise last

SHEETS = {
    "1pG27sAAmhe-xDRuC2XTZkrdgW2ytnFCowV8Cn8UqVU0": [
        ("Stitched Indented BOMs", "bom_stitched.csv"),
        ("Stitch List Input", "stitch_list.csv"),
    ],
    "1K9ultvVCSNOE99njweK6eYZm4O-xUslpJlm0JCmHVpY": [
        ("On Hand Inventory Master", "onhand.csv"),
        ("On Order Inventory Master", "onorder.csv"),
    ],
    "13k_pyhns2mBSxZRzISTGRknbY8cJSIxFrgcmCcBlZdI": [
        ("ASN Data", "asn_latest.csv"),
    ],
    "1is9Rv0-IK4timRd_JTnBrYyLZzGcthae08WIo3aNi2g": [
        ("Build & Ship Plan", "build_plan.csv"),
    ],
    # Cost sheets disabled: file is Excel, not Google Sheet
    # "19gN1nME70YOSsEwXgJ52iTb3lGuJxScb": [
    #     ("2026 EE costs", "2026 Product Cost Database.xlsx - 2026 EE costs.csv"),
    #     ("2026 ME costs", "2026 Product Cost Database.xlsx - 2026 ME costs.csv"),
    # ],
}

# Minimum plausible row count per file. A truncated or filtered export fails
# loudly here instead of silently looking like a stockout (CLAUDE.md 5.5).
MIN_ROWS = {
    "bom_stitched.csv": 3000,
    "stitch_list.csv": 15,
    "onhand.csv": 3000,
    "onorder.csv": 1500,
    "asn_latest.csv": 10,
    "build_plan.csv": 5,
}

DATA_CLOUD = Path("data/cloud")
DATA_CLOUD.mkdir(parents=True, exist_ok=True)


def main() -> int:
    key_json = json.loads(os.environ.get("GSHEETS_KEY", "{}"))
    if not key_json:
        print("ERROR: GSHEETS_KEY env var not set or empty")
        return 1

    gc = gspread.service_account_from_dict(key_json)

    failures = []
    for sheet_id, tabs in SHEETS.items():
        try:
            sheet = with_retry(lambda: gc.open_by_key(sheet_id), f"open {sheet_id[:12]}")
        except Exception as e:
            for _, output_file in tabs:
                failures.append(f"{output_file}: cannot open sheet {sheet_id}: {e}")
                print(f"x {output_file}: cannot open sheet — {e}")
            continue

        for tab_name, output_file in tabs:
            try:
                ws = with_retry(lambda: sheet.worksheet(tab_name), f"tab {tab_name}")
                # get_all_values() pads ragged rows and returns display-formatted
                # strings, which keeps "Updated at" as MM-DD-YYYY rather than a
                # date serial. Do not switch to UNFORMATTED_VALUE.
                data = with_retry(lambda: ws.get_all_values(), output_file)

                if len(data) < 2:
                    failures.append(f"{output_file}: no data rows")
                    print(f"x {output_file}: no data rows")
                    continue

                df = pd.DataFrame(data[1:], columns=data[0])

                floor = MIN_ROWS.get(output_file, 0)
                if len(df) < floor:
                    failures.append(
                        f"{output_file}: {len(df)} rows is below floor {floor} "
                        f"— refusing to overwrite (possible truncation)"
                    )
                    print(f"x {output_file}: {len(df)} rows < floor {floor}, NOT written")
                    continue

                # Atomic write so a crash mid-write cannot leave a partial CSV.
                dest = DATA_CLOUD / output_file
                tmp = dest.with_suffix(dest.suffix + ".tmp")
                df.to_csv(tmp, index=False)
                os.replace(tmp, dest)

                snap = ""
                if "Updated at" in df.columns:
                    vals = [v for v in df["Updated at"].unique() if str(v).strip()]
                    snap = f"  snapshot={vals[0]}" if len(vals) == 1 else f"  snapshot=MIXED{vals[:3]}"
                print(f"OK {output_file} ({len(df)} rows){snap}")

            except Exception as e:
                failures.append(f"{output_file}: {e}")
                print(f"x {output_file}: {e}")

    if failures:
        print("\nSYNC FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nAll sheets synced to data/cloud/")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
