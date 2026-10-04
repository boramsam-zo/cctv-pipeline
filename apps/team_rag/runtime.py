"""Small adapter for the preserved teammate retrieval and six-section report."""
from __future__ import annotations

import os
import math
from pathlib import Path
import time

from .control_rag import ROOT, markdown, read, retrieve, validate

PIPELINE_ID = "team-rag-v1"
TEMPLATE_REVISION = "team-rag-relevance-20261005-v1"


def build_result(event, *, database=None, contacts=None, model_path=None, deadline_at=None):
    """Run existing CPU retrieval using a supplied local E5 model only.

    This never builds an index or downloads model weights. Production callers
    must separately approve and prepare the runtime/model before invocation.
    """
    validate(event)
    if deadline_at is not None and (type(deadline_at) not in (int, float) or not math.isfinite(deadline_at)):
        raise ValueError("team_rag_deadline_invalid")
    def check_budget():
        if deadline_at is not None and time.time() >= deadline_at:
            raise ValueError("team_rag_session_time_budget_exhausted")
    check_budget()
    selected_model = model_path or os.environ.get("RAG_MODEL_PATH")
    if not selected_model or not Path(selected_model).is_dir():
        raise ValueError("team_rag_local_model_not_prepared")
    selected_database = Path(database) if database else ROOT / "data/index.sqlite"
    if not selected_database.is_file():
        raise ValueError("team_rag_index_not_prepared")
    selected_contacts = contacts if contacts is not None else read(ROOT / "contacts.json")
    report = retrieve(selected_database, event, selected_contacts,
                      model_path=str(Path(selected_model).resolve()), budget_check=check_budget)
    return {"pipeline_id": PIPELINE_ID, "report_json": report,
            "report_markdown": markdown(report)}
