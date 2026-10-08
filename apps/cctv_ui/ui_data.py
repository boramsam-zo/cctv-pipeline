"""Shared verified snapshots; cheap revisions are hints, never completion proof."""
from datetime import datetime, timezone
import os
import re

import streamlit as st

from . import client


@st.cache_data(ttl=2, max_entries=8, show_spinner=False)
def _revision(endpoint):
    value = client.request('/ui-revision')
    if not isinstance(value, dict) or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('revision', ''))):
        raise ValueError('invalid_ui_revision')
    return value['revision']


@st.cache_data(ttl=30, max_entries=8, show_spinner=False)
def _snapshot(endpoint, revision):
    value = client.request('/ui-snapshot')
    if (not isinstance(value, dict) or not isinstance(value.get('jobs'), list)
            or not isinstance(value.get('selections'), list)
            or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('revision', '')))):
        raise ValueError('invalid_ui_snapshot')
    # The API validates the original reports and media provenance. A revision
    # only chooses a cache entry; its change alone must never announce success.
    return dict(value, captured_at=datetime.now(timezone.utc).isoformat())


def load_snapshot(*, force=False):
    endpoint = os.environ.get('CCTV_API_URL', '')
    if force:
        _revision.clear(endpoint)
    revision = _revision(endpoint)
    if force:
        _snapshot.clear(endpoint, revision)
    return _snapshot(endpoint, revision)


def invalidate_snapshot():
    _revision.clear()
    _snapshot.clear()


@st.cache_data(ttl=1, max_entries=8, show_spinner=False)
def demo_status(endpoint):
    # Gateway/API admission and the ASGI boundary still check the deadline.
    return client.request('/demo/status')


@st.cache_data(ttl=60, max_entries=8, show_spinner=False)
def pipeline_metadata(endpoint):
    from .report_view import report_pipeline
    return report_pipeline(client.request)
