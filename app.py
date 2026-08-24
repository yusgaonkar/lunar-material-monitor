"""Streamlit UI. Thin — no calculation logic here. See BUILD_PROMPTS.md, Prompt 5."""

import streamlit as st

st.set_page_config(page_title="Lunar Material Planning", layout="wide")

# Initialize session state
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

# Check if we have the required password in secrets
try:
    REQUIRED_PASSWORD = st.secrets.get("password")
except Exception as e:
    st.error(f"Error loading secrets: {e}")
    REQUIRED_PASSWORD = None

# Authentication gate
if not st.session_state.authenticated:
    st.title("Lunar Material Planning")

    # Show debug info
    if REQUIRED_PASSWORD:
        st.info(f"✓ Password loaded from secrets")
    else:
        st.error("✗ No password found in secrets!")

    # Login form
    st.subheader("Login Required")
    password_input = st.text_input(
        "Enter password:",
        type="password",
        placeholder="Enter the app password"
    )

    if st.button("Login", type="primary"):
        if REQUIRED_PASSWORD is None:
            st.error("No password configured. Contact admin.")
        elif password_input == REQUIRED_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("❌ Incorrect password. Try again.")

    st.stop()

# ============ REST OF APP (only shown if authenticated) ============
st.title("Lunar Material Planning")

# Logout button
col1, col2 = st.columns([10, 1])
with col2:
    if st.button("🚪 Logout"):
        st.session_state.authenticated = False
        st.rerun()

st.info("Not built yet. See BUILD_PROMPTS.md, Prompt 5.")
