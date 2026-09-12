"""Lunar Material Monitor — Component-level demand & supply planning.

Run: streamlit run app_minimal.py

To add password protection when deploying:
1. In Streamlit Cloud settings, add secret: app_password = "your_password"
2. Users will be prompted to enter password on first load
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path
import json
import hashlib

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from src import io as lio, engine as eng, inventory_depletion, normalize as nz
from src.engine import Config
from src import supabase_io, asn_processor

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# Get OS username
OS_USER = os.getenv("USER") or os.getenv("USERNAME") or "Unknown"

# === PERFORMANCE TIMING ===
TIMING_LOG_FILE = "data/load_times.jsonl"
_page_start_time = time.perf_counter()
_timings = {}

def _record_timing(phase_name):
    """Record elapsed time for a phase."""
    _timings[phase_name] = time.perf_counter() - _page_start_time
    log.info(f"⏱ {phase_name}: {_timings[phase_name]:.2f}s")

def _save_timings():
    """Append timing summary to log file."""
    try:
        os.makedirs("data", exist_ok=True)
        total_secs = _timings.get("Page render complete", 0)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "timings": _timings,
            "total_load_ms": int(total_secs * 1000)
        }
        with open(TIMING_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.warning(f"Could not save timings: {e}")

# Initialize Supabase
@st.cache_resource
def init_supabase_client():
    """Initialize Supabase client once per session."""
    try:
        url = st.secrets.get("supabase_url")
        key = st.secrets.get("supabase_key")
        if not url or not key:
            log.error(f"Supabase secrets missing: url={bool(url)}, key={bool(key)}")
            return None
        client = supabase_io.init_supabase(url, key)
        log.info("✓ Supabase initialized successfully")
        return client
    except Exception as e:
        log.error(f"Supabase initialization failed: {e}", exc_info=True)
    return None

SUPABASE_CLIENT = init_supabase_client()

# Persistence files
EXCLUSIONS_FILE = "data/exclusions.csv"
NOTES_FILE = "data/notes.jsonl"
WATCHLIST_FILE = "data/watchlist.csv"

st.set_page_config(page_title="Lunar Material Monitor", layout="wide")

# ============================================================================
# PASSWORD PROTECTION (optional, only if app_password is in secrets)
# ============================================================================
def check_password():
    """Returns True if the user has the correct password (or no password is set)."""

    # Try to get password from secrets; if it doesn't exist, allow access
    try:
        app_password = st.secrets.get("app_password", None)
    except (FileNotFoundError, KeyError, AttributeError):
        app_password = None

    if app_password is None:
        return True  # No password configured, allow access

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if st.session_state.get("password") == app_password:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # clear password from session
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

    # First run, show password input
    st.text_input(
        "Enter password to access Lunar Material Monitor:",
        type="password",
        on_change=password_entered,
        key="password",
    )

    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("❌ Incorrect password")

    return False

if not check_password():
    st.stop()  # Do not continue if password is not correct

# ============================================================================
# PERSISTENCE HELPERS
# ============================================================================
def _load_exclusions_from_supabase():
    """Load excluded parts from Supabase or local CSV fallback."""
    if SUPABASE_CLIENT:
        try:
            result = supabase_io.get_all_excluded_parts()
            log.info(f"Loaded {len(result)} exclusions from Supabase")
            return result
        except Exception as e:
            log.warning(f"Error loading exclusions from Supabase: {e}, trying CSV fallback")

    # Fallback to local CSV
    try:
        if os.path.exists(EXCLUSIONS_FILE) and os.path.getsize(EXCLUSIONS_FILE) > 0:
            df = pd.read_csv(EXCLUSIONS_FILE)
            result = set(df["part"].unique()) if "part" in df.columns else set()
            log.info(f"Loaded {len(result)} exclusions from local CSV")
            return result
    except Exception as e:
        log.warning(f"Error loading exclusions from CSV: {e}")
    return set()

def _load_all_notes_from_supabase():
    """Load ALL notes from Supabase. Returns dict: {part -> [notes]}."""
    notes_by_part = {}
    try:
        # Ensure Supabase is initialized
        supabase_io.init_supabase(st.secrets["supabase_url"], st.secrets["supabase_key"])
        supabase = supabase_io.get_supabase()

        if supabase:
            response = supabase.table("notes").select("*").execute()
            for note in response.data:
                part = note.get("component_lpn")
                if part:
                    if part not in notes_by_part:
                        notes_by_part[part] = []
                    notes_by_part[part].append(note)
            log.info(f"Loaded notes for {len(notes_by_part)} parts from Supabase")
        else:
            log.warning("Supabase client is None after init")
    except Exception as e:
        log.warning(f"Error loading all notes: {e}")
    return notes_by_part

# Initialize session state caches on first load
if "notes_cache" not in st.session_state:
    try:
        st.session_state.notes_cache = _load_all_notes_from_supabase()
    except Exception as e:
        log.error(f"Error initializing notes cache: {e}")
        st.session_state.notes_cache = {}

if "exclusions_cache" not in st.session_state:
    try:
        st.session_state.exclusions_cache = _load_exclusions_from_supabase()
    except Exception as e:
        log.error(f"Error initializing exclusions cache: {e}")
        st.session_state.exclusions_cache = set()

def exclude_part(part, reason):
    """Add a part to exclusions with optimistic update (instant UI feedback)."""
    try:
        # Optimistic update - immediately add to session state (instant feedback)
        st.session_state.exclusions_cache.add(part)
        st.success(f"✓ Excluded {part}")

        # Save to Supabase in background (fire and forget)
        supabase_io.exclude_part(part, reason, OS_USER)
        st.rerun()
    except Exception as e:
        # If it fails, remove from optimistic cache
        st.session_state.exclusions_cache.discard(part)
        log.error(f"Error excluding part: {e}")
        st.error(f"Error: {e}")

def load_notes(part):
    """Get notes for a part from session state (instant access, no cache wait)."""
    notes = st.session_state.notes_cache.get(part, [])

    # Convert to display format
    formatted_notes = []
    for note in notes:
        formatted_notes.append({
            "part": part,
            "note": note.get("note", ""),
            "user": note.get("note_user", "Unknown"),
            "timestamp": note.get("timestamp", "")
        })
    return formatted_notes

def add_note(part, note_text):
    """Add a note with optimistic update (instant UI feedback)."""
    try:
        # Optimistic update - immediately add to session state (instant feedback)
        new_note = {
            "component_lpn": part,
            "note": note_text,
            "note_user": OS_USER,
            "timestamp": datetime.now().isoformat()
        }

        if part not in st.session_state.notes_cache:
            st.session_state.notes_cache[part] = []
        st.session_state.notes_cache[part].append(new_note)

        st.success("✓ Note added")

        # Save to Supabase in background (fire and forget)
        supabase_io.save_note(part, note_text, OS_USER)
        st.rerun()
    except Exception as e:
        log.error(f"Error adding note: {e}")
        st.error(f"Error: {e}")

def un_exclude_part(part):
    """Remove a part from exclusions with optimistic update (instant UI feedback)."""
    try:
        # Optimistic update - immediately remove from session state (instant feedback)
        st.session_state.exclusions_cache.discard(part)
        st.success(f"✓ {part} re-enabled")

        # Save to Supabase in background (fire and forget)
        supabase_io.un_exclude_part(part)
        st.rerun()
    except Exception as e:
        # If it fails, add back to optimistic cache
        st.session_state.exclusions_cache.add(part)
        log.error(f"Error un-excluding part: {e}")
        st.error(f"Error: {e}")

def load_watchlist():
    """Load watched parts from CSV."""
    try:
        if os.path.exists(WATCHLIST_FILE) and os.path.getsize(WATCHLIST_FILE) > 0:
            df = pd.read_csv(WATCHLIST_FILE)
            if len(df) > 0 and "part" in df.columns:
                return set(df["part"].unique())
    except Exception as e:
        log.warning(f"Error loading watchlist: {e}")
    return set()

def watch_part(part, comment):
    """Add a part to watchlist."""
    os.makedirs(os.path.dirname(WATCHLIST_FILE) or ".", exist_ok=True)
    watch_data = {
        "part": part,
        "user": OS_USER,
        "timestamp": datetime.now().isoformat(),
        "comment": comment
    }
    try:
        if os.path.exists(WATCHLIST_FILE) and os.path.getsize(WATCHLIST_FILE) > 0:
            df = pd.read_csv(WATCHLIST_FILE)
            df = pd.concat([df, pd.DataFrame([watch_data])], ignore_index=True)
        else:
            df = pd.DataFrame([watch_data])
        df.to_csv(WATCHLIST_FILE, index=False)
        st.success(f"✓ Added {part} to watchlist")
    except Exception as e:
        st.error(f"Error adding to watchlist: {e}")

def unwatch_part(part):
    """Remove a part from watchlist."""
    try:
        if os.path.exists(WATCHLIST_FILE):
            df = pd.read_csv(WATCHLIST_FILE)
            df = df[df["part"] != part]
            df.to_csv(WATCHLIST_FILE, index=False)
            st.success(f"✓ Removed {part} from watchlist")
    except Exception as e:
        st.error(f"Error removing from watchlist: {e}")

# Load watched parts only (excluded parts come from session state)
watched_parts = load_watchlist()

# ============================================================================
# DIALOG FUNCTIONS
# ============================================================================
@st.dialog("Exclude Part")
def dialog_exclude(part):
    """Dialog to exclude a part."""
    st.write(f"**Part:** {part}")
    reason = st.text_area("Reason:", key=f"exclude_reason_{part}", height=100)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Exclude", key=f"exclude_confirm_{part}", use_container_width=True):
            if reason:
                exclude_part(part, reason)
                st.rerun()
            else:
                st.error("Please provide a reason")
    with col2:
        if st.button("Cancel", key=f"exclude_cancel_{part}", use_container_width=True):
            st.rerun()

@st.dialog("Add Note")
def dialog_add_note(part):
    """Dialog to add a note."""
    st.write(f"**Part:** {part}")
    note_text = st.text_area("Note:", key=f"note_text_{part}", height=120)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Add Note", key=f"note_confirm_{part}", use_container_width=True):
            if note_text:
                add_note(part, note_text)
                st.rerun()
            else:
                st.error("Please enter a note")
    with col2:
        if st.button("Cancel", key=f"note_cancel_{part}", use_container_width=True):
            st.rerun()

@st.dialog("Add to Watchlist")
def dialog_watch(part):
    """Dialog to add part to watchlist."""
    st.write(f"**Part:** {part}")
    comment = st.text_area("Why watch this part?:", key=f"watch_comment_{part}", height=100)
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Add to Watchlist", key=f"watch_confirm_{part}", use_container_width=True):
            if comment:
                watch_part(part, comment)
                st.rerun()
            else:
                st.error("Please provide a reason")
    with col2:
        if st.button("Cancel", key=f"watch_cancel_{part}", use_container_width=True):
            st.rerun()

@st.dialog("Notes History")
def dialog_view_notes(part):
    """Dialog to view full notes history for a part."""
    st.write(f"**Part:** {part}")
    notes = load_notes(part)

    if notes:
        for note in notes:
            with st.container(border=True):
                st.caption(f"**{note['user']}** — {note['timestamp'][:10]} {note['timestamp'][11:16]}")
                st.write(note["note"])
    else:
        st.info(f"No notes yet for {part}")

    if st.button("Close", key=f"close_notes_{part}", use_container_width=True):
        st.rerun()


# --- Helper functions ---
def get_buy_parts_under_product(product_lpn: str, bom: pd.DataFrame) -> set:
    """Find all buy parts (Sourcing_Flat_Qty > 0) under a product in the BOM."""
    buy_parts = set()
    visited = set()

    def traverse(parent_lpn):
        if parent_lpn in visited:
            return
        visited.add(parent_lpn)

        children = bom[bom["Parent Product LPN"] == parent_lpn]
        if len(children) == 0:
            return

        # Vectorized instead of iterrows
        sourcing = pd.to_numeric(children["Sourcing Flat Qty"], errors="coerce").fillna(0)
        for child_lpn, qty in zip(children["item_number"].values, sourcing.values):
            if qty > 0:
                buy_parts.add(child_lpn)
            traverse(child_lpn)

    traverse(product_lpn)
    return buy_parts


# --- Cost database loading ---
@st.cache_data(ttl=3600)
def load_cost_frames():
    """Load EE and ME cost databases. Returns dict: {'2026 EE costs.csv': df, '2026 ME costs.csv': df}"""
    cost_ee = lio.load_cost_ee()
    cost_me = lio.load_cost_me()
    return {
        "2026 EE costs.csv": cost_ee if len(cost_ee) > 0 else None,
        "2026 ME costs.csv": cost_me if len(cost_me) > 0 else None
    }


# --- ASN adjustment ---
@st.cache_data(show_spinner="Loading ASN data...")
def load_asn_adjustments(cache_key):
    """Load ASN adjustments for build plan from Google Sheet.

    Reads from data/cloud/asn_latest.csv (synced from Google Sheet).
    Uses process_asn_pivot() to extract current month's shipped quantities.
    """
    try:
        from src.asn_processor import process_asn_pivot
    except ImportError:
        # Fallback if module not available
        return pd.DataFrame(columns=['product_lpn', 'asn_qty'])

    import os

    asn_path = 'data/cloud/asn_latest.csv'

    # Surface staleness: an ASN file older than the inventory export means the
    # last sync did not refresh it, and the build plan will silently deduct
    # yesterday's shipped quantities.
    try:
        if os.path.exists(asn_path) and os.path.exists('data/cloud/onhand.csv'):
            asn_mtime = os.path.getmtime(asn_path)
            oh_mtime = os.path.getmtime('data/cloud/onhand.csv')
            if oh_mtime - asn_mtime > 3600:  # more than an hour behind
                msg = (
                    f"asn_latest.csv is {(oh_mtime - asn_mtime) / 3600:.1f}h older than "
                    f"onhand.csv — the last sync did not refresh ASN. Shipped-to-date "
                    f"figures are stale."
                )
                log.warning(msg)
                st.warning(f"Stale ASN data: {msg}")
    except Exception:
        pass

    try:
        # Try to load from synced Google Sheet
        asn_agg = process_asn_pivot(asn_path)
        if len(asn_agg) > 0:
            return asn_agg
        log.warning(f"{asn_path} parsed to 0 rows — no ASN deductions applied.")
        st.warning("ASN file parsed to 0 rows — no shipped-to-date deductions applied.")
    except FileNotFoundError:
        log.warning(f"{asn_path} not found — no ASN deductions applied.")
        st.warning("ASN file not found — no shipped-to-date deductions applied. Run scripts/sync_gsheets.py.")
    except Exception as e:
        # Never fail silently: an unreadable ASN file is indistinguishable from
        # "nothing shipped" in the output, which understates completed builds.
        log.warning(f"ASN load failed ({asn_path}): {e}")
        st.warning(f"ASN load failed, no deductions applied: {e}")

    # Fallback: empty ASN (no deductions)
    return pd.DataFrame(columns=['product_lpn', 'asn_qty'])


def apply_asn_to_build_plan(build_plan_df: pd.DataFrame, asn_df: pd.DataFrame, bom: pd.DataFrame = None, snapshot_date: pd.Timestamp = None) -> pd.DataFrame:
    """Apply ASN deductions to first month of build plan.

    - Creates "shipped to date" column for first month
    - Deducts ASN from first month qty (ASN period is before snapshot_date)
    - If ASN > first month, overflow carries to second month
    - Handles component→parent relationships (e.g., 90-06831D ← 10-00522D)
    - Adds asn_end_date column so remaining_builds() knows where to start disaggregation
    """
    result = build_plan_df.copy()

    # Direct merge for products in ASN
    result = result.merge(asn_df, left_on='product_lpn', right_on='product_lpn', how='left')
    result['asn_qty'] = result['asn_qty'].fillna(0).astype(int)

    # Find first month dynamically
    first_month = result['period_start'].min()
    second_month = result[result['period_start'] > first_month]['period_start'].min()

    # Add asn_end_date: for first month use snapshot_date, for others use period_start
    # This tells remaining_builds() where to start disaggregation
    result['asn_end_date'] = None
    result.loc[result['period_start'] == first_month, 'asn_end_date'] = snapshot_date

    # Handle component-to-parent for 90-06831D ← 10-00522D
    if not asn_df.empty:
        for idx, row in result.iterrows():
            if row['product_lpn'] == '90-06831D' and row['period_start'] == first_month:
                # Look up 10-00522D ASN (qty 20 per box)
                comp_asn = asn_df[asn_df['product_lpn'] == '10-00522D']['asn_qty'].astype(str).str.replace(',', '').pipe(lambda x: pd.to_numeric(x, errors='coerce')).sum()
                if comp_asn > 0:
                    result.loc[idx, 'asn_qty'] = int(comp_asn / 20)  # Convert units to boxes

    # Apply ASN deductions with overflow to next month
    result['qty_adjusted'] = result['qty']
    result['qty_overflow'] = 0  # Track overflow for next month

    for product in result['product_lpn'].unique():
        prod_data = result[result['product_lpn'] == product].sort_values('period_start')

        # First month: deduct ASN
        first_idx = prod_data[prod_data['period_start'] == first_month].index
        if len(first_idx) > 0:
            idx = first_idx[0]
            asn = result.loc[idx, 'asn_qty']
            first_qty = result.loc[idx, 'qty']

            if asn >= first_qty:
                # ASN exceeds first month: zero out first, overflow to second
                result.loc[idx, 'qty_adjusted'] = 0
                result.loc[idx, 'qty_overflow'] = asn - first_qty
            else:
                # ASN fits in first month
                result.loc[idx, 'qty_adjusted'] = first_qty - asn

        # Second month: apply overflow
        second_idx = prod_data[prod_data['period_start'] == second_month].index
        if len(second_idx) > 0:
            idx = second_idx[0]
            overflow = result.loc[first_idx[0], 'qty_overflow'] if len(first_idx) > 0 else 0
            result.loc[idx, 'qty_adjusted'] = max(0, result.loc[idx, 'qty'] - overflow)

    return result


# --- Load and run (split for performance) ---
def compute_data_cache_key():
    """Fingerprint of every synced CSV in data/cloud.

    Globs the directory rather than listing filenames so a renamed or newly
    added source file cannot silently drop out of the key (a hardcoded list
    that named a non-existent file is exactly how stale ASN data survived a
    resync). Uses (name, mtime, size) per file — mtime alone can collide when
    a rewrite lands inside the same filesystem timestamp granularity.

    Files beginning with "_" are excluded: the app itself writes _unmatched.csv
    and _lunar_debug.csv into this directory, and including them would make the
    key change as a side effect of rendering, invalidating the cache every run.
    """
    import os
    from pathlib import Path

    try:
        cloud = Path("data/cloud")
        if not cloud.is_dir():
            return "nodir"

        parts = []
        for p in sorted(cloud.glob("*.csv")):
            if p.name.startswith("_"):
                continue
            st_ = p.stat()
            parts.append(f"{p.name}:{st_.st_mtime_ns}:{st_.st_size}")

        # overrides.csv rewrites the loaded frames, so it belongs in the key just
        # as much as the source exports — otherwise editing an override changes
        # nothing on screen and looks like the override was ignored.
        ovr = Path("data/overrides.csv")
        if ovr.exists():
            st_ = ovr.stat()
            parts.append(f"overrides:{st_.st_mtime_ns}:{st_.st_size}")

        if not parts:
            return "empty"
        return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]
    except Exception as e:
        # Never silently return a constant — that would re-freeze the cache.
        log.warning(f"compute_data_cache_key failed: {e}")
        return f"err-{pd.Timestamp.now().value}"


# Which frame/column each override touches. load_all() keys carry the ".csv"
# suffix — writing frames["onorder"] raises KeyError, and an earlier version of
# this function did exactly that inside a bare except, so every override logged a
# warning and silently did nothing.
_CATEGORY_TARGETS = [
    ("onhand.csv", "lpn", "item_category"),
    ("onorder.csv", "lunar_lpn", "item_category_"),   # trailing underscore: CLAUDE.md 4
    ("bom_stitched.csv", "item_number", "category_name"),
]


def apply_data_overrides(frames: dict) -> dict:
    """Apply manual overrides from data/overrides.csv to the loaded frames.

    Supported override_type values:
      lunar_onorder_qty  set quantity_open on Lunar Netsuite lines for the part
                         (use 0 to retire a PO that will never land)
      item_category      reclassify the part across on-hand, on-order and BOM

    Overrides are applied before validation and before the engine runs, so every
    downstream number reflects them. Anything that fails to apply is reported in
    the UI rather than swallowed — a silent override is worse than none, because
    the number looks adjusted when it is not.
    """
    overrides_path = Path("data/overrides.csv")
    if not overrides_path.exists():
        return frames

    try:
        ovr = pd.read_csv(overrides_path)
    except Exception as e:
        log.warning(f"could not read overrides.csv: {e}")
        st.warning(f"Overrides file unreadable, none applied: {e}")
        return frames

    required = {"part_lpn", "override_type", "override_value"}
    missing = required - set(ovr.columns)
    if missing:
        st.warning(f"overrides.csv missing column(s) {sorted(missing)} — no overrides applied.")
        return frames

    # Build description lookups FIRST (before override loop)
    # Try onhand first, then fall back to BOM
    oh = frames.get("onhand.csv")
    bom = frames.get("bom_stitched.csv")
    desc_map = {}
    part_desc_map = {}

    if oh is not None and "lpn" in oh.columns and "description" in oh.columns:
        for _, r in oh[["lpn", "description"]].drop_duplicates().iterrows():
            lpn = str(r["lpn"]).strip()
            description = str(r["description"]).strip()
            desc_map[lpn] = description
            part_desc_map[lpn] = description

    # Add BOM descriptions as fallback for parts not in onhand
    if bom is not None and "item_number" in bom.columns and "item_name" in bom.columns:
        for _, r in bom[["item_number", "item_name"]].drop_duplicates().iterrows():
            part = str(r["item_number"]).strip()
            description = str(r["item_name"]).strip()
            if part not in part_desc_map:
                part_desc_map[part] = description

    changes, detailed_changes, failures = [], [], []
    bulk_override_units, bulk_override_cost = 0, 0

    # Counts come from the override FILE, not from successful application. A part whose
    # on-order lines don't match (e.g. it has no Lunar on-order rows at all) is still a
    # part we corrected in the file, and the planner's headline number is the file's.
    _is_bulk = ovr["reason"].astype(str).str.contains("PO line duplication", na=False)
    bulk_override_count = int(
        (_is_bulk & (ovr["override_type"] == "lunar_onorder_qty")).sum()
    )

    # BOM corrections: distinct PARTS, not rows. Each part gets up to two rows (a makebuy
    # row and a sourcing_flat_qty row), so summing the two counts double-counts.
    _bom_rows = ovr[ovr["override_type"].isin(["makebuy", "sourcing_flat_qty"])]
    bom_part_count = int(_bom_rows["part_lpn"].nunique())

    # The headline parts are the ones that flipped Buy -> Make; that flip is what
    # re-sourced everything beneath them. Make -> Buy rows are the consequence, not the cause.
    _to_make = ovr[
        (ovr["override_type"] == "makebuy")
        & (ovr["override_value"].astype(str).str.strip() == "Make")
    ]
    bom_trigger_parts = list(dict.fromkeys(_to_make["part_lpn"].astype(str).str.strip()))

    for _, row in ovr.iterrows():
        part = str(row["part_lpn"]).strip()
        otype = str(row["override_type"]).strip()
        ovalue = row["override_value"]
        product = str(row.get("product_lpn", "") or "").strip()
        reason = str(row.get("reason", "") or "")
        is_bulk = "PO line duplication" in reason

        try:
            if otype == "lunar_onorder_qty":
                oo = frames["onorder.csv"]
                mask = (oo["source_report"] == "Lunar Netsuite") & (oo["lunar_lpn"] == part)
                n = int(mask.sum())
                if n == 0:
                    failures.append(f"{part}: no Lunar on-order lines matched")
                    continue
                before = pd.to_numeric(oo.loc[mask, "quantity_open"], errors="coerce").sum()
                oo.loc[mask, "quantity_open"] = float(ovalue)
                delta = before - float(ovalue)

                desc = part_desc_map.get(part, "")
                desc_str = f" {desc}" if desc else ""

                if is_bulk:
                    bulk_override_units += delta
                    # Estimate cost from unit price
                    prices = pd.to_numeric(oo.loc[mask, "unit_price"], errors="coerce")
                    if len(prices) > 0 and prices.mean() > 0:
                        bulk_override_cost += delta * prices.mean()
                else:
                    change_str = (
                        f"`{part}`{desc_str}: on-order {before:,.0f} → {float(ovalue):,.0f} "
                        f"across {n} line(s){' ' + reason if reason else ''}"
                    )
                    detailed_changes.append(change_str)
                    changes.append(change_str)

            elif otype == "item_category":
                hits = []
                for fname, pk, col in _CATEGORY_TARGETS:
                    df = frames.get(fname)
                    if df is None or pk not in df.columns or col not in df.columns:
                        continue
                    mask = df[pk] == part
                    if mask.any():
                        was = sorted(set(df.loc[mask, col].dropna().astype(str)))
                        df.loc[mask, col] = ovalue
                        hits.append(f"{fname.replace('.csv', '')} ({int(mask.sum())} rows, was {'/'.join(was) or 'blank'})")
                if hits:
                    desc = part_desc_map.get(part, "")
                    desc_str = f" {desc}" if desc else ""
                    change_str = (
                        f"`{part}`{desc_str}: Override to {ovalue} in " + "; ".join(hits)
                    )
                    detailed_changes.append(change_str)
                    changes.append(change_str)
                else:
                    failures.append(f"{part}: no rows matched for item_category")

            elif otype == "makebuy":
                bom = frames.get("bom_stitched.csv")
                if bom is None or "item_number" not in bom.columns or "makebuy" not in bom.columns:
                    failures.append(f"{part} (makebuy): BOM not available")
                    continue
                # item_number is NOT unique across the BOM — 11+ items sit at multiple
                # positions under different products (CLAUDE.md 5.4). A part-only mask
                # would silently rewrite makebuy for every product that shares this
                # part number, not just the one product.csv this override targets.
                if not product:
                    failures.append(f"{part} (makebuy): override missing product_lpn — refusing to apply part-number-wide to avoid corrupting other products' BOM positions")
                    continue
                mask = (bom["item_number"] == part) & (bom["Parent Product LPN"] == product)
                n = int(mask.sum())
                if n == 0:
                    failures.append(f"{part} ({product}): no BOM rows matched")
                    continue
                was = sorted(set(bom.loc[mask, "makebuy"].dropna().astype(str).unique()))
                bom.loc[mask, "makebuy"] = str(ovalue)
                # Track for BOM summary (not detailed display)
                changes.append(f"{part} (in {product}) makebuy -> {ovalue}")

            elif otype == "sourcing_flat_qty":
                bom = frames.get("bom_stitched.csv")
                if bom is None or "item_number" not in bom.columns or "Sourcing Flat Qty" not in bom.columns:
                    failures.append(f"{part} (sourcing_flat_qty): BOM not available")
                    continue
                # Same item_number-not-unique concern as makebuy above — must scope to
                # the specific product this correction applies to.
                if not product:
                    failures.append(f"{part} (sourcing_flat_qty): override missing product_lpn — refusing to apply part-number-wide to avoid corrupting other products' BOM positions")
                    continue
                mask = (bom["item_number"] == part) & (bom["Parent Product LPN"] == product)
                n = int(mask.sum())
                if n == 0:
                    failures.append(f"{part} ({product}): no BOM rows matched")
                    continue
                before = pd.to_numeric(bom.loc[mask, "Sourcing Flat Qty"], errors="coerce").sum()
                bom.loc[mask, "Sourcing Flat Qty"] = float(ovalue)
                # Track for BOM summary (not detailed display)
                changes.append(f"{part} (in {product}) sourcing_flat_qty {before:,.0f} -> {float(ovalue):,.0f}")

            else:
                failures.append(f"{part}: unknown override_type '{otype}'")

        except Exception as e:
            failures.append(f"{part} ({otype}): {e}")

    # Render as one bulleted list, in this order: individual corrections, then the two
    # bulk summaries. Individual lines are the ones a planner may need to act on.
    if detailed_changes or bulk_override_count > 0 or bom_part_count > 0:
        lines = [f"- {c}" for c in detailed_changes]
        for c in detailed_changes:
            log.info(f"override applied: {c}")

        if bulk_override_count > 0:
            lines.append(
                f"- **{bulk_override_count} parts** Lunar on order updated due to "
                f"on order PO line duplication in LunarDB"
            )
            log.info(
                f"bulk override applied: {bulk_override_count} parts, "
                f"{bulk_override_units:,.0f} units, ~${bulk_override_cost/1e6:.1f}m"
            )

        if bom_part_count > 0:
            trig = []
            for p in bom_trigger_parts[:2]:
                d = part_desc_map.get(p, "")
                trig.append(f"`{p}` ({d})" if d else f"`{p}`")
            trig_str = " and ".join(trig)
            if len(bom_trigger_parts) > 2:
                trig_str += f" (and {len(bom_trigger_parts) - 2} more)"
            lines.append(
                f"- **{bom_part_count} parts** sourcing updated due to {trig_str} "
                f"switch from Buy to Make"
            )
            log.info(
                f"BOM overrides applied: {bom_part_count} parts, "
                f"triggered by {bom_trigger_parts}"
            )

        with st.expander("**✓ Data overrides applied**", expanded=False):
            st.success("\n".join(lines))

    return frames


@st.cache_data(show_spinner="Loading data...")
def load_data(data_cache_key):
    """Cached by data file mtime hash: invalidates when files update."""
    return lio.load_all()


def hash_build_plan(build_plan_df):
    """Compute a hash of the build plan for cache invalidation.

    Returns a short hash that changes when the build plan changes,
    enabling run_engine() to re-run only when needed.
    """
    if len(build_plan_df) == 0:
        return "empty"
    # Hash just the key columns to keep it fast
    try:
        key_cols = ['product_lpn', 'qty']
        hash_val = hashlib.md5(
            pd.util.hash_pandas_object(build_plan_df[key_cols], index=False).values
        ).hexdigest()[:8]
        return hash_val
    except Exception:
        return "error"


@st.cache_data(show_spinner="Running engine...")
def run_engine(frames, cache_key, horizon_weeks=52):
    """Cached by cache_key: run the engine.

    cache_key = f"{build_plan_hash}:h{horizon_weeks}" ensures the cache
    invalidates when either the build plan OR the horizon changes.
    The frames dict should already have 'build_plan.csv' populated.

    horizon_weeks: dynamic weeks to plan for, based on build plan's max date.
    """
    snap = lio.snapshot_date(frames.get("onhand.csv"))
    snap = pd.Timestamp(snap) if snap is not None else pd.Timestamp.today()
    cfg = Config(snapshot=snap, horizon_weeks=horizon_weeks)
    result = eng.run(frames, cfg=cfg)
    return result


# Load data once - io.py automatically detects Cloud vs localhost
_record_timing("Starting data load")
data_cache_key = compute_data_cache_key()
frames = load_data(data_cache_key)
_record_timing("Data load complete")

# Apply manual overrides (e.g., cancel stale POs, reclassify parts)
frames = apply_data_overrides(frames)
_record_timing("Overrides applied")

# VALIDATION: Check data consistency
def validate_snapshot_consistency(frames):
    """Validate that all data files have consistent snapshot dates."""
    dates = {}
    for file_key in ["bom_stitched.csv", "onhand.csv", "onorder.csv"]:
        if file_key in frames and "Updated at" in frames[file_key].columns:
            date_str = frames[file_key]["Updated at"].iloc[0] if len(frames[file_key]) > 0 else None
            if date_str:
                dates[file_key] = date_str

    if len(dates) > 1:
        unique_dates = set(dates.values())
        if len(unique_dates) > 1:
            log.warning(f"⚠️ Data files have DIFFERENT snapshot dates: {dates}")
            return False
        else:
            snapshot_date = unique_dates.pop()
            log.info(f"✓ All data files snapshot: {snapshot_date}")
            return True
    return True

try:
    validate_snapshot_consistency(frames)
except Exception as e:
    log.warning(f"Could not validate snapshot consistency: {e}")

# Load and adjust build plan (apply ASN deductions)
build_plan = lio.load_build_plan()
asn_data = load_asn_adjustments(data_cache_key)

# Extract snapshot date dynamically from data files
# The "Updated at" column shows the snapshot date (format: MM-DD-YYYY)
# Use the first file that has the column to extract date
snapshot_date = pd.Timestamp('2026-09-09')  # Fallback (today)
for file_key in ['bom_stitched.csv', 'onhand.csv', 'onorder.csv']:
    if file_key in frames and 'Updated at' in frames[file_key].columns:
        try:
            date_str = frames[file_key]['Updated at'].iloc[0]
            if pd.notna(date_str):
                snapshot_date = pd.to_datetime(date_str, format='%m-%d-%Y')
                break
        except:
            pass
build_plan = apply_asn_to_build_plan(build_plan, asn_data, snapshot_date=snapshot_date)

# Replace qty with qty_adjusted for engine calculations
# (engine will use ASN-deducted quantities for demand)
build_plan_for_engine = build_plan.copy()
build_plan_for_engine['qty'] = build_plan_for_engine['qty_adjusted'].fillna(build_plan_for_engine['qty'])
build_plan_for_engine = build_plan_for_engine.drop(columns=['qty_adjusted', 'qty_overflow'], errors='ignore')

# Debug: verify 90-07675A Aug qty
debug_row = build_plan_for_engine[(build_plan_for_engine['product_lpn'] == '90-07675A') &
                                   (build_plan_for_engine['period_start'] == pd.Timestamp('2026-08-01'))]
if len(debug_row) > 0:
    log.info(f"[ASN DEBUG] 90-07675A Aug qty for engine: {debug_row['qty'].iloc[0]}")

# Compute hash for cache invalidation
bp_hash = hash_build_plan(build_plan_for_engine)

# Add build plan to frames and run engine
frames_with_plan = frames.copy()  # Shallow copy of dict
frames_with_plan["build_plan.csv"] = build_plan_for_engine

# Stage 4: Dynamic horizon — CACHED to avoid recalculating on every page load
@st.cache_data
def calculate_horizon_weeks(build_plan_hash, max_period_str):
    """Calculate minimum horizon_weeks to cover end of max period's month.

    Cached by build_plan_hash so it only recalculates when the build plan changes.
    """
    max_period = pd.to_datetime(max_period_str)
    end_of_max_month = (max_period + pd.offsets.MonthEnd(0)).normalize()

    # Start with default, then expand if needed
    horizon_weeks = 52
    cfg_test = Config(snapshot=snapshot_date, horizon_weeks=horizon_weeks)
    last_period = cfg_test.periods()[-1]

    # Keep adding weeks until Config's last period reaches end of max month
    while last_period < end_of_max_month:
        horizon_weeks += 4
        cfg_test = Config(snapshot=snapshot_date, horizon_weeks=horizon_weeks)
        last_period = cfg_test.periods()[-1]

    return horizon_weeks, last_period

if len(build_plan_for_engine) > 0:
    max_period = pd.to_datetime(build_plan_for_engine['period_start']).max()
    horizon_weeks, last_period = calculate_horizon_weeks(bp_hash, max_period.isoformat())
    log.info(f"📅 Dynamic horizon: build plan max {max_period.date()}, Config extends to {last_period.date()} ({horizon_weeks} weeks)")
else:
    horizon_weeks = 52
    log.warning("Build plan is empty, using default 52-week horizon")

# Composite cache key: invalidate if either build plan OR horizon changes
cache_key = f"{bp_hash}:h{horizon_weeks}"

# Show status message for cold start only (when cache_key changes = new data to load)
if st.session_state.get("last_cache_key") != cache_key:
    st.info("⏳ **Initial load is slower (3-5 min cold start)**. Subsequent interactions are fast thanks to caching. Please wait...")
    st.session_state.last_cache_key = cache_key

result = run_engine(frames_with_plan, cache_key, horizon_weeks=horizon_weeks)

# Override snapshot with dynamically extracted value from data files
result['snapshot'] = snapshot_date

_record_timing("Engine computation complete")

cfg = result["config"]
s = result["summary"]
pab = result["pab"]
receipts = result["receipts"]
demand_detail = result["demand_detail"]
excess = result.get("excess", pd.DataFrame())
products = result["products"]
bom_stitched = frames["bom_stitched.csv"]
onhand_raw = frames.get("onhand.csv", pd.DataFrame())
onorder_raw = frames.get("onorder.csv", pd.DataFrame())
stitch_list = frames.get("stitch_list.csv", pd.DataFrame())

# --- Add ASN shipments to build plan for display ---
asn_all = frames.get("asn_all.csv", pd.DataFrame())
if len(asn_all) > 0:
    asn_all["shipped_date"] = pd.to_datetime(asn_all["shipped_date"], errors="coerce")
    asn_aug = asn_all[(asn_all["shipped_date"].dt.month == 8) & (asn_all["shipped_date"].dt.day <= 25)]
    asn_by_part = asn_aug.groupby("customer_part_number")["quantity"].sum()
    build_plan_for_engine["asn_qty"] = build_plan_for_engine["product_lpn"].map(asn_by_part).fillna(0)
    build_plan_for_engine["qty_adjusted"] = build_plan_for_engine["qty"] - build_plan_for_engine["asn_qty"]
else:
    build_plan_for_engine["asn_qty"] = 0
    build_plan_for_engine["qty_adjusted"] = build_plan_for_engine["qty"]

# --- Compute obsolescence_state for all parts (for filtering) ---
# The Gen 1 / Gen 2 part sets depend only on stitch_list + bom_stitched, so they are
# built ONCE here. Previously this block lived inside a per-part function called via
# .apply(), which re-scanned the 4,379-row BOM ~18x for every part (~36k scans) and
# again for summary_with_allocation. That was the entire 34s / 79s page-load cost.
@st.cache_data(show_spinner=False)
def _build_generation_part_sets(_stitch_list, _bom_stitched, cache_key: str):
    """Return (gen_1_parts, gen_2_parts) as frozensets. One pass over the BOM."""
    if len(_stitch_list) == 0:
        return frozenset(), frozenset()

    gen_1_products = set(
        _stitch_list[_stitch_list["Generation Alias"].str.contains("Gen 1", na=False)]["Parent Product LPN"].unique()
    )
    gen_2_products = set(
        _stitch_list[_stitch_list["Generation Alias"].str.contains("Gen 2", na=False)]["Parent Product LPN"].unique()
    )

    # Single groupby instead of one full-frame scan per product
    parts_by_product = _bom_stitched.groupby("Parent Product LPN")["item_number"].unique()

    gen_1_parts = set()
    for product in gen_1_products:
        if product in parts_by_product.index:
            gen_1_parts.update(parts_by_product.loc[product])

    gen_2_parts = set()
    for product in gen_2_products:
        if product in parts_by_product.index:
            gen_2_parts.update(parts_by_product.loc[product])

    return frozenset(gen_1_parts), frozenset(gen_2_parts)


_gen_cache_key = f"{len(stitch_list)}:{len(bom_stitched)}"
GEN_1_PARTS, GEN_2_PARTS = _build_generation_part_sets(stitch_list, bom_stitched, _gen_cache_key)


def _obsolescence_series(parts: pd.Series) -> pd.Series:
    """Vectorised obsolescence_state for a Series of part numbers."""
    gen1 = parts.isin(GEN_1_PARTS)
    gen2 = parts.isin(GEN_2_PARTS)
    return pd.Series(
        np.select(
            [gen1 & gen2, gen1 & ~gen2, ~gen1 & gen2],
            ["Active in Both", "Gen 1 Only", "Gen 2 Only"],
            default="Obsolete",
        ),
        index=parts.index,
    )


def compute_obsolescence_state(part):
    """Single-part lookup. Kept for any existing callers; now O(1)."""
    gen1 = part in GEN_1_PARTS
    gen2 = part in GEN_2_PARTS
    if gen1 and gen2:
        return "Active in Both"
    elif gen1:
        return "Gen 1 Only"
    elif gen2:
        return "Gen 2 Only"
    return "Obsolete"


# Add obsolescence_state to summary data for filtering
if len(s) > 0 and "part" in s.columns:
    s["obsolescence_state"] = _obsolescence_series(s["part"])
if "summary_with_allocation" in result and len(result["summary_with_allocation"]) > 0 and "part" in result["summary_with_allocation"].columns:
    result["summary_with_allocation"]["obsolescence_state"] = _obsolescence_series(
        result["summary_with_allocation"]["part"]
    )

_record_timing("Obsolescence state computed")

# --- Header ---
st.title("Lunar Material Monitor")
st.caption(f"Component runout tracking | Snapshot: {result['snapshot'].date()}")

# DATA AUDIT: Show which files are being loaded (helps catch data inconsistencies)
# --- Session state for tab persistence ---
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Shortage Report"

# --- Tab selector (preserved across reruns) ---
st.subheader("View")
active_tab = st.radio("", ["Shortage Report", "Drill-Down Grid", "Excess Monitor", "Inventory Projection", "Exclusion Review"],
                       horizontal=True, label_visibility="collapsed",
                       key="tab_selector")
st.session_state.active_tab = active_tab

# --- Filters ---
st.subheader("Filters")

# Filter option lists. These are cheap (simple .unique() calls) — the 34s previously
# attributed to this block was actually the obsolescence_state .apply() above.
@st.cache_data(show_spinner=False)
def _build_filter_options(_s, _onhand_raw, _onorder_raw, _products, cache_key: str):
    parts = set(_s["part"].unique())
    if len(_onhand_raw) > 0 and "lpn" in _onhand_raw.columns:
        parts.update(_onhand_raw["lpn"].unique())
    # A part with on-order supply but no demand and no on-hand row (NPI parts such as
    # 10-08151A) was absent from the Part Number filter entirely. Union on-order too.
    if len(_onorder_raw) > 0 and "lunar_lpn" in _onorder_raw.columns:
        parts.update(_onorder_raw["lunar_lpn"].dropna().unique())
    parts.discard(None)
    parts = {p for p in parts if pd.notna(p) and str(p).strip() != ""}
    return {
        "cms": ["All"] + sorted(_s["cm"].unique()),
        "products": sorted(_products["display_name"].unique()),
        "parts": sorted(parts),
        "categories": sorted(_onhand_raw["item_category"].dropna().unique()) if len(_onhand_raw) > 0 else [],
    }

filter_cache = _build_filter_options(
    s, onhand_raw, onorder_raw, result["products"], f"{len(s)}:{len(onhand_raw)}:{len(onorder_raw)}:{len(result['products'])}"
)

# Main filters on left + Checkboxes on extreme right
col_left, col_right = st.columns([5.5, 1.5], gap="large")

# LEFT COLUMN: Filters (CM, Products, Part Number, Category, Generation) with Planning Horizon below
with col_left:
    # Top row filters - wider columns
    filter_cols = st.columns([1, 1.8, 1.8, 1.8, 1.8])

    cm_filter = filter_cols[0].selectbox("CM", filter_cache["cms"])
    prod_filter = filter_cols[1].multiselect("Products", filter_cache["products"])
    part_filter = filter_cols[2].multiselect("Part Number", filter_cache["parts"])
    category_filter = filter_cols[3].multiselect("Category", filter_cache["categories"])

    generation_filter_options = ["Active in Both", "Gen 1 Only", "Gen 2 Only", "Obsolete"]
    generation_filter = filter_cols[4].multiselect("Product Generation", generation_filter_options)

    # Exclude mode toggles for each filter
    exclude_cols = st.columns([1, 1.8, 1.8, 1.8, 1.8])
    exclude_cols[0].write("")  # Spacer under CM (no exclude for CM)
    prod_exclude = exclude_cols[1].checkbox("Exclude", key="prod_exclude", help="Exclude selected products instead of including them")
    part_exclude = exclude_cols[2].checkbox("Exclude", key="part_exclude", help="Exclude selected parts instead of including them")
    cat_exclude = exclude_cols[3].checkbox("Exclude", key="cat_exclude", help="Exclude selected categories instead of including them")
    gen_exclude = exclude_cols[4].checkbox("Exclude", key="gen_exclude", help="Exclude selected generations instead of including them")

    # Planning Horizon - separate row, left-indented
    horizon_cols = st.columns([0.5, 2.5])  # 0.5 for indent, 2.5 for the slider
    with horizon_cols[1]:
        weeks_window = st.slider(
            "Planning Horizon (weeks)",
            min_value=1,
            max_value=cfg.horizon_weeks,
            value=12,
            step=1
        )

_record_timing("Filters built")

# RIGHT COLUMN: Stacked checkboxes on extreme right
with col_right:
    st.write("")  # Spacing for alignment
    show_short_only = st.checkbox("Short only", value=True)
    exclude_uom_issues = st.checkbox("Exclude UoM", value=True)
    show_watched_only = st.checkbox("Watched only", value=False)
    include_allocations = st.checkbox("Lunar Alloc", value=False,
                                      help="Recalculate with Lunar inventory allocations")

# Choose between conservative (default) or allocation scenario
if include_allocations and "summary_with_allocation" in result:
    summary_to_use = result["summary_with_allocation"].copy()
    pab_to_use = result["pab_with_allocation"].copy()
else:
    summary_to_use = s.copy()
    pab_to_use = pab.copy()

# Filter data
filtered = summary_to_use.copy()

# Exclude parts that are on the exclusion list
if "exclusions_cache" in st.session_state and len(st.session_state.exclusions_cache) > 0:
    filtered = filtered[~filtered["part"].isin(st.session_state.exclusions_cache)]

if cm_filter != "All":
    filtered = filtered[filtered["cm"] == cm_filter]

# Product filter: show only buy parts under selected products (also filter by CM)
if prod_filter:
    # Get all product LPNs and their CMs for selected display_names
    selected_products_df = products[products["display_name"].isin(prod_filter)][["product", "cm"]]
    selected_products = selected_products_df["product"].unique()
    selected_cms = set(selected_products_df["cm"].unique())

    # Get all buy parts under these products
    parts_in_products = set()
    for product_lpn in selected_products:
        parts_in_products.update(get_buy_parts_under_product(product_lpn, bom_stitched))

    # Filter to show only parts in selected products AND in the selected products' CMs
    if prod_exclude:
        # Exclude mode: show everything EXCEPT selected products
        filtered = filtered[
            ~(filtered["part"].isin(parts_in_products)) |
            ~(filtered["cm"].isin(selected_cms))
        ]
    else:
        # Include mode: show only selected products
        filtered = filtered[
            (filtered["part"].isin(parts_in_products)) &
            (filtered["cm"].isin(selected_cms))
        ]

if part_filter:
    if part_exclude:
        filtered = filtered[~filtered["part"].isin(part_filter)]
    else:
        filtered = filtered[filtered["part"].isin(part_filter)]

if category_filter:
    if cat_exclude:
        filtered = filtered[~filtered["item_category"].isin(category_filter)]
    else:
        filtered = filtered[filtered["item_category"].isin(category_filter)]

if generation_filter and "obsolescence_state" in filtered.columns:
    if gen_exclude:
        filtered = filtered[~filtered["obsolescence_state"].isin(generation_filter)]
    else:
        filtered = filtered[filtered["obsolescence_state"].isin(generation_filter)]
if show_short_only:
    filtered = filtered[filtered["is_shortage"]]

# Exclude UoM issues (non-"ea"/"each" UoMs)
if exclude_uom_issues:
    uom_mask = filtered["uom"].fillna("ea").str.lower().str.strip().isin(["ea", "each", ""])
    filtered = filtered[uom_mask]

# Filter for watched only
if show_watched_only:
    filtered = filtered[filtered["part"].isin(watched_parts)]

# Apply time window: show if shortage within window OR on-hand insufficient within window
cutoff = cfg.week0 + pd.Timedelta(weeks=weeks_window)

# For each part, check if demand within time window exceeds on-hand
demand_by_part_cm = demand_detail[demand_detail["period"] <= cutoff].groupby(["cm", "part"], as_index=False)["qty"].sum()
demand_by_part_cm.columns = ["cm", "part", "demand_in_window"]

filtered = filtered.merge(demand_by_part_cm, on=["cm", "part"], how="left")
filtered["demand_in_window"] = filtered["demand_in_window"].fillna(0)

# Show if: first_shortage within window OR on-hand < demand within window
filtered = filtered[
    (filtered["first_shortage_date"].isna()) |
    (filtered["first_shortage_date"] <= cutoff) |
    (filtered["cm_available"] < filtered["demand_in_window"])
]
filtered = filtered.drop(columns=["demand_in_window"])


# Sort by DOS (FATP) (ascending - lowest first = most urgent)
# Use dos_fatp if available, else fall back to old days_of_supply column
if "dos_fatp" in filtered.columns:
    filtered = filtered.sort_values("dos_fatp", ascending=True, na_position="last")
elif "days_of_supply" in filtered.columns:
    filtered = filtered.sort_values("days_of_supply", ascending=True, na_position="last")

# Add note if using allocations
if include_allocations:
    st.info("🔄 **Allocation scenario active**: Drill-down shows PAB recalculated with Lunar allocations. "
            "Recommendation column shows PO quantities needed from Lunar to resolve each shortage.")

# --- Build plan grid (filtered by CM and products) ---
def get_pcba_pull_forward_daily(bp, snapshot_date):
    """Apply daily time-phasing + 28-day shift, reaggregate to calendar months.

    Matches engine logic: spread monthly qty across working days, shift 28 days, reaggregate.
    Returns: {month_str: total_qty} dict for the 4-week pull-forward aggregated result.
    """
    bp_filt = bp.copy()
    bp_filt["period_start"] = pd.to_datetime(bp_filt["period_start"], errors="coerce")
    bp_filt["qty"] = pd.to_numeric(bp_filt["qty"], errors="coerce").fillna(0.0)
    bp_filt = bp_filt[(bp_filt["qty"] != 0) & bp_filt["period_start"].notna()]

    if len(bp_filt) == 0:
        return {}

    daily_demand = []

    for _, row in bp_filt.iterrows():
        period_start = row["period_start"]
        month_end = (period_start + pd.offsets.MonthEnd(0)).normalize()

        # For snapshot month, count working days from snapshot onward; otherwise from 1st
        if period_start.month == snapshot_date.month and period_start.year == snapshot_date.year:
            count_start = snapshot_date
        else:
            count_start = period_start

        # Count working days (Mon-Fri)
        all_days = pd.date_range(count_start, month_end, freq="D")
        working_days = [d for d in all_days if d.weekday() < 5]

        if len(working_days) == 0:
            continue

        daily_rate = row["qty"] / len(working_days)

        # Shift each day back 28 days and track the shifted month
        for day in working_days:
            shifted_day = day - pd.Timedelta(days=28)
            month_str = shifted_day.strftime("%Y-%m")
            daily_demand.append({"month_str": month_str, "qty": daily_rate})

    if not daily_demand:
        return {}

    # Reaggregate by shifted month
    df = pd.DataFrame(daily_demand)
    monthly_agg = df.groupby("month_str")["qty"].sum().to_dict()
    return monthly_agg


@st.cache_data(show_spinner="Processing excess monitor...")
def process_excess_monitor(excess_df):
    """Cached: process excess monitor data (only called when Excess Monitor tab is rendered).

    This defers processing of excess data until it's actually needed.
    """
    if len(excess_df) == 0:
        return pd.DataFrame()
    return excess_df


def get_aggregation_function():
    """Return the aggregation function (cached resource)."""
    def aggregate_pab_by_grain(pab_df, grain="Day"):
        return _aggregate_pab_by_grain_impl(pab_df, grain)
    return aggregate_pab_by_grain


def _aggregate_pab_by_grain_impl(pab_df, grain="Day"):
    """Implementation of PAB aggregation (not cached).

    For daily granularity:
    - demand, receipts, net_flow: SUM across the period
    - pab (inventory): LAST value in the period (end-of-period balance)
    - period: FIRST date of the period
    """
    if len(pab_df) == 0:
        return pab_df

    pab_df = pab_df.copy()

    if grain == "Day":
        pab_df["period_key"] = pab_df["period"].dt.strftime("%Y-%m-%d")
    elif grain == "Week":
        pab_df["period_key"] = (pab_df["period"] - pd.to_timedelta(pab_df["period"].dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    elif grain == "Month":
        pab_df["period_key"] = pab_df["period"].dt.strftime("%Y-%m-01")

    # Sum all numeric columns EXCEPT 'pab' (which should be end-of-period balance)
    agg_dict = {col: "sum" for col in pab_df.columns if col not in ["period", "period_key", "cm", "part", "pab"]}

    # Add pab separately: use last (end-of-period) value
    agg_dict["pab"] = "last"

    agg_df = pab_df.groupby(["cm", "part", "period_key"], as_index=False).agg({
        **agg_dict,
        "period": "first"
    })
    return agg_df


def render_pab_drill_down(pab_to_show, filtered_parts, demand_detail, receipts):
    """Render the Demand/Supply/Inventory drill-down table with grain toggle."""

    # Get cached aggregation function
    aggregate_pab_by_grain = get_aggregation_function()

    # Grain selector
    col_title, col_grain = st.columns([3, 1])
    with col_title:
        st.write("**Demand, Supply & Inventory**")
    with col_grain:
        grain = st.selectbox("View by:", ["Day", "Week", "Month"], key=f"pab_grain_{id(pab_to_show)}")

    st.caption("3 rows per part: Demand | Supply | Inventory (color-coded: green=positive, red=negative)")

    if len(pab_to_show) == 0:
        st.info("No PAB data for selected filters.")
        return

    # Aggregate to selected grain
    pab_aggregated = aggregate_pab_by_grain(pab_to_show, grain)

    # Build 3-row grid for each part
    grid_data = []

    for cm, part in filtered_parts.values:
        part_pab = pab_aggregated[
            (pab_aggregated["cm"] == cm) & (pab_aggregated["part"] == part)
        ].sort_values("period")

        if len(part_pab) == 0:
            continue

        # Get part description
        part_desc = demand_detail[(demand_detail["cm"] == cm) & (demand_detail["part"] == part)]
        if len(part_desc) > 0:
            desc = str(part_desc.iloc[0]["description"])[:50]
        else:
            desc = "—"

        # Get demand_source
        part_demand_source = "Unknown"
        if len(part_desc) > 0 and "demand_source" in demand_detail.columns:
            part_demand_source = part_desc["demand_source"].iloc[0]

        # Demand row
        demand_row = {"CM": cm, "Part": part, "Description": desc, "Source": part_demand_source, "Metric": "Demand"}
        for _, pab_row in part_pab.iterrows():
            period_key = pab_row.get("period_key", pab_row["period"].strftime("%Y-%m-%d"))
            demand_row[period_key] = int(pab_row["demand"])
        grid_data.append(demand_row)

        # Supply row
        supply_row = {"CM": cm, "Part": part, "Description": desc, "Source": part_demand_source, "Metric": "Supply"}
        for _, pab_row in part_pab.iterrows():
            period_key = pab_row.get("period_key", pab_row["period"].strftime("%Y-%m-%d"))
            supply_row[period_key] = int(pab_row["receipts"])
        grid_data.append(supply_row)

        # Inventory row
        inv_row = {"CM": cm, "Part": part, "Description": desc, "Source": part_demand_source, "Metric": "Inventory"}
        for _, pab_row in part_pab.iterrows():
            period_key = pab_row.get("period_key", pab_row["period"].strftime("%Y-%m-%d"))
            inv_row[period_key] = int(pab_row["pab"])
        grid_data.append(inv_row)

    if grid_data:
        grid_df = pd.DataFrame(grid_data)

        # Style the inventory rows
        def color_inventory_row(row):
            if row["Metric"] != "Inventory":
                return [""] * len(row)

            colors = []
            for col in row.index:
                if col in ["CM", "Part", "Description", "Source", "Metric"]:
                    colors.append("")
                else:
                    val = row[col]
                    if isinstance(val, (int, float)):
                        if val < 0:
                            intensity = min(abs(val) / 100000, 1.0)
                            colors.append(f"background-color: rgba(255, 0, 0, {0.3 + intensity * 0.7})")
                        else:
                            intensity = min(val / 100000, 1.0)
                            colors.append(f"background-color: rgba(0, 128, 0, {0.2 + intensity * 0.5})")
                    else:
                        colors.append("")
            return colors

        styled_grid = grid_df.style.apply(color_inventory_row, axis=1)
        st.dataframe(styled_grid, use_container_width=True)
    else:
        st.info("No inventory data to display.")


def get_build_plan_grid(bp, demand_det, cm_filt, prod_filt, weeks_cutoff):
    """Returns a pivot table: products x weeks with total column."""
    bp_filt = bp[bp["period_start"] <= weeks_cutoff].copy()

    # Filter by products that appear in demand_detail
    if cm_filt != "All":
        prods_in_cm = set(demand_det[demand_det["cm"] == cm_filt]["product"].unique())
        bp_filt = bp_filt[bp_filt["product_lpn"].isin(prods_in_cm)]

    if prod_filt:
        # Get products that match the selected display_names
        # demand_det uses "alias" so we need to extract it from display_name
        prods_for_alias = set()
        for display_name in prod_filt:
            # Extract alias from display_name (format: "LPN - Alias")
            if " - " in display_name:
                alias = display_name.split(" - ", 1)[1]
                matching = demand_det[demand_det["alias"] == alias]["product"].unique()
                prods_for_alias.update(matching)
        bp_filt = bp_filt[bp_filt["product_lpn"].isin(prods_for_alias)]

    if len(bp_filt) == 0:
        return None

    # Add product descriptions
    prod_desc = products[["product", "alias"]].drop_duplicates().rename(
        columns={"product": "product_lpn", "alias": "Product"})
    bp_filt = bp_filt.merge(prod_desc, on="product_lpn", how="left")
    bp_filt["Product"] = bp_filt["product_lpn"] + " - " + bp_filt["Product"].fillna("")

    # Pivot to periods as columns
    bp_filt["period_str"] = bp_filt["period_start"].dt.strftime("%Y-%m")
    pivot = bp_filt.pivot_table(
        index="Product", columns="period_str", values="qty", aggfunc="sum", fill_value=0
    ).astype(int)

    # Add total column
    pivot["Total"] = pivot.sum(axis=1).astype(int)
    pivot = pivot.sort_values("Total", ascending=False)

    return pivot


def get_toplevel_build_plan(bp, products, cm_filt, prod_filt, weeks_cutoff):
    """Returns top-level (90-) products with regular build plan (no pull-forward)."""
    bp_filt = bp[bp["period_start"] <= weeks_cutoff].copy()

    # Filter by CM: get products in that CM from products master
    if cm_filt != "All":
        prods_in_cm = set(products[products["cm"] == cm_filt]["product"].unique())
        bp_filt = bp_filt[bp_filt["product_lpn"].isin(prods_in_cm)]

    # Filter by products: use products master display_name mapping
    if prod_filt:
        prods_for_display = set(
            products[products["display_name"].isin(prod_filt)]["product"].unique()
        )
        bp_filt = bp_filt[bp_filt["product_lpn"].isin(prods_for_display)]

    if len(bp_filt) == 0:
        return None

    # Add product display names
    prod_desc = products[["product", "display_name"]].drop_duplicates().rename(
        columns={"product": "product_lpn", "display_name": "Product"})
    bp_filt = bp_filt.merge(prod_desc, on="product_lpn", how="left")

    # Pivot: products x months (use adjusted qty which includes ASN deductions)
    bp_filt["period_str"] = bp_filt["period_start"].dt.strftime("%Y-%m")
    bp_filt["qty_for_display"] = bp_filt["qty_adjusted"].fillna(bp_filt["qty"])
    pivot = bp_filt.pivot_table(
        index="Product", columns="period_str", values="qty_for_display", aggfunc="sum", fill_value=0
    ).astype(int)

    # Add "shipped to date" column for first month if ASN data exists
    first_month_str = bp_filt["period_str"].min()
    if first_month_str in pivot.columns and bp_filt["asn_qty"].sum() > 0:
        asn_by_product = bp_filt[bp_filt["period_str"] == first_month_str].groupby("Product")["asn_qty"].sum().astype(int)
        shipped_col = asn_by_product.reindex(pivot.index, fill_value=0)

        # Insert "shipped to date" before first month
        first_col_idx = pivot.columns.get_loc(first_month_str)
        pivot.insert(first_col_idx, f"{first_month_str[:7]} shipped", shipped_col)
        pivot.rename(columns={first_month_str: f"{first_month_str[:7]} balance"}, inplace=True)

    pivot["Total"] = pivot.sum(axis=1).astype(int)
    pivot = pivot.sort_values("Total", ascending=False)

    return pivot


def get_pcba_build_plan(bp, bom, products, cm_filt, prod_filt, weeks_cutoff, demand_detail, snapshot_date=None):
    """Returns PCBA (30-) pull-forward demand with daily-grain time-phasing.

    Each PCBA shows the demand for its parent product (not aggregated across all products).
    Columns: PCBA - Description | Pull Forward Demand | 2026-08 | 2026-09 | ... | Grand Total
    """
    # Get snapshot date from demand_detail if not provided
    if snapshot_date is None:
        if len(demand_detail) > 0:
            snapshot_date = pd.to_datetime(demand_detail["period"].min()).normalize()
        else:
            return None

    # Get products for selected display_names (or all if none selected)
    if prod_filt:
        selected_products = products[products["display_name"].isin(prod_filt)]["product"].unique()
    else:
        # Show all products if no filter selected
        selected_products = products["product"].unique()

    if len(selected_products) == 0:
        return None

    # Include one month beyond weeks_cutoff to capture spillover from time-phasing
    next_month_cutoff = weeks_cutoff + pd.DateOffset(months=1)

    data = []

    # For each product, get its build plan and PCBAs
    for product_lpn in selected_products:
        # Get build plan for this product
        bp_product = bp[(bp["period_start"] <= next_month_cutoff) & (bp["product_lpn"] == product_lpn)].copy()

        if len(bp_product) == 0:
            continue

        # Apply daily time-phasing with 28-day pull-forward shift for this product
        monthly_demand_dict = get_pcba_pull_forward_daily(bp_product, snapshot_date)

        if not monthly_demand_dict:
            continue

        # Get PCBAs under this product only
        bom_product = bom[bom["Parent Product LPN"] == product_lpn]
        pcbas_product = bom_product[
            (bom_product["Parent PCBA LPN"].notna()) &
            (bom_product["Parent PCBA LPN"] != "")
        ]["Parent PCBA LPN"].unique()

        if len(pcbas_product) == 0:
            continue

        # Create rows: each PCBA of this product gets this product's demand
        for pcba in pcbas_product:
            for month_str, qty in monthly_demand_dict.items():
                data.append({
                    "pcba": pcba,
                    "month_str": month_str,
                    "demand": qty
                })

    if not data:
        return None

    result_df = pd.DataFrame(data)

    # Pivot: PCBA x shifted months
    pivot = result_df.pivot_table(
        index="pcba", columns="month_str", values="demand", aggfunc="first", fill_value=0
    ).astype(int)

    # Sort columns chronologically and filter to only show months within weeks_cutoff
    pivot = pivot[sorted(pivot.columns)]

    # Filter columns to only those within weeks_cutoff
    valid_cols = [col for col in pivot.columns if pd.Timestamp(col) <= weeks_cutoff]
    if not valid_cols:
        return None
    pivot = pivot[valid_cols]

    # Rename first month column to "Pull Forward Demand"
    first_month = pivot.columns[0]
    pivot = pivot.rename(columns={first_month: "Pull Forward Demand"})

    # Add Grand Total column
    pivot["Grand Total"] = pivot.sum(axis=1).astype(int)

    # Add PCBA descriptions from BOM and combine into single column
    pcba_list = result_df["pcba"].unique()
    pcba_descriptions = bom[bom["item_number"].isin(pcba_list)][["item_number", "item_name"]].drop_duplicates()
    pcba_desc_map = dict(zip(pcba_descriptions["item_number"], pcba_descriptions["item_name"]))

    # Reorder: PCBA - Description, Pull Forward Demand, months, Grand Total
    pivot_reset = pivot.reset_index()
    pivot_reset["pcba_desc"] = pivot_reset["pcba"] + " - " + pivot_reset["pcba"].map(pcba_desc_map).fillna("")
    pivot_reset = pivot_reset.drop("pcba", axis=1)
    pivot_reset = pivot_reset.set_index("pcba_desc")
    pivot_reset.index.name = "PCBA"

    # Move Grand Total to last position
    grand_total = pivot_reset.pop("Grand Total")
    pivot_reset["Grand Total"] = grand_total

    pivot_reset = pivot_reset.sort_values("Pull Forward Demand", ascending=False)

    return pivot_reset


build_plan_grid = get_build_plan_grid(build_plan, demand_detail, cm_filter, prod_filter, cutoff)
toplevel_plan = get_toplevel_build_plan(build_plan, products, cm_filter, prod_filter, cutoff)
pcba_plan = get_pcba_build_plan(build_plan, bom_stitched, products, cm_filter, prod_filter, cutoff, demand_detail, cfg.snapshot)

# ============================================================================
# HELPER: Unit price lookup with priority chain
# ============================================================================
def get_unit_prices_with_source(onhand, onorder, frames):
    """
    Get unit prices for CM and Lunar with source tracking.
    CM blended (cm_prices, used for the CM Projection balance calc):
        Cost DB, if a part has one -> else weighted average price BLENDED across
        both CM on-hand and CM on-order together (not an on-hand-first fallback).
    CM per-bucket (cm_onhand_prices / cm_onorder_prices): always the plain weighted
        average of that bucket alone, regardless of Cost DB - used for
        cm_on_hand_extended_cost / cm_on_order_extended_cost.
    Lunar On-Hand: Lunar on-hand inventory (weighted avg)
    Lunar On-Order: Lunar on-order inventory (weighted avg, separate from on-hand)

    Returns: cm_prices, lunar_prices (blended), cm_sources, lunar_onhand_prices,
             lunar_onorder_prices, cm_onhand_prices, cm_onorder_prices
    """
    cm_prices = {}  # {part: (price, source)}
    lunar_prices = {}  # {part: price} - blended for depletion
    cm_sources = {}  # {part: source}
    lunar_onhand_prices = {}  # {part: price} - on-hand specific
    lunar_onorder_prices = {}  # {part: price} - on-order specific

    # 1. Try cost database (EE and ME costs) for CM
    # EE costs use "Min Quote" column; ME costs use "Unit Price (@MOQ/EAU)" column
    cost_files = [frames.get("2026 EE costs.csv"), frames.get("2026 ME costs.csv")]
    for cost_file in cost_files:
        if cost_file is not None and len(cost_file) > 0:
            # Determine which price column this cost file uses (EE vs ME)
            price_col = None
            if "Min Quote" in cost_file.columns:
                price_col = "Min Quote"  # EE costs
            elif "Unit Price (@MOQ/EAU)" in cost_file.columns:
                price_col = "Unit Price (@MOQ/EAU)"  # ME costs

            if price_col and "LPN" in cost_file.columns:
                for _, row in cost_file.iterrows():
                    lpn = row.get("LPN", "").strip() if isinstance(row.get("LPN", ""), str) else str(row.get("LPN", ""))
                    price_str = row.get(price_col, "")
                    if lpn and pd.notna(price_str):
                        try:
                            price = float(str(price_str).replace("$", "").strip())
                            if price > 0:
                                cm_prices[lpn] = (price, "Cost DB")
                                cm_sources[lpn] = "Cost DB"
                        except:
                            pass

    # cm_onhand_prices / cm_onorder_prices: per-bucket weighted avg, always computed
    # regardless of Cost DB status. These feed cm_on_hand_extended_cost /
    # cm_on_order_extended_cost, which must reflect what's actually on hand / on
    # order, not the blended cm_unit_price.
    cm_onhand_prices = {}
    cm_onorder_prices = {}

    # Running (qty, value) totals per part, used below to build the TRUE blended
    # weighted average across on-hand AND on-order together - not an on-hand-first,
    # on-order-as-fallback priority. Spec: "use the cm cost from input database when
    # pricing is available and if not available then use weighted average price
    # BETWEEN on hand and on order."
    cm_onhand_totals = {}   # {part: (qty, value)}
    cm_onorder_totals = {}  # {part: (qty, value)}

    # 2. CM on-hand: per-bucket price + totals for the blend below.
    # NOTE the two guards differ deliberately. The totals only require qty > 0:
    # a part sitting at a $0 unit price still occupies real units and MUST enter
    # the blend's denominator, or the blend collapses to the other bucket's price.
    # The per-bucket price additionally requires value > 0 so we report 0.0 rather
    # than a misleading fabricated price.
    if "lpn" in onhand.columns:
        cm_onhand = onhand[onhand["source_report"] != "Lunar Netsuite"]
        for part, group in cm_onhand.groupby("lpn"):
            total_qty = group["unrestricted_qty"].sum()
            total_value = (group["unrestricted_qty"] * group.get("unit_price", 0)).sum()
            if total_qty > 0:
                cm_onhand_totals[part] = (total_qty, total_value)
                if total_value > 0:
                    cm_onhand_prices[part] = total_value / total_qty

    # 3. CM on-order: per-bucket price + totals for the blend below (same guard split)
    if "lunar_lpn" in onorder.columns:
        cm_onorder = onorder[onorder["source_report"] != "Lunar Netsuite"]
        for part, group in cm_onorder.groupby("lunar_lpn"):
            total_qty = group["quantity_open"].sum()
            total_value = (group["quantity_open"] * group.get("unit_price", 0)).sum()
            if total_qty > 0:
                cm_onorder_totals[part] = (total_qty, total_value)
                if total_value > 0:
                    cm_onorder_prices[part] = total_value / total_qty

    # 3b. Blended cm_unit_price for parts Cost DB didn't cover: weighted average
    # combining BOTH buckets' qty and value together (not a fallback chain).
    for part in set(cm_onhand_totals) | set(cm_onorder_totals):
        if part in cm_prices:
            continue  # Cost DB already covers this part
        oh_qty, oh_val = cm_onhand_totals.get(part, (0, 0))
        oo_qty, oo_val = cm_onorder_totals.get(part, (0, 0))
        combined_qty = oh_qty + oo_qty
        combined_val = oh_val + oo_val
        if combined_qty > 0:
            weighted_price = combined_val / combined_qty
            cm_prices[part] = (weighted_price, "Weighted Avg (On-Hand + On-Order)")
            cm_sources[part] = "Weighted Avg (On-Hand + On-Order)"

    # 4. PRIMARY: Calculate from Lunar on-hand (weighted avg from unrestricted_value / unrestricted_qty)
    # Store separate on-hand prices
    if "lpn" in onhand.columns:
        lunar_onhand = onhand[onhand["source_report"] == "Lunar Netsuite"]
        for part, group in lunar_onhand.groupby("lpn"):
            # Use unrestricted_value / unrestricted_qty, NOT the unit_price column
            total_qty = group["unrestricted_qty"].sum()
            total_value = group["unrestricted_value"].sum()
            # Store on-hand price
            lunar_onhand_prices[part] = total_value / total_qty if total_qty > 0 else 0
            # Also add to lunar_prices for blended calculation
            lunar_prices[part] = lunar_onhand_prices[part]

    # 5. SEPARATE: Calculate from Lunar on-order (weighted avg, tracked separately from on-hand)
    if "lunar_lpn" in onorder.columns:
        lunar_oo = onorder[onorder["source_report"] == "Lunar Netsuite"]
        for part, group in lunar_oo.groupby("lunar_lpn"):
            total_qty = group["quantity_open"].sum()
            total_value = (group["quantity_open"] * group.get("unit_price", 0)).sum()
            weighted_price = total_value / total_qty if total_qty > 0 else 0

            # Store on-order price
            lunar_onorder_prices[part] = weighted_price

            # For blended price: if part not in on-hand, use on-order price
            if part not in lunar_prices and weighted_price > 0:
                lunar_prices[part] = weighted_price
            # If part exists in both on-hand and on-order, lunar_prices stays as on-hand
            # (blended calculation will happen later when we have qty data)

    return cm_prices, lunar_prices, cm_sources, lunar_onhand_prices, lunar_onorder_prices, cm_onhand_prices, cm_onorder_prices

def get_unit_prices(onhand, onorder, frames):
    """
    Get unit prices for CM and Lunar by part, with priority chain:
    CM: Cost DB → CM on-hand → CM on-order
    Lunar: Lunar on-hand → Lunar on-order

    Returns: {part: cm_price}, {part: lunar_price}
    """
    cm_prices = {}
    lunar_prices = {}

    # 1. Try cost database (EE and ME costs)
    # Look for cost files in frames or uploaded files
    cost_files = [frames.get("2026 EE costs.csv"), frames.get("2026 ME costs.csv")]
    for cost_file in cost_files:
        if cost_file is not None and len(cost_file) > 0:
            if "LPN" in cost_file.columns and "Unit Price (@MOQ/EAU)" in cost_file.columns:
                for _, row in cost_file.iterrows():
                    lpn = row.get("LPN")
                    price_str = row.get("Unit Price (@MOQ/EAU)", "")
                    if lpn and pd.notna(price_str):
                        try:
                            # Parse price (e.g., "$ 5.00" → 5.00)
                            price = float(str(price_str).replace("$", "").strip())
                            if price > 0:
                                cm_prices[lpn] = price
                        except:
                            pass

    # 2. Calculate from CM on-hand (for parts not in cost DB)
    if "_cm" in onhand.columns and "_lpn" in onhand.columns:
        cm_onhand = onhand[onhand["_owner"] != "Lunar"]
        for part, group in cm_onhand.groupby("_lpn"):
            if part not in cm_prices:  # Only if not in cost DB
                total_qty = group["unrestricted_qty"].sum()
                total_value = (group["unrestricted_qty"] * group.get("unit_price", 0)).sum()
                if total_qty > 0 and total_value > 0:
                    cm_prices[part] = total_value / total_qty

    # 3. Calculate from CM on-order (for parts still missing)
    if "_lpn" in onorder.columns and "_cm" in onorder.columns:
        for part, group in onorder.groupby("_lpn"):
            if part not in cm_prices:
                total_qty = group["quantity_open"].sum()
                total_value = (group["quantity_open"] * group.get("unit_price", 0)).sum()
                if total_qty > 0 and total_value > 0:
                    cm_prices[part] = total_value / total_qty

    # 4. Calculate from Lunar on-hand
    if "_lpn" in onhand.columns:
        lunar_onhand = onhand[onhand["_owner"] == "Lunar"]
        for part, group in lunar_onhand.groupby("_lpn"):
            total_qty = group["unrestricted_qty"].sum()
            total_value = (group["unrestricted_qty"] * group.get("unit_price", 0)).sum()
            if total_qty > 0 and total_value > 0:
                lunar_prices[part] = total_value / total_qty

    # 5. Calculate from Lunar on-order (for parts still missing)
    if "_lpn" in onorder.columns and "_vendor" in onorder.columns:
        lunar_oo = onorder[onorder["_vendor"] == "Lunar"]
        for part, group in lunar_oo.groupby("_lpn"):
            if part not in lunar_prices:
                total_qty = group["quantity_open"].sum()
                total_value = (group["quantity_open"] * group.get("unit_price", 0)).sum()
                if total_qty > 0 and total_value > 0:
                    lunar_prices[part] = total_value / total_qty

    return cm_prices, lunar_prices

# ============================================================================
# RENDER ACTIVE TAB
# ============================================================================
if st.session_state.active_tab == "Shortage Report":
    # SHORTAGE REPORT
    st.subheader("Build Plan (Filtered)")

    # Collapsible build plan sections
    col1, col2 = st.columns(2)

    with col1:
        with st.expander("▼ Top-Level Products", expanded=True):
            if toplevel_plan is not None and len(toplevel_plan) > 0:
                st.caption(f"90- products with Build Plan demand by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
                st.dataframe(toplevel_plan, use_container_width=True)
            else:
                st.info("No top-level products planned for selected filters.")

    with col2:
        with st.expander("▼ PCBA Build Plan", expanded=False):
            if pcba_plan is not None and len(pcba_plan) > 0:
                st.caption(f"30- PCBA parts with 4-week pull-forward by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
                st.dataframe(pcba_plan, use_container_width=True)
            else:
                st.info("No PCBA parts in pull-forward for selected filters.")

    st.divider()
    st.subheader("Components Short Within Selected Time Window")
    st.caption(f"Parts running out by {cutoff.strftime('%Y-%m-%d')} ({weeks_window} weeks)")

    if len(filtered) == 0:
        st.info("No shortages in this time window.")
    else:
        # Build shortage report
        report = []
        for _, row in filtered.iterrows():
            cm, part = row["cm"], row["part"]

            # Get incoming supply for this part
            incoming = receipts[
                (receipts["cm"] == cm) & (receipts["part"] == part) &
                (receipts["period"] <= cutoff)
            ]
            has_past_due = False
            if len(incoming):
                supply_parts = []
                for _, r in incoming.sort_values("period").iterrows():
                    # Use original_eta if available (past-due items), otherwise use period
                    display_date = r["_original_eta"] if pd.notna(r.get("_original_eta")) else r["period"]
                    display_date_str = display_date.strftime("%Y-%m-%d")

                    qty_str = f"{int(r['receipts']):,} on {display_date_str}"

                    # Add [Past Due] note if applicable
                    if r.get("is_past_due", False):
                        qty_str += " [Past Due]"
                        has_past_due = True

                    supply_parts.append(qty_str)

                supply_str = "\n".join(supply_parts)  # Use newline instead of pipe for readability
            else:
                supply_str = "—"

            # Format products as "Product (usage)" and get usage from demand_detail
            prods_list = row["products"].split(", ") if pd.notna(row["products"]) else []
            prod_with_usage = []
            for prod in prods_list:
                usage_rows = demand_detail[
                    (demand_detail["alias"] == prod) & (demand_detail["part"] == part)
                ]
                if len(usage_rows):
                    usage = int(usage_rows.iloc[0]["usage"])
                    prod_with_usage.append(f"{prod} ({usage})")
                else:
                    prod_with_usage.append(prod)
            products_str = ", ".join(prod_with_usage)

            # Handle NaN description
            desc = row["description"] if pd.notna(row["description"]) else "—"
            desc = str(desc)[:40] if desc != "—" else "—"

            # Get runout dates for color-coding incoming supply
            runout_date_fatp = row.get("runout_date_fatp")
            runout_date_pcba = row.get("runout_date_pcba_kitting")
            runout_date_to_use = runout_date_pcba if pd.notna(runout_date_pcba) else runout_date_fatp

            report_item = {
                "CM": cm,
                "Part": part,
                "Description": desc,
                "Products": products_str,
                "Build Coverage": int(row["blocks_buildable"]),
                "First Short Date": row["first_shortage_date"].strftime("%Y-%m-%d") if pd.notna(row["first_shortage_date"]) else "—",
                "DOS (FATP)": int(row.get("dos_fatp", 999)) if (pd.notna(row.get("dos_fatp")) and row.get("dos_fatp", 999) < 999) else "∞",
                "DOS (PCBA Kitting)": int(row.get("dos_pcba_kitting", 999)) if (pd.notna(row.get("dos_pcba_kitting")) and row.get("dos_pcba_kitting", 999) < 999) else "∞",
                "Runout Date (FATP)": row.get("runout_date_fatp").strftime("%Y-%m-%d") if pd.notna(row.get("runout_date_fatp")) else "—",
                "Runout Date (PCBA Kitting)": row.get("runout_date_pcba_kitting").strftime("%Y-%m-%d") if pd.notna(row.get("runout_date_pcba_kitting")) else "—",
                "Raw Inventory": int(row.get("raw_inventory", 0)),
                "WIP Inventory": int(row.get("wip_inventory", 0)),
                "Total Inventory": int(row.get("raw_inventory", 0) + row.get("wip_inventory", 0)),
                "Shortage Qty": int(row["shortage_qty"]) if pd.notna(row["shortage_qty"]) else 0,
                "Incoming Supply": supply_str,
                "_runout_date_for_coloring": runout_date_to_use,
            }

            # Add allocation recommendation if toggle is on
            if include_allocations:
                alloc_status = row.get("allocation_status", "")
                qty_alloc = row.get("qty_to_allocate", 0)

                if alloc_status == "RESOLVED_BY_ALLOCATION":
                    report_item["Recommended"] = f"✓ PO {int(qty_alloc):,} units from Lunar → RESOLVED"
                    report_item["First Short Date"] = "—"  # Hide date since it's resolved
                elif pd.notna(qty_alloc) and qty_alloc > 0:
                    fully_covered = row.get("fully_covered", False)
                    if fully_covered:
                        report_item["Recommended"] = f"PO {int(qty_alloc):,} units from Lunar to fully resolve"
                    else:
                        report_item["Recommended"] = f"PO {int(qty_alloc):,} units from Lunar (partial resolve)"
                else:
                    report_item["Recommended"] = "No Lunar inventory available"

            report.append(report_item)

        report_df = pd.DataFrame(report)

        # Add Notes column showing preview
        def get_notes_preview(part):
            notes = load_notes(part)
            if not notes:
                return ""
            first_note = notes[0]["note"][:60]  # First 60 chars
            count_str = f" (+{len(notes)-1})" if len(notes) > 1 else ""
            return f"📝 {first_note}...{count_str}" if len(notes[0]["note"]) > 60 else f"📝 {first_note}{count_str}"

        report_df["Notes"] = report_df["Part"].apply(get_notes_preview)

        # Color-code Incoming Supply based on runout date
        def get_supply_color(row):
            """Color-code incoming supply: GREEN if solves, ORANGE if timing tight, RED otherwise.

            Logic:
            - GREEN: Supply arrives >1 week before runout (solves easily)
            - ORANGE: Supply arrives within 1 week before runout (solves but tight)
            - RED: Supply arrives at/after runout OR no supply (doesn't solve or too late)
            """
            supply_str = row["Incoming Supply"]
            runout_date = row.get("_runout_date_for_coloring")

            # Parse incoming supply to get earliest delivery date
            if supply_str == "—" or pd.isna(supply_str):
                return "background-color: #cc0000; color: white; font-weight: bold"  # RED: no incoming supply

            # Check for [Past Due] marker
            if "[Past Due]" in str(supply_str):
                return "background-color: #cc0000; color: white; font-weight: bold"  # RED: past due

            # Extract first date from supply string (format: "qty on YYYY-MM-DD")
            try:
                import re
                dates = re.findall(r'\d{4}-\d{2}-\d{2}', str(supply_str))
                if dates and pd.notna(runout_date):
                    earliest_supply = pd.to_datetime(dates[0])
                    days_diff = (earliest_supply - runout_date).days

                    # days_diff < 0: supply before runout (good)
                    # days_diff >= 0: supply at/after runout (bad)
                    if days_diff < -7:
                        # Supply arrives >1 week before runout → solves easily
                        return "background-color: #006600; color: white; font-weight: bold"  # GREEN
                    elif days_diff < 0:
                        # Supply arrives within 1 week before runout → solves but tight
                        return "background-color: #ff9900; color: black; font-weight: bold"  # ORANGE
                    else:
                        # Supply arrives at or after runout → doesn't solve
                        return "background-color: #cc0000; color: white; font-weight: bold"  # RED
            except Exception:
                pass

            return ""  # No coloring if parsing fails

        # Store color info for later styling
        report_df["_supply_color"] = report_df.apply(get_supply_color, axis=1)

        # Quick Actions section (between title and table)
        st.subheader("Quick Actions")
        action_cols = st.columns([3, 1, 1, 1, 1])
        with action_cols[0]:
            selected_part = st.selectbox(
                "Select part:",
                options=sorted(report_df["Part"].unique()),
                key="actions_part_select",
                label_visibility="collapsed"
            )
        with action_cols[1]:
            if st.button("🏷️ Exclude", key="quick_exclude", use_container_width=True):
                dialog_exclude(selected_part)
        with action_cols[2]:
            if st.button("➕ Add Note", key="quick_note", use_container_width=True):
                dialog_add_note(selected_part)
        with action_cols[3]:
            if st.button("📖 View Notes History", key="quick_view_notes", use_container_width=True):
                dialog_view_notes(selected_part)
        with action_cols[4]:
            part_is_watched = selected_part in watched_parts
            watch_label = "✓ Watched" if part_is_watched else "👁️ Watch"
            if st.button(watch_label, key="quick_watch", use_container_width=True):
                if part_is_watched:
                    unwatch_part(selected_part)
                    st.rerun()
                else:
                    dialog_watch(selected_part)

        st.divider()

        # Prepare data for clean dataframe display
        col_order = ["CM", "Part", "Description", "Products", "Raw Inventory", "WIP Inventory",
                     "Total Inventory", "DOS (FATP)", "DOS (PCBA Kitting)",
                     "Runout Date (FATP)", "Runout Date (PCBA Kitting)",
                     "Build Coverage", "First Short Date", "Incoming Supply"]
        if include_allocations and "Recommended" in report_df.columns:
            col_order.append("Recommended")

        # Add Notes and Watched columns
        def format_notes(part):
            """Format latest note with → indicator if notes exist."""
            notes = load_notes(part)
            if not notes:
                return ""
            # Get latest note (last in list)
            latest_note = notes[-1]["note"][:60]
            arrow = " →" if len(notes) > 0 else ""
            return f"📝 {latest_note}...{arrow}" if len(notes[-1]["note"]) > 60 else f"📝 {latest_note}{arrow}"

        report_df["Notes"] = report_df["Part"].apply(format_notes)
        report_df["Watched"] = report_df["Part"].apply(
            lambda p: "👁️" if p in watched_parts else ""
        )

        # Reorder: add Notes and Watched at the end
        col_order.extend(["Notes", "Watched"])
        # Filter to only display columns (exclude helper columns starting with _)
        display_cols = [c for c in col_order if c in report_df.columns and not c.startswith("_")]
        report_df_display = report_df[display_cols].copy()

        # Style: color-code Incoming Supply based on runout date
        def highlight_incoming_supply(row):
            """Highlight Incoming Supply column based on runout alignment."""
            colors = [""] * len(row)
            for i, col in enumerate(row.index):
                if col == "Incoming Supply":
                    # Get the color from _supply_color column (or empty if not in display cols)
                    part_val = row.get("Part")
                    # Find matching row in report_df to get color
                    matching = report_df[report_df["Part"] == part_val]
                    if len(matching) > 0:
                        colors[i] = matching["_supply_color"].iloc[0]
            return colors

        styled_df = report_df_display.style.apply(highlight_incoming_supply, axis=1)

        # Display clean dataframe with styling
        st.dataframe(styled_df, use_container_width=True, height=500)

        st.write(f"**Total: {len(report)} parts short**")

# ============================================================================
# DRILL-DOWN GRID
# ============================================================================
elif st.session_state.active_tab == "Drill-Down Grid":
    st.subheader("Build Plan (Filtered)")

    # Collapsible build plan sections
    col1, col2 = st.columns(2)

    with col1:
        with st.expander("▼ Top-Level Products", expanded=True):
            if toplevel_plan is not None and len(toplevel_plan) > 0:
                st.caption(f"90- products with Build Plan demand by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
                st.dataframe(toplevel_plan, use_container_width=True)
            else:
                st.info("No top-level products planned for selected filters.")

    with col2:
        with st.expander("▼ PCBA Build Plan", expanded=False):
            if pcba_plan is not None and len(pcba_plan) > 0:
                st.caption(f"30- PCBA parts with 4-week pull-forward by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
                st.dataframe(pcba_plan, use_container_width=True)
            else:
                st.info("No PCBA parts in pull-forward for selected filters.")

    st.divider()

    # Helper function: aggregate PAB data by grain
    def aggregate_pab_by_grain(pab_df, grain="Day"):
        """Group PAB data to Daily, Weekly (Monday), or Monthly grain.

        For aggregation:
        - demand, receipts, net_flow: SUM across the period
        - pab (inventory): LAST value in the period (end-of-period balance)
        - period: FIRST date of the period
        """
        if len(pab_df) == 0:
            return pab_df

        pab_df = pab_df.copy()

        if grain == "Day":
            pab_df["period_key"] = pab_df["period"].dt.strftime("%Y-%m-%d")
        elif grain == "Week":
            # Group to Monday of each week
            pab_df["period_key"] = (pab_df["period"] - pd.to_timedelta(pab_df["period"].dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
        elif grain == "Month":
            # Group to first day of month
            pab_df["period_key"] = pab_df["period"].dt.strftime("%Y-%m-01")

        # Sum all numeric columns EXCEPT 'pab' (which should be end-of-period balance)
        agg_dict = {col: "sum" for col in pab_df.columns if col not in ["period", "period_key", "cm", "part", "pab"]}

        # Add pab separately: use last (end-of-period) value
        agg_dict["pab"] = "last"

        agg_df = pab_df.groupby(["cm", "part", "period_key"], as_index=False).agg({
            **agg_dict,
            "period": "first"  # Keep original period for reference
        })
        return agg_df

    # Filter toggle: All parts vs Only PCBA parts
    st.subheader("Demand, Supply & Inventory")

    col_filter, col_grain = st.columns([2, 1])

    with col_filter:
        filter_option = st.radio(
            "Show:",
            ["All parts", "Only PCBA parts"],
            horizontal=True,
            key="demand_source_filter"
        )

    with col_grain:
        grain = st.selectbox(
            "View by:",
            ["Day", "Week", "Month"],
            key="pab_grain"
        )

    st.caption("3 rows per part: Demand | Supply | Inventory (color-coded: green=positive, red=negative)")

    if len(filtered) == 0:
        st.info("No shortages to display.")
    else:
        parts_to_show = filtered[["cm", "part"]].drop_duplicates()

        # Apply demand_source filter
        if filter_option == "Only PCBA parts":
            # Show only parts with demand_source='PCBA_PullForward'
            pcba_parts_available = demand_detail[
                demand_detail["demand_source"] == "PCBA_PullForward"
            ][["cm", "part"]].drop_duplicates()
            parts_to_show = pcba_parts_available

            if len(parts_to_show) == 0:
                st.info("No PCBA pull-forward parts in demand data.")

        pab_filtered = pab_to_use[
            (pab_to_use["period"] <= cutoff) &
            (pab_to_use[["cm", "part"]].apply(tuple, axis=1).isin(
                parts_to_show.apply(tuple, axis=1)
            ))
        ].copy()

        if len(pab_filtered) == 0:
            st.info("No PAB data for selected filters.")
        else:
            # Aggregate to selected grain
            pab_filtered = aggregate_pab_by_grain(pab_filtered, grain)

            # Build 3-row grid for each part
            grid_data = []

            for cm, part in parts_to_show.values:
                part_pab = pab_filtered[
                    (pab_filtered["cm"] == cm) & (pab_filtered["part"] == part)
                ].sort_values("period")

                if len(part_pab) == 0:
                    continue

                # Get part description
                part_desc = filtered[
                    (filtered["cm"] == cm) & (filtered["part"] == part)
                ].iloc[0]
                desc = part_desc.get("description", "—") if pd.notna(part_desc.get("description")) else "—"
                if desc != "—":
                    desc = str(desc)[:50]

                # Get demand_source for this part (handle if column doesn't exist)
                part_rows = demand_detail[(demand_detail["cm"] == cm) & (demand_detail["part"] == part)]
                part_demand_source = "Unknown"
                if len(part_rows) > 0:
                    if "demand_source" in demand_detail.columns:
                        part_demand_source = part_rows["demand_source"].iloc[0]
                    else:
                        part_demand_source = "Build Plan"  # Default if column missing

                # Build all 3 rows in ONE pass (Demand, Supply, Inventory) instead of 3 loops
                base_row = {
                    "CM": cm,
                    "Part": part,
                    "Description": desc,
                    "Source": part_demand_source,
                }

                # Vectorized: format all period keys once
                period_keys = []
                for _, row in part_pab.iterrows():
                    if "period_key" in row.index:
                        period_keys.append(row["period_key"])
                    else:
                        period_keys.append(row["period"].strftime("%Y-%m-%d"))

                # Single pass: populate all three rows at once
                demand_row = {**base_row, "Metric": "Demand"}
                supply_row = {**base_row, "Metric": "Supply"}
                inv_row = {**base_row, "Metric": "Inventory"}

                for period_key, (_, pab_row) in zip(period_keys, part_pab.iterrows()):
                    demand_row[period_key] = int(pab_row["demand"])
                    supply_row[period_key] = int(pab_row["receipts"])
                    inv_row[period_key] = int(pab_row["pab"])

                grid_data.extend([demand_row, supply_row, inv_row])

            if grid_data:
                grid_df = pd.DataFrame(grid_data)

                # Style the inventory rows with color gradient
                def color_inventory_row(row):
                    if row["Metric"] != "Inventory":
                        return [""] * len(row)

                    colors = []
                    for col in row.index:
                        if col in ["CM", "Part", "Description", "Source", "Metric"]:
                            colors.append("")
                        else:
                            val = row[col]
                            if isinstance(val, (int, float)):
                                if val < 0:
                                    # Red for negative
                                    intensity = min(abs(val) / 100000, 1.0)  # Scale for visibility
                                    colors.append(f"background-color: rgba(255, 0, 0, {0.3 + intensity * 0.7})")
                                else:
                                    # Green for positive
                                    intensity = min(val / 100000, 1.0)
                                    colors.append(f"background-color: rgba(0, 128, 0, {0.2 + intensity * 0.5})")
                            else:
                                colors.append("")
                    return colors

                styled_df = grid_df.style.apply(color_inventory_row, axis=1)
                st.dataframe(styled_df, use_container_width=True, height=500)
            else:
                st.info("No data to display.")

        # Inline part management
        st.divider()
        available_parts = sorted([f"{r[0]}@{r[1]}" for r in parts_to_show.values])
        if len(available_parts) > 0:
            selected_drill_part = st.selectbox(
                "Manage part (click 🏷️ to exclude or ➕ to add note):",
                options=["—"] + available_parts,
                key="manage_drill_part_select",
                label_visibility="collapsed"
            )

            if selected_drill_part != "—":
                drill_cm, drill_part = selected_drill_part.split("@")
                col_exclude, col_note = st.columns(2)

                with col_exclude:
                    if st.button("🏷️ Exclude", key="exclude_btn_drill", use_container_width=True):
                        st.session_state.drill_action = "exclude"
                        st.session_state.drill_part = drill_part

                with col_note:
                    if st.button("➕ Add Note", key="note_btn_drill", use_container_width=True):
                        st.session_state.drill_action = "note"
                        st.session_state.drill_part = drill_part

                # Show form based on selected action
                if st.session_state.get("drill_part") == drill_part:
                    if st.session_state.get("drill_action") == "exclude":
                        st.warning(f"**Exclude {drill_part}?**")
                        reason = st.text_input("Reason (e.g., printed labels, not tracked):", key="exclude_reason_drill")
                        col_confirm, col_cancel = st.columns(2)
                        with col_confirm:
                            if st.button("Confirm Exclude", key="confirm_exclude_drill", use_container_width=True):
                                if reason:
                                    exclude_part(drill_part, reason)
                                    st.session_state.pop("drill_action", None)
                                    st.session_state.pop("drill_part", None)
                                    st.rerun()
                                else:
                                    st.error("Please provide a reason")
                        with col_cancel:
                            if st.button("Cancel", key="cancel_exclude_drill", use_container_width=True):
                                st.session_state.pop("drill_action", None)
                                st.session_state.pop("drill_part", None)
                                st.rerun()

                    elif st.session_state.get("drill_action") == "note":
                        st.info(f"**Add note to {drill_part}**")
                        note_text = st.text_area("Note:", key="note_text_drill", height=80)
                        col_confirm, col_cancel = st.columns(2)
                        with col_confirm:
                            if st.button("Add Note", key="confirm_note_drill", use_container_width=True):
                                if note_text:
                                    add_note(drill_part, note_text)
                                    st.session_state.pop("drill_action", None)
                                    st.session_state.pop("drill_part", None)
                                    st.rerun()
                                else:
                                    st.error("Please enter a note")
                        with col_cancel:
                            if st.button("Cancel", key="cancel_note_drill", use_container_width=True):
                                st.session_state.pop("drill_action", None)
                                st.session_state.pop("drill_part", None)
                                st.rerun()

        # Optional: show one part's full timeline
        st.subheader("Detailed Timeline (Select a Part)")
        if len(parts_to_show) > 0:
            part_choice = st.selectbox(
                "Part",
                [f"{r[0]}@{r[1]}" for r in parts_to_show.values],
                key="drill_part"
            )
            cm_sel, part_sel = part_choice.split("@")

            part_timeline = pab_filtered[
                (pab_filtered["cm"] == cm_sel) & (pab_filtered["part"] == part_sel)
            ].sort_values("period")

            if len(part_timeline):
                part_info = filtered[
                    (filtered["cm"] == cm_sel) & (filtered["part"] == part_sel)
                ].iloc[0]
                st.write(f"**{part_sel}** — {part_info['description']}")
                st.write(f"State: {part_info['state']} | Opening: {int(part_info['opening']):,} "
                        f"| Build Coverage: {int(part_info['blocks_buildable'])}")

                timeline_display = part_timeline[[
                    "period", "opening", "demand", "receipts", "net_flow", "pab"
                ]].copy()
                timeline_display["period"] = timeline_display["period"].dt.strftime("%Y-%m-%d")
                timeline_display.columns = ["Week", "Opening", "Demand", "Receipts", "Net Flow", "PAB"]
                for col in ["Opening", "Demand", "Receipts", "Net Flow", "PAB"]:
                    timeline_display[col] = timeline_display[col].astype(int)

                st.dataframe(timeline_display, use_container_width=True)


# ============================================================================
# EXCESS MONITOR
# ============================================================================
elif st.session_state.active_tab == "Excess Monitor":
    st.subheader("Build Plan (Filtered)")

    # Collapsible build plan sections
    col1, col2 = st.columns(2)

    with col1:
        with st.expander("▼ Top-Level Products", expanded=True):
            if toplevel_plan is not None and len(toplevel_plan) > 0:
                st.caption(f"90- products with Build Plan demand by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
                st.dataframe(toplevel_plan, use_container_width=True)
            else:
                st.info("No top-level products planned for selected filters.")

    with col2:
        with st.expander("▼ PCBA Build Plan", expanded=False):
            if pcba_plan is not None and len(pcba_plan) > 0:
                st.caption(f"30- PCBA parts with 4-week pull-forward by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
                st.dataframe(pcba_plan, use_container_width=True)
            else:
                st.info("No PCBA parts in pull-forward for selected filters.")

    st.divider()
    st.subheader("Parts with Excess Supply Beyond Demand")
    st.caption("Receipts scheduled after the build plan ends for each product")

    # Lazy-load excess data only when this tab is rendered
    excess_processed = process_excess_monitor(excess)

    if len(excess_processed) == 0:
        st.info("No excess supply detected.")
    else:
        # Filter excess by CM and products
        excess_filtered = excess_processed.copy()

        if cm_filter != "All":
            excess_filtered = excess_filtered[excess_filtered["cm"] == cm_filter]

        if prod_filter:
            # Extract aliases from display_names for matching
            aliases = [dn.split(" - ", 1)[1] if " - " in dn else dn for dn in prod_filter]
            pattern = "|".join(aliases)
            if prod_exclude:
                excess_filtered = excess_filtered[
                    ~excess_filtered["products"].str.contains(pattern, na=False)
                ]
            else:
                excess_filtered = excess_filtered[
                    excess_filtered["products"].str.contains(pattern, na=False)
                ]

        if len(excess_filtered) == 0:
            st.info("No excess supply for selected filters.")
        else:
            # Build excess report, grouped by (cm, part)
            report = []
            for (cm, part), group in excess_filtered.groupby(["cm", "part"]):
                first_row = group.iloc[0]
                total_excess_qty = int(group["qty_to_cancel"].sum())
                total_excess_cost = float(group["cost_to_save"].sum())

                # Collect all PO cancellation suggestions for this part
                po_suggestions = []
                for _, row in group.iterrows():
                    sugg = f"{row['action_text']}"
                    po_suggestions.append(sugg)
                suggestions_str = " | ".join(po_suggestions)

                last_demand_period = first_row["last_demand_period"]
                last_demand_str = (
                    last_demand_period.strftime("%Y-%m-%d")
                    if pd.notna(last_demand_period)
                    else "—"
                )

                report.append({
                    "CM": cm,
                    "Part": part,
                    "Description": first_row["description"][:40],
                    "Products": first_row["products"],
                    "Last Demand": last_demand_str,
                    "Excess Qty": total_excess_qty,
                    "Excess Cost": f"${total_excess_cost:,.0f}" if total_excess_cost > 0 else "—",
                    "Suggested Cancellations": suggestions_str,
                })

            report_df = pd.DataFrame(report)

            # Add Notes column showing preview
            def get_notes_preview_excess(part):
                notes = load_notes(part)
                if not notes:
                    return ""
                first_note = notes[0]["note"][:60]
                count_str = f" (+{len(notes)-1})" if len(notes) > 1 else ""
                return f"📝 {first_note}...{count_str}" if len(notes[0]["note"]) > 60 else f"📝 {first_note}{count_str}"

            report_df["Notes"] = report_df["Part"].apply(get_notes_preview_excess)

            col_order = [
                "CM", "Part", "Description", "Products", "Last Demand",
                "Excess Qty", "Excess Cost", "Suggested Cancellations", "Notes"
            ]
            report_df = report_df[[c for c in col_order if c in report_df.columns]]

            st.dataframe(report_df, use_container_width=True, height=500)

            # Inline part management
            st.divider()
            selected_excess_part = st.selectbox(
                "Manage part (click 🏷️ to exclude or ➕ to add note):",
                options=["—"] + sorted(report_df["Part"].unique()),
                key="manage_excess_part_select",
                label_visibility="collapsed"
            )

            if selected_excess_part != "—":
                col_exclude, col_note = st.columns(2)

                with col_exclude:
                    if st.button("🏷️ Exclude", key="exclude_btn_excess", use_container_width=True):
                        st.session_state.excess_action = "exclude"
                        st.session_state.excess_part = selected_excess_part

                with col_note:
                    if st.button("➕ Add Note", key="note_btn_excess", use_container_width=True):
                        st.session_state.excess_action = "note"
                        st.session_state.excess_part = selected_excess_part

                # Show form based on selected action
                if st.session_state.get("excess_part") == selected_excess_part:
                    if st.session_state.get("excess_action") == "exclude":
                        st.warning(f"**Exclude {selected_excess_part}?**")
                        reason = st.text_input("Reason (e.g., printed labels, not tracked):", key="exclude_reason_excess")
                        col_confirm, col_cancel = st.columns(2)
                        with col_confirm:
                            if st.button("Confirm Exclude", key="confirm_exclude_excess", use_container_width=True):
                                if reason:
                                    exclude_part(selected_excess_part, reason)
                                    st.session_state.pop("excess_action", None)
                                    st.session_state.pop("excess_part", None)
                                    st.rerun()
                                else:
                                    st.error("Please provide a reason")
                        with col_cancel:
                            if st.button("Cancel", key="cancel_exclude_excess", use_container_width=True):
                                st.session_state.pop("excess_action", None)
                                st.session_state.pop("excess_part", None)
                                st.rerun()

                    elif st.session_state.get("excess_action") == "note":
                        st.info(f"**Add note to {selected_excess_part}**")
                        note_text = st.text_area("Note:", key="note_text_excess", height=80)
                        col_confirm, col_cancel = st.columns(2)
                        with col_confirm:
                            if st.button("Add Note", key="confirm_note_excess", use_container_width=True):
                                if note_text:
                                    add_note(selected_excess_part, note_text)
                                    st.session_state.pop("excess_action", None)
                                    st.session_state.pop("excess_part", None)
                                    st.rerun()
                                else:
                                    st.error("Please enter a note")
                        with col_cancel:
                            if st.button("Cancel", key="cancel_note_excess", use_container_width=True):
                                st.session_state.pop("excess_action", None)
                                st.session_state.pop("excess_part", None)
                                st.rerun()

            st.write(f"**Total: {len(report)} parts with excess supply**")

            # Detail view: show all excess onorder lines
            st.subheader("Detailed Excess Lines")
            st.caption("All onorder lines flagged for cancellation")

            detail_df = excess_filtered[[
                "cm", "part", "description", "receipt_date", "po_number", "po_line_item",
                "quantity_open", "qty_to_cancel", "unit_price", "cost_to_save"
            ]].copy()

            detail_df.columns = [
                "CM", "Part", "Description", "Arrival", "PO", "Line",
                "Qty Open", "Qty Cancel", "Unit Price", "Cost to Save"
            ]

            detail_df["Arrival"] = detail_df["Arrival"].dt.strftime("%Y-%m-%d")
            detail_df["Unit Price"] = detail_df["Unit Price"].apply(
                lambda x: f"${x:.2f}" if pd.notna(x) and x > 0 else "—"
            )
            detail_df["Cost to Save"] = detail_df["Cost to Save"].apply(
                lambda x: f"${x:,.0f}" if pd.notna(x) and x > 0 else "—"
            )

            st.dataframe(detail_df, use_container_width=True, height=300)

# ============================================================================
# INVENTORY DEPLETION
# ============================================================================
elif st.session_state.active_tab == "Inventory Projection":
    # Initialize session state for inventory source if not present
    if "inventory_source_selection" not in st.session_state:
        st.session_state.inventory_source_selection = "On Hand + On Order"

    try:
        # Get inventory and BOM data from frames
        onhand_raw = frames.get("onhand.csv", pd.DataFrame())
        onorder_raw = frames.get("onorder.csv", pd.DataFrame())
        bom = frames.get("bom_stitched.csv", pd.DataFrame())
        stitch_list = frames.get("stitch_list.csv", pd.DataFrame())

        if len(onhand_raw) == 0 or len(bom) == 0:
            st.warning("Inventory or BOM data not loaded. Please upload files first.")
        else:
            # Normalize inventory data for depletion analysis
            onhand = nz.normalize_onhand(onhand_raw)
            onorder = nz.normalize_onorder(onorder_raw)

            # OPTIMIZATION: Filter data EARLY before expensive calculations
            # Only process parts that match the selected filter
            if part_filter:
                onhand_filtered = onhand[onhand["lpn"].isin(part_filter)]
                onorder_filtered = onorder[onorder["lunar_lpn"].isin(part_filter)]
                pab_filtered = pab[pab["part"].isin(part_filter)]
            else:
                onhand_filtered = onhand
                onorder_filtered = onorder
                pab_filtered = pab

            # Cache with inventory_source as part of key by using hash
            # Cache the function with inventory_source_key in the name to force cache differentiation
            @st.cache_data(show_spinner=False, ttl=None)
            def _compute_inventory_depletion_base(_pab, _onhand, _onorder, cache_bust_key=None):
                """Compute balance table once and cache it.

                cache_bust_key (no leading underscore, so Streamlit DOES hash it) must
                capture everything that should invalidate this cache: the inventory
                source toggle, plus a version marker for the pricing/column-structure
                logic in get_unit_prices_with_source(). Previously this was named
                _cache_bust_key - the underscore told Streamlit to skip hashing it
                entirely, so the cache silently never invalidated on inventory-source
                changes, and never picked up edits to get_unit_prices_with_source()
                (a separate function this one calls, whose own source Streamlit does
                not track). Bump PRICING_LOGIC_VERSION whenever that function's pricing
                rules change, so a code change forces recomputation even though _pab/
                _onhand/_onorder are unchanged and unhashed.
                """
                # --- CM name normalization ---
                # Create mapping from full CM names (from inventory) to short CM names (from engine/stitch_list)
                # Examples: "Sienna GA" → "Sienna", "Qualitel WA" → "Qualitel", "Plexus" → "Plexus"
                cm_name_map = {
                    "Sienna GA": "Sienna",
                    "Qualitel WA": "Qualitel",
                    "Plexus": "Plexus",
                    "Unigen": "Unigen",
                    "Celestica MX": "Celestica",
                    "Lunar": "Lunar"  # Keep Lunar as-is
                }

                # --- CM PAB by month (end-of-month snapshot) ---
                pab_monthly = _pab.copy()
                pab_monthly["period_date"] = pd.to_datetime(pab_monthly["period"])
                pab_monthly["month"] = pab_monthly["period_date"].dt.to_period("M")

                # Get end-of-month PAB for each (cm, part, month)
                pab_eom = pab_monthly.loc[pab_monthly.groupby(["cm", "part", "month"])["period_date"].idxmax()]
                pab_eom["month_str"] = pab_eom["month"].astype(str)

                # Note: PAB calculation already done by engine
                # When "On Hand Only" is selected, we zero out on-order columns later
                # This effectively removes on-order supply from the display

                # Pivot: rows = (cm, part), columns = months
                cm_pab = pab_eom.pivot_table(
                    index=["cm", "part"],
                    columns="month_str",
                    values="pab",
                    aggfunc="first"
                ).reset_index()

                # Build comprehensive part universe from all inventory sources
                # This ensures we capture parts with demand AND parts with inventory but no demand
                all_parts = set()

                # Add all parts from on-hand
                if "lpn" in _onhand.columns:
                    onhand_parts = _onhand[_onhand["lpn"].notna()]["lpn"].unique()
                    all_parts.update(onhand_parts)

                # Add all parts from on-order
                if "lunar_lpn" in _onorder.columns:
                    onorder_parts = _onorder[_onorder["lunar_lpn"].notna()]["lunar_lpn"].unique()
                    all_parts.update(onorder_parts)

                # Build comprehensive base table with all parts, all CMs from on-hand
                cm_universe = set()
                if "source_report" in _onhand.columns:
                    for source in _onhand["source_report"].unique():
                        if source and source != "Lunar Netsuite":
                            cm = source.split(":")[1].strip() if ":" in source else None
                            if cm:
                                cm_universe.add(cm)

                # Create base table with all (cm, part) combinations that appear in inventory
                all_cm_part = []
                for part in all_parts:
                    # Get CMs that have this part in on-hand or on-order
                    cms_for_part = set()

                    # Check on-hand
                    if "lpn" in _onhand.columns and "source_report" in _onhand.columns:
                        part_oh = _onhand[_onhand["lpn"] == part]
                        for source in part_oh["source_report"].unique():
                            if source != "Lunar Netsuite":
                                cm = source.split(":")[1].strip() if ":" in source else None
                                if cm:
                                    cms_for_part.add(cm)

                    # Check on-order
                    if "lunar_lpn" in _onorder.columns and "source_report" in _onorder.columns:
                        part_oo = _onorder[_onorder["lunar_lpn"] == part]
                        for source in part_oo["source_report"].unique():
                            if source != "Lunar Netsuite":
                                cm = source.split(":")[1].strip() if ":" in source else None
                                if cm:
                                    cms_for_part.add(cm)

                    # Add Lunar as a "CM" for tracking Lunar inventory
                    cms_for_part.add("Lunar")

                    for cm in cms_for_part:
                        all_cm_part.append({"cm": cm, "part": part})

                base_table = pd.DataFrame(all_cm_part)

                # Normalize CM names in base_table to match engine PAB CM names
                base_table["cm"] = base_table["cm"].map(cm_name_map).fillna(base_table["cm"])

                # base_table so far is built purely from on-hand/on-order INVENTORY
                # presence per CM. That misses any (cm, part) the engine generated
                # real demand for but which has no physical CM inventory record yet —
                # exactly the case for a part just flipped make->buy in a BOM override
                # (10-07946A: Qualitel now owes demand for it, but Qualitel has never
                # received a unit, so no on-hand/on-order row exists to seed the CM
                # universe). Union in every (cm, part) the engine's own PAB output
                # knows about so a left-join below can't silently drop it.
                if len(cm_pab) > 0:
                    engine_cm_part = cm_pab[["cm", "part"]].drop_duplicates()
                    base_table = pd.concat(
                        [base_table, engine_cm_part], ignore_index=True
                    ).drop_duplicates(subset=["cm", "part"])

                # Left-join PAB data (parts without demand will have NaN in PAB columns)
                cm_pab_full = base_table.merge(cm_pab, on=["cm", "part"], how="left")

                # --- Lunar depletion calculation ---
                # Get Lunar inventory position
                # 1. Lunar unrestricted_qty (on-hand inventory)
                lunar_oh = onhand[onhand["source_report"] == "Lunar Netsuite"].copy()

                # VALIDATION: Log parts with multiple rows (different locations)
                lunar_dups = lunar_oh.groupby("lpn").size()
                if (lunar_dups > 1).any():
                    dup_parts = lunar_dups[lunar_dups > 1]
                    for part in dup_parts.index:
                        part_data = lunar_oh[lunar_oh["lpn"] == part][["lpn", "location", "unrestricted_qty"]]
                        total = part_data["unrestricted_qty"].astype(float).sum()
                        log.info(f"Lunar {part}: {len(part_data)} rows across locations, total={total:.0f} units")

                lunar_unrestricted = lunar_oh.groupby("lpn").agg(
                    unrestricted=("unrestricted_qty", "sum")
                ).rename_axis("part").reset_index()

                # Capture the part -> Lunar on-hand qty map NOW. The scenario loop below
                # rebinds `lunar_unrestricted` to a scalar (lunar_data["unrestricted"]
                # .values[0]), so by the time the depletion model runs it is an int, not
                # this frame. Reading it there silently yielded an empty pool and drove
                # every B3 balance to zero.
                LUNAR_ONHAND_QTY = dict(
                    zip(lunar_unrestricted["part"], lunar_unrestricted["unrestricted"])
                )

                # 2. CM orders placed against Lunar (where Lunar is the vendor)
                # Filter: po_vendor contains "Lunar" and extract CM from source_report
                cm_orders_lunar = _onorder[_onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
                cm_orders_lunar["cm_extracted"] = cm_orders_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

                cm_orders_by_cm_part = cm_orders_lunar.groupby(["cm_extracted", "lunar_lpn"]).agg(
                    cm_orders=("quantity_open", "sum")
                ).reset_index()
                cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

                # 3. Calculate uncommitted per part
                total_cm_orders_by_part = cm_orders_by_cm_part.groupby("part")["cm_orders"].sum().reset_index()
                total_cm_orders_by_part.columns = ["part", "total_cm_orders"]

                lunar_start = lunar_unrestricted.merge(total_cm_orders_by_part, on="part", how="left")
                lunar_start["total_cm_orders"] = lunar_start["total_cm_orders"].fillna(0)
                lunar_start["uncommitted"] = lunar_start["unrestricted"] - lunar_start["total_cm_orders"]
                lunar_start["uncommitted"] = lunar_start["uncommitted"].clip(lower=0)  # Floor at 0

                # Get Lunar's own on-order (source_report = "Lunar Netsuite") for supply replenishment
                lunar_oo = _onorder[_onorder["source_report"] == "Lunar Netsuite"].copy()

                # Prepare dated orders for later depletion calculation
                # CM orders to Lunar with receipt/ship dates
                cm_orders_lunar_dated = cm_orders_lunar.copy()
                cm_orders_lunar_dated["eta"] = cm_orders_lunar_dated["receipt_date"].fillna(cm_orders_lunar_dated["ship_date"])
                cm_orders_lunar_dated = cm_orders_lunar_dated[cm_orders_lunar_dated["eta"].notna()].copy()
                cm_orders_lunar_dated["eta"] = pd.to_datetime(cm_orders_lunar_dated["eta"])
                cm_orders_lunar_dated["eta_month"] = cm_orders_lunar_dated["eta"].dt.to_period("M")
                # Normalize CM names to match balance_table (e.g., "Sienna GA" → "Sienna")
                cm_orders_lunar_dated["cm_extracted"] = cm_orders_lunar_dated["cm_extracted"].map(cm_name_map).fillna(cm_orders_lunar_dated["cm_extracted"])

                # Lunar's own on-order with receipt/ship dates
                lunar_oo_dated = lunar_oo[lunar_oo["receipt_date"].notna() | lunar_oo["ship_date"].notna()].copy()
                lunar_oo_dated["eta"] = lunar_oo_dated["receipt_date"].fillna(lunar_oo_dated["ship_date"])
                lunar_oo_dated["eta"] = pd.to_datetime(lunar_oo_dated["eta"])
                lunar_oo_dated["eta_month"] = lunar_oo_dated["eta"].dt.to_period("M")

                # Calculate monthly Lunar depletion for each (CM, part)
                # This will be calculated AFTER allocations so we can use the allocated amounts
                # For now, create placeholder
                lunar_depletion_temp = pd.DataFrame()

                # Add inventory columns to comprehensive base table
                # Get item_category and description from onhand
                item_cat = _onhand[["lpn", "item_category", "description"]].drop_duplicates().rename(columns={"lpn": "part"})
                balance_table = cm_pab_full.merge(item_cat, on="part", how="left")

                # Fill missing categories/descriptions from BOM (for NPI parts not yet in inventory)
                bom_cat = bom[["item_number", "category_name", "item_name"]].drop_duplicates().rename(columns={"item_number": "part", "category_name": "item_category", "item_name": "description"})
                balance_table["item_category"] = balance_table["item_category"].fillna(balance_table["part"].map(bom_cat.set_index("part")["item_category"]))
                balance_table["description"] = balance_table["description"].fillna(balance_table["part"].map(bom_cat.set_index("part")["description"]))

                # Extract CM from source_report for on-hand data (extract full name like "Sienna GA")
                onhand_with_cm = onhand[onhand["source_report"] != "Lunar Netsuite"].copy()
                onhand_with_cm["cm"] = onhand_with_cm["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

                # Get CM on-hand (without unit price for now)
                cm_oh = onhand_with_cm.groupby(["cm", "lpn"]).agg(
                    cm_on_hand=("unrestricted_qty", "sum")
                ).reset_index()
                cm_oh.columns = ["cm", "part", "cm_on_hand"]

                # Normalize CM names in cm_oh to match balance_table
                cm_oh["cm"] = cm_oh["cm"].map(cm_name_map).fillna(cm_oh["cm"])

                # Extract CM from source_report for on-order data (extract full name like "Sienna GA")
                onorder_with_cm = _onorder[_onorder["source_report"] != "Lunar Netsuite"].copy()
                onorder_with_cm["cm"] = onorder_with_cm["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

                # Get CM on-order
                cm_oo = onorder_with_cm.groupby(["cm", "lunar_lpn"]).agg(
                    cm_on_order=("quantity_open", "sum")
                ).reset_index()
                cm_oo.columns = ["cm", "part", "cm_on_order"]

                # Normalize CM names in cm_oo to match balance_table
                cm_oo["cm"] = cm_oo["cm"].map(cm_name_map).fillna(cm_oo["cm"])

                balance_table = balance_table.merge(cm_oh, on=["cm", "part"], how="left")

                # Merge CM on-order data
                balance_table = balance_table.merge(cm_oo, on=["cm", "part"], how="left")
                balance_table["cm_on_order"] = balance_table["cm_on_order"].fillna(0)

                # Get Lunar on-order by part (source_report = "Lunar Netsuite")
                lunar_oo = _onorder[_onorder["source_report"] == "Lunar Netsuite"].copy()
                lunar_oo_by_part = lunar_oo.groupby("lunar_lpn").agg(
                    on_order=("quantity_open", "sum")
                ).reset_index()
                lunar_oo_by_part.columns = ["part", "lunar_on_order_total"]

                # ========== 3-SCENARIO LUNAR ALLOCATION LOGIC ==========
                # Stage 1 (always first): Identify CM POs to Lunar and allocate those
                # Then detect scenario and allocate Lunar inventory accordingly

                # Extract CM orders placed TO Lunar (po_vendor contains "Lunar")
                cm_orders_to_lunar = _onorder[_onorder["po_vendor"].str.contains("Lunar", case=False, na=False)].copy()
                # Extract full CM name like "Sienna GA" from "CM: Sienna GA"
                cm_orders_to_lunar["cm"] = cm_orders_to_lunar["source_report"].str.extract(r"CM:\s*(.+)$", expand=False).str.strip()

                # Normalize CM names to match balance_table (e.g., "Sienna GA" → "Sienna")
                cm_orders_to_lunar["cm"] = cm_orders_to_lunar["cm"].map(cm_name_map).fillna(cm_orders_to_lunar["cm"])

                # For Stage 1 allocation: SUM all schedule lines
                # Schedule lines with different receipt dates are cumulative supply, not duplicates
                # DO NOT deduplicate - let groupby sum all rows for the same (cm, part)
                cm_orders_by_cm_part = cm_orders_to_lunar.groupby(["cm", "lunar_lpn"]).agg(
                    cm_orders=("quantity_open", "sum")
                ).reset_index()
                cm_orders_by_cm_part.columns = ["cm", "part", "cm_orders"]

                # Pre-compute CM worst PAB by (cm, part) for shortage detection
                pab_copy = pab.copy()
                pab_copy["period_date"] = pd.to_datetime(pab_copy["period"])
                pab_copy["month"] = pab_copy["period_date"].dt.to_period("M")
                cm_part_worst_pab = (
                    pab_copy.groupby(["cm", "part"])["pab"].min().reset_index()
                ).rename(columns={"pab": "worst_pab"})

                # Calculate stage 1 allocations and shortages per part
                stage1_by_part = {}  # {part: {cm: qty}}
                total_shortages_by_part = {}  # {part: {cm: shortage_qty}}

                for part in balance_table["part"].unique():
                    stage1_by_part[part] = {}
                    total_shortages_by_part[part] = {}

                    part_rows = balance_table[balance_table["part"] == part]
                    lunar_data = lunar_start[lunar_start["part"] == part]

                    if len(lunar_data) > 0:
                        lunar_unrestricted = lunar_data["unrestricted"].values[0]
                        uncommitted = lunar_data["uncommitted"].values[0]
                    else:
                        lunar_unrestricted = 0
                        uncommitted = 0

                    # Stage 1: Allocate CM POs to Lunar to specific CMs
                    # Cap each CM's allocation to what Lunar actually has available (sequential allocation)
                    stage1_total_by_part = 0
                    remaining_lunar_inventory = lunar_unrestricted

                    for _, row in part_rows.iterrows():
                        cm = row["cm"]
                        if cm == "Lunar":
                            continue

                        # Get this CM's orders from Lunar (po_vendor = "Lunar")
                        cm_lunar_orders = cm_orders_by_cm_part[
                            (cm_orders_by_cm_part["cm"] == cm) &
                            (cm_orders_by_cm_part["part"] == part)
                        ]
                        cm_orders = cm_lunar_orders["cm_orders"].values[0] if len(cm_lunar_orders) > 0 else 0

                        # Allocate MIN(requested, remaining available)
                        allocated = min(cm_orders, remaining_lunar_inventory)
                        stage1_by_part[part][cm] = allocated
                        stage1_total_by_part += allocated
                        remaining_lunar_inventory -= allocated

                    # Calculate shortage per CM (worst PAB for this part)
                    for _, row in part_rows.iterrows():
                        cm = row["cm"]
                        if cm == "Lunar":
                            continue

                        pab_match = cm_part_worst_pab[
                            (cm_part_worst_pab["cm"] == cm) &
                            (cm_part_worst_pab["part"] == part)
                        ]
                        worst_pab = pab_match["worst_pab"].values[0] if len(pab_match) > 0 else 0

                        if worst_pab < 0:
                            shortage = abs(worst_pab)
                        else:
                            shortage = 0
                        total_shortages_by_part[part][cm] = shortage

                    # NOTE: Old scenario-based allocation logic removed.
                    # The B1/B2/B3 model (below) now handles all Lunar allocation.

                # ============================================================
                # INVENTORY DEPLETION MODEL
                # ============================================================
                # CM Depletion (the plain month columns, already month-end PAB
                #   from the engine = opening(raw + WIP) + receipts - BOM demand).
                #   Only fix here: a (cm, part) with inventory but NO demand has no
                #   PAB rows, left-joins to NaN, and previously fell to 0 - i.e. real
                #   CM stock silently vanished from the projection. Those now hold
                #   flat at their opening balance.
                #
                # Lunar Depletion - Lunar's book split into three buckets per part:
                #   B1  committed against CM->Lunar POs  -> row cm=<CM>,  depletes on PO ETA
                #   B2  covering residual CM shortage    -> row cm=<CM>,  depletes as the
                #                                           shortage actually lands
                #   B3  free / uncommitted remainder     -> row cm="Lunar", stays flat
                #   Lunar chart = B1 + B2 + B3.
                #
                # Lunar on-order is phased in by ETA month (you cannot ship a CM
                # material that has not arrived). Allocation is B1 first, then B2,
                # pro-rata within a bucket when the pool cannot cover it.
                #
                # Replaces a model that depleted against *open POs only*. Open POs
                # are a rolling ~4-month book (4.1M units Jan-27 -> ~0 by May-27), so
                # once it ran dry the subtrahend stopped growing and every line went
                # flat by construction - recognising <10% of real consumption while
                # the build plan ramps 4.7x. Demand now drives depletion.
                # ============================================================
                static_cols_exclude = ["cm", "part", "description", "item_category", "cm_on_hand", "cm_on_order", "cm_unit_price", "cm_unit_price_source", "lunar_on_hand_alloc", "lunar_on_order_alloc", "lunar_unit_price", "obsolescence_state"]
                months = sorted([col for col in balance_table.columns if col not in static_cols_exclude and not col.startswith("Lunar_")])
                month_periods = [pd.Period(m, freq="M") for m in months]

                # --- month-end PAB: drives CM depletion and the shortage B2 covers ---
                _pab_with_month = _pab.copy()
                _pab_with_month["period_date"] = pd.to_datetime(_pab_with_month["period"])
                _pab_with_month["month"] = _pab_with_month["period_date"].dt.to_period("M")
                _pab_eom = _pab_with_month.loc[_pab_with_month.groupby(["cm", "part", "month"])["period_date"].idxmax()]
                cm_endpab = dict(
                    zip(zip(_pab_eom["cm"], _pab_eom["part"], _pab_eom["month"]), _pab_eom["pab"])
                )

                # Opening (raw + WIP) so no-demand parts hold flat instead of vanishing
                opening_lookup = {}
                _op = result.get("opening")
                if _op is not None and len(_op) > 0:
                    opening_lookup = dict(zip(zip(_op["cm"], _op["part"]), _op["opening"]))

                def _sum_lookup(df, keys):
                    """groupby(keys)['quantity_open'].sum() as a plain dict."""
                    if df is None or len(df) == 0:
                        return {}
                    return df.groupby(keys)["quantity_open"].sum().to_dict()

                lunar_receipt_lookup = _sum_lookup(lunar_oo_dated, ["lunar_lpn", "eta_month"])
                cm_po_lookup = _sum_lookup(cm_orders_lunar_dated, ["cm_extracted", "lunar_lpn", "eta_month"])

                # Past-due supply/commitments: anything with an ETA before the window is
                # available (or already gone) as of period 1, not absent. Dropping these
                # silently deleted most of Lunar's $42M on-order book from the pool.
                first_month = month_periods[0] if month_periods else None
                past_lunar_lookup, past_cm_po_lookup = {}, {}
                if first_month is not None:
                    if lunar_oo_dated is not None and len(lunar_oo_dated) > 0:
                        past_lunar_lookup = _sum_lookup(
                            lunar_oo_dated[lunar_oo_dated["eta_month"] < first_month], ["lunar_lpn"]
                        )
                    if cm_orders_lunar_dated is not None and len(cm_orders_lunar_dated) > 0:
                        past_cm_po_lookup = _sum_lookup(
                            cm_orders_lunar_dated[cm_orders_lunar_dated["eta_month"] < first_month],
                            ["cm_extracted", "lunar_lpn"],
                        )

                # Built before the scenario loop shadowed `lunar_unrestricted` (see above).
                lunar_oh_lookup = LUNAR_ONHAND_QTY
                if not lunar_oh_lookup:
                    log.error("Lunar on-hand lookup is EMPTY - every Lunar balance will be zero")

                cm_arr = balance_table["cm"].tolist()
                part_arr = balance_table["part"].tolist()
                n_rows = len(balance_table)
                cm_cols = {m: [None] * n_rows for m in months}  # None = keep existing PAB value
                lunar_cols = {m: [0.0] * n_rows for m in months}
                lunar_bucket_total = [0.0] * n_rows

                rows_by_part = {}
                for i, (cm, part) in enumerate(zip(cm_arr, part_arr)):
                    rows_by_part.setdefault(part, []).append((i, cm))

                # Composition of each part's Lunar pool, captured here because only this
                # loop knows it. The pool is on-hand PLUS receipts; the allocation columns
                # must therefore split a row's allocation between those two sources rather
                # than reporting the pool as on-hand and the receipts a second time on top.
                pool_mix = {}  # part -> (pool_total, pool_onhand_component)

                for part, rows in rows_by_part.items():
                    cms = [cm for _, cm in rows if cm != "Lunar"]

                    # ---- Lunar supply, TIME-PHASED ----
                    # `seed` is what Lunar can actually touch in period 1: on-hand plus
                    # receipts whose ETA has already passed. Past-due CM POs have already
                    # shipped, so those units have left Lunar's book and net out here.
                    seed = float(lunar_oh_lookup.get(part, 0.0))
                    seed += float(past_lunar_lookup.get(part, 0.0))
                    for cm in cms:
                        seed -= float(past_cm_po_lookup.get((cm, part), 0.0))
                    seed = max(0.0, seed)

                    # `inflow[mp]` lands IN month mp and is not available before it.
                    # The previous model summed every month's receipts into one scalar
                    # available from period 1, so a December PO funded an August
                    # commitment and the Aug-26 opening position carried the entire
                    # forward on-order book. That is the bug this replaces.
                    inflow = {
                        mp: float(lunar_receipt_lookup.get((part, mp), 0.0))
                        for mp in month_periods
                    }

                    # Pool total is still the whole horizon — it drives the on-hand /
                    # on-order price split further down, which is a mix question, not a
                    # timing one.
                    lunar_pool = seed + sum(inflow.values())
                    _oh_component = min(float(lunar_oh_lookup.get(part, 0.0)), lunar_pool)
                    pool_mix[part] = (lunar_pool, _oh_component)

                    # ---- B1 demand: Lunar stock committed against CM -> Lunar POs ----
                    b1_sched = {
                        cm: {mp: float(cm_po_lookup.get((cm, part, mp), 0.0)) for mp in month_periods}
                        for cm in cms
                    }

                    # ---- B2 demand: Lunar stock covering the CM's residual shortage ----
                    # Per-month increment of the running worst shortage, so the draw
                    # on Lunar happens in the month the shortage actually appears.
                    short_inc = {}
                    for cm in cms:
                        worst, inc = 0.0, {}
                        for mp in month_periods:
                            v = cm_endpab.get((cm, part, mp))
                            sh = -float(v) if (v is not None and pd.notna(v) and v < 0) else 0.0
                            inc[mp] = max(0.0, sh - worst)
                            worst = max(worst, sh)
                        short_inc[cm] = inc

                    # ---- Chronological allocation against running availability ----
                    # Walk the horizon in order. Only stock that has arrived by month mp
                    # can be drawn in mp; B1 (firm PO commitments) outranks B2 (shortage
                    # cover) within a month, and a shortfall scales pro-rata across CMs.
                    avail = seed
                    b1_drawn = {cm: {} for cm in cms}
                    b2_drawn = {cm: {} for cm in cms}
                    for mp in month_periods:
                        avail += inflow[mp]
                        for want_src, drawn in ((b1_sched, b1_drawn), (short_inc, b2_drawn)):
                            want = {cm: want_src[cm].get(mp, 0.0) for cm in cms}
                            tot = sum(want.values())
                            if tot <= avail:
                                got, avail = dict(want), avail - tot
                            else:
                                sc = (avail / tot) if tot > 0 else 0.0
                                got, avail = {cm: want[cm] * sc for cm in cms}, 0.0
                            for cm in cms:
                                drawn[cm][mp] = got[cm]

                    # ---- Month-end position actually sitting on Lunar's book ----
                    # bal(t) = seed + receipts through t - draws through t. Attributed to
                    # each CM as the earmark it has not yet taken delivery of (nearest
                    # commitment first), with whatever is left over free on Lunar's book.
                    earmark_by_month, free_by_month = {}, {}
                    cum_in, cum_out = seed, 0.0
                    for t_idx, mp in enumerate(month_periods):
                        cum_in += inflow[mp]
                        cum_out += sum(b1_drawn[cm][mp] + b2_drawn[cm][mp] for cm in cms)
                        rem = max(0.0, cum_in - cum_out)
                        res = {cm: 0.0 for cm in cms}
                        for stage in (b1_drawn, b2_drawn):
                            for fmp in month_periods[t_idx + 1:]:
                                if rem <= 0:
                                    break
                                for cm in cms:
                                    take = min(rem, stage[cm].get(fmp, 0.0))
                                    res[cm] += take
                                    rem -= take
                                    if rem <= 0:
                                        break
                        earmark_by_month[mp] = res
                        free_by_month[mp] = rem

                    for i, cm in rows:
                        if cm == "Lunar":
                            # B3: free remainder. No longer flat — it starts at whatever
                            # is unspoken-for out of `seed` and steps up as receipts land.
                            for m, mp in zip(months, month_periods):
                                lunar_cols[m][i] = free_by_month[mp]
                                cm_cols[m][i] = 0.0
                            lunar_bucket_total[i] = (
                                free_by_month[month_periods[-1]] if month_periods else 0.0
                            )
                            continue

                        # ---- CM depletion: month-end PAB, floored; flat if no demand ----
                        has_demand = any((cm, part, mp) in cm_endpab for mp in month_periods)
                        if not has_demand:
                            flat = max(0.0, float(opening_lookup.get((cm, part), 0.0)))
                            for m in months:
                                cm_cols[m][i] = flat
                        # else: leave the existing signed PAB month columns untouched —
                        # the shortage report and drill-down read them as signed.

                        # ---- Lunar depletion: earmark Lunar physically holds at t ----
                        # Only stock that has arrived can be earmarked, so this now
                        # starts from the seed position and rises with receipts instead
                        # of opening at the full horizon commitment.
                        lunar_bucket_total[i] = (
                            sum(b1_drawn[cm].values()) + sum(b2_drawn[cm].values())
                        )
                        for m, mp in zip(months, month_periods):
                            lunar_cols[m][i] = earmark_by_month[mp].get(cm, 0.0)

                balance_table = balance_table.reset_index(drop=True)

                # Write back only the CM cells we overrode (no-demand parts held flat)
                for m in months:
                    col = cm_cols[m]
                    if any(v is not None for v in col):
                        base = balance_table[m].tolist()
                        balance_table[m] = [
                            (base[i] if col[i] is None else col[i]) for i in range(n_rows)
                        ]

                # Split each row's bucket total into its on-hand and on-order components.
                #
                # `lunar_bucket_total` is a slice of the Lunar POOL, and the pool is
                # on-hand + receipts. Assigning it wholesale to lunar_on_hand_alloc while
                # lunar_on_order_alloc separately carried the receipts counted the receipts
                # twice: 10-003933 showed on_hand 191,952 (= 1,872 on-hand + 190,080
                # receipts) alongside on_order 190,080, so the price denominator was
                # 382,032 instead of 191,952 and the blended price came out at half the
                # true figure.
                #
                # Split in the pool's own proportions. Every row of a part draws from the
                # same pool, so one ratio applies to all of them and the components sum
                # back to the bucket total — no row can claim more on-hand than exists.
                _oh_alloc, _oo_alloc = [0.0] * n_rows, [0.0] * n_rows
                for _i, (_cm, _part) in enumerate(zip(cm_arr, part_arr)):
                    _alloc = float(lunar_bucket_total[_i])
                    _pool, _pool_oh = pool_mix.get(_part, (0.0, 0.0))
                    if _pool > 0:
                        _frac = _pool_oh / _pool
                        _oh_alloc[_i] = _alloc * _frac
                        _oo_alloc[_i] = _alloc - _oh_alloc[_i]
                    else:
                        _oh_alloc[_i] = _alloc
                        _oo_alloc[_i] = 0.0

                balance_table["lunar_on_hand_alloc"] = _oh_alloc
                balance_table["lunar_on_order_alloc"] = _oo_alloc

                balance_table = pd.concat(
                    [
                        balance_table,
                        pd.DataFrame({f"Lunar_balance_{m}": v for m, v in lunar_cols.items()}),
                    ],
                    axis=1,
                )

                # Add unit prices and source
                cost_frames = load_cost_frames()  # Load EE and ME cost databases
                cm_prices, lunar_prices, cm_sources, lunar_onhand_prices, lunar_onorder_prices, cm_onhand_prices, cm_onorder_prices = get_unit_prices_with_source(_onhand, _onorder, cost_frames)

                # Blended CM price: Cost DB first, else weighted avg of on-hand/on-order.
                # Used for the CM Projection balance calc (per row's "when row is CM
                # depletion" spec) - this is cm_unit_price / cm_unit_price_source below.
                balance_table["cm_unit_price"] = balance_table["part"].map(lambda p: cm_prices.get(p, (0, ""))[0])
                balance_table["cm_unit_price_source"] = balance_table["part"].map(lambda p: cm_prices.get(p, (0, ""))[1])

                # CM prices split by bucket (mirrors the Lunar on-hand/on-order split
                # below), so cm_on_hand_extended_cost / cm_on_order_extended_cost each
                # use the price that actually applies to that bucket, not the blended one.
                balance_table["cm_on_hand_unit_price"] = balance_table["part"].map(lambda p: cm_onhand_prices.get(p, 0))
                balance_table["cm_on_order_unit_price"] = balance_table["part"].map(lambda p: cm_onorder_prices.get(p, 0))

                # Lunar prices: separate on-hand and on-order
                balance_table["lunar_unit_price_onhand"] = balance_table["part"].map(lambda p: lunar_onhand_prices.get(p, 0))
                balance_table["lunar_unit_price_onorder"] = balance_table["part"].map(lambda p: lunar_onorder_prices.get(p, 0))

                # Blended price for depletion (weighted average of on-hand and on-order)
                balance_table["lunar_unit_price"] = balance_table.apply(
                    lambda row: (
                        (row.get("lunar_on_hand_alloc", 0) * row["lunar_unit_price_onhand"] +
                         row.get("lunar_on_order_alloc", 0) * row["lunar_unit_price_onorder"]) /
                        (row.get("lunar_on_hand_alloc", 0) + row.get("lunar_on_order_alloc", 0))
                    ) if (row.get("lunar_on_hand_alloc", 0) + row.get("lunar_on_order_alloc", 0)) > 0 else 0,
                    axis=1
                )

                # ========== VALIDATION TEST: Lunar allocation value sumproduct ==========
                try:
                    # Calculate sumproduct of (Lunar on-hand allocation qty × Lunar unit price)
                    lunar_rows = balance_table[balance_table["cm"] == "Lunar"].copy()
                    lunar_rows["allocation_value"] = lunar_rows["lunar_on_hand_alloc"] * lunar_rows["lunar_unit_price"]

                    total_allocation_value = lunar_rows["allocation_value"].sum()
                    expected_value = 26775885.06
                    variance = abs(total_allocation_value - expected_value)

                    log.info("=" * 80)
                    log.info("VALIDATION TEST: Lunar Allocation Value")
                    log.info(f"Total Lunar allocation value: ${total_allocation_value:,.2f}")
                    log.info(f"Expected value:               ${expected_value:,.2f}")
                    log.info(f"Variance:                     ${variance:,.2f}")

                    if variance > 1.0:
                        log.warning(f"VARIANCE EXCEEDS $1 - Identifying top 5 discrepant parts")

                        # Identify discrepancies per part
                        part_discrepancies = []
                        for part in lunar_rows["part"].unique():
                            part_data = lunar_rows[lunar_rows["part"] == part]
                            part_value = part_data["allocation_value"].sum()

                            # Expected value per part (rough estimate based on on-hand inventory)
                            # For validation, we'll just flag high-value parts
                            if part_value > 100000:  # Parts with > $100k allocation
                                part_discrepancies.append({
                                    "part": part,
                                    "allocation_qty": part_data["lunar_on_hand_alloc"].values[0],
                                    "unit_price": part_data["lunar_unit_price"].values[0],
                                    "allocation_value": part_value
                                })

                        # Sort by value descending and show top 5
                        part_discrepancies.sort(key=lambda x: x["allocation_value"], reverse=True)
                        log.warning("Top 5 parts by allocation value:")
                        for i, disc in enumerate(part_discrepancies[:5], 1):
                            log.warning(
                                f"  {i}. {disc['part']}: qty={disc['allocation_qty']:.0f} × "
                                f"${disc['unit_price']:.2f} = ${disc['allocation_value']:,.2f}"
                            )
                    else:
                        log.info("VALIDATION PASSED: Allocation value within tolerance")

                    log.info("=" * 80)
                except Exception as val_e:
                    log.error(f"Validation test error: {val_e}", exc_info=True)

                # Fill NaNs
                balance_table = balance_table.fillna(0)

                return balance_table

            # Call the cached function with spinner
            # Convert inventory_source to cache key (0 = On Hand Only, 1 = On Hand + On Order)
            # Read from the actual widget key
            selected_inv_source = st.session_state.get("depletion_inventory_source", "On Hand + On Order")
            inventory_source_key = 0 if selected_inv_source == "On Hand Only" else 1

            # FILTER onorder DATA BEFORE CALLING FUNCTION
            onorder_for_calc = onorder_filtered.copy()
            if inventory_source_key == 0:
                # "On Hand Only": zero out ALL on-order quantities
                onorder_for_calc["quantity_open"] = 0

            # PRICING_LOGIC_VERSION: bump this whenever get_unit_prices_with_source()'s
            # rules change (e.g. Stage 2's on-hand+on-order blend, Stage 3's Cost DB loading).
            # It's baked into the cache key below so a logic change forces recomputation
            # even though _pab/_onhand/_onorder are unhashed and inventory_source_key alone
            # wouldn't change.
            PRICING_LOGIC_VERSION = 15  # v15: consolidate BOM override display by product, remove failures list, transpose chart tables

            with st.spinner("Loading inventory projection..."):
                try:
                    balance_table = _compute_inventory_depletion_base(
                        pab_filtered, onhand_filtered, onorder_for_calc,
                        cache_bust_key=f"{inventory_source_key}:v{PRICING_LOGIC_VERSION}"
                    )
                except Exception as e:
                    import traceback
                    st.error(f"Error in depletion function:\n{str(e)}\n\n{traceback.format_exc()}")
                    st.stop()

            if len(balance_table) > 0:
                # balance_table is already in wide format from compute_inventory_depletion
                # Columns: cm, part, description, on_hand, on_order, [period1, period2, ...]

                # DATA AUDIT: Check extended cost discrepancies vs input reports
                # Add Generation/Obsolescence columns
                # Build mapping of products by generation from stitch_list
                gen_1_products = set(stitch_list[stitch_list["Generation Alias"].str.contains("Gen 1", na=False)]["Parent Product LPN"].unique())
                gen_2_products = set(stitch_list[stitch_list["Generation Alias"].str.contains("Gen 2", na=False)]["Parent Product LPN"].unique())

                # Build mapping of parts used in each generation's BOM
                gen_1_parts = set()
                gen_2_parts = set()

                for product in gen_1_products:
                    product_parts = bom_stitched[bom_stitched["Parent Product LPN"] == product]["item_number"].unique()
                    gen_1_parts.update(product_parts)

                for product in gen_2_products:
                    product_parts = bom_stitched[bom_stitched["Parent Product LPN"] == product]["item_number"].unique()
                    gen_2_parts.update(product_parts)

                # Add obsolescence state column to balance_table
                def get_obsolescence_state(part):
                    gen1 = part in gen_1_parts
                    gen2 = part in gen_2_parts
                    if gen1 and gen2:
                        return "Active in Both"
                    elif gen1 and not gen2:
                        return "Gen 1 Only"
                    elif not gen1 and gen2:
                        return "Gen 2 Only"
                    else:
                        return "Obsolete"

                balance_table["obsolescence_state"] = balance_table["part"].apply(get_obsolescence_state)
                _record_timing("Obsolescence state added")

                # Display Lunar allocation validation
                # Apply global filters to balance_table
                filtered_balance = balance_table.copy()
                output_table = pd.DataFrame()  # Initialize to prevent NameError if filters result in empty set
                _record_timing("Balance table copied")

                if cm_filter != "All":
                    filtered_balance = filtered_balance[filtered_balance["cm"] == cm_filter]

                if part_filter:
                    if part_exclude:
                        filtered_balance = filtered_balance[~filtered_balance["part"].isin(part_filter)]
                    else:
                        filtered_balance = filtered_balance[filtered_balance["part"].isin(part_filter)]

                if category_filter:
                    if cat_exclude:
                        filtered_balance = filtered_balance[~filtered_balance["item_category"].isin(category_filter)]
                    else:
                        filtered_balance = filtered_balance[filtered_balance["item_category"].isin(category_filter)]

                if generation_filter:
                    if gen_exclude:
                        filtered_balance = filtered_balance[~filtered_balance["obsolescence_state"].isin(generation_filter)]
                    else:
                        filtered_balance = filtered_balance[filtered_balance["obsolescence_state"].isin(generation_filter)]

                # Display the CM runout and Lunar depletion table
                if len(filtered_balance) > 0:
                    output_table = filtered_balance.copy()

                    # Drop lunar allocation value (used for calculations only)
                    output_table = output_table.drop(columns=["lunar_allocation_value"], errors="ignore")

                    # CRITICAL: Apply "On Hand Only" filter BEFORE calculating extended costs
                    if st.session_state.inventory_source_selection == "On Hand Only":
                        output_table["cm_on_order"] = 0
                        output_table["lunar_on_order_alloc"] = 0

                    # Calculate extended costs for CM inventory (mirrors Lunar logic below,
                    # using the bucket-specific cm_on_hand_unit_price / cm_on_order_unit_price
                    # added in get_unit_prices_with_source, not the blended cm_unit_price)
                    output_table["cm_on_hand_extended_cost"] = (
                        output_table["cm_on_hand"].fillna(0) * output_table["cm_on_hand_unit_price"].fillna(0)
                    ).round(0).astype(int)
                    output_table["cm_on_order_extended_cost"] = (
                        output_table["cm_on_order"].fillna(0) * output_table["cm_on_order_unit_price"].fillna(0)
                    ).round(0).astype(int)
                    _record_timing("Extended costs calculated")

                    # Calculate extended costs for Lunar inventory
                    output_table["lunar_on_hand_extended_cost"] = (
                        output_table["lunar_on_hand_alloc"].fillna(0) * output_table["lunar_unit_price_onhand"].fillna(0)
                    ).round(0).astype(int)
                    output_table["lunar_on_order_extended_cost"] = (
                        output_table["lunar_on_order_alloc"].fillna(0) * output_table["lunar_unit_price_onorder"].fillna(0)
                    ).round(0).astype(int)

                    # Static columns, ordered per spec: identity/classification, then the
                    # full CM block (qty/price/extended per bucket, then blended price +
                    # source), then the full Lunar block, mirroring the same shape.
                    static_cols = [
                        "cm", "part", "description", "item_category", "obsolescence_state",
                        "cm_on_hand", "cm_on_hand_unit_price", "cm_on_hand_extended_cost",
                        "cm_on_order", "cm_on_order_unit_price", "cm_on_order_extended_cost",
                        "cm_unit_price", "cm_unit_price_source",
                        "lunar_on_hand_alloc", "lunar_unit_price_onhand", "lunar_on_hand_extended_cost",
                        "lunar_on_order_alloc", "lunar_unit_price_onorder", "lunar_on_order_extended_cost",
                        "lunar_unit_price"
                    ]

                    # Separate CM and Lunar month columns
                    cm_months = sorted([col for col in output_table.columns if col not in static_cols and not col.startswith("Lunar_")])
                    lunar_months = sorted([col for col in output_table.columns if col.startswith("Lunar_")])

                    # Format numeric columns as integers
                    for col in cm_months + lunar_months:
                        output_table[col] = output_table[col].fillna(0).astype(int)

                    for col in ["cm_on_hand", "cm_on_order", "lunar_on_hand_alloc", "lunar_on_order_alloc",
                                "cm_on_hand_extended_cost", "cm_on_order_extended_cost",
                                "lunar_on_hand_extended_cost", "lunar_on_order_extended_cost"]:
                        if col in output_table.columns:
                            output_table[col] = output_table[col].fillna(0).astype(int)

                    # Reindex to the exact static_cols order, followed by projection_type
                    # (added downstream) and then the period columns. Guards against any
                    # column absent for a given filter/inventory-source combination.
                    _ordered = [c for c in static_cols if c in output_table.columns]
                    _rest = [c for c in output_table.columns if c not in _ordered]
                    output_table = output_table[_ordered + _rest]

                    # ========== RESTRUCTURE: Duplicate rows - one for CM depletion, one for Lunar depletion ==========
                    # Create duplicate rows: original shows CM depletion, duplicate shows Lunar depletion

                    # Create CM depletion rows (original data with projection_type="CM Depletion")
                    cm_depletion_table = output_table.copy()
                    cm_depletion_table["projection_type"] = "CM Depletion"

                    # Create Lunar depletion rows (duplicate data with projection_type="Lunar Depletion")
                    lunar_depletion_table = output_table.copy()
                    lunar_depletion_table["projection_type"] = "Lunar Depletion"

                    # For Lunar depletion rows, replace CM month columns with Lunar_balance values
                    for month in cm_months:
                        lunar_balance_col = f"Lunar_balance_{month}"
                        if lunar_balance_col in lunar_depletion_table.columns:
                            lunar_depletion_table[month] = lunar_depletion_table[lunar_balance_col]

                    # Combine: CM depletion rows + Lunar depletion rows
                    output_table = pd.concat([cm_depletion_table, lunar_depletion_table], ignore_index=True)
                    _record_timing("Rows duplicated and combined")

                    # Update static_cols to include projection_type
                    static_cols_with_type = static_cols + ["projection_type"]

                    # For DISPLAY: show static columns + cm_months only (not lunar_months)
                    existing_static = [c for c in static_cols_with_type if c in output_table.columns]
                    display_col_order = existing_static + cm_months

                if len(output_table) > 0:
                    try:
                        st.subheader("CM & Lunar Inventory Projection by Period")

                        _record_timing("Table ready for display")

                        # Inventory source and segmentation options
                        col_inv_src, col_segment = st.columns(2)

                        with col_inv_src:
                            selected_value = st.selectbox(
                                "Inventory to use:",
                                ["On Hand + On Order", "On Hand Only"],
                                index=0 if st.session_state.get("depletion_inventory_source", "On Hand + On Order") == "On Hand + On Order" else 1,
                                key="depletion_inventory_source"
                            )
                            # Force the session state to update
                            st.session_state.inventory_source_selection = selected_value

                        with col_segment:
                            # Chart segmentation dropdown
                            segmentation_option = st.selectbox(
                                "Segment charts by:",
                                ["Item Category", "Product Generation"],
                                index=0,
                                key="depletion_segment_select"
                            )

                        # Prepare display table
                        display_table = output_table.copy()

                        # Generate inventory projection charts
                        # Calculate monthly sums for charts (from original quantities, floor negatives at 0, multiply by unit price)
                        # Get unique segments based on selection
                        if segmentation_option == "Item Category":
                            segments = sorted([str(c) for c in output_table["item_category"].dropna().unique()])
                            segment_col = "item_category"
                        else:  # Product Generation
                            segments = sorted([str(c) for c in output_table["obsolescence_state"].dropna().unique()])
                            segment_col = "obsolescence_state"

                        categories = segments  # Rename for consistency with existing code

                        if len(categories) > 0 and len(cm_months) > 0:
                            # Build stacked data by segment
                            lunar_data = {seg: [] for seg in categories}
                            cm_data = {seg: [] for seg in categories}
                            months_list = []

                            for col in cm_months:
                                months_list.append(col)

                                for segment in categories:
                                    # Filter by segment AND by projection type
                                    # Lunar chart: all CMs with Lunar Depletion projection
                                    lunar_seg_data = output_table[
                                        (output_table[segment_col].astype(str) == str(segment)) &
                                        (output_table["projection_type"] == "Lunar Depletion")
                                    ]
                                    # CM chart: all CMs with CM Depletion projection
                                    cm_seg_data = output_table[
                                        (output_table[segment_col].astype(str) == str(segment)) &
                                        (output_table["projection_type"] == "CM Depletion")
                                    ]
                                    seg_data = cm_seg_data  # Keep for compatibility with existing code below

                                    # CM data: use CM_seg_data which only has CM Depletion rows
                                    if len(cm_seg_data) > 0:
                                        cm_sum = (np.maximum(cm_seg_data[col], 0) * cm_seg_data["cm_unit_price"]).sum()
                                    else:
                                        cm_sum = 0.0

                                    # Lunar data: use lunar_seg_data which only has Lunar Depletion rows
                                    if len(lunar_seg_data) > 0:
                                        lunar_sum = (np.maximum(lunar_seg_data[col], 0) * lunar_seg_data["lunar_unit_price"]).sum()
                                    else:
                                        lunar_sum = 0.0

                                    lunar_data[segment].append(float(lunar_sum / 1_000_000))
                                    cm_data[segment].append(float(cm_sum / 1_000_000))

                            # Create DataFrames with Month index
                            lunar_df = pd.DataFrame(lunar_data, index=months_list)
                            cm_df = pd.DataFrame(cm_data, index=months_list)

                            # Create side-by-side columns for charts
                            col1, col2 = st.columns(2)

                            with col1:
                                st.subheader("Lunar Inventory Projection")
                                lunar_view = st.radio("View as:", ["Chart", "Table"], horizontal=True, key="lunar_proj_view")

                                # Create Plotly bar chart with totals at top
                                fig_lunar = go.Figure()

                                # Add stacked bars for each segment
                                for segment in lunar_df.columns:
                                    fig_lunar.add_trace(go.Bar(
                                        x=lunar_df.index,
                                        y=lunar_df[segment],
                                        name=segment,
                                        hovertemplate='<b>%{x}</b><br>' + segment + ': $%{y:.2f}M<extra></extra>'
                                    ))

                                # Calculate totals and add invisible bar with labels for total
                                totals = lunar_df.sum(axis=1)
                                fig_lunar.add_trace(go.Bar(
                                    x=lunar_df.index,
                                    y=[0] * len(lunar_df),  # invisible bar
                                    text=totals.round(2).astype(str),
                                    textposition='outside',
                                    hoverinfo='skip',
                                    showlegend=False,
                                    marker=dict(opacity=0)
                                ))

                                fig_lunar.update_layout(
                                    barmode='stack',
                                    height=400,
                                    xaxis_title='Month',
                                    yaxis_title='Value ($M)',
                                    hovermode='x unified',
                                    showlegend=True,
                                    margin=dict(t=80)
                                )

                                if lunar_view == "Chart":
                                    st.plotly_chart(fig_lunar, use_container_width=True, key="lunar_inventory_chart")
                                else:
                                    st.dataframe(lunar_df.T, use_container_width=True)

                            with col2:
                                st.subheader("CM Inventory Projection")
                                cm_view = st.radio("View as:", ["Chart", "Table"], horizontal=True, key="cm_proj_view")

                                # Create Plotly bar chart with totals at top
                                fig_cm = go.Figure()

                                # Add stacked bars for each segment
                                for segment in cm_df.columns:
                                    fig_cm.add_trace(go.Bar(
                                        x=cm_df.index,
                                        y=cm_df[segment],
                                        name=segment,
                                        hovertemplate='<b>%{x}</b><br>' + segment + ': $%{y:.2f}M<extra></extra>'
                                    ))

                                # Calculate totals and add invisible bar with labels for total
                                totals = cm_df.sum(axis=1)
                                fig_cm.add_trace(go.Bar(
                                    x=cm_df.index,
                                    y=[0] * len(cm_df),  # invisible bar
                                    text=totals.round(2).astype(str),
                                    textposition='outside',
                                    hoverinfo='skip',
                                    showlegend=False,
                                    marker=dict(opacity=0)
                                ))

                                fig_cm.update_layout(
                                    barmode='stack',
                                    height=400,
                                    xaxis_title='Month',
                                    yaxis_title='Value ($M)',
                                    hovermode='x unified',
                                    showlegend=True,
                                    margin=dict(t=80)
                                )

                                if cm_view == "Chart":
                                    st.plotly_chart(fig_cm, use_container_width=True, key="cm_inventory_chart")
                                else:
                                    st.dataframe(cm_df.T, use_container_width=True)
                        else:
                            st.info("No data to display in charts")

                        st.divider()

                        # Display mode radio buttons (below charts)
                        view_mode = st.radio("Display Mode", ["Quantity", "Value ($)"], horizontal=True, index=0)

                        # Apply view mode to display table
                        if view_mode == "Value ($)":
                            # Convert PAB columns to values (multiply by unit price)
                            # Floor negative values at 0
                            # Use appropriate unit price based on projection type:
                            # - CM Depletion rows: use cm_unit_price for cm_months columns
                            # - Lunar Depletion rows: use lunar_unit_price for all balance columns (even though stored in cm_months)

                            for col in cm_months:
                                # For CM Depletion rows, use cm_unit_price
                                # For Lunar Depletion rows, use lunar_unit_price
                                cm_mask = display_table["projection_type"] == "CM Depletion"
                                lunar_mask = display_table["projection_type"] == "Lunar Depletion"

                                display_table.loc[cm_mask, col] = (np.maximum(display_table.loc[cm_mask, col], 0) * display_table.loc[cm_mask, "cm_unit_price"]).round(0).astype(int)
                                display_table.loc[lunar_mask, col] = (np.maximum(display_table.loc[lunar_mask, col], 0) * display_table.loc[lunar_mask, "lunar_unit_price"]).round(0).astype(int)

                            # Lunar columns (if any exist) multiplied by lunar_unit_price
                            for col in lunar_months:
                                display_table[col] = (np.maximum(display_table[col], 0) * display_table["lunar_unit_price"]).round(0).astype(int)

                        # Display only the selected columns (not the separate Lunar_balance columns)
                        display_table_final = display_table[display_col_order]
                        st.dataframe(display_table_final, use_container_width=True, height=500)

                        # Export button (export all rows)
                        csv = display_table.to_csv(index=False)
                        st.download_button("📥 Download as CSV", csv, "inventory_depletion.csv", "text/csv")
                    except Exception as format_error:
                        st.error(f"Error formatting table: {format_error}")
                        log.error(f"Format error: {format_error}", exc_info=True)
                        st.dataframe(output_table, use_container_width=True, height=500)

                else:
                    st.info("No data matches the selected filters.")
            else:
                st.info("No inventory depletion data available.")
    except Exception as e:
        st.error(f"❌ Error computing inventory depletion: {str(e)}")
        log.error(f"Inventory depletion error: {e}", exc_info=True)
        st.write("**Debug info:**")
        st.write(f"Error type: {type(e).__name__}")
        st.write(f"Error message: {str(e)}")

elif st.session_state.active_tab == "Exclusion Review":
    st.subheader("Excluded Parts Review")
    st.caption("Un-exclude parts to resume monitoring.")

    excluded_list = sorted(list(st.session_state.exclusions_cache)) if st.session_state.exclusions_cache else []

    if not excluded_list:
        st.info("No excluded parts. All parts are under monitoring.")
    else:
        # Quick actions at top (like shortage report)
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            part_choice = st.selectbox("Part:", excluded_list, key="excl_part_select", label_visibility="collapsed")
        with col2:
            if st.button("🔄 Un-Exclude", use_container_width=True, key="excl_unexclude_btn"):
                un_exclude_part(part_choice)
        with col3:
            if st.button("📝 Notes", use_container_width=True, key="excl_notes_btn"):
                st.session_state.show_excl_notes = part_choice

        # Show notes history if selected
        if st.session_state.get("show_excl_notes") == part_choice:
            st.write(f"**Notes for {part_choice}:**")
            notes_list = load_notes(part_choice)
            if notes_list:
                for n in reversed(notes_list):
                    with st.container(border=True):
                        st.caption(f"{n['user']} — {n['timestamp'][:10]} {n['timestamp'][11:16]}")
                        st.write(n["note"])
            else:
                st.info(f"No notes for {part_choice}")
            st.divider()

        # Table: PN, Description, Notes
        table_data = []
        for part in excluded_list:
            # Get description from filtered data
            part_desc = "—"
            if len(filtered) > 0:
                part_row = filtered[filtered['part'] == part]
                if len(part_row) > 0:
                    part_desc = part_row.iloc[0].get('description', '—')

            # Get notes count
            notes_list = load_notes(part)
            notes_summary = f"{len(notes_list)} note(s)" if len(notes_list) > 0 else "—"

            table_data.append({
                "Part": part,
                "Description": str(part_desc)[:80],
                "Notes": notes_summary
            })

        st.dataframe(pd.DataFrame(table_data), use_container_width=True, height=300)

# === Record final timing and save log ===
_record_timing("Page render complete")
_save_timings()

# Display timing summary in sidebar
with st.sidebar:
    with st.expander("⏱ Load Times", expanded=False):
        for phase, elapsed in sorted(_timings.items()):
            st.caption(f"{phase}: **{elapsed:.2f}s**")
        total = _timings.get("Page render complete", 0)
        st.divider()
        st.caption(f"**Total: {total:.2f}s**")

        # Slowest phase, so the next bottleneck is always visible
        if _timings:
            prev = 0.0
            deltas = {}
            for phase, elapsed in sorted(_timings.items(), key=lambda kv: kv[1]):
                deltas[phase] = elapsed - prev
                prev = elapsed
            worst = max(deltas.items(), key=lambda kv: kv[1])
            st.caption(f"Slowest phase: **{worst[0]}** ({worst[1]:.2f}s)")
