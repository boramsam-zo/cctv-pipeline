"""Build the fixed offline A/B runner bundle; stdlib only, without models or videos."""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "cctv-ab-runner-20261001-01"
DEFAULT_OUTPUT = ROOT / "artifacts/runpod/ab-runner-20261001-01"
CONTRACT = ROOT / "artifacts/runpod/model-comparison-p2-20261001-01/comparison-contract.json"
SOURCES = {
    "A": {
        "archive": "A-cctv-runpod-a4000.zip", "root": "cctv-runpod-a4000",
        "sha256": "4428fa3ebb2a6044a511712eb1a0b0940640ca880f42837d8a6636a9be3dbff4",
        "manifest": "bundle-manifest.json", "files": 25,
    },
    "B": {
        "archive": "B-deployment_handoff_1001.zip", "root": "deployment_handoff_1001",
        "sha256": "d6ca3e617968f4db1ae3ed23a7ab2210b1ec6d8b11efd07c48481ed2055e678e",
        "manifest": "manifest.json", "files": 17,
    },
}

RUNPOD = """A/B reference comparison — fixed trial 2026-10-01

Execution location: Runpod, existing RTX A4500 Pod.
Reuse the installed A .venv and the verified A/B sources and ten video inputs.
No installation, API server, S3, Gemini, embedding or weight download is needed.

FIRST STEP — hashes only; model calls 0, video decode 0:
/workspace/cctv-ab-20261001-01/cctv-runpod-a4000/.venv/bin/python -B /workspace/cctv-ab-runner-20261001-01/runner.py
Expected: ab_runner_preflight_passed, videos_verified=10, max_jobs=20, model_calls=0.
Return this output for verification before executing the following model command.

LATER STEP — after preflight passes, run the already approved fixed model trial:
/workspace/cctv-ab-20261001-01/cctv-runpod-a4000/.venv/bin/python -B /workspace/cctv-ab-runner-20261001-01/runner.py --run

Limits: GPU slot 1; ten inputs, A then B each; maximum 20 attempts; 300 seconds
per job and 1800 seconds for the trial, including model load and media creation.
Stop on failure; no automatic retry of failed, partial or unknown attempts.
Existing successful results are verified and reused. Never delete the state or
trial directory to bypass the attempt limit. The time limit does not stop Pod billing.

Results: /workspace/cctv-ab-20261001-01/comparison-20261001-01/
state.json holds attempts and status; comparison.json and comparison.html summarize
saved results. Each input/version directory retains raw output, media and worker.log.
Source video copies in inputs/ allow reviewing the exported HTML on Mac.
Failure, no_candidates and completed remain separate. No candidates is not proof
that an input is normal. Check generated clips and frames against the source video.

AFTER completion or failure — export saved files, model calls 0:
/workspace/cctv-ab-20261001-01/cctv-runpod-a4000/.venv/bin/python -B /workspace/cctv-ab-runner-20261001-01/runner.py --export
Download the resulting ZIP to Mac and verify its printed hash before stopping the Pod.
Export packages retained originals, results, logs and a manifest; it runs no models.

These ten inputs were exposed during training. This is a functional/media/runtime
reference comparison, not an independent accuracy or mAP evaluation. A and B differ
in YOLO weights/settings and event/media processing; their X3D weights are identical.
Read contract.json for frozen settings, assessment rules and all known differences.
Export results and logs to Mac and verify their hashes before stopping/terminating
the Pod; storage persistence has not been demonstrated.
"""

# This helper is delivered beside the ZIP, not inside its self-described manifest.
EXTRACTOR = '''"""Extract only the hash-verified fixed runner bundle without overwriting files."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

NAMES = {"runner.py", "job.py", "contract.json", "source-files.json", "RUNPOD.txt"}
ROOT = "cctv-ab-runner-20261001-01"

def extract(archive, expected_hash, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; preserved")
    if not destination.parent.is_dir() or destination.parent.is_symlink():
        raise ValueError("Destination parent must be an existing directory")
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > 2_000_000:
        raise ValueError("Expected the small regular runner ZIP")
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError("Runner ZIP hash mismatch; no extraction")
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        expected = {ROOT + "/" + name for name in NAMES | {"bundle-manifest.json"}}
        if len(bundle.namelist()) != len(expected) or set(bundle.namelist()) != expected:
            raise ValueError("Unexpected runner ZIP members")
        if any(not stat.S_ISREG(info.external_attr >> 16) for info in bundle.infolist()):
            raise ValueError("Runner ZIP must contain regular files")
        if sum(info.file_size for info in bundle.infolist()) > 2_000_000:
            raise ValueError("Runner ZIP expanded size exceeds limit")
        payloads = {name: bundle.read(ROOT + "/" + name) for name in NAMES | {"bundle-manifest.json"}}
    manifest = json.loads(payloads["bundle-manifest.json"])
    if manifest.get("schema_version") != "cctv-ab-runner-bundle-v1" or set(manifest["files"]) != NAMES:
        raise ValueError("Runner manifest file list mismatch")
    for name in NAMES:
        spec = manifest["files"][name]
        payload = payloads[name]
        if len(payload) != spec["bytes"] or hashlib.sha256(payload).hexdigest() != spec["sha256"]:
            raise ValueError("Runner member hash mismatch")
    destination.mkdir(mode=0o700)
    for name, payload in payloads.items():
        target = destination / name
        with target.open("xb") as stream:
            stream.write(payload)
        if target.read_bytes() != payload:
            raise ValueError("Runner write verification failed; preserve partial directory")
    return {"status": "ab_runner_extracted", "directory": str(destination),
            "files_verified": len(payloads), "model_calls": 0}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(extract(args.zip, args.sha256, args.destination), indent=2))
'''


def fingerprint(data):
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def safe_relative(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
            or str(path) != name or name.endswith("/")):
        raise ValueError("Unexpected source manifest path")


def source_files(version):
    spec = SOURCES[version]
    archive = ROOT / "artifacts/runpod/ab-transfer-20261001-01" / spec["archive"]
    data = archive.read_bytes()
    if fingerprint(data)["sha256"] != spec["sha256"]:
        raise ValueError("Original source ZIP hash mismatch: " + version)
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        prefix = spec["root"] + "/"
        manifest_data = bundle.read(prefix + spec["manifest"])
        manifest = json.loads(manifest_data)
        files = manifest["files"] if version == "A" else manifest
        if len(files) != spec["files"] or spec["manifest"] in files:
            raise ValueError("Original source manifest count/recursion mismatch")
        for name in files:
            safe_relative(name)
        expected = {prefix + name for name in files} | {prefix + spec["manifest"]}
        if len(bundle.namelist()) != len(expected) or set(bundle.namelist()) != expected:
            raise ValueError("Original source ZIP member list mismatch")
        for entry in bundle.infolist():
            if not stat.S_ISREG(entry.external_attr >> 16):
                raise ValueError("Original source ZIP member is not a regular file")
        for name, expected_hash in files.items():
            if fingerprint(bundle.read(prefix + name)) != expected_hash:
                raise ValueError("Original source file mismatch: " + version + "/" + name)
    return {**files, spec["manifest"]: fingerprint(manifest_data)}


def build(output):
    """Freeze source/configuration bytes once; preserve an existing destination."""
    if output.exists() or output.is_symlink():
        raise ValueError("Bundle output directory already exists; refusing overwrite")
    if not output.parent.is_dir():
        raise ValueError("Bundle output parent must exist")
    source_identities = {version: source_files(version) for version in ("A", "B")}
    contract_data = CONTRACT.read_bytes()
    contract = json.loads(contract_data)
    for version, spec in SOURCES.items():
        if contract["versions"][version]["bundle_sha256"] != spec["sha256"]:
            raise ValueError("Approved contract source ZIP mismatch")
        for name, expected in contract["versions"][version]["model_files"].items():
            if source_identities[version][name] != expected:
                raise ValueError("Approved contract model identity mismatch")
    payloads = {"runner.py": (ROOT / "apps/model_compare/runner.py").read_bytes(),
                "job.py": (ROOT / "apps/model_compare/job.py").read_bytes(),
                "contract.json": contract_data,
                "source-files.json": json_bytes(source_identities),
                "RUNPOD.txt": RUNPOD.encode()}
    for name in ("runner.py", "job.py"):
        compile(payloads[name], name, "exec")  # Syntax only; never import or execute.
    manifest = {"schema_version": "cctv-ab-runner-bundle-v1",
                "files": {name: fingerprint(data) for name, data in payloads.items()}}
    payloads["bundle-manifest.json"] = json_bytes(manifest)
    output.mkdir(mode=0o700)
    archive = output / (BUNDLE + ".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in payloads.items():
            entry = zipfile.ZipInfo(BUNDLE + "/" + name)
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(entry, data)
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.namelist()) != len(payloads) or bundle.testzip() is not None:
            raise ValueError("Written runner ZIP validation failed; preserved")
        for name, data in payloads.items():
            if bundle.read(BUNDLE + "/" + name) != data:
                raise ValueError("Written runner ZIP changed; preserved")
    for name in ("contract.json", "source-files.json", "bundle-manifest.json"):
        with (output / name).open("xb") as stream:
            stream.write(payloads[name])
    with (output / "extract_uploaded_runner.py").open("x") as stream:
        stream.write(EXTRACTOR)
    result = {"status": "ab_runner_bundle_prepared", "archive": archive.name,
              **fingerprint(archive.read_bytes()), "bundle_root": BUNDLE,
              "files": len(payloads),
              "original_source_files_verified": {v: len(names) for v, names in source_identities.items()},
              "frozen_files": manifest["files"],
              "extractor": fingerprint(EXTRACTOR.encode()),
              "model_calls": 0, "video_decodes": 0, "remote_actions": 0,
              "model_compatibility_verified": False}
    with (output / "preparation.json").open("wb") as stream:
        stream.write(json_bytes(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="New artifact directory; existing directories are never overwritten")
    print(json.dumps(build(parser.parse_args().output), indent=2, ensure_ascii=False))
