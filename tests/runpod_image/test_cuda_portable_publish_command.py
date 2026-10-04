"""Publish preparation checks with a fake Docker CLI; no Docker or network use."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "artifacts/runpod/service-cuda-portable-20261003-01"
IMAGE_ID = "sha256:95590931c8cda4b68ac22b099ed90035f387249d48179734eb155849b2754ddd"
DIGEST = "sha256:" + "a" * 64  # Deliberately different from the local image ID.
DESTINATION = "dahyeguk/cctv-runpod-ab:p6-cuda-portable-v1"
FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
state = Path(os.environ["FAKE_STATE"])
with (state / "calls.jsonl").open("a") as out:
    out.write(json.dumps(args) + "\n")
expected = os.environ["FAKE_IMAGE_ID"]
mode = os.environ.get("FAKE_MODE", "success")
if args[:2] == ["image", "inspect"]:
    print("sha256:" + "b" * 64 if mode == "wrong_source" else expected)
elif args[:2] == ["image", "ls"]:
    if mode == "conflict":
        print("sha256:" + "c" * 64)
    elif mode == "same_destination":
        print(expected)
elif args[0] == "tag":
    assert args[1] == expected
elif args[0] == "push":
    print("layer: Pushed", flush=True)
    if mode == "push_failure":
        print("denied: requested access to the resource is denied", file=sys.stderr)
        sys.exit(7)
    if mode != "no_digest":
        print("p6-cuda-portable-v1: digest: " + os.environ["FAKE_DIGEST"] + " size: 1234", flush=True)
else:
    raise SystemExit("Unexpected Docker command: " + repr(args))
'''


class CudaPortablePublishCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cctv-publish-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        for filename in ("publish-command-01.sh", "image-build-receipt.json"):
            shutil.copyfile(RELEASE / filename, self.directory / filename)
        binary = self.directory / "bin"
        binary.mkdir()
        (binary / "docker").write_text(FAKE_DOCKER)
        (binary / "docker").chmod(0o755)
        self.env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                        FAKE_STATE=str(self.directory), FAKE_IMAGE_ID=IMAGE_ID, FAKE_DIGEST=DIGEST)

    def run_command(self, mode="success"):
        return subprocess.run(["bash", str(self.directory / "publish-command-01.sh")],
                              env=dict(self.env, FAKE_MODE=mode), text=True, capture_output=True, timeout=10)

    def calls(self):
        path = self.directory / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def receipt(self):
        return json.loads((self.directory / "image-publish-receipt-01.json").read_text())

    def assert_no_mutation(self):
        self.assertFalse(any(args[0] in {"tag", "push"} for args in self.calls()))

    def test_success_captures_push_digest_and_live_log(self):
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("layer: Pushed", result.stdout)
        calls = self.calls()
        self.assertEqual([args for args in calls if args[0] == "tag"], [["tag", IMAGE_ID, DESTINATION]])
        self.assertEqual([args for args in calls if args[0] == "push"], [["push", DESTINATION]])
        receipt = self.receipt()
        self.assertEqual(receipt["status"], "cuda_portable_image_push_completed")
        self.assertEqual(receipt["image_id"], IMAGE_ID)
        self.assertEqual(receipt["registry_digest"], DIGEST)
        self.assertEqual(receipt["immutable_reference"], "dahyeguk/cctv-runpod-ab@" + DIGEST)
        self.assertFalse(receipt["registry_digest_independently_verified"])
        self.assertEqual(receipt["push_attempts"], 1)
        self.assertEqual(receipt["log_sha256"], hashlib.sha256((self.directory / "image-publish-01.log").read_bytes()).hexdigest())

    def test_wrong_source_stops_before_tag_or_push(self):
        result = self.run_command("wrong_source")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source image differs", result.stderr)
        self.assert_no_mutation()

    def test_failed_push_preserves_failure_and_does_not_retry(self):
        result = self.run_command("push_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.receipt()["push_exit_code"], 7)
        self.assertEqual(self.receipt()["status"], "cuda_portable_image_push_failed")
        self.assertIsNone(self.receipt()["registry_digest"])
        self.assertIn("denied:", (self.directory / "image-publish-01.log").read_text())
        self.assertEqual(sum(args[0] == "push" for args in self.calls()), 1)

    def test_conflicting_local_destination_is_preserved(self):
        result = self.run_command("conflict")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("destination tag points to another image", result.stderr)
        self.assert_no_mutation()

    def test_same_destination_can_be_published(self):
        result = self.run_command("same_destination")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.receipt()["registry_digest"], DIGEST)

    def test_changed_build_receipt_is_blocked_before_docker(self):
        with (self.directory / "image-build-receipt.json").open("a") as output:
            output.write("\n")
        result = self.run_command()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Build receipt differs", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_missing_push_digest_is_not_marked_successful(self):
        result = self.run_command("no_digest")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.receipt()["push_exit_code"], 0)
        self.assertEqual(self.receipt()["status"], "cuda_portable_image_push_digest_unconfirmed")
        self.assertIsNone(self.receipt()["registry_digest"])
        self.assertEqual(sum(args[0] == "push" for args in self.calls()), 1)

    def test_rerun_preserves_existing_evidence_and_does_not_push(self):
        self.assertEqual(self.run_command().returncode, 0)
        receipt_bytes = (self.directory / "image-publish-receipt-01.json").read_bytes()
        first_calls = self.calls()
        result = self.run_command()
        self.assertEqual(result.returncode, 0)
        self.assertIn("Saved completed push receipt", result.stdout)
        self.assertEqual(first_calls, self.calls())
        self.assertEqual(receipt_bytes, (self.directory / "image-publish-receipt-01.json").read_bytes())

    def test_new_manual_attempt_after_failure_preserves_previous_files(self):
        self.assertNotEqual(self.run_command("push_failure").returncode, 0)
        originals = {path.name: path.read_bytes() for path in self.directory.glob("image-publish-*")}
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, contents in originals.items():
            self.assertEqual((self.directory / name).read_bytes(), contents)
        second = json.loads((self.directory / "image-publish-receipt-02.json").read_text())
        self.assertEqual(second["status"], "cuda_portable_image_push_completed")
        self.assertEqual(second["push_attempts"], 1)
        self.assertEqual(second["script_automatic_retries"], 0)
        self.assertEqual(sum(args[0] == "push" for args in self.calls()), 2)

    def test_new_manual_attempt_preserves_interrupted_attempt_log(self):
        old_log = self.directory / "image-publish-01.log"
        old_log.write_bytes(b"incomplete previous output\n")
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(old_log.read_bytes(), b"incomplete previous output\n")
        second = json.loads((self.directory / "image-publish-receipt-02.json").read_text())
        self.assertEqual(second["registry_digest"], DIGEST)
        self.assertEqual(sum(args[0] == "push" for args in self.calls()), 1)


if __name__ == "__main__":
    unittest.main()
