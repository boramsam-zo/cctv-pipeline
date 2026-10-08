"""Read-only schema-definition reuse without changing the execution adapter.

The pinned team_scene source and its execution contract remain authoritative.
Only flow_service.view's explicit scope uses this adapter; generation and worker
calls delegate to the original contract. Every call still reads, checks file
integrity, and creates a fresh schema/validator for the actual saved response.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from pathlib import Path
from threading import Lock

from apps.scene_description import scene, team_scene


ADAPTER_VERSION = 'cctv-ui-schema-validation-v1'
_read_only = ContextVar('cctv_ui_readonly_validation', default=False)
_install_lock = Lock()
_installed = False
_original_contract = team_scene.contract


@lru_cache(maxsize=8)
def _check_schema_definition(raw):
    """Remember only successful definition checks of immutable verified bytes."""
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(scene.parse(raw))


def _read_contract(directory=team_scene.CONTRACT_DIRECTORY):
    from jsonschema import Draft202012Validator
    texts, verified_bytes = {}, {}
    for name, expected in team_scene.CONTRACT_HASHES.items():
        raw = team_scene._read(Path(directory) / name, 65536)
        team_scene._require(scene.sha(raw) == expected, 'team_scene_contract_changed')
        texts[name] = raw.decode('utf-8')
        verified_bytes[name] = raw
    _check_schema_definition(verified_bytes['event.schema.json'])
    # No mutable schema or validator is cached or shared between callers.
    schema = scene.parse(texts['event.schema.json'])
    return schema, texts['VLM_PROMPT.md'], Draft202012Validator(schema)


@wraps(_original_contract)
def _scoped_contract(*args, **kwargs):
    if _read_only.get():
        return _read_contract(*args, **kwargs)
    return _original_contract(*args, **kwargs)


def _install_adapter():
    global _installed
    with _install_lock:
        if not _installed:
            team_scene.contract = _scoped_contract
            _installed = True


@contextmanager
def readonly_validation():
    """Opt in for one saved-result view, isolated from other threads/tasks."""
    _install_adapter()
    token = _read_only.set(True)
    try:
        yield
    finally:
        _read_only.reset(token)
