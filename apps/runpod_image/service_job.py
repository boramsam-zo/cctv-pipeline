"""Bridge a verified image execution ledger to versioned service evidence.

Importing this module performs no inference, SDK import, or external operation.
The service supplies explicit finite limits; completed P3/P4 plans are never reused
as authorization for a fresh execution.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from apps.be_worker.analysis_profiles import gpu_identity, profile
from apps.be_worker.common import checked_id, read_json, save
from apps.runpod_image import image_runtime as legacy_runtime
from apps.runpod_image.runtime import image_runtime as classifier_runtime

# Keep existing deployments readable/runnable; the new image opts into v4.
runtime = (classifier_runtime if os.environ.get('CCTV_ANALYSIS_DEPLOYMENT') == 'mlops-20261004-v1'
           else legacy_runtime)


class Runtime:
    def __init__(self, image_root=runtime.IMAGE_ROOT, state_root=runtime.STATE_ROOT, *, service_root=None):
        self.image_root, self.state_root = Path(image_root), Path(state_root)
        self.service_root = (Path(service_root) if service_root is not None
                             else Path(__file__).absolute().parents[2])
        self.actual_gpu = None

    def verify_service(self, *, snapshot=None):
        """Attest the code outside the image manifest before reporting/using it.

        The CLI build/start checks remain useful, but direct Uvicorn startup and
        a long-running service must not silently execute edited adapter bytes
        under the previous model profile and GPU reuse key.
        """
        deployment = (snapshot or {}).get('deployment', os.environ.get('CCTV_ANALYSIS_DEPLOYMENT'))
        if deployment != 'mlops-20261004-v1':
            return
        from apps.runpod_image import verify_service_bundle
        verify_service_bundle.verify(self.service_root)
        manifest = read_json(self.service_root / 'service-manifest.json')
        catalog = read_json(self.service_root / verify_service_bundle.MLOPS_CATALOG)
        if (manifest.get('schema_version') != 'cctv-runpod-service-bundle-v4'
                or manifest.get('deployment') != deployment):
            raise ValueError('service_classifier_release_mismatch')
        if snapshot is not None:
            if (catalog['image_manifest_sha256'] != snapshot['image_manifest_sha256']
                    or catalog['execution_adapter_sha256'] != snapshot['execution_adapter_sha256']
                    or any(snapshot['code_files'].get(name) != digest
                           for name, digest in catalog['code_files'].items())):
                raise ValueError('service_classifier_profile_code_mismatch')

    def startup(self):
        self.actual_gpu = None
        self.verify_service()
        manifest, digest = runtime.inspect_image(self.image_root)
        snapshots = [profile(version) for version in runtime.VERSIONS]
        for snapshot in snapshots:
            self.verify_service(snapshot=snapshot)
        expected_gpu = snapshots[0]['runtime']['gpu']
        if (any(snapshot['image_manifest_sha256'] != digest
                or snapshot['runtime']['gpu'] != expected_gpu for snapshot in snapshots)
                or manifest.get('gpu', runtime.GPU) != expected_gpu):
            raise ValueError('service_image_profile_mismatch')
        environment = runtime.check_environment(manifest)
        if environment['mismatches']:
            raise ValueError('service_image_environment_mismatch')
        if sys.platform != 'linux' or self.state_root != runtime.STATE_ROOT:
            raise ValueError('service_requires_fixed_linux_gpu_state')
        requested_gpu = os.environ.get('CCTV_EXPECTED_GPU', 'auto')
        if requested_gpu == '':
            raise ValueError('service_expected_gpu_empty')
        result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total',
                                 '--format=csv,noheader,nounits'], capture_output=True,
                                text=True, check=True, timeout=10)
        devices = [line.split(',') for line in result.stdout.strip().splitlines()]
        portable = expected_gpu == 'NVIDIA CUDA GPU'
        if (len(devices) != 1 or len(devices[0]) != 2
                or (not runtime.valid_gpu_name(devices[0][0].strip()) if portable
                    else devices[0][0].strip() != expected_gpu)
                or (requested_gpu != 'auto' and devices[0][0].strip() != requested_gpu)):
            raise ValueError('service_requires_one_verified_a4500' if expected_gpu == runtime.GPU
                             else 'service_gpu_does_not_match_profile')
        # Validate the reported value without imposing a VRAM capacity minimum.
        memory_mib = devices[0][1].strip()
        if not memory_mib.isascii() or not memory_mib.isdecimal() or int(memory_mib) <= 0:
            raise ValueError('service_gpu_memory_invalid')
        self.actual_gpu = devices[0][0].strip()
        return {'gpu': self.actual_gpu, 'vram_mib': int(memory_mib),
                'model_libraries_imported': False, 'model_calls': 0,
                'gpu_inference_verified': False}

    def plan_directory(self, state):
        return self.state_root / ('api-' + checked_id(state['job_id']))

    def command(self, directory, state, seconds):
        self.verify_service(snapshot=state['analysis_profile'])
        identity = gpu_identity(state['input_sha256'], state['analysis_profile'])
        if identity['gpu_key'] != state['gpu_key'] or not runtime.positive_number(seconds):
            raise ValueError('service_execution_identity_or_limit')
        plan = {'schema_version': 'cctv-image-run-plan-v1', 'approved': True,
                'plan_id': 'api-' + checked_id(state['job_id']), 'state_root': str(self.state_root),
                'image_manifest_sha256': state['analysis_profile']['image_manifest_sha256'],
                'jobs': [{'input_id': 'input', 'version': state['analysis_profile']['version'],
                          'input': str((Path(directory) / 'input.mp4').absolute()),
                          'sha256': state['input_sha256'], 'bytes': state['input_bytes']}],
                'limits': {'max_jobs': 1, 'max_attempts_per_job': 1, 'gpu_concurrency': 1,
                           'automatic_retries': 0, 'gemini_calls': 0, 'embedding_calls': 0,
                           'per_job_timeout_seconds': seconds, 'whole_plan_timeout_seconds': seconds}}
        if state['analysis_profile']['runtime']['gpu'] != runtime.GPU:
            plan['gpu'] = state['analysis_profile']['runtime']['gpu']
        if plan.get('gpu') == 'NVIDIA CUDA GPU':
            if not runtime.valid_gpu_name(self.actual_gpu):
                raise ValueError('service_gpu_not_observed')
            plan['observed_gpu'] = self.actual_gpu
        path = Path(directory) / 'execution-plan.json'
        if path.exists():
            raise ValueError('execution_plan_already_exists_no_automatic_rerun')
        save(path, plan)
        return [sys.executable, '-B', str(self.image_root / 'runtime/image_runtime.py'),
                'run', '--version', state['analysis_profile']['version'], '--plan', str(path),
                '--state-root', str(self.state_root)]

    def completed_output(self, directory, state):
        """Read only: uncertain or incomplete attempts cannot become a rerun."""
        plan = read_json(Path(directory) / 'execution-plan.json')
        folder = self.plan_directory(state)
        saved_plan = runtime.read_json(runtime.relative_file(folder, 'plan.json'))
        ledger = runtime.read_json(runtime.relative_file(folder, 'state.json'))
        expected = plan['jobs'][0]
        identity = gpu_identity(state['input_sha256'], state['analysis_profile'])
        if (saved_plan != plan or plan['plan_id'] != folder.name or len(plan['jobs']) != 1
                or expected['version'] != state['analysis_profile']['version']
                or expected['sha256'] != state['input_sha256'] or expected['bytes'] != state['input_bytes']
                or plan.get('gpu', runtime.GPU) != state['analysis_profile']['runtime']['gpu']
                or identity['gpu_key'] != state['gpu_key']
                or ledger.get('status') != 'completed_plan' or len(ledger.get('jobs', [])) != 1):
            raise ValueError('completed_execution_receipt_required')
        runtime.validate_state(ledger, plan,
            {'plan_sha256': runtime.file_hash(Path(directory) / 'execution-plan.json'),
             'image_manifest_sha256': state['analysis_profile']['image_manifest_sha256']},
            ledger['last_observed_at'])
        item = ledger['jobs'][0]
        if item.get('attempts') != 1 or item.get('status') not in runtime.SUCCESS:
            raise ValueError('completed_single_attempt_required')
        input_directory = folder / ('input-' + expected['version'])
        runtime.check_file(input_directory / 'input.mp4', expected)
        output = input_directory / 'output'
        helpers = runtime.load_helpers(self.image_root)
        expected_gpu = state['analysis_profile']['runtime']['gpu']
        if expected_gpu == 'NVIDIA CUDA GPU':
            expected_gpu = runtime.plan_gpu(plan, expected_gpu)
        if expected_gpu == runtime.GPU:
            result, digest = helpers.verified_result(output, item, expected)
        else:
            result, digest = helpers.verified_result(output, item, expected, expected_gpu=expected_gpu)
        if digest != item.get('result_sha256'):
            raise ValueError('completed_result_receipt_mismatch')
        return output, digest, result

    def prepare_output(self, directory, state):
        from apps.be_worker import ab_evidence
        output = Path(directory) / 'output'
        source, digest, result = self.completed_output(directory, state)
        if output.exists():
            manifest = ab_evidence.validate_local(output)
            if (any(manifest.get(key) != state[key] for key in ('analysis_profile', 'gpu_key', 'input_sha256'))
                    or manifest.get('normalized_result_sha256') != digest):
                raise ValueError('prepared_evidence_identity_mismatch')
            index = read_json(output / 'evidence.json')
            return {'outcome': index['status'], 'event_count': len(index['events'])}
        ab_evidence.prepare(source, output, snapshot=state['analysis_profile'],
                            input_sha256=state['input_sha256'], result_sha256=digest)
        return {'outcome': result['status'], 'event_count': result['event_count']}


def publish_existing(directory):
    from apps.runpod_worker.storage import publish
    directory = Path(directory)
    state = read_json(directory / 'status.json')
    identity = gpu_identity(state['input_sha256'], state['analysis_profile'])
    if identity['gpu_key'] != state['gpu_key']:
        raise ValueError('publication_identity_mismatch')
    return publish(directory / 'output', checked_id(state['job_id']), state['s3_config'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Publish existing verified output; no inference')
    parser.add_argument('--publish-existing', type=Path, required=True)
    args = parser.parse_args()
    receipt = publish_existing(args.publish_existing)
    print(json.dumps({'status': 's3_evidence_published', 's3_uploaded': receipt['s3_uploaded'], 'model_calls': 0}))
