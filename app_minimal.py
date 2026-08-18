"""Lunar Material Monitor — Component-level demand & supply planning.

Run: streamlit run app_minimal.py

To add password protection when deploying:
1. In Streamlit Cloud settings, add secret: app_password = "your_password"
2. Users will be prompted to enter password on first load
"""

import logging
import os
from datetime import datetime
import json

import streamlit as st
import pandas as pd
import numpy as np

from src import io as lio, engine as eng
from src import supabase_io, asn_processor

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# Get OS username
OS_USER = os.getenv("USER") or os.getenv("USERNAME") or "Unknown"

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

# Password protection disabled temporarily for testing
# if not check_password():
#     st.stop()  # Do not continue if password is not correct

# ============================================================================
# PERSISTENCE HELPERS
# ============================================================================
@st.cache_data(ttl=60)
def load_exclusions():
    """Load excluded parts from Supabase (cached for 60 sec)."""
    if SUPABASE_CLIENT:
        try:
            return supabase_io.get_all_excluded_parts()
        except Exception as e:
            log.warning(f"Error loading exclusions from Supabase: {e}")
    return set()

def exclude_part(part, reason):
    """Add a part to exclusions via Supabase."""
    if SUPABASE_CLIENT:
        try:
            supabase_io.exclude_part(part, reason, OS_USER)
            st.cache_data.clear()  # Clear caches
            st.success(f"✓ Excluded {part}")
            st.rerun()
        except Exception as e:
            st.error(f"Error excluding part: {e}")
    else:
        st.error("Supabase not initialized")

@st.cache_data(ttl=60)
def load_notes(part):
    """Load notes for a part from Supabase (cached for 60 sec)."""
    if SUPABASE_CLIENT:
        try:
            notes = supabase_io.load_notes(part)
            # Convert Supabase format to old format for compatibility
            formatted_notes = []
            for note in notes:
                formatted_notes.append({
                    "part": part,
                    "note": note.get("note", ""),
                    "user": note.get("note_user", "Unknown"),
                    "timestamp": note.get("timestamp", "")
                })
            return formatted_notes
        except Exception as e:
            log.warning(f"Error loading notes from Supabase: {e}")
    return []

def add_note(part, note_text):
    """Add a note to a part via Supabase."""
    if SUPABASE_CLIENT:
        try:
            supabase_io.save_note(part, note_text, OS_USER)
            st.cache_data.clear()  # Clear caches so new note shows up
            st.success("✓ Note added")
        except Exception as e:
            st.error(f"Error adding note: {e}")
    else:
        st.error("Supabase not initialized")

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

# Load excluded and watched parts
excluded_parts = load_exclusions()
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


# --- ASN adjustment ---
@st.cache_data(ttl=3600)
def load_asn_adjustments():
    """Load and process ASN data (8/1-8/18, from unified ASN file)."""
    try:
        asn_data = asn_processor.process_asn_file(
            "data/asn_aug18.csv",
            "2026-08-01", "2026-08-18", cm='unified'
        )
        return asn_data
    except Exception as e:
        log.warning(f"Could not load ASN data: {e}")
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
                comp_asn = asn_df[asn_df['product_lpn'] == '10-00522D']['asn_qty'].sum()
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


# --- Load and run ---
@st.cache_data(show_spinner="Initializing engine...")
def load_and_run():
    """Cached: loads data and runs engine once per session.

    Engine uses qty_adjusted (ASN-deducted quantities) for demand calculations.
    """
    frames = lio.load_all()

    # Load and adjust build plan (apply ASN deductions)
    build_plan = lio.load_build_plan()
    asn_data = load_asn_adjustments()
    # Snapshot date: the date through which ASN data has been received (8/1-8/18)
    # Demand disaggregation starts from 8/18 onwards (day after last ASN day)
    snapshot_date = pd.Timestamp('2026-08-18')
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

    frames["build_plan.csv"] = build_plan_for_engine

    result = eng.run(frames)

    # Keep full build_plan with both qty and qty_adjusted for display
    bom_stitched = frames["bom_stitched.csv"]
    return result, build_plan, bom_stitched


result, build_plan, bom_stitched = load_and_run()
cfg = result["config"]
s = result["summary"]
pab = result["pab"]
receipts = result["receipts"]
demand_detail = result["demand_detail"]
# Lazy-load excess to defer computation until user views that tab
excess = result.get("excess", pd.DataFrame())  # Empty until accessed
products = result["products"]

# Session state for lazy-loading expensive tabs
if "computed_excess" not in st.session_state:
    st.session_state.computed_excess = False

# --- Header ---
st.title("Lunar Material Monitor")
st.warning("⚠️ **PILOT / NOT IN PRODUCTION** — Data not yet validated. Use for planning only.")
st.caption(f"Component runout tracking | Snapshot: {result['snapshot'].date()} | Week 0: {cfg.week0.date()}")

# --- Session state for tab persistence ---
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Shortage Report"

# --- Tab selector (preserved across reruns) ---
st.subheader("View")
active_tab = st.radio("", ["Shortage Report", "Drill-Down Grid", "Excess Monitor", "Exclusion Review"],
                       horizontal=True, label_visibility="collapsed",
                       key="tab_selector")
st.session_state.active_tab = active_tab

# --- Filters ---
st.subheader("Filters")

# Main filters on left + Checkboxes on extreme right
col_left, col_right = st.columns([5.5, 1.5], gap="large")

# LEFT COLUMN: CM, Products, Part Number, Planning Horizon
with col_left:
    filter_cols = st.columns([1, 1.5, 1.5, 1.8])

    cm_filter = filter_cols[0].selectbox("CM", ["All"] + sorted(s["cm"].unique()))

    # Filter products: only show 90- top-level products from the products master
    prod_filter = filter_cols[1].multiselect("Products", sorted(result["products"]["display_name"].unique()))

    part_filter = filter_cols[2].multiselect("Part Number", sorted(s["part"].unique()))

    weeks_window = filter_cols[3].slider(
        "Planning Horizon (weeks)",
        min_value=1,
        max_value=cfg.horizon_weeks,
        value=12,
        step=1
    )

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
if len(excluded_parts) > 0:
    filtered = filtered[~filtered["part"].isin(excluded_parts)]

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
    filtered = filtered[
        (filtered["part"].isin(parts_in_products)) &
        (filtered["cm"].isin(selected_cms))
    ]

if part_filter:
    filtered = filtered[filtered["part"].isin(part_filter)]
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
filtered = filtered.sort_values("first_shortage_date", na_position="last")

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


@st.cache_resource
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

            report_item = {
                "CM": cm,
                "Part": part,
                "Description": desc,
                "Products": products_str,
                "Build Coverage": int(row["blocks_buildable"]),
                "First Short Date": row["first_shortage_date"].strftime("%Y-%m-%d") if pd.notna(row["first_shortage_date"]) else "—",
                "Shortage Type": row.get("shortage_type", "—"),
                "Raw Inventory": int(row.get("raw_inventory", 0)),
                "WIP Inventory": int(row.get("wip_inventory", 0)),
                "Total Inventory": int(row.get("raw_inventory", 0) + row.get("wip_inventory", 0)),
                "Shortage Qty": int(row["shortage_qty"]) if pd.notna(row["shortage_qty"]) else 0,
                "Incoming Supply": supply_str,
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
                     "Total Inventory", "Build Coverage", "First Short Date", "Shortage Type",
                     "Incoming Supply"]
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
        report_df_display = report_df[[c for c in col_order if c in report_df.columns]].copy()

        # Style: highlight Incoming Supply cells red if they contain [Past Due]
        def highlight_past_due(s):
            """Highlight Incoming Supply column red if it contains [Past Due]."""
            if s.name == "Incoming Supply":
                return ["background-color: #cc0000; color: white; font-weight: bold;" if "[Past Due]" in str(v) else "" for v in s]
            return [""] * len(s)

        styled_df = report_df_display.style.apply(highlight_past_due, axis=0)

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

    if len(excess) == 0:
        st.info("No excess supply detected.")
    else:
        # Filter excess by CM and products
        excess_filtered = excess.copy()

        if cm_filter != "All":
            excess_filtered = excess_filtered[excess_filtered["cm"] == cm_filter]

        if prod_filter:
            # Extract aliases from display_names for matching
            aliases = [dn.split(" - ", 1)[1] if " - " in dn else dn for dn in prod_filter]
            excess_filtered = excess_filtered[
                excess_filtered["products"].str.contains("|".join(aliases), na=False)
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

elif st.session_state.active_tab == "Exclusion Review":
    st.subheader("Excluded Parts Review")
    st.caption("Review excluded parts and notes. Un-exclude to resume monitoring.")

    # Get list of excluded parts (filter out any NaN or non-string values)
    excluded_list = [str(p) for p in list(excluded_parts) if pd.notna(p) and p] if excluded_parts else []

    if not excluded_list:
        st.info("No excluded parts.")
    else:
        # Simple table of excluded parts
        table_data = []
        for part in sorted(excluded_list):
            try:
                df_ex = pd.read_csv(EXCLUSIONS_FILE)
                part_row = df_ex[df_ex["part"] == part]
                if len(part_row) > 0:
                    part_row = part_row.iloc[-1]
                    reason = part_row.get("reason", "—") if "reason" in df_ex.columns else "—"
                    user = part_row.get("user", "—") if "user" in df_ex.columns else "—"
                    date = str(part_row.get("timestamp", "—"))[:10] if "timestamp" in df_ex.columns else "—"
                else:
                    reason = user = date = "—"
            except:
                reason = user = date = "—"

            notes_count = len(load_notes(part))
            table_data.append({
                "Part": part,
                "Reason": str(reason)[:50],
                "User": str(user),
                "Date": date,
                "Notes": f"{notes_count} note(s)" if notes_count > 0 else ""
            })

        st.dataframe(pd.DataFrame(table_data), use_container_width=True, height=300)

        st.divider()
        st.subheader("Un-Exclude")

        part_choice = st.selectbox("Part:", excluded_list, key="ex_part_sel", label_visibility="collapsed")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Un-Exclude", key="ex_btn", use_container_width=True):
                if SUPABASE_CLIENT:
                    try:
                        supabase_io.un_exclude_part(part_choice)
                        st.success(f"✓ {part_choice} re-enabled")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                else:
                    st.error("Supabase not initialized")

        with col2:
            if st.button("📝 Notes", key="ex_notes", use_container_width=True):
                st.session_state.show_ex_notes = part_choice

        if st.session_state.get("show_ex_notes") == part_choice:
            notes_list = load_notes(part_choice)
            if notes_list:
                for n in reversed(notes_list):
                    with st.container(border=True):
                        st.caption(f"{n['user']} - {n['timestamp'][:10]}")
                        st.write(n["note"])
            else:
                st.info(f"No notes for {part_choice}")
    st.caption("Review excluded parts and notes. Un-exclude to resume monitoring.")
    st.caption("Periodically review excluded parts and notes. Un-exclude to resume monitoring.")

    if len(excluded_parts) == 0:
        st.info("No excluded parts. All parts are under monitoring.")
    else:
        # Clean excluded_parts: filter out NaN/non-string values and sort
        clean_excluded = sorted([str(p) for p in excluded_parts if pd.notna(p) and str(p).strip()])

        # Build exclusion table with notes
        exclusion_rows = []

        for part in clean_excluded:
            # Get exclusion metadata
            try:
                df_excl = pd.read_csv(EXCLUSIONS_FILE)
                part_exclusion = df_excl[df_excl["part"] == part].sort_values("timestamp", ascending=False).iloc[0]
                excl_reason = part_exclusion["reason"] if pd.notna(part_exclusion.get("reason")) else "—"
                excl_user = part_exclusion["user"] if pd.notna(part_exclusion.get("user")) else "—"
                excl_date = part_exclusion["timestamp"][:10] if pd.notna(part_exclusion.get("timestamp")) else "—"
            except:
                excl_reason = "—"
                excl_user = "—"
                excl_date = "—"

            # Get notes history
            notes = load_notes(part)
            notes_preview = ""
            notes_count = len(notes)
            if notes_count > 0:
                latest_note = notes[-1]["note"][:80]
                notes_preview = f"📝 {latest_note}{'...' if len(notes[-1]['note']) > 80 else ''}"
                if notes_count > 1:
                    notes_preview += f" (+{notes_count-1})"

            exclusion_rows.append({
                "Part": part,
                "Excluded Date": excl_date,
                "Excluded By": excl_user,
                "Reason": excl_reason[:60],
                "Notes History": notes_preview,
                "Notes Count": notes_count,
            })

        excl_df = pd.DataFrame(exclusion_rows)

        # Display table
        st.dataframe(excl_df, use_container_width=True, height=400)

        st.divider()
        st.subheader("Un-Exclude Part")

        col1, col2 = st.columns([2, 1])
        with col1:
            part_to_review = st.selectbox(
                "Select part to un-exclude:",
                options=clean_excluded,
                key="uexclude_part_select",
                label_visibility="collapsed"
            )

        with col2:
            if st.button("🔄 Un-Exclude", use_container_width=True, key="uexclude_btn"):
                if part_to_review and SUPABASE_CLIENT:
                    try:
                        supabase_io.un_exclude_part(part_to_review)
                        st.success(f"✓ {part_to_review} re-enabled for monitoring")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
                elif not SUPABASE_CLIENT:
                    st.error("Supabase not initialized")

        st.divider()
        st.subheader("Exclusion Details")

        if part_to_review:
            # Show full details for selected part
            part_notes = load_notes(part_to_review)

            col1, col2 = st.columns(2)

            with col1:
                st.write(f"**Part:** {part_to_review}")
                try:
                    df_excl = pd.read_csv(EXCLUSIONS_FILE)
                    part_excl = df_excl[df_excl["part"] == part_to_review].sort_values("timestamp", ascending=False).iloc[0]
                    st.write(f"**Reason:** {part_excl['reason'] if pd.notna(part_excl.get('reason')) else '—'}")
                    st.write(f"**Excluded By:** {part_excl['user'] if pd.notna(part_excl.get('user')) else '—'}")
                    st.write(f"**Date:** {part_excl['timestamp'][:10] if pd.notna(part_excl.get('timestamp')) else '—'}")
                except:
                    pass

            with col2:
                st.write(f"**Notes History:** {len(part_notes)} entries")
                if part_notes:
                    st.write(f"**Latest Note Date:** {part_notes[-1]['timestamp'][:10]}")

            # Show all notes
            if part_notes:
                st.write("**All Notes:**")
                for i, note in enumerate(reversed(part_notes)):
                    with st.container(border=True):
                        st.caption(f"{i+1}. **{note['user']}** — {note['timestamp'][:10]} {note['timestamp'][11:16]}")
                        st.write(note["note"])
