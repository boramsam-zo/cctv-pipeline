"""Freeze generation choices per job; credentials never enter the snapshot.

Contract hashes use fixed synthetic inputs and the real request constructors.
They identify prompt/schema/query changes without importing model libraries or
calling providers. They are not hashes of a real job's media or API request.
"""
import os
from pathlib import Path
import re

from .common import WorkerError, digest

SCHEMA_VERSION = 'be-generation-settings-v1'
AB_SCHEMA_VERSION = 'be-generation-settings-ab-v1'
DEFAULT_MODEL = 'gemini-3.5-flash-lite'
SCENE_MODEL_ENV = 'BE_GEMINI_SCENE_MODEL'
REPORT_MODEL_ENV = 'BE_GEMINI_REPORT_MODEL'
KEY_PATH = Path('/run/secrets/be_gemini_api_key')

# This literal describes jobs admitted before settings snapshots existed.
# Never rebuild it from environment variables or newer prompt constants.
_LEGACY = {
    'schema_version': 'be-generation-settings-v1',
    'scene_model': 'gemini-3.5-flash-lite',
    'report_model': 'gemini-3.5-flash-lite',
    'scene_fps': 4,
    'scene_prompt_version': 'cctv-scene-v2',
    'scene_prompt_sha256': '78b6a7abd40ae600fc5283828daa47a5c980522ef478798eab71af1fcc1c7a11',
    'report_prompt_version': 'scene-report-v1',
    'report_prompt_sha256': '75340938de5c3612ee1ebd2bb8b6b758a01d529742056caf3f0916de2f08e037',
    'query_sha256': 'e50bb7d6f0a9ed838278fb90b018385acce9029b81df757f9b416819b865bce0',
    'embedding_profile': 'gemini-001-768-retrieval-v1',
}


def _model(value):
    # Syntax validation is not a claim that an account supports the model.
    if (not isinstance(value, str)
            or not re.fullmatch(r'gemini-[A-Za-z0-9][A-Za-z0-9.-]{0,120}', value)
            or 'embedding' in value.lower()):
        raise WorkerError('generation_model_invalid')
    return value


def _current_contract():
    # Local imports keep the settings module independent of job-module import
    # order. These modules only construct data here; no provider is contacted.
    from apps.scene_description import scene
    from apps.rag_index import index
    from . import report_jobs

    # P5 must not silently change the corpus/query embedding contract, even if
    # someone changes a model/dimension constant without renaming its profile.
    if (index.MODEL, index.DIMENSIONS, index.PROFILE) != (
            'gemini-embedding-001', 768, 'gemini-001-768-retrieval-v1'):
        raise WorkerError('generation_contract_changed')
    event = {
        'source_start_s': 0, 'source_end_s': 1,
        'candidate_time_s': 0.5, 'candidate_clip_time_s': 0.5,
        'representative_source_time_s': 0.5, 'yolo_objects': [],
    }
    result = {'description': {
        'accident_suspicion': 'uncertain', 'incident_types': ['unknown'],
        'summary': 'contract-sentinel',
        'observations': [{'text': 'contract-observation',
                          'clip_times_seconds': [0.5], 'asset_refs': ['clip']}],
        'uncertainties': ['contract-uncertainty'],
    }}
    return {
        'schema_version': SCHEMA_VERSION,
        'scene_fps': 4,
        'scene_prompt_version': scene.PROMPT_VERSION,
        'scene_prompt_sha256': digest(scene.build_request(
            event, {'clip': b'', 'frame': b''}, 4)),
        'report_prompt_version': report_jobs.VERSION,
        'report_prompt_sha256': digest(scene.encoded(
            report_jobs.report_request(result, [], '2000-01-01'))),
        'query_sha256': digest(report_jobs.query_from_description(result).encode()),
        'embedding_profile': index.PROFILE,
    }


def _ab_contract(analysis_profile):
    from .analysis_profiles import validate_profile
    from apps.scene_description import ab_scene
    try:
        frozen = validate_profile(analysis_profile)
    except ValueError:
        raise WorkerError('generation_analysis_profile_invalid') from None
    version = frozen['version']
    event = {
        'evidence_version': ab_scene.LOCAL_SCHEMA, 'version': version,
        'source_start_s': 0, 'source_end_s': 1,
        'candidate_time_s': .5, 'candidate_clip_time_s': .5,
        'representative_source_time_s': .5, 'display_sample_time_s': .5,
        'representative_within_clip': True,
        'frame_role': 'unannotated_frame' if version == 'A' else 'annotated_frame',
        'frame_mime': 'image/png' if version == 'A' else 'image/jpeg',
    }
    media = {'clip': b'', 'frame': b''}
    if version == 'A':
        event['yolo_objects'] = []
    else:
        event['incident_context'] = {
            'schema_version': '2.0', 'event_id': 'event_000',
            'candidate_time_s': .5, 'display_frame_time_s': .5,
            'accident_participant_candidates': [], 'tracks': [],
            'related_object_ranking': [], 'files': {},
            'vlm_instruction': 'untrusted contract sentinel',
        }
        event['class_mapping'] = {
            'checkpoint_names': {str(i): f'class_{i}' for i in range(4)},
            'effective_names': {'0': 'Car', '1': 'Truck', '2': 'Bus', '3': 'Motorcycle'},
            'mapping_source': 'provided_sidecar', 'independent_training_metadata_verified': False,
        }
        media['roi_clip'] = b''
    # Keep report/query/corpus identity from the actual common constructors.
    # Only the versioned evidence prompt/schema changes here; legacy is literal.
    contract = _current_contract()
    contract.update(schema_version=AB_SCHEMA_VERSION,
                    evidence_version=ab_scene.LOCAL_SCHEMA, analysis_version=version,
                    scene_prompt_version=ab_scene.PROMPT_VERSION,
                    scene_prompt_sha256=digest(ab_scene.build_request(event, media, 4)))
    return contract


def from_environment(environ=None, *, analysis_profile=None):
    """Read model defaults once at admission, without reading any credentials."""
    environ = os.environ if environ is None else environ
    from . import team_settings
    if team_settings.enabled(environ):
        return team_settings.from_environment(environ, analysis_profile)
    return validate(dict(
        _current_contract() if analysis_profile is None else _ab_contract(analysis_profile),
        scene_model=_model(environ.get(SCENE_MODEL_ENV, DEFAULT_MODEL)),
        report_model=_model(environ.get(REPORT_MODEL_ENV, DEFAULT_MODEL)),
    ), analysis_profile=analysis_profile)


def legacy_settings():
    """Return the fixed pre-P5 configuration, unaffected by newer defaults."""
    return dict(_LEGACY)


def validate(snapshot, *, analysis_profile=None):
    """Validate saved settings; optional profile binds the selected A/B version."""
    if not isinstance(snapshot, dict):
        raise WorkerError('generation_settings_invalid')
    from . import team_settings
    if team_settings.is_team(snapshot):
        return team_settings.validate(snapshot, analysis_profile)
    is_ab = snapshot.get('schema_version') == AB_SCHEMA_VERSION
    expected_keys = set(_LEGACY) | ({'evidence_version', 'analysis_version'} if is_ab else set())
    if set(snapshot) != expected_keys:
        raise WorkerError('generation_settings_invalid')
    if type(snapshot['scene_fps']) is not int:
        raise WorkerError('generation_settings_invalid')
    if any(type(value) is not str for name, value in snapshot.items() if name != 'scene_fps'):
        raise WorkerError('generation_settings_invalid')
    _model(snapshot['scene_model'])
    _model(snapshot['report_model'])
    if is_ab:
        from .analysis_profiles import profile
        try:
            frozen = profile(snapshot['analysis_version']) if analysis_profile is None else analysis_profile
        except ValueError:
            raise WorkerError('generation_contract_changed') from None
        current = _ab_contract(frozen)
    else:
        if analysis_profile is not None:
            raise WorkerError('generation_contract_changed')
        current = _current_contract()
    if any(snapshot[name] != value for name, value in current.items()):
        raise WorkerError('generation_contract_changed')
    return dict(snapshot)


def read_generation_key():
    """Read the Compose secret afresh for each new call, never from key env vars."""
    try:
        value = KEY_PATH.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        raise WorkerError('generation_secret_unavailable') from None
    if not value or any(character.isspace() or ord(character) < 32 or ord(character) == 127
                        for character in value):
        raise WorkerError('generation_secret_unavailable')
    return value
