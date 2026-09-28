"""Real ASGI/queue tests with explicitly synthetic workers; no model imports."""

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import io
import zipfile
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "apps/runpod_worker"
sys.path.insert(0, str(SOURCE))
import service

TOKEN = "test-only-token-" + "x" * 40


def fixture_outputs(directory):
    output = directory / "output"
    output.mkdir()
    files = {}
    for name in ("result.json", "vlm_handoffs.json", "runtime.json"):
        data = b'{"is_test_fixture": true}'
        (output / name).write_bytes(data)
        files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (output / "manifest.json").write_text(json.dumps({"files": files}))


async def request(app, path, method="GET", body=b"", token=TOKEN, content_type="video/mp4", declared=None):
    headers = [(b"authorization", ("Bearer " + token).encode()),
               (b"content-type", content_type.encode())]
    if declared is not None:
        headers.append((b"content-length", str(declared).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http", "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": headers, "client": ("127.0.0.1", 1111), "server": ("test", 80)}
    sent = False
    messages = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    code = next(m["status"] for m in messages if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        value = data
    return code, value


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = service.create_app(self.root, TOKEN, runner=self.runner)
        await self.app.state.jobs.start()

    async def asyncTearDown(self):
        await self.app.state.jobs.close()
        self.temp.cleanup()

    async def runner(self, directory):
        fixture_outputs(directory)

    async def submit(self, body=b"synthetic-video-placeholder"):
        return await request(self.app, "/jobs", "POST", body)

    async def test_auth_applies_before_upload_and_to_health(self):
        self.assertEqual((await request(self.app, "/health", token="bad"))[0], 401)
        self.assertEqual((await request(self.app, "/jobs", "POST", b"abc", token="bad"))[0], 401)
        self.assertFalse(list(self.root.glob("*/input.mp4")))
        code, result = await request(self.app, "/health")
        self.assertEqual(code, 200)
        self.assertFalse(result["checks_model_inference"])

    async def test_upload_returns_job_and_zip_after_completion(self):
        code, result = await self.submit()
        self.assertEqual(code, 202)
        job_id = result["job_id"]
        await self.app.state.jobs.queue.join()
        state = (await request(self.app, f"/jobs/{job_id}"))[1]
        self.assertEqual(state["status"], "succeeded")
        self.assertEqual(state["input_sha256"], hashlib.sha256(b"synthetic-video-placeholder").hexdigest())
        code, archive = await request(self.app, f"/jobs/{job_id}/result")
        self.assertEqual(code, 200)
        self.assertTrue(archive.startswith(b"PK"))

    async def test_empty_mismatched_and_oversized_uploads_rejected(self):
        self.assertEqual((await self.submit(b""))[0], 400)
        self.assertEqual((await request(self.app, "/jobs", "POST", b"abc", declared=4))[0], 400)
        self.assertEqual((await request(self.app, "/jobs", "POST", declared=service.MAX_BYTES + 1))[0], 413)
        with patch.object(service, "MAX_BYTES", 2):
            self.assertEqual((await self.submit(b"abc"))[0], 400)
        self.assertFalse(list(self.root.glob("*/status.json")))

    async def test_wrong_content_type_and_unknown_id_rejected(self):
        self.assertEqual((await request(self.app, "/jobs", "POST", b"x", content_type="application/json"))[0], 415)
        self.assertEqual((await request(self.app, "/jobs/not-a-job"))[0], 404)

    async def test_failure_never_exposes_a_normal_result(self):
        async def fail(directory):
            raise RuntimeError("synthetic decode failure")
        self.app.state.jobs.runner = fail
        _, result = await self.submit()
        await self.app.state.jobs.queue.join()
        job_id = result["job_id"]
        state = (await request(self.app, f"/jobs/{job_id}"))[1]
        self.assertEqual(state["status"], "failed")
        self.assertNotIn("decision", state)
        self.assertEqual((await request(self.app, f"/jobs/{job_id}/result"))[0], 409)
        code, _ = await request(self.app, f"/jobs/{job_id}/log")
        self.assertEqual(code, 200)

    async def test_corrupt_result_manifest_fails(self):
        async def corrupt(directory):
            fixture_outputs(directory)
            (directory / "output/result.json").write_text('{"changed": true}')
        self.app.state.jobs.runner = corrupt
        _, result = await self.submit()
        await self.app.state.jobs.queue.join()
        self.assertEqual(self.app.state.jobs.get(result["job_id"])["status"], "failed")

    async def test_media_in_result_archive(self):
        async def media(directory):
            fixture_outputs(directory)
            output = directory / "output"
            path = output / "evidence/event_000/clip.mp4"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"synthetic-clip")
            manifest = json.loads((output / "manifest.json").read_text())
            manifest["files"]["evidence/event_000/clip.mp4"] = {
                "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            (output / "manifest.json").write_text(json.dumps(manifest))
        self.app.state.jobs.runner = media
        _, result = await self.submit()
        await self.app.state.jobs.queue.join()
        code, data = await request(self.app, f"/jobs/{result['job_id']}/result")
        self.assertEqual(code, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(archive.read("evidence/event_000/clip.mp4"), b"synthetic-clip")

    async def test_s3_stage_receipt_and_failure(self):
        # Exercise real child orchestration without an AWS connection.
        self.app.state.jobs.storage_config = {"bucket": "test"}
        script = self.root / "storage.py"
        script.write_text(
            "import json,sys\nfrom pathlib import Path\n"
            "output=Path(sys.argv[sys.argv.index('--output')+1])\n"
            "(output/'s3_receipt.json').write_text(json.dumps({'s3_uploaded':True,'manifest_key':'test/manifest.json'}))\n")
        with patch.object(service, "ROOT", self.root):
            _, result = await self.submit()
            await self.app.state.jobs.queue.join()
            state = self.app.state.jobs.get(result["job_id"])
            self.assertEqual(state["status"], "succeeded")
            self.assertEqual(state["storage"], "s3")
            self.assertEqual(state["s3"]["manifest_key"], "test/manifest.json")
            script.write_text("raise RuntimeError('simulated S3 failure')\n")
            _, failed = await self.submit()
            await self.app.state.jobs.queue.join()
            self.assertEqual(self.app.state.jobs.get(failed["job_id"])["error"], "s3_upload_failed")
            self.assertTrue((self.root / failed["job_id"] / "output/result.json").exists())

    async def test_one_worker_and_bounded_queue(self):
        release = asyncio.Event()
        started = asyncio.Event()
        active = maximum = calls = 0

        async def slow(directory):
            nonlocal active, maximum, calls
            active += 1
            maximum = max(maximum, active)
            calls += 1
            started.set()
            await release.wait()
            fixture_outputs(directory)
            active -= 1

        self.app.state.jobs.runner = slow
        self.assertEqual((await self.submit())[0], 202)
        await started.wait()
        self.assertEqual((await self.submit())[0], 202)
        self.assertEqual((await self.submit())[0], 429)
        release.set()
        await self.app.state.jobs.queue.join()
        self.assertEqual((maximum, calls), (1, 2))

    async def test_storage_count_limit(self):
        self.app.state.jobs.max_jobs = 1
        await self.submit()
        await self.app.state.jobs.queue.join()
        self.assertEqual((await self.submit())[0], 429)

    async def test_second_api_process_refused(self):
        other = service.Jobs(self.root)
        with self.assertRaises(RuntimeError):
            await other.start()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_jobs_marked_failed_on_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = service.Jobs(root)
            root.mkdir(exist_ok=True)
            job_id = manager.reserve()
            manager.update(job_id, status="running")
            await manager.start()
            try:
                self.assertEqual(manager.get(job_id)["error"], "service_restarted")
            finally:
                await manager.close()

    async def test_actual_child_timeout_kills_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "worker.py").write_text('import time\ntime.sleep(60)\n')
            manager = service.Jobs(root, timeout=0.05)
            processes = []
            original = asyncio.create_subprocess_exec

            async def capture(*args, **kwargs):
                process = await original(*args, **kwargs)
                processes.append(process)
                return process

            with patch.object(service, "ROOT", root), patch.object(asyncio, "create_subprocess_exec", capture):
                with self.assertRaises(asyncio.TimeoutError):
                    await manager.run_process(root)
            self.assertEqual(len(processes), 1)
            self.assertIsNotNone(processes[0].returncode)
            self.assertNotEqual(processes[0].returncode, 0)


if __name__ == "__main__":
    unittest.main()
