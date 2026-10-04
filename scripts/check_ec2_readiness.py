"""Read-only EC2 inventory. Run as root via stdin; never reads secret contents.

Only documented CCTV paths are inspected. No installs, model calls, Docker
creation, schema writes, or configuration sourcing. Missing default paths do
not prove that an alternative deployment is absent.
"""
import datetime
import json
import pathlib
import subprocess


def run(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__}
    # Do not echo stderr: engine/connection errors can contain private settings.
    if result.returncode:
        return {"ok": False, "exit_code": result.returncode}
    return {"ok": True, "lines": result.stdout.strip().splitlines()}


def inspect():
    paths = {
        "db_compose": "/opt/cctv-stack/compose.yaml",
        "be_environment": "/etc/cctv/be-worker-v1.env",
        "be_compose": "/opt/cctv-be-release-20260929-01/apps/be_worker/queue.compose.yaml",
        "be_config": "/srv/cctv/be/input/config-ec2-v1.json",
        "be_spool": "/srv/cctv/be/spool",
        "runpod_token": "/etc/cctv/be-secrets/runpod_worker_token",
        "be_gemini_key": "/etc/cctv/be-secrets/gemini_api_key",
        "shared_gemini_key": "/etc/cctv/secrets/gemini_api_key",
        "aws_credentials_file": "/etc/cctv/be-secrets/aws_credentials",
        "aws_config_file": "/etc/cctv/be-secrets/aws_config",
    }
    files = {}
    for name, value in paths.items():
        path = pathlib.Path(value)
        try:
            info = path.stat()
            files[name] = {"exists": True, "is_file": path.is_file(),
                           "nonempty": info.st_size > 0,
                           "mode": oct(info.st_mode & 0o777)}
        except FileNotFoundError:
            files[name] = {"exists": False}
        except OSError as exc:
            files[name] = {"exists": None, "error": type(exc).__name__}
    base = ["docker", "compose", "-f", paths["db_compose"]]
    psql = base + ["exec", "-T", "--user", "postgres", "-e",
                   "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=5000",
                   "db", "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", "cctv"]
    tables = {}
    for table in ("cctv_jobs.queue", "cctv_scene.descriptions", "cctv_rag.chunks"):
        existence = run(psql + ["-c", f"SELECT to_regclass('{table}') IS NOT NULL;"])
        entry = {"exists_query": existence}
        if existence.get("lines") == ["t"]:
            entry["count_query"] = run(psql + ["-c", f"SELECT count(*) FROM {table};"])
        tables[table] = entry
    return {
        "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "documented_default_paths_only; alternate_paths_unverified",
        "files": files,
        "cctv_directories": sorted(p.name for p in pathlib.Path('/opt').glob('cctv-*') if p.is_dir()),
        "containers": run(base + ["ps", "--all", "--format", "{{.Service}}\t{{.State}}\t{{.Health}}\t{{.Image}}"]),
        "be_image": run(["docker", "image", "ls", "cctv-be-queue", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}"]),
        "rag_image": run(["docker", "image", "ls", "cctv-rag-tools", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}"]),
        "tables": tables,
        "unverified": ["secret_validity", "custom_environment_paths", "runpod_connectivity", "s3_access", "end_to_end"],
        "model_calls": 0, "database_writes": 0,
    }


if __name__ == '__main__':
    print(json.dumps(inspect(), ensure_ascii=False, indent=2))
