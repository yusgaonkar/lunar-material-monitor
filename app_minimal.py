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

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# Get OS username
OS_USER = os.getenv("USER") or os.getenv("USERNAME") or "Unknown"

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
        if st.session_state["password"] == app_password:
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
def load_exclusions():
    """Load excluded parts from CSV."""
    try:
        if os.path.exists(EXCLUSIONS_FILE) and os.path.getsize(EXCLUSIONS_FILE) > 0:
            df = pd.read_csv(EXCLUSIONS_FILE)
            if len(df) > 0 and "part" in df.columns:
                return set(df["part"].unique())
    except Exception as e:
        log.warning(f"Error loading exclusions: {e}")
    return set()

def exclude_part(part, reason):
    """Add a part to exclusions."""
    os.makedirs(os.path.dirname(EXCLUSIONS_FILE) or ".", exist_ok=True)
    exclusion_data = {
        "part": part,
        "user": OS_USER,
        "timestamp": datetime.now().isoformat(),
        "reason": reason
    }
    try:
        if os.path.exists(EXCLUSIONS_FILE) and os.path.getsize(EXCLUSIONS_FILE) > 0:
            df = pd.read_csv(EXCLUSIONS_FILE)
            df = pd.concat([df, pd.DataFrame([exclusion_data])], ignore_index=True)
        else:
            df = pd.DataFrame([exclusion_data])
        df.to_csv(EXCLUSIONS_FILE, index=False)
        st.success(f"✓ Excluded {part}")
    except Exception as e:
        st.error(f"Error excluding part: {e}")

def load_notes(part):
    """Load notes for a part."""
    try:
        if not os.path.exists(NOTES_FILE) or os.path.getsize(NOTES_FILE) == 0:
            return []
        notes = []
        with open(NOTES_FILE, "r") as f:
            for line in f:
                if line.strip():
                    note = json.loads(line)
                    if note.get("part") == part:
                        notes.append(note)
        return notes
    except Exception as e:
        log.warning(f"Error loading notes: {e}")
        return []

def add_note(part, note_text):
    """Add a note to a part."""
    os.makedirs(os.path.dirname(NOTES_FILE) or ".", exist_ok=True)
    note_data = {
        "part": part,
        "user": OS_USER,
        "timestamp": datetime.now().isoformat(),
        "note": note_text
    }
    try:
        with open(NOTES_FILE, "a") as f:
            f.write(json.dumps(note_data) + "\n")
        st.success("✓ Note added")
    except Exception as e:
        st.error(f"Error adding note: {e}")

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

# --- Load and run ---
@st.cache_data
def load_and_run():
    frames = lio.load_all()
    result = eng.run(frames)
    build_plan = lio.load_build_plan()
    bom_stitched = frames["bom_stitched.csv"]
    return result, build_plan, bom_stitched


result, build_plan, bom_stitched = load_and_run()
cfg = result["config"]
s = result["summary"]
pab = result["pab"]
receipts = result["receipts"]
demand_detail = result["demand_detail"]
excess = result["excess"]
products = result["products"]

# --- Header ---
st.title("Lunar Material Monitor")
st.warning("⚠️ **PILOT / NOT IN PRODUCTION** — Data not yet validated. Use for planning only.")
st.caption(f"Component runout tracking | Snapshot: {result['snapshot'].date()} | Week 0: {cfg.week0.date()}")

# --- Session state for tab persistence ---
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Shortage Report"

# --- Tab selector (preserved across reruns) ---
st.subheader("View")
active_tab = st.radio("", ["Shortage Report", "Drill-Down Grid", "Excess Monitor"],
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
    prod_filter = filter_cols[1].multiselect("Products", sorted(result["products"]["alias"].unique()))

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

# Product filter: show only buy parts under selected products
if prod_filter:
    # Get all product LPNs for selected aliases
    selected_products = products[products["alias"].isin(prod_filter)]["product"].unique()

    # Get all buy parts under these products
    parts_in_products = set()
    for product_lpn in selected_products:
        parts_in_products.update(get_buy_parts_under_product(product_lpn, bom_stitched))

    # Filter to show only parts in selected products
    filtered = filtered[filtered["part"].isin(parts_in_products)]

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
def get_buy_parts_under_product(product_lpn: str, bom: pd.DataFrame) -> set:
    """Find all buy parts (Sourcing_Flat_Qty > 0) under a product in the BOM."""
    buy_parts = set()
    visited = set()

    def traverse(parent_lpn):
        if parent_lpn in visited:
            return
        visited.add(parent_lpn)

        children = bom[bom["Parent Product LPN"] == parent_lpn]
        for _, child_row in children.iterrows():
            child_lpn = child_row["item_number"]
            sourcing_qty = pd.to_numeric(child_row["Sourcing Flat Qty"], errors="coerce") or 0

            if sourcing_qty > 0:
                buy_parts.add(child_lpn)

            traverse(child_lpn)

    traverse(product_lpn)
    return buy_parts


def get_build_plan_grid(bp, demand_det, cm_filt, prod_filt, weeks_cutoff):
    """Returns a pivot table: products x weeks with total column."""
    bp_filt = bp[bp["period_start"] <= weeks_cutoff].copy()

    # Filter by products that appear in demand_detail
    if cm_filt != "All":
        prods_in_cm = set(demand_det[demand_det["cm"] == cm_filt]["product"].unique())
        bp_filt = bp_filt[bp_filt["product_lpn"].isin(prods_in_cm)]

    if prod_filt:
        # Get products that match the selected aliases
        prods_for_alias = set()
        for alias in prod_filt:
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

    # Filter by products: use products master alias mapping
    if prod_filt:
        prods_for_alias = set(
            products[products["alias"].isin(prod_filt)]["product"].unique()
        )
        bp_filt = bp_filt[bp_filt["product_lpn"].isin(prods_for_alias)]

    if len(bp_filt) == 0:
        return None

    # Add product descriptions
    prod_desc = products[["product", "alias"]].drop_duplicates().rename(
        columns={"product": "product_lpn", "alias": "Product"})
    bp_filt = bp_filt.merge(prod_desc, on="product_lpn", how="left")
    bp_filt["Product"] = bp_filt["product_lpn"] + " - " + bp_filt["Product"].fillna("")

    # Pivot: products x months
    bp_filt["period_str"] = bp_filt["period_start"].dt.strftime("%Y-%m")
    pivot = bp_filt.pivot_table(
        index="Product", columns="period_str", values="qty", aggfunc="sum", fill_value=0
    ).astype(int)

    pivot["Total"] = pivot.sum(axis=1).astype(int)
    pivot = pivot.sort_values("Total", ascending=False)

    return pivot


def get_pcba_build_plan(bp, bom, products, cm_filt, prod_filt, weeks_cutoff):
    """Returns PCBA (30-) pull-forward demand: shift build plan 4 weeks back, use BOM Flat Qty."""
    bp_filt = bp.copy()
    bp_filt["period_start"] = pd.to_datetime(bp_filt["period_start"])
    bp_filt = bp_filt[bp_filt["period_start"] <= weeks_cutoff]

    # Get products in selected CM from products master
    if cm_filt != "All":
        prods_in_cm = set(products[products["cm"] == cm_filt]["product"].unique())
        bp_filt = bp_filt[bp_filt["product_lpn"].isin(prods_in_cm)]

    # Filter by products: use products master alias mapping
    if prod_filt:
        prods_for_alias = set(
            products[products["alias"].isin(prod_filt)]["product"].unique()
        )
        bp_filt = bp_filt[bp_filt["product_lpn"].isin(prods_for_alias)]

    if len(bp_filt) == 0:
        return None

    # For each top-level product, find all 30- children and their Flat Qty
    # Create PCBA demand rows
    pcba_rows = []

    for product_lpn in bp_filt["product_lpn"].unique():
        # Get BOM for this product: find all 30- parts and their Flat Qty
        prod_bom = bom[bom["Parent Product LPN"] == product_lpn]
        pcba_parts = prod_bom[prod_bom["item_number"].str.startswith("30-", na=False)][
            ["item_number", "item_name", "Flat Qty"]
        ].drop_duplicates("item_number")

        if len(pcba_parts) == 0:
            continue

        # For each PCBA, create rows with pull-forward demand
        for _, pcba_row in pcba_parts.iterrows():
            pcba_lpn = pcba_row["item_number"]
            pcba_flat_qty = pd.to_numeric(pcba_row["Flat Qty"], errors="coerce") or 1.0

            # Get build plan for this product
            prod_plan = bp_filt[bp_filt["product_lpn"] == product_lpn]

            for _, plan_row in prod_plan.iterrows():
                # Calculate PCBA demand and shift back 4 weeks
                pcba_qty = plan_row["qty"] * pcba_flat_qty
                original_period = pd.to_datetime(plan_row["period_start"])
                shifted_period = original_period - pd.Timedelta(weeks=4)

                # Clamp shifted period to first week in visible range (weeks_cutoff)
                min_visible_week = bp_filt["period_start"].min()
                if shifted_period < min_visible_week:
                    shifted_period = min_visible_week

                pcba_rows.append({
                    "PCBA_LPN": pcba_lpn,
                    "PCBA_Name": pcba_row["item_name"],
                    "period_start": shifted_period,
                    "pcba_qty": pcba_qty
                })

    if not pcba_rows:
        return None

    pcba_df = pd.DataFrame(pcba_rows)
    pcba_df["PCBA"] = pcba_df["PCBA_LPN"] + " - " + pcba_df["PCBA_Name"]
    pcba_df["period_str"] = pcba_df["period_start"].dt.strftime("%Y-%m")

    # Pivot: PCBAs x periods
    pivot = pcba_df.pivot_table(
        index="PCBA", columns="period_str", values="pcba_qty", aggfunc="sum", fill_value=0
    ).astype(int)

    pivot["Total"] = pivot.sum(axis=1).astype(int)
    pivot = pivot.sort_values("Total", ascending=False)

    return pivot


build_plan_grid = get_build_plan_grid(build_plan, demand_detail, cm_filter, prod_filter, cutoff)
toplevel_plan = get_toplevel_build_plan(build_plan, products, cm_filter, prod_filter, cutoff)
pcba_plan = get_pcba_build_plan(build_plan, bom_stitched, products, cm_filter, prod_filter, cutoff)

# ============================================================================
# RENDER ACTIVE TAB
# ============================================================================
if st.session_state.active_tab == "Shortage Report":
    # SHORTAGE REPORT
    st.subheader("Build Plan (Filtered)")
    if build_plan_grid is not None and len(build_plan_grid) > 0:
        st.caption(f"Products planned by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
        st.dataframe(build_plan_grid, use_container_width=True)
    else:
        st.info("No build plan data for selected filters.")

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
            if len(incoming):
                supply_str = " | ".join(
                    f"{int(r['receipts']):,} on {r['period'].strftime('%Y-%m-%d')}"
                    for _, r in incoming.sort_values("period").iterrows()
                )
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
        report_df_display = report_df[[c for c in col_order if c in report_df.columns]]

        # Display clean dataframe
        st.dataframe(report_df_display, use_container_width=True, height=500)

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
        with st.expander("▼ PCBA Build Plan", expanded=True):
            if pcba_plan is not None and len(pcba_plan) > 0:
                st.caption(f"30- PCBA parts with 4-week pull-forward by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
                st.dataframe(pcba_plan, use_container_width=True)
            else:
                st.info("No PCBA parts in pull-forward for selected filters.")

    st.divider()

    # Filter toggle: All parts vs Only PCBA parts
    st.subheader("Demand, Supply & Inventory by Week")
    filter_option = st.radio(
        "Show:",
        ["All parts", "Only PCBA parts"],
        horizontal=True,
        key="demand_source_filter"
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
                desc = part_desc["description"][:50]

                # Get demand_source for this part (handle if column doesn't exist)
                part_rows = demand_detail[(demand_detail["cm"] == cm) & (demand_detail["part"] == part)]
                part_demand_source = "Unknown"
                if len(part_rows) > 0:
                    if "demand_source" in demand_detail.columns:
                        part_demand_source = part_rows["demand_source"].iloc[0]
                    else:
                        part_demand_source = "Build Plan"  # Default if column missing

                # Demand row
                demand_row = {
                    "CM": cm,
                    "Part": part,
                    "Description": desc,
                    "Source": part_demand_source,
                    "Metric": "Demand"
                }
                for _, pab_row in part_pab.iterrows():
                    week_key = pab_row["period"].strftime("%Y-%m-%d")
                    demand_row[week_key] = int(pab_row["demand"])
                grid_data.append(demand_row)

                # Supply row
                supply_row = {
                    "CM": cm,
                    "Part": part,
                    "Description": desc,
                    "Source": part_demand_source,
                    "Metric": "Supply"
                }
                for _, pab_row in part_pab.iterrows():
                    week_key = pab_row["period"].strftime("%Y-%m-%d")
                    supply_row[week_key] = int(pab_row["receipts"])
                grid_data.append(supply_row)

                # Inventory row (will be color-coded)
                inv_row = {
                    "CM": cm,
                    "Part": part,
                    "Description": desc,
                    "Source": part_demand_source,
                    "Metric": "Inventory"
                }
                for _, pab_row in part_pab.iterrows():
                    week_key = pab_row["period"].strftime("%Y-%m-%d")
                    inv_row[week_key] = int(pab_row["pab"])
                grid_data.append(inv_row)

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
    if build_plan_grid is not None and len(build_plan_grid) > 0:
        st.caption(f"Products planned by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
        st.dataframe(build_plan_grid, use_container_width=True)
    else:
        st.info("No build plan data for selected filters.")

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
            excess_filtered = excess_filtered[
                excess_filtered["products"].str.contains("|".join(prod_filter), na=False)
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
