"""Streamlit UI. Thin — no calculation logic here. See BUILD_PROMPTS.md, Prompt 5."""

import streamlit as st

st.set_page_config(page_title="Lunar Material Planning", layout="wide")

# Password authentication
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("Lunar Material Planning")
    col1, col2 = st.columns([1, 2])
    with col1:
        pwd = st.text_input("Password", type="password", key="pwd_input")
        if st.button("Login"):
            if pwd == st.secrets.get("password", ""):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Invalid password")
    st.stop()

# Logged in — show the app
st.title("Lunar Material Planning")
if st.button("Logout", key="logout_btn"):
    st.session_state.authenticated = False
    st.rerun()

st.info("Not built yet. See BUILD_PROMPTS.md, Prompt 5.")
