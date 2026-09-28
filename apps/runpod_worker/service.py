"""Authenticated, single-GPU temporary-disk API for a first Runpod connection."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import uuid
import zipfile

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from verify_bundle import sha256
from storage import verified_files, settings

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 200 * 1024 * 1024
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def valid_outputs(directory):
    return verified_files(directory)


class Jobs:
    def __init__(self, root, runner=None, max_jobs=10, max_active=2, timeout=300):
        self.root = Path(root)
        self.max_jobs, self.max_active, self.timeout = max_jobs, max_active, timeout
        self.runner = runner or self.run_process
        self.queue = asyncio.Queue()
        self.lock_file = None
        self.task = None
        self.storage_config = settings()

    async def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.root / "service.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise RuntimeError("Only one API process may use this job directory")
        for record in self.root.glob("*/status.json"):
            state = json.loads(record.read_text())
            if state["status"] in ("uploading", "queued", "running"):
                state.update(status="failed", error="service_restarted", finished_at=utcnow())
                save_json(record, state)
        self.task = asyncio.create_task(self.consume())

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.lock_file:
            self.lock_file.close()

    def record(self, job_id):
        if not ID_PATTERN.fullmatch(job_id):
            raise FileNotFoundError
        return self.root / job_id / "status.json"

    def get(self, job_id):
        return json.loads(self.record(job_id).read_text())

    def update(self, job_id, **fields):
        state = self.get(job_id)
        state.update(fields)
        save_json(self.record(job_id), state)
        return state

    def reserve(self):
        records = [json.loads(p.read_text()) for p in self.root.glob("*/status.json")]
        if len(records) >= self.max_jobs:
            raise ValueError("job_storage_limit_download_results_before_cleanup")
        active = sum(r["status"] in ("uploading", "queued", "running") for r in records)
        if active >= self.max_active:
            raise ValueError("queue_full")
        if shutil.disk_usage(self.root).free < 1024 ** 3 + 2 * MAX_BYTES:
            raise ValueError("insufficient_disk_space")
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir()
        save_json(directory / "status.json", {
            "schema_version": "runpod-local-api-v1", "job_id": job_id,
            "status": "uploading", "created_at": utcnow(),
            "error": None, "storage": "temporary_container_disk",
        })
        return job_id

    async def run_process(self, directory):
        with (directory / "worker.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(ROOT / "worker.py"),
                "--video", str(directory / "input.mp4"),
                "--output", str(directory / "output"),
                stdout=log, stderr=asyncio.subprocess.STDOUT,
                cwd=ROOT, start_new_session=True,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=self.timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                raise
            if process.returncode != 0:
                raise RuntimeError("model_worker_failed_see_worker_log")

    async def consume(self):
        while True:
            job_id = await self.queue.get()
            directory = self.root / job_id
            self.update(job_id, status="running", started_at=utcnow())
            stage = "analysis"
            try:
                await self.runner(directory)
                manifest = valid_outputs(directory / "output")
                receipt = {}
                if self.storage_config:
                    stage = "s3_upload"
                    self.update(job_id, phase=stage)
                    # Separate process so cancellation/timeout terminates uploads too.
                    with (directory / "worker.log").open("ab") as log:
                        process = await asyncio.create_subprocess_exec(
                            sys.executable, str(ROOT / "storage.py"),
                            "--output", str(directory / "output"), "--job", job_id,
                            stdout=log, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
                        try:
                            await asyncio.wait_for(process.wait(), timeout=300)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            await process.wait()
                            raise
                        if process.returncode:
                            raise RuntimeError("s3_upload_failed_retry_existing_outputs")
                    receipt = json.loads((directory / "output/s3_receipt.json").read_text())
                stage = "archive"
                with zipfile.ZipFile(directory / "results.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                    names = list(manifest["files"]) + ["manifest.json"]
                    if receipt:
                        names.append("s3_receipt.json")
                    for name in names:
                        archive.write(directory / "output" / name, name)
                    if (directory / "worker.log").exists():
                        archive.write(directory / "worker.log", "worker.log")
                self.update(job_id, status="succeeded", phase="complete", finished_at=utcnow(),
                    storage="s3" if receipt else "temporary_container_disk",
                    s3_uploaded=bool(receipt), s3=receipt or None)
            except asyncio.CancelledError:
                self.update(job_id, status="failed", error="service_stopped", finished_at=utcnow())
                raise
            except asyncio.TimeoutError:
                self.update(job_id, status="failed", error=stage + "_timeout", finished_at=utcnow())
            except Exception as exc:
                # Full exception belongs in local logs, not in the public response.
                with (directory / "worker.log").open("a") as log:
                    log.write(f"\n{type(exc).__name__}: {exc}\n")
                self.update(job_id, status="failed", error=stage + "_failed", finished_at=utcnow())
            finally:
                self.queue.task_done()


class BearerAuth:
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            expected = ("Bearer " + self.token).encode()
            if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(root=None, token=None, runner=None):
    if token is None:
        token = (ROOT / ".api-token").read_text().strip()
    if len(token) < 32 or not token.isascii():
        raise ValueError("API token must contain at least 32 ASCII characters")
    jobs = Jobs(root or ROOT / "runtime/jobs", runner=runner)

    @asynccontextmanager
    async def lifespan(app):
        await jobs.start()
        try:
            yield
        finally:
            await jobs.close()

    async def health(request):
        return JSONResponse({"status": "ok", "api_schema": "runpod-local-api-v1",
                             "gpu_concurrency": 1, "max_video_seconds": 120,
                             "storage": "s3" if jobs.storage_config else "temporary_container_disk",
                             "checks_model_inference": False})

    async def submit(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "video/mp4":
            return JSONResponse({"error": "send_raw_mp4_with_video/mp4_content_type"}, status_code=415)
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"error": "invalid_content_length"}, status_code=400)
        if declared > MAX_BYTES or declared < 0:
            return JSONResponse({"error": "upload_limit_200_MiB"}, status_code=413)
        try:
            job_id = jobs.reserve()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=429)
        directory = jobs.root / job_id
        size = 0
        try:
            async with asyncio.timeout(90):
                with (directory / "input.mp4").open("wb") as target:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("upload_limit_200_MiB")
                        target.write(chunk)
            if size == 0 or (declared and size != declared):
                raise ValueError("empty_or_incomplete_upload")
        except BaseException as exc:
            shutil.rmtree(directory)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            return JSONResponse({"error": "upload_failed_or_limit_exceeded"}, status_code=400)
        jobs.update(job_id, status="queued", input_bytes=size,
                    input_sha256=sha256(directory / "input.mp4"))
        jobs.queue.put_nowait(job_id)
        return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)

    async def status(request):
        try:
            return JSONResponse(jobs.get(request.path_params["job_id"]))
        except FileNotFoundError:
            return JSONResponse({"error": "job_not_found"}, status_code=404)

    async def result(request):
        try:
            state = jobs.get(request.path_params["job_id"])
        except FileNotFoundError:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        if state["status"] != "succeeded":
            return JSONResponse({"error": "result_not_available", "status": state["status"]}, status_code=409)
        return FileResponse(jobs.root / state["job_id"] / "results.zip",
                            media_type="application/zip", filename=f"{state['job_id']}-results.zip")

    async def log_file(request):
        try:
            state = jobs.get(request.path_params["job_id"])
        except FileNotFoundError:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        path = jobs.root / state["job_id"] / "worker.log"
        if not path.is_file():
            return JSONResponse({"error": "log_not_available"}, status_code=404)
        return FileResponse(path, media_type="text/plain", filename="worker.log")

    app = Starlette(lifespan=lifespan, routes=[
        Route("/health", health), Route("/jobs", submit, methods=["POST"]),
        Route("/jobs/{job_id}", status), Route("/jobs/{job_id}/result", result),
        Route("/jobs/{job_id}/log", log_file),
    ])
    app.add_middleware(BearerAuth, token=token)
    app.state.jobs = jobs
    return app
