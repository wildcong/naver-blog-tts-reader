"""Server privacy policy; reader.py authenticates before accessing content."""

from pathlib import Path

from starlette.middleware import Middleware
import streamlit as st

from auth import PrivacyAuthMiddleware


app = st.App(
    Path(__file__).with_name("reader.py"),
    middleware=[Middleware(PrivacyAuthMiddleware)],
)
