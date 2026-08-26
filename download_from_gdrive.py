#!/usr/bin/env python3
"""Download latest data files from Google Drive.

Usage: python download_from_gdrive.py

This script fetches the 7 data files from the Lunar Planner Google Drive folder
and saves them to ./data/ with the current date stamp.
"""

import requests
import sys
from pathlib import Path

# Google Drive file IDs (discovered via MCP)
FILES = {
    "bom_stitched_2026-08-26.csv": "1vQKTDPsGo5xQwTzhSDyAnDPUP1VtV5ut",
    "stitch_list_2026-08-26.csv": "1grBWZlQbwP6itojWHPTQA1RGGZU1qsYe",
    "inventory_onhand_2026-08-26.csv": "15IMmB9tewvHLaxW_vKB5AWrCLYBxk2a3",
    "inventory_onorder_2026-08-26.csv": "1mFBMeknL7Rhgg1SRvBwO1mpRECSq0G8w",
    "build_plan_2026-08-26.csv": "1bD_HxslpcejyOYG2ZQgzHe_YTiaNjoVU",
    "asn_qualitel_2026-08-26.csv": "12HF1qup1tPVm-ntCD08bh2GqDIuePHO9",
    "asn_sienna_2026-08-26.csv": "12H1cy0hjFVddZhl-H6u6GLWfYDJ-iDml",
}

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

def download_file(file_id: str, file_name: str) -> bool:
    """Download a file from Google Drive by ID and save it locally."""
    url = f"https://drive.google.com/uc?export=download&id={file_id}"

    try:
        print(f"  Downloading {file_name}...", end=" ", flush=True)
        response = requests.get(url, timeout=300)
        response.raise_for_status()

        output_path = DATA_DIR / file_name
        output_path.write_bytes(response.content)

        size_mb = len(response.content) / (1024 * 1024)
        print(f"✓ ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

def main():
    print(f"Downloading files to {DATA_DIR}...")
    print()

    success = 0
    for file_name, file_id in FILES.items():
        if download_file(file_id, file_name):
            success += 1

    print()
    print(f"Downloaded {success}/{len(FILES)} files")

    if success == len(FILES):
        print("\n✓ All files downloaded successfully!")
        print("The Streamlit app will use these files on next run.")
        return 0
    else:
        print(f"\n✗ {len(FILES) - success} files failed. Check errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
