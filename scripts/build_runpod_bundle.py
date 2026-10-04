"""Package the reviewed handoff with the temporary Runpod API; no model imports."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = ROOT / "apps/runpod_worker"
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "cctv-runpod-a4000"
    if target.exists():
        raise FileExistsError("Use a fresh output directory; existing bundles are preserved")
    original = json.loads((args.handoff / "manifest.json").read_text())
    checks = []
    for name, expected in original.items():
        path = args.handoff / name
        if path.stat().st_size != expected["bytes"] or digest(path) != expected["sha256"]:
            raise ValueError(f"Received manifest mismatch: {name}")
        checks.append({"file": name, "sha256": expected["sha256"], "matches": True})
    for path in source.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and not path.name.startswith("."):
            dest = target / path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
    for name in original:
        dest = target / "handoff" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.handoff / name, dest)
    shutil.copyfile(args.handoff / "manifest.json", target / "handoff/manifest.json")
    files = {}
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix == ".py":
            ast.parse(path.read_text())
        files[path.relative_to(target).as_posix()] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    manifest = {"schema_version": "runpod-bundle-v1", "profile": "a4000-single-job-evidence-optional-s3",
                "files": files, "received_source_checks": checks,
                "gpu_execution_verified": False, "s3_connected": False,
                "colab_success": "user_report_only_execution_environment_not_received"}
    (target / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2))
    archive = args.output / "cctv-runpod-a4000.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(args.output).as_posix())
    with zipfile.ZipFile(archive) as output:
        assert output.testzip() is None
        for item in output.infolist():
            assert not item.filename.startswith("/") and ".." not in Path(item.filename).parts
            assert output.read(item) == (args.output / item.filename).read_bytes()
    report = {"archive": archive.name, "bytes": archive.stat().st_size, "sha256": digest(archive),
              "files": len(files) + 1, "received_files_unchanged": len(checks),
              "zip_crc_and_contents_verified": True, "model_import_or_execution": False}
    (args.output / "verification.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
