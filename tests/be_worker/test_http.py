"""Loopback HTTP against the existing Runpod app; only GPU/S3 execution is synthetic."""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from apps.be_worker import tracing, worker
from apps.be_worker.common import read_json
from apps.be_worker.runpod_api import ApiError, RunpodAPI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps/runpod_worker'))
import service
import storage

spec = importlib.util.spec_from_file_location('http_scene_fixture', ROOT / 'tests/scene_description/test_scene.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = 'synthetic-private-token-' + 't' * 40
        self.s3 = fixture_module.MemoryS3()
        self.inferences = 0
        self.uploads = 0
        self.env = patch.dict(os.environ, {'EVIDENCE_STORAGE': 's3', 'S3_BUCKET': 'cctv-test-bucket',
            'AWS_DEFAULT_REGION': 'ap-northeast-2', 'S3_PREFIX': 'cctv-evidence'})
        self.env.start()
        self.addCleanup(self.env.stop)

        async def synthetic_runner(directory):
            self.inferences += 1
            output = directory / 'output'
            output.mkdir()
            fixture_module.fixture(output)
            path = output / 'manifest.json'
            manifest = json.loads(path.read_text())
            manifest['input_sha256'] = hashlib.sha256((directory / 'input.mp4').read_bytes()).hexdigest()
            path.write_text(json.dumps(manifest))

        async def synthetic_upload(*args, **kwargs):
            self.uploads += 1
            assert Path(args[1]).name == 'storage.py'
            output, identifier = Path(args[args.index('--output') + 1]), args[args.index('--job') + 1]
            storage.publish(output, identifier, storage.settings(), self.s3)
            class Process:
                returncode = 0
                async def wait(self):
                    return 0
            return Process()

        self.upload_patch = patch.object(asyncio, 'create_subprocess_exec', side_effect=synthetic_upload)
        self.upload_patch.start()
        self.addCleanup(self.upload_patch.stop)
        app = service.create_app(root=self.root / 'pod-jobs', runner=synthetic_runner, token=self.token)
        # Reproduce the observed proxy rejection before the API's token validation.
        async def client_signature_filter(request, call_next):
            if request.headers.get('user-agent', '').startswith('Python-urllib/'):
                return JSONResponse({'synthetic_proxy_error': 1010}, status_code=403)
            return await call_next(request)
        app.add_middleware(BaseHTTPMiddleware, dispatch=client_signature_filter)
        self.redirect_target_calls = 0
        async def redirect(request):
            return RedirectResponse('/redirect-target', status_code=307)
        async def target(request):
            self.redirect_target_calls += 1
            return JSONResponse({'unexpected': True})
        app.router.routes.extend([Route('/redirect', redirect), Route('/redirect-target', target)])
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(self.sock.close)
        self.sock.bind(('127.0.0.1', 0))
        self.url = 'http://127.0.0.1:' + str(self.sock.getsockname()[1])
        self.server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False, lifespan='on'))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.sock]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.server.started)

    def tearDown(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.sock.close()
        self.upload_patch.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_real_cli_upload_queue_status_s3_and_scene_contract(self):
        video = self.root / 'video.mp4'
        body = b'\x00\x00\x00\x18ftypisom-synthetic-byte-stream'
        video.write_bytes(body)
        token_path = self.root / 'token'
        token_path.write_text(self.token)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'runpod_url': self.url, 's3_bucket': 'cctv-test-bucket', 's3_region': 'ap-northeast-2'}))
        output = self.root / 'be-job'
        result = subprocess.run([sys.executable, str(ROOT / 'apps/be_worker/worker.py'), 'run',
            '--config', str(config), '--video', str(video), '--output', str(output),
            '--token-file', str(token_path), '--wait-seconds', '5'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(self.token, result.stdout + result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response['status'], 'evidence_ready')
        self.assertEqual((self.inferences, self.uploads), (1, 1))
        state = worker.load_state(output)
        self.assertEqual((self.root / 'pod-jobs' / state['provider_job_id'] / 'input.mp4').read_bytes(), body)
        trace = tracing.Recorder(output, 'collect', state)
        handoff = worker.collect(output, state, self.s3, trace)
        trace.finish(state)
        self.assertEqual(handoff['status'], 'handoff_ready')
        scene_config = read_json(output / 'scene-event_000.json')
        self.assertEqual(scene_config['job_id'], state['provider_job_id'])
        media = fixture_module.scene.fetch(scene_config, self.root / 'verified-media', self.s3)
        self.assertEqual(media['status'], 'verified')

    def test_wrong_auth_fails_before_any_submission(self):
        api = RunpodAPI(self.url, 'wrong-synthetic-token')
        with self.assertRaises(ApiError) as error:
            api.health()
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(self.inferences, 0)

    def test_adapter_refuses_redirect_with_bearer_token(self):
        api = RunpodAPI(self.url, self.token)
        with self.assertRaises(ApiError) as error:
            api.request('/redirect')
        self.assertEqual(error.exception.status, 307)
        self.assertEqual(self.redirect_target_calls, 0)


if __name__ == '__main__':
    unittest.main()
