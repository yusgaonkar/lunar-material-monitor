#!/usr/bin/env python3
"""Download Google Sheets as CSVs. Requires GSHEETS_KEY env var (service account JSON)."""
import json
import os
import sys
from pathlib import Path

import gspread
import pandas as pd

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
    # Cost sheets disabled: file is Excel, not Google Sheet
    # "19gN1nME70YOSsEwXgJ52iTb3lGuJxScb": [
    #     ("2026 EE costs", "2026 Product Cost Database.xlsx - 2026 EE costs.csv"),
    #     ("2026 ME costs", "2026 Product Cost Database.xlsx - 2026 ME costs.csv"),
    # ],
}

DATA_CLOUD = Path("data/cloud")
DATA_CLOUD.mkdir(parents=True, exist_ok=True)

try:
    # Authenticate via service account (from env var)
    key_json = json.loads(os.environ.get("GSHEETS_KEY", "{}"))
    if not key_json:
        print("ERROR: GSHEETS_KEY env var not set or empty")
        sys.exit(1)

    gc = gspread.service_account_from_dict(key_json)

    for sheet_id, tabs in SHEETS.items():
        sheet = gc.open_by_key(sheet_id)
        for tab_name, output_file in tabs:
            ws = sheet.worksheet(tab_name)
            data = ws.get_all_values()
            df = pd.DataFrame(data[1:], columns=data[0])
            df.to_csv(DATA_CLOUD / output_file, index=False)
            print(f"✓ {output_file} ({len(df)} rows)")

    print("\nAll sheets synced to data/cloud/")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
