"""Run the fixed A/B reference trial once; stdlib-only parent, no model imports."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import html
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import socket
import subprocess
import sys
import time
import zipfile

TRIAL = "comparison-20261001-01"
INPUTS = "cctv-ab-inputs-20261001-01"
SOURCE_DIRS = {"A": "cctv-runpod-a4000", "B": "deployment_handoff_1001"}
SUCCESS = {"completed", "no_candidates"}
LIMITS = {"max_inference_jobs": 20, "max_attempts_per_input_version": 1,
          "gpu_concurrency": 1, "per_job_timeout_seconds": 300,
          "whole_trial_timeout_seconds": 1800, "automatic_retries": 0,
          "additional_weight_downloads": 0, "gemini_calls": 0, "embedding_calls": 0}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_read(path):
    return json.loads(path.read_text(), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))


def safe_file(root, relative):
    part = PurePosixPath(relative)
    if not relative or part.is_absolute() or ".." in part.parts or "\\" in relative:
        raise ValueError("Unsafe artifact path")
    target = root
    for component in part.parts:
        target = target / component
        if target.is_symlink():
            raise ValueError("Symlink is not an evidence file")
    if not target.is_file() or root.resolve() not in target.resolve().parents:
        raise ValueError("Missing file or path outside its root")
    return target


def verify_file(root, name, spec):
    path = safe_file(root, name)
    if path.stat().st_size != spec["bytes"] or digest(path) != spec["sha256"]:
        raise ValueError("File identity mismatch: " + name)
    return path


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    # A retained temporary file indicates a previous interrupted write. Preserve it.
    with temporary.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_contract(contract):
    if any(contract["limits"].get(k) != v for k, v in LIMITS.items()):
        raise ValueError("Execution limits differ from the approved trial")
    inputs = contract["inputs"]
    ids = [f"input_{i:03d}" for i in range(1, 11)]
    if [item["input_id"] for item in inputs] != ids:
        raise ValueError("Expected exactly the ten approved inputs")
    if any(item["file"] != item["input_id"] + ".mp4" for item in inputs):
        raise ValueError("Unexpected input filename")
    expected = [{"input_id": i, "version": v} for i in ids for v in ("A", "B")]
    if contract["run_order"] != expected:
        raise ValueError("Expected one A then one B attempt per input")
    if set(contract["versions"]) != {"A", "B"}:
        raise ValueError("Only A and B are approved")


def preflight(bundle, root):
    """Read hashes only. Does not import model libraries, decode or create a trial."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected the existing A/B work directory")
    manifest = json_read(bundle / "bundle-manifest.json")
    for name, spec in manifest["files"].items():
        verify_file(bundle, name, spec)
    contract = json_read(bundle / "contract.json")
    validate_contract(contract)
    sources = json_read(bundle / "source-files.json")
    for version, names in sources.items():
        source = root / SOURCE_DIRS[version]
        if source.is_symlink() or not source.is_dir():
            raise ValueError("Source directory missing or linked")
        for name, spec in names.items():
            verify_file(source, name, spec)
    for item in contract["inputs"]:
        verify_file(root / INPUTS, item["file"], item)
    python = root / SOURCE_DIRS["A"] / ".venv/bin/python"
    if not python.is_file():
        raise ValueError("Existing A Python environment is missing; no installation attempted")
    identity = {name: digest(bundle / name) for name in
                ("contract.json", "source-files.json", "runner.py", "job.py")}
    return contract, identity, python


@contextmanager
def trial_lock(root):
    path = root / ".ab-comparison.lock"
    if path.is_symlink():
        raise ValueError("Lock path must not be a symlink")
    with path.open("a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another comparison process holds the GPU slot") from None
        # Inherited FD keeps the slot locked until a surviving child exits too.
        yield stream.fileno()


def machine_identity():
    return {"host": socket.gethostname(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def new_state(contract, identity, machine, now=None):
    now = time.monotonic() if now is None else now
    return {"schema_version": "cctv-ab-trial-v1", "identity": identity,
            "machine": machine, "started_at": time.time(), "started_monotonic": now,
            "deadline_monotonic": now + contract["limits"]["whole_trial_timeout_seconds"],
            "status": "ready", "gemini_calls": 0, "embedding_calls": 0,
            "jobs": [{**item, "status": "not_started", "attempts": 0}
                     for item in contract["run_order"]]}


def validate_state(state, contract, identity, machine):
    if state["identity"] != identity or state["machine"] != machine:
        raise ValueError("Trial code/configuration or Pod changed; automatic resume denied")
    if len(state["jobs"]) != len(contract["run_order"]):
        raise ValueError("Trial roster changed")
    if state["deadline_monotonic"] != state["started_monotonic"] + 1800:
        raise ValueError("Trial deadline changed")
    for stored, expected in zip(state["jobs"], contract["run_order"]):
        if any(stored[k] != expected[k] for k in ("input_id", "version")):
            raise ValueError("Job identity changed")
        required_attempts = 0 if stored["status"] in {"not_started", "not_run_time_limit"} else 1
        if stored["attempts"] != required_attempts:
            raise ValueError("Attempt ledger is inconsistent")


def job_key(job):
    return job["input_id"] + "-" + job["version"]


def valid_gpu_name(value):
    """An observed CUDA device name, distinct from a selector or contract marker."""
    return (isinstance(value, str) and 1 <= len(value) <= 160 and value == value.strip()
            and value.isascii() and value.isprintable()
            and value.casefold() not in {'auto', 'nvidia cuda gpu', 'gpu', 'cuda', 'unknown', 'none', 'null'}
            and re.search(r'\bcpu\b', value, re.IGNORECASE) is None)


def verified_result(output, job, item, *, expected_gpu="NVIDIA RTX A4500"):
    if not valid_gpu_name(expected_gpu):
        raise ValueError("Invalid expected GPU name")
    result_path = safe_file(output, "result.json")
    result = json_read(result_path)
    count = result["event_count"]
    if (result["version"] != job["version"] or result["input_sha256"] != item["sha256"]
            or result["status"] not in SUCCESS or type(count) is not int or count < 0
            or (result["status"] == "no_candidates") != (count == 0)):
        raise ValueError("Result identity or candidate status mismatch")
    if result["gpu"] != expected_gpu:
        raise ValueError("Result GPU differs from approved Pod")
    artifacts = result["artifacts"]
    names = [entry["path"] for entry in artifacts]
    if len(set(names)) != len(names) or "result.json" in names:
        raise ValueError("Duplicate or recursive result artifact")
    if not any(name.endswith(".json") for name in names):
        raise ValueError("Raw JSON missing")
    if count and not (any(name.endswith(".mp4") for name in names)
                      and any(name.endswith((".jpg", ".png", ".jpeg")) for name in names)):
        raise ValueError("Candidate evidence media missing")
    for entry in artifacts:
        verify_file(output, entry["path"], entry)
    for section in ("timings", "memory"):
        if not result[section]:
            raise ValueError("Missing measurements")
        for value in result[section].values():
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid measurement")
    return result, digest(result_path)


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_process(command, log_path, seconds, lock_fd, env):
    started = time.monotonic()
    process = None
    with log_path.open("xb") as log:
        try:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True, pass_fds=(lock_fd,), env=env)
            try:
                code = process.wait(timeout=max(0.001, seconds - (time.monotonic() - started)))
                status = "exited" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                stop_group(process)
                status, code = "timed_out", process.returncode
        except BaseException:
            if process is not None:
                stop_group(process)
            raise
    return {"status": status, "returncode": code,
            "subprocess_wall_seconds": time.monotonic() - started}


def clean_environment(jobdir):
    # The offline comparison has no need for server/API/cloud credentials.
    keep = ("HOME", "PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "CUDA_VISIBLE_DEVICES",
            "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               PYTHONUNBUFFERED="1", YOLO_CONFIG_DIR=str(jobdir / "yolo-config"),
               XDG_CONFIG_HOME=str(jobdir / "config"), MPLCONFIGDIR=str(jobdir / "matplotlib"),
               TORCH_HOME=str(jobdir / "torch-cache"),
               YOLO_OFFLINE="true", HF_HUB_OFFLINE="1", WANDB_DISABLED="true")
    return env


def write_summary(trial, state):
    rows = []
    for job in state["jobs"]:
        key = job_key(job)
        result = job.get("result", {})
        row = {"job": key, "status": job["status"], "attempts": job["attempts"],
               "event_count": result.get("event_count"),
               "subprocess_wall_seconds": job.get("subprocess_wall_seconds"),
               "timings": result.get("timings"), "memory": result.get("memory"),
               "candidate_times_s": result.get("candidate_times_s"),
               "error": job.get("error"), "artifacts": result.get("artifacts", [])}
        rows.append(row)
    atomic_json(trial / "comparison.json", {"status": state["status"], "jobs": rows,
                "purpose": "training-exposed reference comparison; no accuracy/mAP claim",
                "gemini_calls": 0, "embedding_calls": 0})
    body = ['<!doctype html><html lang="ko"><meta charset="utf-8"><title>A/B 비교</title>',
            '<style>body{font-family:system-ui;max-width:1100px;margin:30px auto;padding:16px} '
            'td,th{border:1px solid #ccc;padding:8px;vertical-align:top}table{border-collapse:collapse}'
            'video,img{max-width:460px;display:block;margin:8px 0}pre{white-space:pre-wrap}</style>',
            '<h1>A/B 영상 참고 비교</h1><p>학습 노출 영상 10개. 후보 없음은 정상 확정이 아닙니다. '
            '실패·미실행은 비교 완료에 포함하지 않습니다. 사고·객체의 적합성은 원본과 대조해 검수해주세요.</p>',
            '<p>A: YOLO640/conf0.25/단일 프레임 · B: YOLO960/conf0.20/다중 프레임·사건 병합/ROI</p>',
            '<p>B 원본 클립은 mp4v 형식입니다. 브라우저 재생 호환은 미확인입니다. '
            '재생되지 않으면 아래 파일 링크로 로컬 영상 플레이어에서 확인해주세요.</p>',
            '<table><tr><th>입력/버전</th><th>상태</th><th>후보 수</th><th>실행시간(초)</th></tr>']
    for row in rows:
        values = (row['job'], row['status'], row['event_count'], row['subprocess_wall_seconds'])
        body.append('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in values) + '</tr>')
    body.append('</table>')
    for row in rows:
        if not row['artifacts']:
            continue
        body.append('<h2>' + html.escape(row['job']) + '</h2>')
        source = 'inputs/' + row['job'].rsplit('-', 1)[0] + '.mp4'
        body.append('<h3>원본 영상</h3><video controls preload="none" src="' + html.escape(source, quote=True) + '"></video>')
        body.append('<pre>' + html.escape(json.dumps({k: row[k] for k in ('candidate_times_s', 'timings', 'memory')}, indent=2)) + '</pre>')
        for artifact in row['artifacts']:
            relative = row['job'] + '/output/' + artifact['path']
            link = html.escape(relative, quote=True)
            body.append('<a href="' + link + '">' + html.escape(artifact['path']) + '</a><br>')
            if relative.endswith('.mp4'):
                body.append('<video controls preload="none" src="' + link + '"></video>')
            elif relative.endswith(('.jpg', '.jpeg', '.png')):
                body.append('<img loading="lazy" src="' + link + '" alt="생성된 근거 프레임">')
    body.append('</html>')
    (trial / "comparison.html").write_text('\n'.join(body))


def execute(contract, identity, root, bundle, python, machine, lock_fd):
    validate_contract(contract)
    trial = root / TRIAL
    if trial.is_symlink():
        raise ValueError("Trial directory must not be a symlink")
    if not trial.exists():
        trial.mkdir(mode=0o700)
        state = new_state(contract, identity, machine)
        atomic_json(trial / "state.json", state)
    else:
        state = json_read(safe_file(trial, "state.json"))
        validate_state(state, contract, identity, machine)
    inputs = {item["input_id"]: item for item in contract["inputs"]}
    # Keep a portable source-video copy beside results for the later Mac review.
    review_inputs = trial / "inputs"
    if review_inputs.is_symlink():
        raise ValueError("Review input directory must not be a symlink")
    review_inputs.mkdir(exist_ok=True)
    for item in contract["inputs"]:
        destination = review_inputs / item["file"]
        if not destination.exists():
            source = verify_file(root / INPUTS, item["file"], item)
            with source.open("rb") as src, destination.open("xb") as dst:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(block)
                dst.flush()
                os.fsync(dst.fileno())
        verify_file(review_inputs, item["file"], item)
    for job in state["jobs"]:
        if job["status"] in SUCCESS:
            result, result_sha = verified_result(trial / job_key(job) / "output", job, inputs[job["input_id"]])
            if job["result_sha256"] != result_sha:
                raise ValueError("Previously completed result changed")
            job["result"] = result
        elif job["status"] != "not_started":
            state["status"] = "blocked_previous_attempt"
            if job["status"] == "running":
                job["status"] = "unknown_after_interruption"
            atomic_json(trial / "state.json", state)
            write_summary(trial, state)
            return state
    for job in state["jobs"]:
        if job["status"] in SUCCESS:
            continue
        remaining = state["deadline_monotonic"] - time.monotonic()
        if remaining <= 0:
            job["status"] = "not_run_time_limit"
            state["status"] = "whole_trial_time_limit"
            continue
        jobdir = trial / job_key(job)
        jobdir.mkdir(mode=0o700)  # Existing untracked directories are preserved and stop the run.
        item = inputs[job["input_id"]]
        verify_file(root / INPUTS, item["file"], item)
        job.update(status="running", attempts=1, started_at=time.time())
        state["status"] = "running"
        atomic_json(trial / "state.json", state)  # Persist intent before Popen.
        write_summary(trial, state)
        command = [str(python), "-B", str(bundle / "job.py"), "--work-root", str(root),
                   "--version", job["version"], "--input", str(root / INPUTS / item["file"]),
                   "--output", str(jobdir / "output")]
        print(json.dumps({"job": job_key(job), "status": "running", "attempt": 1}), flush=True)
        try:
            allowance = min(300, max(0, state["deadline_monotonic"] - time.monotonic()))
            if allowance <= 0:
                job.update(status="not_run_time_limit", attempts=0)
                state["status"] = "whole_trial_time_limit"
                atomic_json(trial / "state.json", state)
                continue
            outcome = run_process(command, jobdir / "worker.log", allowance, lock_fd, clean_environment(jobdir))
            job.update(outcome)
            if outcome["status"] == "exited":
                result, result_sha = verified_result(jobdir / "output", job, item)
                job.update(status=result["status"], result=result, result_sha256=result_sha)
        except BaseException as exc:
            job.update(status="failed" if isinstance(exc, Exception) else "interrupted",
                       error=type(exc).__name__ + ": " + str(exc))
        job["finished_at"] = time.time()
        atomic_json(trial / "state.json", state)
        print(json.dumps({"job": job_key(job), "status": job["status"],
                          "event_count": job.get("result", {}).get("event_count")}), flush=True)
        if job["status"] not in SUCCESS:
            state["status"] = "stopped_on_failure"
            break
    if all(job["status"] in SUCCESS for job in state["jobs"]):
        state["status"] = "completed_reference_trial"
    atomic_json(trial / "state.json", state)
    write_summary(trial, state)
    return state


def export_results(root):
    """Snapshot retained files after processing stops; no model or API calls."""
    trial = root / TRIAL
    if trial.is_symlink() or not trial.is_dir():
        raise ValueError("No trial results to export")
    records = {}
    for path in sorted(trial.rglob("*")):
        if path.is_symlink():
            raise ValueError("Unexpected link in retained results")
        if path.is_file():
            name = path.relative_to(trial).as_posix()
            records[name] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    if not records or "state.json" not in records:
        raise ValueError("Missing attempt ledger")
    if "export-manifest.json" in records:
        raise ValueError("Reserved export manifest already exists; retained files preserved")
    manifest = json.dumps({"schema_version": "cctv-ab-export-v1", "files": records},
                          sort_keys=True, indent=2).encode()
    snapshot_id = hashlib.sha256(manifest).hexdigest()[:16]
    archive = root / (TRIAL + "-results-" + snapshot_id + ".zip")
    if archive.exists():
        raise FileExistsError("Export already exists; preserved: " + str(archive))
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as output:
        output.writestr(TRIAL + "/export-manifest.json", manifest)
        for name, spec in records.items():
            path = verify_file(trial, name, spec)
            output.write(path, TRIAL + "/" + name)
    with zipfile.ZipFile(archive) as check:
        expected_names = {TRIAL + "/" + name for name in records} | {TRIAL + "/export-manifest.json"}
        if len(check.namelist()) != len(expected_names) or set(check.namelist()) != expected_names:
            raise ValueError("Export ZIP member list mismatch")
        if check.testzip() is not None:
            raise ValueError("Export ZIP CRC verification failed; preserve for diagnosis")
        for name, spec in records.items():
            data = check.read(TRIAL + "/" + name)
            if len(data) != spec["bytes"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
                raise ValueError("Export file verification failed")
    result = {"status": "ab_results_exported", "archive": str(archive),
              "bytes": archive.stat().st_size, "sha256": digest(archive),
              "files": len(records), "model_calls": 0}
    atomic_json(archive.with_suffix(".json"), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, default=Path("/workspace/cctv-ab-20261001-01"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="Run approved jobs; default checks hashes only")
    mode.add_argument("--export", action="store_true", help="Archive retained results without model calls")
    args = parser.parse_args()
    bundle = Path(__file__).resolve().parent
    root = args.work_root.absolute()
    if args.export:
        with trial_lock(root):
            print(json.dumps(export_results(root)))
        return
    contract, identity, python = preflight(bundle, root)
    if not args.run:
        print(json.dumps({"status": "ab_runner_preflight_passed", "videos_verified": 10,
                          "versions": ["A", "B"], "max_jobs": 20, "model_calls": 0}))
        return
    if sys.platform != "linux":
        raise RuntimeError("Actual model jobs run on Runpod Linux only")
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Received signal " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    with trial_lock(root) as lock_fd:
        state = execute(contract, identity, root, bundle, python, machine_identity(), lock_fd)
    print(json.dumps({"status": state["status"], "attempts": sum(j["attempts"] for j in state["jobs"]),
                      "results": str(root / TRIAL), "gemini_calls": 0}))
    if state["status"] != "completed_reference_trial":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
