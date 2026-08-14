"""Supabase backend for persistent storage (exclusions, notes).

Replaces CSV file operations with API calls to Supabase tables.
"""

import json
import logging
from datetime import datetime
import pandas as pd
from supabase import create_client, Client

log = logging.getLogger(__name__)

# Initialize client (credentials come from Streamlit secrets)
supabase: Client | None = None


def init_supabase(url: str, key: str) -> Client:
    """Initialize Supabase client with credentials."""
    global supabase
    supabase = create_client(url, key)
    return supabase


def load_exclusions() -> pd.DataFrame:
    """Load exclusions from Supabase table, return as DataFrame matching CSV schema."""
    if supabase is None:
        raise RuntimeError("Supabase not initialized. Call init_supabase() first.")

    try:
        response = supabase.table("exclusions").select("*").execute()
        data = response.data

        if not data:
            # Return empty DataFrame with expected columns
            return pd.DataFrame(columns=["component_lpn", "reason", "added_by", "added_date"])

        # Convert to DataFrame
        df = pd.DataFrame(data)

        # Ensure correct column names and types
        df = df[["component_lpn", "reason", "added_by", "added_date"]].copy()
        df["added_date"] = pd.to_datetime(df["added_date"])

        log.info(f"Loaded {len(df)} exclusions from Supabase")
        return df

    except Exception as e:
        log.error(f"Error loading exclusions: {e}")
        # Return empty DataFrame so app doesn't crash
        return pd.DataFrame(columns=["component_lpn", "reason", "added_by", "added_date"])


def save_exclusions(df: pd.DataFrame) -> bool:
    """Save exclusions to Supabase table."""
    if supabase is None:
        raise RuntimeError("Supabase not initialized.")

    try:
        # Delete all and re-insert (simple upsert)
        supabase.table("exclusions").delete().neq("id", 0).execute()

        if len(df) == 0:
            return True

        # Prepare data for insert
        records = df[["component_lpn", "reason", "added_by", "added_date"]].to_dict("records")

        # Convert dates to ISO format strings
        for record in records:
            if pd.notna(record["added_date"]):
                if isinstance(record["added_date"], str):
                    record["added_date"] = record["added_date"][:10]  # YYYY-MM-DD
                else:
                    record["added_date"] = record["added_date"].strftime("%Y-%m-%d")

        supabase.table("exclusions").insert(records).execute()
        log.info(f"Saved {len(records)} exclusions to Supabase")
        return True

    except Exception as e:
        log.error(f"Error saving exclusions: {e}")
        return False


def exclude_part(component_lpn: str, reason: str, added_by: str) -> bool:
    """Add a part to exclusions."""
    if supabase is None:
        raise RuntimeError("Supabase not initialized.")

    try:
        record = {
            "component_lpn": component_lpn,
            "reason": reason,
            "added_by": added_by,
            "added_date": datetime.now().strftime("%Y-%m-%d"),
        }
        supabase.table("exclusions").insert([record]).execute()
        log.info(f"Excluded part {component_lpn}")
        return True
    except Exception as e:
        log.error(f"Error excluding part {component_lpn}: {e}")
        return False


def un_exclude_part(component_lpn: str) -> bool:
    """Remove a part from exclusions."""
    if supabase is None:
        raise RuntimeError("Supabase not initialized.")

    try:
        supabase.table("exclusions").delete().eq("component_lpn", component_lpn).execute()
        log.info(f"Un-excluded part {component_lpn}")
        return True
    except Exception as e:
        log.error(f"Error un-excluding part {component_lpn}: {e}")
        return False


def load_notes(component_lpn: str) -> list[dict]:
    """Load notes for a component, return as list of dicts."""
    if supabase is None:
        raise RuntimeError("Supabase not initialized.")

    try:
        response = (
            supabase.table("notes")
            .select("*")
            .eq("component_lpn", component_lpn)
            .order("timestamp", desc=True)
            .execute()
        )

        notes = response.data

        # Convert timestamp strings to readable format if needed
        for note in notes:
            if isinstance(note["timestamp"], str):
                # Keep as ISO string, will be formatted in UI
                pass

        log.info(f"Loaded {len(notes)} notes for {component_lpn}")
        return notes

    except Exception as e:
        log.error(f"Error loading notes for {component_lpn}: {e}")
        return []


def save_note(component_lpn: str, note_text: str, note_user: str) -> bool:
    """Add a note for a component."""
    if supabase is None:
        raise RuntimeError("Supabase not initialized.")

    try:
        record = {
            "component_lpn": component_lpn,
            "note": note_text,
            "note_user": note_user,
            "timestamp": datetime.now().isoformat(),
        }
        supabase.table("notes").insert([record]).execute()
        log.info(f"Saved note for {component_lpn}")
        return True
    except Exception as e:
        log.error(f"Error saving note for {component_lpn}: {e}")
        return False


def get_all_excluded_parts() -> set[str]:
    """Get set of all excluded part LPNs."""
    df = load_exclusions()
    return set(df["component_lpn"].unique()) if len(df) > 0 else set()
