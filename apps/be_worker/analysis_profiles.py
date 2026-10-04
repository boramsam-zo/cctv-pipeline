"""Frozen A/B identities; no admission, storage, models or network operations.

These are the two sources reproduced in P4, not a registry of model names.
P3 contract/source-files and the P4 image manifest are checked by the companion
tests. A label alone cannot attest to the profile of a historical result.

GPU identity deliberately excludes purpose, Gemini settings and document date.
The child analysis request key additionally binds an explicit purpose. It is NOT
a complete report workflow ID: a future full-report workflow must also bind its
frozen generation settings and reference date, while reusing this GPU identity.
The original snapshots stay readable independently of the active deployment.
New deployments require an explicitly selected, packaged release catalog.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re

PROFILE_SCHEMA = 'cctv-analysis-profile-v1'
VERSIONS = ('A', 'B')  # Historical A/B catalog; use supported_versions for new admission.
PURPOSES = ('gpu_comparison', 'full_report')
LEGACY_DEPLOYMENT = 'a4500-p4-legacy'
RTX2000ADA_DEPLOYMENT = 'rtx2000ada-20261003-v1'
PORTABLE_DEPLOYMENT = 'cuda-portable-20261003-v1'
PORTABLE_GPU = 'NVIDIA CUDA GPU'
MLOPS_DEPLOYMENT = 'mlops-20261004-v1'
_RELEASE_CATALOGS = {
    RTX2000ADA_DEPLOYMENT: Path(__file__).with_name(RTX2000ADA_DEPLOYMENT + '.json'),
    PORTABLE_DEPLOYMENT: Path(__file__).with_name(PORTABLE_DEPLOYMENT + '.json'),
    MLOPS_DEPLOYMENT: Path(__file__).with_name(MLOPS_DEPLOYMENT + '.json'),
}
_RELEASE_GPUS = {RTX2000ADA_DEPLOYMENT: 'NVIDIA RTX 2000 Ada Generation',
                 PORTABLE_DEPLOYMENT: PORTABLE_GPU, MLOPS_DEPLOYMENT: PORTABLE_GPU}

_IMAGE_MANIFEST = 'ce53bcbaa27a4ff5edfc77b688756a8aae45f9a572755a19ad7ddf8430dfccd2'
_ADAPTER = '766134f063e2f2e5b0e5699078b8662e75fc3493550ad42d2612644c847c93a0'
_X3D = {'sha256': 'ca2db4a25c9cd2d859a7143932d911a3351beacc5193de3ada16f3115bea09bf',
        'bytes': 36344683}
_COMMON_CODE = {
    'sources/cctv-runpod-a4000/worker.py': '41126a160970d9d3a64c9fe93ab850f5cd24891037a7aaf9644e89754c667473',
    'sources/cctv-runpod-a4000/verify_bundle.py': '45aa8f82e3aec1bff31533f709f1f8af34b4e504e4467a4b3ec3741d10c62e99',
    'sources/cctv-runpod-a4000/evidence.py': 'c2c639876ef577f068182c8e09cdf7adcbf0349ef881b2d861da1f9b0ccfbc1f',
    'sources/cctv-runpod-a4000/local_hub/hubconf.py': '0162b333adf9b336459658f1d982e02ed49ee06ce035409fa0a531cf711671b0',
}
_RUNTIME = {
    'python': '3.12.3', 'cuda_runtime': '12.8', 'gpu': 'NVIDIA RTX A4500',
    'packages': {
        'torch': '2.8.0+cu128', 'torchvision': '0.23.0+cu128',
        'ultralytics': '8.4.155', 'pytorchvideo': '0.1.5',
        'fvcore': '0.1.5.post20221221', 'iopath': '0.1.10', 'av': '16.1.0',
        'numpy': '2.2.6', 'opencv-python': '4.12.0.88',
        'parameterized': '0.9.0', 'networkx': '3.3',
    },
}

# The source-files hashes cover every frozen source file, including inference
# code and settings. comparison_version_sha256 binds the P3 version description.
# image_manifest_sha256 additionally binds the packaged environment and adapter.
_PROFILES = {
    'A': {
        'schema_version': PROFILE_SCHEMA,
        'version': 'A',
        'source_archive_sha256': '4428fa3ebb2a6044a511712eb1a0b0940640ca880f42837d8a6636a9be3dbff4',
        'source_files_sha256': '1e9de94bee0de0227be4dc95b8cb68cb5f83247023cc9ba2348d3390689e3690',
        'comparison_version_sha256': 'fd5411ed9769d04aa694d4b4d8071c9b3978d5d88eb140072725eabb7a4c4748',
        'image_manifest_sha256': _IMAGE_MANIFEST,
        'execution_adapter_sha256': _ADAPTER,
        'code_files': {
            **_COMMON_CODE,
            'sources/cctv-runpod-a4000/handoff/pipeline.py': '18a5a2bca54395332e227b6e75f4aef11e8b703140e48d0ae7221f1ffca35d60',
            'sources/cctv-runpod-a4000/handoff/timeline.py': '86ea59d83623554fedf4e08df95f4dec07bddbf41486b6a6493f4d21daf77f20',
        },
        'runtime': deepcopy(_RUNTIME),
        'execution_settings': {'device': 'cuda', 'batch_size': 8,
                               'evidence_before_s': 3.0, 'evidence_after_s': 3.0},
        'model_files': {
            'handoff/models/x3d_s_real_so_tad_epoch7_best_model.pt': deepcopy(_X3D),
            'handoff/models/yolo11s_accident_150_e30_best.pt': {
                'sha256': '44f7e937068ec8862e30245314ec1cd54d6782e716eec1abc3fcf3ab7b761007',
                'bytes': 19156378},
        },
    },
    'B': {
        'schema_version': PROFILE_SCHEMA,
        'version': 'B',
        'source_archive_sha256': 'd6ca3e617968f4db1ae3ed23a7ab2210b1ec6d8b11efd07c48481ed2055e678e',
        'source_files_sha256': '109b4ea424370b6c98b858a36863287680ef323e10478c0d04f241bd51b507d2',
        'comparison_version_sha256': '5425e27ae403a252d6f94bd929e9966265dda258b910ef4bf285f9b74141e36a',
        'image_manifest_sha256': _IMAGE_MANIFEST,
        'execution_adapter_sha256': _ADAPTER,
        'code_files': {
            **_COMMON_CODE,
            'sources/deployment_handoff_1001/pipeline.py': 'b223671abbad3581c03262ff260b62c712b81e03eec468f04374f082012359ec',
            'sources/deployment_handoff_1001/timeline.py': '4321268ba8c6f34d3c4ad04e06d06d608021f5a1268e269a2b1af74334ebc103',
            'sources/deployment_handoff_1001/incident_context.py': 'fe329a2174f63455594b19b94c328c9c4b5c1577d1bcdbbe3f2158df009b493a',
        },
        'runtime': deepcopy(_RUNTIME),
        'execution_settings': {'device': 'cuda', 'batch_size': 8,
                               'evidence_before_s': 3.0, 'evidence_after_s': 5.0},
        'model_files': {
            'models/x3d_s_real_so_tad_epoch7_best_model.pt': deepcopy(_X3D),
            'models/yolo11s_so_tad_985_no_class4_960_b16_best.pt': {
                'sha256': '51b8873f49d6ddf8611de6e3bd2bc88fca00d6d5de62638791d01b25fd9a2dcf',
                'bytes': 19214682},
            'models/yolo11s_so_tad_985_no_class4_960_b16_classes.json': {
                'sha256': '5b325b332dac34daba4e381c9efc33d696c70a9ec30d5a33b0f89e562e7e3457',
                'bytes': 456},
        },
    },
}


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise ValueError('analysis_identity_invalid_json') from None


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _input_sha(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('analysis_input_sha256_invalid')
    return value


def _version(version):
    if not isinstance(version, str) or version not in (*VERSIONS, 'R2'):
        raise ValueError('analysis_version_unknown')
    return version


def valid_gpu_name(value):
    """Validate an observed CUDA device name, never a selector or contract marker."""
    return (isinstance(value, str) and 1 <= len(value) <= 160
            and value == value.strip() and value.isascii() and value.isprintable()
            and value.casefold() not in {'auto', 'nvidia cuda gpu', 'gpu', 'cuda', 'unknown', 'none', 'null'}
            and re.search(r'\bcpu\b', value, re.I) is None)


def _release_profiles(deployment):
    """Read a fixed packaged descriptor, never a caller-selected file path.

    The service/BE bundle manifests bind this small catalog along with code.
    The descriptor only changes GPU and packaging/adapter provenance; every
    model, inference source, call setting and package remains pinned above.
    """
    if deployment not in _RELEASE_GPUS or deployment not in _RELEASE_CATALOGS:
        raise ValueError('analysis_deployment_unknown')
    if deployment == MLOPS_DEPLOYMENT:
        return _mlops_profiles()
    path = _RELEASE_CATALOGS[deployment]
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            raise ValueError('analysis_deployment_catalog_invalid')
        release = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError('analysis_deployment_catalog_unavailable') from None
    if (not isinstance(release, dict) or set(release) != {
            'schema_version', 'deployment', 'gpu', 'image_manifest_sha256',
            'execution_adapter_sha256'}
            or release['schema_version'] != 'cctv-analysis-deployment-v1'
            or release['deployment'] != deployment
            or release['gpu'] != _RELEASE_GPUS[deployment]):
        raise ValueError('analysis_deployment_catalog_invalid')
    hashes = [release['image_manifest_sha256'], release['execution_adapter_sha256']]
    if (any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
            or len(set(value)) < 2 for value in hashes)
            or len(set(hashes)) != 2 or hashes[0] == _IMAGE_MANIFEST or hashes[1] == _ADAPTER):
        raise ValueError('analysis_deployment_catalog_provenance_invalid')
    result = deepcopy(_PROFILES)
    for value in result.values():
        value['runtime']['gpu'] = release['gpu']
        for field in ('image_manifest_sha256', 'execution_adapter_sha256'):
            value[field] = release[field]
    return result


def _mlops_profiles(path=None):
    from .model_catalog import contract_for_version
    path = _RELEASE_CATALOGS[MLOPS_DEPLOYMENT] if path is None else path
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            raise ValueError('analysis_deployment_catalog_invalid')
        release = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError('analysis_deployment_catalog_unavailable') from None
    required = {'schema_version', 'deployment', 'gpu', 'image_manifest_sha256',
                'execution_adapter_sha256', 'code_files'}
    if (set(release) not in (required, required | {'classifiers'})
            or release['schema_version'] != 'cctv-mlops-deployment-v1'
            or release['deployment'] != MLOPS_DEPLOYMENT or release['gpu'] != PORTABLE_GPU
            or not isinstance(release['code_files'], dict) or not release['code_files']):
        raise ValueError('analysis_deployment_catalog_invalid')
    for value in [release['image_manifest_sha256'], release['execution_adapter_sha256'],
                  *release['code_files'].values()]:
        _input_sha(value)
    contracts = release.get('classifiers', {version: contract_for_version(version) for version in ('B', 'R2')})
    if not isinstance(contracts, dict) or set(contracts) != {'B', 'R2'}:
        raise ValueError('analysis_deployment_classifier_contracts_invalid')
    for version, contract in contracts.items():
        if (not isinstance(contract, dict) or set(contract) != set(contract_for_version(version))
                or contract['model_id'] != contract_for_version(version)['model_id']):
            raise ValueError('analysis_deployment_classifier_contracts_invalid')
        _input_sha(contract['weights_sha256'])
        _canonical(contract)
    result = {}
    for version in ('B', 'R2'):
        item = deepcopy(_PROFILES['B'])
        item.update(version=version, deployment=MLOPS_DEPLOYMENT,
                    classifier=deepcopy(contracts[version]))
        item['runtime']['gpu'] = PORTABLE_GPU
        item['code_files'].update(release['code_files'])
        for field in ('image_manifest_sha256', 'execution_adapter_sha256'):
            item[field] = release[field]
        if version == 'R2':
            del item['model_files']['models/x3d_s_real_so_tad_epoch7_best_model.pt']
            item['model_files']['models/r2plus1d_18/candidate-epoch-7.pt'] = {
                'sha256': item['classifier']['weights_sha256'],
                'bytes': item['classifier']['weights_bytes']}
        result[version] = item
    return result


def _mlops_snapshots():
    """Trusted packaged release history, never arbitrary request-supplied catalogs."""
    current = _RELEASE_CATALOGS.get(MLOPS_DEPLOYMENT)
    paths = [current] if current is not None else []
    archive = Path(__file__).with_name('model_releases')
    if archive.is_dir() and not archive.is_symlink():
        for path in sorted(archive.glob('*.json')):
            try:
                if path.is_symlink() or not re.fullmatch('[a-f0-9]{64}', path.stem) or path.stat().st_size > 65536:
                    continue
                if _sha(json.loads(path.read_text(encoding='utf-8'))) == path.stem:
                    paths.append(path)
            except (OSError, ValueError):
                continue
    result = []
    for path in paths:
        try:
            result.append(_mlops_profiles(path))
        except (ValueError, OSError):
            continue  # A damaged newer release must not hide a valid saved release.
    return result


def companion_profiles(snapshot):
    """Resolve the exact release pair of a saved request, not today's defaults."""
    frozen = validate_profile(snapshot)
    if frozen.get('deployment') == MLOPS_DEPLOYMENT:
        for entries in _mlops_snapshots():
            if _canonical(entries[frozen['version']]) == _canonical(frozen):
                return entries
        raise ValueError('analysis_profile_changed')
    deployment = deployment_for_profile(frozen)
    return {version: profile(version, deployment=deployment) for version in supported_versions(deployment)}


def supported_versions(deployment=None):
    selected = os.environ.get('CCTV_ANALYSIS_DEPLOYMENT', LEGACY_DEPLOYMENT) if deployment is None else deployment
    if selected == MLOPS_DEPLOYMENT:
        return ('B', 'R2')
    if selected != LEGACY_DEPLOYMENT and selected not in _RELEASE_CATALOGS:
        raise ValueError('analysis_deployment_unknown')
    return ('A', 'B')


def profile(version, *, deployment=None):
    """Select one trusted runtime snapshot; no selector preserves legacy identity."""
    _version(version)
    selected = os.environ.get('CCTV_ANALYSIS_DEPLOYMENT', LEGACY_DEPLOYMENT) if deployment is None else deployment
    if not isinstance(selected, str):
        raise ValueError('analysis_deployment_unknown')
    if selected == LEGACY_DEPLOYMENT:
        if version not in _PROFILES:
            raise ValueError('analysis_version_not_in_deployment')
        return deepcopy(_PROFILES[version])
    result = _release_profiles(selected)
    if version not in result:
        raise ValueError('analysis_version_not_in_deployment')
    return result[version]


def trusted_profiles(version):
    """Return readable frozen releases independently of the runtime selector."""
    _version(version)
    result = [deepcopy(_PROFILES[version])] if version in _PROFILES else []
    for deployment, path in _RELEASE_CATALOGS.items():
        if deployment == MLOPS_DEPLOYMENT:
            for entries in _mlops_snapshots():
                if version in entries:
                    result.append(entries[version])
            continue
        if path.exists() or path.is_symlink():
            available = _release_profiles(deployment)
            if version in available:
                result.append(available[version])
    return result


def validate_profile(snapshot, *, expected_version=None):
    """Require full frozen provenance; missing legacy provenance never means A."""
    if not isinstance(snapshot, dict):
        raise ValueError('analysis_profile_required')
    version = _version(snapshot.get('version'))
    if expected_version is not None:
        _version(expected_version)  # Selection validation must not select a deployment.
        if snapshot['version'] != expected_version:
            raise ValueError('analysis_profile_version_mismatch')
    # Canonical bytes also distinguish bool/int and changed numeric types, which
    # ordinary dict equality would conflate. Object key order is immaterial.
    canonical = _canonical(snapshot)
    # Read legacy first so historical results remain valid even if a new
    # deployment is unavailable or its packaged descriptor has been damaged.
    if version in _PROFILES and canonical == _canonical(_PROFILES[version]):
        return deepcopy(_PROFILES[version])
    if snapshot.get('deployment') == MLOPS_DEPLOYMENT:
        for entries in _mlops_snapshots():
            known = entries.get(version)
            if known is not None and canonical == _canonical(known):
                return known
        raise ValueError('analysis_profile_changed')
    claimed_runtime = snapshot.get('runtime')
    claimed_gpu = claimed_runtime.get('gpu') if isinstance(claimed_runtime, dict) else None
    # Route only to the descriptor named by this frozen contract. A missing or
    # damaged current-release catalog must not invalidate a historical release.
    for deployment, path in _RELEASE_CATALOGS.items():
        if deployment == MLOPS_DEPLOYMENT:
            continue
        if claimed_gpu == _RELEASE_GPUS.get(deployment) and (path.exists() or path.is_symlink()):
            known = _release_profiles(deployment).get(version)
            if known is not None and canonical == _canonical(known):
                return known
    raise ValueError('analysis_profile_changed')


def deployment_for_profile(snapshot):
    """Resolve an exact trusted snapshot without consulting runtime selection."""
    frozen = validate_profile(snapshot)
    if frozen.get('deployment') == MLOPS_DEPLOYMENT:
        return MLOPS_DEPLOYMENT
    if frozen['version'] in _PROFILES and _canonical(frozen) == _canonical(_PROFILES[frozen['version']]):
        return LEGACY_DEPLOYMENT
    for deployment, gpu in _RELEASE_GPUS.items():
        if frozen['runtime']['gpu'] == gpu:
            return deployment
    raise ValueError('analysis_profile_changed')


def gpu_identity(input_sha256, snapshot):
    """Identify reusable GPU evidence, independently of downstream report work."""
    frozen = validate_profile(snapshot)
    # Provenance-only changes (image packaging, archive/docs, comparison notes)
    # do not invalidate GPU evidence. The complete profile must still be trusted.
    # Changes to inference code, weights, call settings or runtime DO change it.
    # The portable CUDA profile names a shared contract, so completed evidence
    # can be reused across GPU models; observed device names remain in outputs.
    pipeline = {name: frozen[name] for name in (
        'schema_version', 'version', 'model_files', 'code_files', 'runtime',
        'execution_settings', 'execution_adapter_sha256')}
    if 'classifier' in frozen:
        pipeline['classifier'] = frozen['classifier']
    body = dict(schema_version='cctv-gpu-identity-v1',
                input_sha256=_input_sha(input_sha256), pipeline_sha256=_sha(pipeline))
    return dict(body, profile=frozen, profile_sha256=_sha(frozen), gpu_key='gpu-v1:' + _sha(body))


def request_identity(input_sha256, snapshot, purpose):
    """Identify a GPU child by purpose, not a full report or its generation/date.

    Later full-report workflow deduplication must add frozen generation settings
    and reference date. Computing this analysis key submits or authorizes nothing.
    """
    if not isinstance(purpose, str) or purpose not in PURPOSES:
        raise ValueError('analysis_purpose_unknown')
    gpu = gpu_identity(input_sha256, snapshot)
    body = dict(schema_version='cctv-analysis-request-v1',
                input_sha256=gpu['input_sha256'], pipeline_sha256=gpu['pipeline_sha256'],
                purpose=purpose)
    return dict(body, profile=gpu['profile'], profile_sha256=gpu['profile_sha256'], gpu_key=gpu['gpu_key'],
                request_key='analysis-v1:' + _sha(body))


def selection_identity(input_sha256, selection, purpose):
    """A+B means the ordered A then B children; each reuses its standalone key.

    This is an identity description, not a transaction or worker scheduling API.
    Successful-result reuse still requires the caller to verify the saved result.
    """
    choices = {'A': ('A',), 'B': ('B',), 'A+B': ('A', 'B'), 'R2': ('R2',)}
    if not isinstance(selection, str) or selection not in choices:
        raise ValueError('analysis_selection_unknown')
    children = [request_identity(input_sha256, profile(version), purpose)
                for version in choices[selection]]
    body = dict(schema_version='cctv-analysis-selection-v1',
                input_sha256=_input_sha(input_sha256), selection=selection, purpose=purpose,
                child_request_keys=[child['request_key'] for child in children])
    return dict(body, children=children, selection_key='selection-v1:' + _sha(body))
