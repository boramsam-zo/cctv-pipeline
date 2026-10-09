"""Frozen contract for newly admitted B/team reports; no provider/model imports."""
from pathlib import Path
import os
import re

from .common import WorkerError, digest

SCHEMA = 'be-generation-settings-team-rag-v1'
PIPELINE = 'team-rag-v1'
ROOT = Path(__file__).resolve().parents[1]
HASH_PATHS = {'scene_prompt_sha256': 'team_rag/VLM_PROMPT.md',
              'event_schema_sha256': 'team_rag/event.schema.json',
              'scene_adapter_sha256': 'scene_description/team_scene.py',
              'report_code_sha256': 'team_rag/control_rag.py',
              'report_runtime_sha256': 'team_rag/runtime.py',
              'model_manifest_sha256': 'team_rag/e5-model-manifest.json',
              'corpus_sha256': 'team_rag/data/chunks.jsonl',
              'index_sha256': 'team_rag/data/index.sqlite',
              'contacts_sha256': 'team_rag/contacts.json'}


def enabled(environ=None):
    value = (os.environ if environ is None else environ).get('BE_REPORT_PIPELINE', 'legacy')
    if value not in ('legacy', PIPELINE):
        raise WorkerError('report_pipeline_invalid')
    return value == PIPELINE


def is_team(value):
    return isinstance(value, dict) and value.get('schema_version') == SCHEMA


def metadata():
    team = enabled()
    result = {'pipeline_id': PIPELINE if team else 'legacy',
              'document_reference_date_supported': not team}
    from .analysis_profiles import supported_versions, MLOPS_DEPLOYMENT
    if os.environ.get('CCTV_ANALYSIS_DEPLOYMENT') == MLOPS_DEPLOYMENT:
        from .model_catalog import contract_for_version
        result['analysis_models'] = [dict(selection=version, **contract_for_version(version))
                                     for version in supported_versions()]
    return result


def _schema_fields(version):
    """Literal v1 semantics, including historical records; no release reads."""
    if version not in ('B', 'R2', 'LC'):
        raise WorkerError('team_rag_requires_version_b')
    return dict(schema_version=SCHEMA, report_pipeline=PIPELINE,
                scene_fps=4, scene_prompt_version='cctv-team-scene-v2-01',
                report_model='cpu-template', report_prompt_version='team-report-2.1-fixes-v1',
                embedding_profile='multilingual-e5-small-384-team-v1',
                embedding_revision='614241f622f53c4eeff9890bdc4f31cfecc418b3',
                analysis_version=version, document_reference_date_supported=False)


def contract(version='B'):
    fields = _schema_fields(version)
    try:
        hashes = {key: digest((ROOT / name).read_bytes()) for key, name in HASH_PATHS.items()}
    except OSError:
        raise WorkerError('team_rag_release_files_missing') from None
    return dict(hashes, **fields)


def validate_stored(value, profile):
    """Read-only shape validation; this does not authorize using old settings.

    The calling view must bind the values to its saved task ID and immutable
    task receipt. A SHA string here is provenance, not a digital signature.
    Admission/resume/execution must continue using validate() below.
    """
    from .analysis_profiles import validate_profile
    from .generation_settings import _model
    try:
        frozen = validate_profile(profile)
        expected = _schema_fields(frozen['version'])
    except (ValueError, TypeError):
        raise WorkerError('team_rag_requires_version_b') from None
    if not isinstance(value, dict) or set(value) != set(expected) | set(HASH_PATHS) | {'scene_model'}:
        raise WorkerError('team_generation_settings_invalid')
    if (any(value[key] != item or type(value[key]) is not type(item) for key, item in expected.items())
            or any(not isinstance(value[key], str) or not re.fullmatch('[a-f0-9]{64}', value[key])
                   for key in HASH_PATHS)):
        raise WorkerError('team_stored_generation_settings_invalid')
    _model(value['scene_model'])
    return dict(value)


def validate(value, profile):
    from .analysis_profiles import validate_profile
    from .generation_settings import _model
    try:
        frozen = validate_profile(profile)
        if frozen['version'] not in ('B', 'R2', 'LC'):
            raise ValueError('unsupported_team_classifier')
    except (ValueError, TypeError):
        raise WorkerError('team_rag_requires_version_b') from None
    expected = contract(frozen['version'])
    if not isinstance(value, dict) or set(value) != set(expected) | {'scene_model'}:
        raise WorkerError('team_generation_settings_invalid')
    if any(value[key] != item or type(value[key]) is not type(item) for key, item in expected.items()):
        raise WorkerError('team_generation_contract_changed')
    _model(value['scene_model'])
    return dict(value)


def from_environment(environ, profile):
    from .generation_settings import _model, DEFAULT_MODEL, SCENE_MODEL_ENV
    version = profile.get('version') if isinstance(profile, dict) else None
    return validate(dict(contract(version), scene_model=_model(environ.get(SCENE_MODEL_ENV, DEFAULT_MODEL))), profile)


def reference_date(value, day):
    if is_team(value):
        if day is not None:
            raise WorkerError('team_date_filter_unsupported')
        return None
    from datetime import date
    return date.fromisoformat(str(day)).isoformat()
