"""Safety/error handling only. These tests never run R3D-18 training."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_check", ROOT / "scripts/check_local_model.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class LocalModelCheckTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "configs/local_model_check.json").read_text())

    def test_default_and_step_budget(self):
        for steps in (1, 2, 3):
            check.validate(self.config, steps, {})
        for steps in (0, 4, -1, True, 1.5):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                check.validate(self.config, steps, {})

    def test_pretrained_real_data_and_larger_inputs_rejected(self):
        for key, value in (("weights", "DEFAULT"), ("purpose", "real_training"),
                           ("batch_size", 2), ("frames", 32), ("height", 224),
                           ("dtype", "float16"), ("num_workers", 2), ("train_scope", "head_only")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                check.validate({**self.config, key: value}, 1, {})

    def test_cpu_fallback_rejected(self):
        with self.assertRaisesRegex(ValueError, "fallback"):
            check.validate(self.config, 1, {"PYTORCH_ENABLE_MPS_FALLBACK": "1"})
        check.validate(self.config, 1, {"PYTORCH_ENABLE_MPS_FALLBACK": "0"})

    def test_memory_override_rejected_even_zero_or_large_limit(self):
        for key in check.PROTECTED_ENV[1:]:
            for value in ("0", "0.0", "1.7", "9", "nan"):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "memory_protection"):
                    check.validate(self.config, 1, {key: value})

    def test_learning_rate_and_cpu_budget(self):
        for value in (0, -1, float("nan"), float("inf"), True, 0.1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                check.validate({**self.config, "learning_rate": value}, 1, {})
        for value in (0, 5, True):
            with self.assertRaises(ValueError):
                check.validate({**self.config, "cpu_threads": value}, 1, {})

    def test_unavailable_mps_stops_before_model_without_cpu_retry(self):
        # Empty fake modules ensure no torch tensors or model are executed.
        with patch.dict(sys.modules, {"torch": SimpleNamespace(), "torchvision": SimpleNamespace()}), \
             patch.object(check, "environment", return_value={"virtual_environment": True, "mps_available": False}), \
             patch.object(check.platform, "system", return_value="Darwin"), \
             patch.object(check.platform, "machine", return_value="arm64"), \
             patch.dict(os.environ, {}, clear=True):
            result = check.run(self.config, "mps", 1)
        self.assertEqual(result["status"], "failed")
        self.assertIn("no_cpu_fallback", result["error"]["message"])
        self.assertEqual(result["optimizer_updates"], 0)
        self.assertEqual(result["completed_steps"], 0)
        self.assertIsNone(result["actual_model_device"])

    def test_non_mac_does_not_claim_mps_verified(self):
        with patch.dict(sys.modules, {"torch": SimpleNamespace(), "torchvision": SimpleNamespace()}), \
             patch.object(check, "environment", return_value={"virtual_environment": True, "mps_available": True}), \
             patch.object(check.platform, "system", return_value="Linux"), \
             patch.dict(os.environ, {}, clear=True):
            result = check.run(self.config, "mps", 1)
        self.assertEqual(result["error"]["message"], "mps_not_verified_on_this_host")
        self.assertFalse(result["real_data_read"])

    def test_source_output_refused_and_existing_results_preserved(self):
        with self.assertRaises(ValueError):
            check.output_path(ROOT / "data/local/accident/result.json")
        with tempfile.TemporaryDirectory() as directory, patch.object(check, "ROOT", Path(directory)):
            output = Path(directory) / "artifacts/model-check/result.json"
            self.assertEqual(check.output_path(output), output.resolve())
            output.parent.mkdir(parents=True)
            output.write_text("original")
            with self.assertRaises(FileExistsError):
                check.output_path(output)
            self.assertEqual(output.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
