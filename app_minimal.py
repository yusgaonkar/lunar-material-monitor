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

# ============================================================================
# DIALOG FUNCTIONS (Modal windows for part management)
# ============================================================================
@st.dialog("Exclude Part from Report")
def open_exclude_dialog(part):
    """Dialog to exclude a part."""
    st.write(f"**Part:** {part}")
    reason = st.text_area("Reason for exclusion (e.g., printed labels, not tracked):",
                         key=f"exclude_reason_{part}", height=100)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Exclude", key=f"confirm_exclude_{part}", use_container_width=True):
            if reason:
                exclude_part(part, reason)
                st.rerun()
            else:
                st.error("Please provide a reason")
    with col2:
        if st.button("Cancel", key=f"cancel_exclude_{part}", use_container_width=True):
            st.rerun()

@st.dialog("Add Planner Note")
def open_note_dialog(part):
    """Dialog to add a note."""
    st.write(f"**Part:** {part}")
    note_text = st.text_area("Your note:",
                            key=f"note_text_{part}", height=150)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Add Note", key=f"confirm_note_{part}", use_container_width=True):
            if note_text:
                add_note(part, note_text)
                st.rerun()
            else:
                st.error("Please enter a note")
    with col2:
        if st.button("Cancel", key=f"cancel_note_{part}", use_container_width=True):
            st.rerun()

@st.dialog("View Notes History")
def open_view_notes_dialog(part):
    """Dialog to view notes for a part."""
    st.write(f"**Part:** {part}")
    notes = load_notes(part)

    if notes:
        for i, note in enumerate(notes):
            with st.container(border=True):
                st.caption(f"**{note['user']}** — {note['timestamp'][:10]} {note['timestamp'][11:16]}")
                st.write(note["note"])
    else:
        st.info(f"No notes yet for {part}")

    if st.button("Close", key=f"close_notes_{part}", use_container_width=True):
        st.rerun()

# Load excluded parts
excluded_parts = load_exclusions()

# --- Load and run ---
@st.cache_data
def load_and_run():
    frames = lio.load_all()
    result = eng.run(frames)
    build_plan = lio.load_build_plan()
    return result, build_plan


result, build_plan = load_and_run()
cfg = result["config"]
s = result["summary"]
pab = result["pab"]
receipts = result["receipts"]
demand_detail = result["demand_detail"]
excess = result["excess"]

# --- Header ---
st.title("🌙 Lunar Material Monitor")
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

# --- Scenario toggle ---
include_allocations = st.checkbox("Include Lunar Allocations", value=False,
                                  help="Recalculate with Lunar inventory allocations, shows on drill-down")

# --- Common filters ---
st.subheader("Filters")
cols = st.columns([2, 2, 2, 1, 1])
cm_filter = cols[0].selectbox("CM", ["All"] + sorted(s["cm"].unique()))
prod_filter = cols[1].multiselect("Products", sorted(set(
    p for prods in s["products"].fillna("") for p in prods.split(", ") if p)))
weeks_window = cols[2].slider("Time window (weeks)", min_value=1, max_value=cfg.horizon_weeks, value=12, step=1)
show_short_only = cols[3].checkbox("Short only", value=True)
exclude_uom_issues = cols[4].checkbox("Exclude UoM issues", value=True)

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
if prod_filter:
    filtered = filtered[filtered["products"].str.contains("|".join(prod_filter), na=False)]
if show_short_only:
    filtered = filtered[filtered["is_shortage"]]

# Exclude UoM issues (non-"ea"/"each" UoMs)
if exclude_uom_issues:
    uom_mask = filtered["uom"].fillna("ea").str.lower().str.strip().isin(["ea", "each", ""])
    filtered = filtered[uom_mask]

# Apply time window: only parts that run out within N weeks
cutoff = cfg.week0 + pd.Timedelta(weeks=weeks_window)
filtered = filtered[
    (filtered["first_shortage_date"].isna()) | (filtered["first_shortage_date"] <= cutoff)
]
filtered = filtered.sort_values("first_shortage_date", na_position="last")

# Add note if using allocations
if include_allocations:
    st.info("🔄 **Allocation scenario active**: Drill-down shows PAB recalculated with Lunar allocations. "
            "Recommendation column shows PO quantities needed from Lunar to resolve each shortage.")

# --- Build plan grid (filtered by CM and products) ---
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
    prod_desc = demand_det[["product", "alias"]].drop_duplicates().rename(
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

build_plan_grid = get_build_plan_grid(build_plan, demand_detail, cm_filter, prod_filter, cutoff)

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

            # UoM flag — only if BOM UoM is not "ea" or "each"
            uom = str(row.get("uom", "ea")).lower().strip()
            uom_flag = "⚠️" if uom not in ("ea", "each", "") else ""

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
                "Shortage Qty": int(row["shortage_qty"]) if pd.notna(row["shortage_qty"]) else 0,
                "Incoming Supply": supply_str,
                "UoM": uom_flag if uom_flag else "✓",
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

        # Display with column order
        col_order = ["CM", "Part", "Description", "Products", "UoM", "Build Coverage",
                     "First Short Date", "Shortage Qty", "Incoming Supply"]
        if include_allocations and "Recommended" in report_df.columns:
            col_order.append("Recommended")
        report_df = report_df[[c for c in col_order if c in report_df.columns]]

        # Add Notes and Actions columns
        report_df["Notes"] = report_df["Part"].apply(
            lambda p: f"📝 {len(load_notes(p))}" if len(load_notes(p)) > 0 else ""
        )
        report_df["Actions"] = report_df["Part"].apply(
            lambda p: f"🏷️ 📋"
        )

        st.dataframe(report_df, use_container_width=True, height=500)

        # Display action buttons for each part
        st.divider()
        st.subheader("Part Management")

        # Create columns for part selection and action buttons
        part_col, action_col = st.columns([2, 1])
        with part_col:
            selected_part = st.selectbox(
                "Select part to manage:",
                options=sorted(report_df["Part"].unique()),
                key="manage_part_select",
                label_visibility="collapsed"
            )

        with action_col:
            col_exclude, col_note, col_view = st.columns(3)
            with col_exclude:
                if st.button("🏷️ Exclude", key="exclude_trigger", use_container_width=True,
                            help="Exclude this part from the report"):
                    open_exclude_dialog(selected_part)
            with col_note:
                if st.button("➕ Add Note", key="note_trigger", use_container_width=True,
                            help="Add a planner note"):
                    open_note_dialog(selected_part)
            with col_view:
                if st.button("📖 View Notes", key="view_trigger", use_container_width=True,
                            help="View all notes for this part"):
                    open_view_notes_dialog(selected_part)

        if (report_df["UoM"] == "⚠️").any():
            st.caption("⚠️ = BOM UoM is not 'each' (gm, ml, sheets, etc.) — verify conversion if short qty seems extreme")
        st.write(f"**Total: {len(report)} parts short**")

# ============================================================================
# DRILL-DOWN GRID
# ============================================================================
elif st.session_state.active_tab == "Drill-Down Grid":
    st.subheader("Build Plan (Filtered)")
    if build_plan_grid is not None and len(build_plan_grid) > 0:
        st.caption(f"Products planned by {cutoff.strftime('%Y-%m')} ({weeks_window} weeks)")
        st.dataframe(build_plan_grid, use_container_width=True)
    else:
        st.info("No build plan data for selected filters.")

    st.subheader("Demand, Supply & Inventory by Week")
    st.caption("3 rows per part: Demand | Supply | Inventory (color-coded: green=positive, red=negative)")

    if len(filtered) == 0:
        st.info("No shortages to display.")
    else:
        parts_to_show = filtered[["cm", "part"]].drop_duplicates()

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

                # Demand row (with Notes and Actions)
                demand_row = {"CM": cm, "Part": part, "Description": desc, "Metric": "Demand"}
                note_count = len(load_notes(part))
                demand_row["Notes"] = f"📝 {note_count}" if note_count > 0 else ""
                demand_row["Actions"] = "🏷️ 📋"
                for _, pab_row in part_pab.iterrows():
                    week_key = pab_row["period"].strftime("%Y-%m-%d")
                    demand_row[week_key] = int(pab_row["demand"])
                grid_data.append(demand_row)

                # Supply row
                supply_row = {"CM": cm, "Part": part, "Description": desc, "Metric": "Supply", "Notes": "", "Actions": ""}
                for _, pab_row in part_pab.iterrows():
                    week_key = pab_row["period"].strftime("%Y-%m-%d")
                    supply_row[week_key] = int(pab_row["receipts"])
                grid_data.append(supply_row)

                # Inventory row (will be color-coded)
                inv_row = {"CM": cm, "Part": part, "Description": desc, "Metric": "Inventory", "Notes": "", "Actions": ""}
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
                        if col in ["CM", "Part", "Description", "Metric"]:
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

        # Part management section
        st.divider()
        st.subheader("Part Management")

        # Create columns for part selection and action buttons
        part_col, action_col = st.columns([2, 1])
        with part_col:
            available_parts = sorted([f"{r[0]}@{r[1]}" for r in parts_to_show.values])
            if len(available_parts) > 0:
                selected_drill_part = st.selectbox(
                    "Select part to manage:",
                    options=available_parts,
                    key="manage_drill_part_select",
                    label_visibility="collapsed"
                )
                drill_cm, drill_part = selected_drill_part.split("@")
            else:
                st.info("No parts available for management")
                drill_cm, drill_part = None, None

        if drill_cm and drill_part:
            with action_col:
                col_exclude, col_note, col_view = st.columns(3)
                with col_exclude:
                    if st.button("🏷️ Exclude", key="drill_exclude_trigger", use_container_width=True,
                                help="Exclude this part from the report"):
                        open_exclude_dialog(drill_part)
                with col_note:
                    if st.button("➕ Add Note", key="drill_note_trigger", use_container_width=True,
                                help="Add a planner note"):
                        open_note_dialog(drill_part)
                with col_view:
                    if st.button("📖 View Notes", key="drill_view_trigger", use_container_width=True,
                                help="View all notes for this part"):
                        open_view_notes_dialog(drill_part)

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
            col_order = [
                "CM", "Part", "Description", "Products", "Last Demand",
                "Excess Qty", "Excess Cost", "Suggested Cancellations"
            ]
            report_df = report_df[[c for c in col_order if c in report_df.columns]]

            # Add Notes and Actions columns
            report_df["Notes"] = report_df["Part"].apply(
                lambda p: f"📝 {len(load_notes(p))}" if len(load_notes(p)) > 0 else ""
            )
            report_df["Actions"] = report_df["Part"].apply(
                lambda p: f"🏷️ 📋"
            )

            st.dataframe(report_df, use_container_width=True, height=500)

            # Part management section
            st.divider()
            st.subheader("Part Management")

            # Create columns for part selection and action buttons
            part_col, action_col = st.columns([2, 1])
            with part_col:
                selected_excess_part = st.selectbox(
                    "Select part to manage:",
                    options=sorted(report_df["Part"].unique()),
                    key="manage_excess_part_select",
                    label_visibility="collapsed"
                )

            with action_col:
                col_exclude, col_note, col_view = st.columns(3)
                with col_exclude:
                    if st.button("🏷️ Exclude", key="excess_exclude_trigger", use_container_width=True,
                                help="Exclude this part from the report"):
                        open_exclude_dialog(selected_excess_part)
                with col_note:
                    if st.button("➕ Add Note", key="excess_note_trigger", use_container_width=True,
                                help="Add a planner note"):
                        open_note_dialog(selected_excess_part)
                with col_view:
                    if st.button("📖 View Notes", key="excess_view_trigger", use_container_width=True,
                                help="View all notes for this part"):
                        open_view_notes_dialog(selected_excess_part)

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
