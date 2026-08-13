# Lunar Material Planning Tool — pilot

Component-level demand & supply planning for Lunar's material planning group.
Runout report + exec shortage summary. Local Streamlit pilot.

**Read `CLAUDE.md` first** — it is the domain spec and Claude Code loads it
automatically each session.

## Setup

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt

## Run

    python scripts/run_report.py      # engine only, prints to stdout
    streamlit run app.py              # UI

## Before anything works

Populate `data/` — see `data/README.md` for the export checklist.
# Updated Thu Aug 13 15:28:42 IST 2026
