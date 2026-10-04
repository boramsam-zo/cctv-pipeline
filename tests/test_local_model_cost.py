"""Tiny CPU fixtures test the cost checker; never real R3D-18/CCTV training."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import torch

from scripts import check_local_model_cost as cli
from boram3_cctv import model_cost_check as cost


class TinyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.features = torch.nn.Sequential(torch.nn.Conv3d(3, 4, 1), torch.nn.BatchNorm3d(4), torch.nn.ReLU())
        self.fc = torch.nn.Linear(4, 2)

    def forward(self, inputs):
        return self.fc(self.features(inputs).mean((2, 3, 4)))


class LocalModelCostTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((cli.ROOT / "configs/local_model_cost_check.json").read_text())
        torch.set_num_threads(1)
        torch.manual_seed(42)

    def test_only_approved_budget_and_profile_allowed(self):
        cost.validate(self.config, {})
        for key, value in (("optimizer_updates", 2), ("eval_calls", 4), ("warmup_forward_calls", 2),
                           ("max_seconds", 181), ("batch_size", 2), ("train_scope", "all"),
                           ("height", 112), ("width", 448), ("frames", 32), ("weights", "DEFAULT"),
                           ("purpose", "real_training"), ("dtype", "float16"), ("num_workers", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                cost.validate({**self.config, key: value}, {})

    def test_invalid_numeric_settings_fail(self):
        for key, value in (("seed", True), ("seed", -1), ("cpu_threads", 5),
                           ("learning_rate", 0), ("learning_rate", float("nan")),
                           ("momentum", float("inf")), ("momentum", -0.1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                cost.validate({**self.config, key: value}, {})

    def test_fallback_and_memory_override_refused(self):
        for env in [{"PYTORCH_ENABLE_MPS_FALLBACK": "1"}, {"PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0"},
                    {"PYTORCH_MPS_LOW_WATERMARK_RATIO": "9"}]:
            with self.subTest(env=env), self.assertRaises(ValueError):
                cost.validate(self.config, env)

    def test_timer_synchronizes_before_start_and_after_operation(self):
        calls = []
        times = iter([2.0, 2.25])
        def clock():
            calls.append("clock")
            return next(times)
        result, elapsed = cost.timed(lambda: calls.append("operation") or 7,
                                     lambda: calls.append("sync"), clock=clock)
        self.assertEqual(calls, ["sync", "clock", "operation", "sync", "clock"])
        self.assertEqual((result, elapsed), (7, 0.25))

    def measure(self, model, report):
        stages, snapshots = [], []
        inputs, targets = torch.rand(1, 3, 4, 8, 8), torch.zeros(1, dtype=torch.long)
        cost.measure_model(model, inputs, targets, self.config, torch.device("cpu"),
                           report, stages.append, snapshots.append)
        return stages, snapshots

    def test_one_update_warmup_and_eval_are_separate_and_backbone_frozen(self):
        model, report = TinyModel(), {"optimizer_updates": 0}
        before = copy.deepcopy(model.state_dict())
        stages, snapshots = self.measure(model, report)
        self.assertEqual(report["optimizer_updates"], 1)
        self.assertEqual(report["warmup"]["forward_calls"], 1)
        self.assertEqual(report["warmup"]["optimizer_updates"], 0)
        self.assertEqual(report["forward_calls_total"], 5)
        self.assertEqual(len(report["eval_forward_seconds"]), 3)
        self.assertIsNone(report["eval_summary"]["p95"])
        self.assertIn("training_backward", stages)
        self.assertIn("after_backward_before_update", snapshots)
        for name, value in model.state_dict().items():
            if name.startswith("fc."):
                self.assertFalse(torch.equal(value, before[name]))
            else:
                self.assertTrue(torch.equal(value, before[name]), name)
        self.assertTrue(report["state_checks"]["batchnorm_buffers_unchanged"])

    def test_unintended_bn_buffer_change_is_detected(self):
        model, report = TinyModel(), {"optimizer_updates": 0}
        original = cost.frozen_fingerprints
        calls = 0
        def changed_fingerprint(model):
            nonlocal calls
            calls += 1
            if calls == 2:
                model.features[1].running_mean.add_(1)
            return original(model)
        with patch.object(cost, "frozen_fingerprints", side_effect=changed_fingerprint):
            with self.assertRaisesRegex(RuntimeError, "batchnorm_changed"):
                self.measure(model, report)

    def test_backward_failure_does_not_update_or_fallback(self):
        report = {"optimizer_updates": 0}
        with patch.object(torch.Tensor, "backward", side_effect=RuntimeError("test_backward_failure")):
            with self.assertRaisesRegex(RuntimeError, "test_backward_failure"):
                self.measure(TinyModel(), report)
        self.assertEqual(report["optimizer_updates"], 0)

    def test_nonfinite_output_stops_before_update(self):
        model, report = TinyModel(), {"optimizer_updates": 0}
        with torch.no_grad():
            model.fc.weight.fill_(float("nan"))
        with self.assertRaisesRegex(RuntimeError, "invalid_warmup_output"):
            self.measure(model, report)
        self.assertEqual(report["optimizer_updates"], 0)

    def test_mps_unavailable_records_failure_before_model_initialization(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "environment", return_value={"virtual_environment": True}), \
             patch("torch.backends.mps.is_available", return_value=False), \
             patch.object(cli.platform, "system", return_value="Darwin"), \
             patch.object(cli.platform, "machine", return_value="arm64"), \
             patch.dict(os.environ, {"PYTORCH_ENABLE_MPS_FALLBACK": "0"}), \
             patch("torchvision.models.video.r3d_18") as build:
            report = cli.worker(self.config, "mps", Path(temp))
        build.assert_not_called()
        self.assertEqual(report["status"], "failed")
        self.assertIn("mps_unavailable_no_cpu_fallback", report["error"]["message"])
        self.assertEqual(report["optimizer_updates"], 0)
        self.assertFalse(report["implicit_cpu_fallback"])

    def test_non_mac_cannot_claim_mps_execution(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "environment", return_value={"virtual_environment": True}), \
             patch.object(cli.platform, "system", return_value="Linux"):
            report = cli.worker(self.config, "mps", Path(temp))
        self.assertIn("mps_not_verified", report["error"]["message"])
        self.assertIsNone(report["actual_model_device"])

    def test_output_refuses_existing_files_and_raw_data(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, "ROOT", Path(temp).resolve()):
            with self.assertRaises(ValueError):
                cli.output_directory(Path(temp) / "data/result")
            path = Path(temp).resolve() / "artifacts/model-check/existing"
            path.mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                cli.output_directory(path)

    def test_timeout_kills_process_group(self):
        process = Mock(pid=999999, returncode=-9)
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 180), -9]
        with patch.object(cli.subprocess, "Popen", return_value=process), patch.object(cli.os, "killpg") as kill:
            code, timed_out = cli.supervise(["fixture"], None, 180)
        self.assertEqual(code, -9)
        self.assertTrue(timed_out)
        kill.assert_called_once_with(999999, signal.SIGKILL)

    def test_partial_diagnostic_is_failure_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.json"
            path.write_text('{"status":')
            self.assertEqual(cli.diagnostic_json(path)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
