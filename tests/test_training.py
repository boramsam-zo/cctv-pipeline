"""Temporary generated videos and a tiny CPU network, never ACCIDENT training."""
import copy
import csv
import importlib.util
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if not all(importlib.util.find_spec(name) for name in ("torch", "torchvision", "av", "numpy", "PIL")):
    raise unittest.SkipTest("Training tests require scripts/requirements-training.txt; use .venv-model-check/bin/python")

import av
import numpy as np
import torch
from boram3_cctv import preprocessing as pp, training as tr, training_data as td
from scripts import train_r3d18 as cli

torch.set_num_threads(2)


class TinyVideo(torch.nn.Module):
    def __init__(self, classes=2):
        super().__init__()
        self.features = torch.nn.Sequential(torch.nn.Conv3d(3, 4, 1), torch.nn.BatchNorm3d(4),
                                            torch.nn.ReLU(), torch.nn.AdaptiveAvgPool3d(1))
        self.fc = torch.nn.Linear(4, classes)

    def forward(self, inputs):
        return self.fc(self.features(inputs).flatten(1))


class LimitedRealTrainingTests(unittest.TestCase):
    """Generated receipts; exercise the new train-only contract without real data."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = json.loads((ROOT / "configs/train_r3d18.limited-real.json").read_text())
        self.config.update(train_manifest="manifest.json", data_root="data", metadata_path="data/metadata-real.csv")
        (self.root / "data/real_videos").mkdir(parents=True)
        (self.root / "data/metadata-real.csv").write_text("path,split_in_distribution\n")
        self.receipt = {"chosen_evaluation_protocol": "split_in_distribution", "inspection_status": "completed",
                        "input_sha256": {"data/metadata-real.csv": pp.sha256(self.root / "data/metadata-real.csv")},
                        "samples": []}
        self.manifest = {"schema_version": 1, "purpose": td.LIMITED_REAL, "official_split_column": "split_in_distribution",
                         "validation_segments": [], "test_segments": [], "reviewed_inputs_report": "receipt.json",
                         "authorization": {"source": "user_message", "training_authorized": True,
                            "conservative_groups_authorized": True, "max_epochs": 5, "max_loop_seconds": 1800,
                            "basis": "generated software fixture only"}, "segments": []}
        for i, target in enumerate([1, 1, 0, 0, 0]):
            sid = "fixture_" + str(i)
            source = self.root / f"data/real_videos/{i}.mp4"
            source.write_bytes(b"generated source " + (str(i).encode() if target else b"one normal"))
            h = pp.sha256(source)
            array, frames = self.root / (sid + ".npy"), self.root / (sid + ".json")
            array.write_bytes(b"generated array identity fixture")
            frames.write_text("{}")
            sample = {"sample_id": sid, "source_kind": "accident" if target else "qmul", "source_sha256": h,
                      "target": target, "video_path": f"real_videos/{i}.mp4", "start_seconds": 1.0, "end_seconds": 3.0,
                      "input_quality_status": "confirmed", "event_id": None, "upstream_original_id": None,
                      "csv_official_splits": {"split_in_distribution": "train", "split_geo_aware": "test"} if target else None,
                      "label_review": {"status": "approved", "label": "accident" if target else "normal",
                         "scope": "segment", "start_seconds": 1.0, "end_seconds": 3.0, "source_sha256": h,
                         "policy_version": "fixture", "reviewer": "fixture", "reviewed_at": "2026-09-21", "basis": "test only"},
                      "prepared_input": {"array_path": array.name, "array_sha256": pp.sha256(array),
                                         "frames_path": frames.name, "frames_sha256": pp.sha256(frames)}}
            self.receipt["samples"].append(sample)
            self.manifest["segments"].append({"sample_id": sid, "split": "train", "management_group": "uncertain" if target else "one-normal"})
        self.save()

    def save(self):
        (self.root / "receipt.json").write_text(json.dumps(self.receipt))
        self.manifest["reviewed_inputs_sha256"] = pp.sha256(self.root / "receipt.json")
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))

    def test_train_only_accepts_both_classes_without_fake_event_ids(self):
        r = td.limited_real_preflight(self.config, self.root)
        self.assertEqual(r["status"], "ready")
        self.assertEqual([p["target"] for p in r["prepared"]], [1, 1, 0, 0, 0])
        self.assertTrue(all(p["event_id"] is None for p in r["prepared"]))
        self.assertEqual(r["validation_segments"], 0)
        self.assertFalse(r["generalization_evaluation"])

    def test_official_test_is_rejected_in_train_only_mode(self):
        self.receipt["samples"][0]["csv_official_splits"]["split_in_distribution"] = "test"
        self.save()
        with self.assertRaisesRegex(td.TrainingError, "IID_test"):
            td.limited_real_preflight(self.config, self.root)

    def test_conservative_and_same_original_groups_cannot_be_split(self):
        for index in (1, 3):
            old = self.manifest["segments"][index]["management_group"]
            self.manifest["segments"][index]["management_group"] = "different"
            self.save()
            with self.assertRaises(td.TrainingError):
                td.limited_real_preflight(self.config, self.root)
            self.manifest["segments"][index]["management_group"] = old

    def test_no_validation_or_unapproved_execution_in_train_only_mode(self):
        original = copy.deepcopy(self.manifest)
        for mutate in (lambda: self.manifest.update(validation_segments=["fixture_0"]),
                       lambda: self.manifest["segments"][0].update(split="validation"),
                       lambda: self.manifest["authorization"].update(training_authorized=False)):
            self.manifest = copy.deepcopy(original)
            mutate(); self.save()
            with self.assertRaises(td.TrainingError):
                td.limited_real_preflight(self.config, self.root)

    def test_changed_evidence_input_or_label_is_rejected(self):
        array = self.root / self.receipt["samples"][0]["prepared_input"]["array_path"]
        array.write_bytes(b"changed")
        with self.assertRaisesRegex(td.TrainingError, "input_changed"):
            td.limited_real_preflight(self.config, self.root)
        array.write_bytes(b"generated array identity fixture")
        self.receipt["samples"][0]["label_review"]["label"] = "normal"
        self.save()
        with self.assertRaisesRegex(td.TrainingError, "unconfirmed"):
            td.limited_real_preflight(self.config, self.root)

    def test_epoch_time_and_scope_limits_are_explicit(self):
        for key, value in (("max_epochs", 6), ("max_seconds", 1801), ("max_steps", 26),
                           ("train_scope", "all"), ("probe_max_seconds", 61)):
            with self.subTest(key=key), self.assertRaises(td.TrainingError):
                td.validate_config({**self.config, key: value})
        with self.assertRaisesRegex(td.TrainingError, "epoch_or_segment"):
            td.limited_real_preflight({**self.config, "max_steps": 20}, self.root)

    def test_hard_probe_deadline_stops_before_full_training_limit(self):
        r = cli.supervise([sys.executable, "-c", "import time; time.sleep(5)"], self.root, 5, probe_seconds=0.1)
        self.assertEqual(r["status"], "probe_time_limit")

    def test_step_observer_stops_and_saves_partial_training(self):
        config = {**self.config, "max_steps": 25}
        dataset = [(torch.zeros(3, 4, 8, 8), 0, "fixture_0"), (torch.zeros(3, 4, 8, 8), 1, "fixture_1")]
        r = tr.run_loop(TinyVideo(), dataset, config, {}, self.root / "partial", torch.device("cpu"),
                        step_observer=lambda record: "resource_limit")
        self.assertEqual(r["steps_this_run"], 1)
        self.assertEqual(r["stop_reason"], "resource_limit")
        self.assertTrue((self.root / "partial/checkpoint.pt").is_file())

    def test_probe_resume_reload_share_five_epoch_budget(self):
        import torchvision
        output = self.root / "worker"
        output.mkdir()
        dataset = [(torch.full((3, 4, 8, 8), i / 10), int(i < 2), "fixture_" + str(i)) for i in range(5)]
        request = {"config": {**self.config, "device": "cpu"}, "output": str(output), "provenance": {},
                   "weights": "explicit-generated-fixture", "prepared": []}
        with patch.object(tr, "build_pretrained_model", return_value=TinyVideo()), \
             patch.object(tr, "SegmentDataset", return_value=dataset), \
             patch.object(torchvision.models.video, "r3d_18", side_effect=lambda **kw: TinyVideo()):
            code = cli.run_limited_real_worker(request, torch, torchvision, tr)
        result = json.loads((output / "result.json").read_text())
        self.assertEqual(code, 0, result)
        self.assertEqual(result["training_updates"], 25)
        self.assertEqual(result["completed_epochs"], 5)
        self.assertEqual(result["probe"]["steps_this_run"], 2)
        self.assertEqual(result["loop"]["steps_this_run"], 23)
        self.assertTrue(result["reload_verified"])
        self.assertTrue(result["frozen_state"]["unchanged"])
        self.assertEqual(len(list(output.glob("progress-*.json"))), 25)


class SourceTrainingWorkerTests(unittest.TestCase):
    def test_three_probe_and_37_resumed_updates_share_40_step_budget(self):
        self.check_budget(8, 40)

    def test_33_inputs_share_165_total_updates_including_probe(self):
        self.check_budget(33, 165)

    def check_budget(self, samples, updates):
        import torchvision
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'worker'; output.mkdir()
            config=json.loads((ROOT/'configs/train_r3d18.limited-real.json').read_text())
            config.update(purpose=td.SOURCE_REAL, initialization='official_pretrained_fresh_head',
                          max_steps=updates,max_seconds=300,probe_steps=3,device='cpu',
                          source_input_limit=33 if samples>8 else 8)
            dataset=[(torch.full((3,4,8,8),i/10),int(i<5),f'fixture_{i}') for i in range(samples)]
            records=[{'array_path':'unused.npy','frames_path':'unused.json','target':int(i<5),
                      'label_provenance':{'kind':'source_annotation_derived' if i<3 else 'human_review'}} for i in range(samples)]
            request={'config':config,'output':str(output),'provenance':{'purpose':td.SOURCE_REAL},
                     'weights':'fixture-only','prepared':records}
            with patch.object(tr,'build_pretrained_model',return_value=TinyVideo()), \
                 patch.object(tr,'SegmentDataset',return_value=dataset), \
                 patch.object(torchvision.models.video,'r3d_18',side_effect=lambda **kw:TinyVideo()):
                code=cli.run_limited_real_worker(request,torch,torchvision,tr)
            result=pp.read_json(output/'result.json')
            self.assertEqual(code,0,result)
            self.assertEqual((result['training_updates'],result['completed_epochs']),(updates,5))
            self.assertEqual((result['probe']['steps_this_run'],result['loop']['steps_this_run']),(3,updates-3))
            self.assertTrue(result['frozen_state']['unchanged'])
            self.assertTrue(result['resume_verified'] and result['reload_verified'])
            self.assertFalse(result['validation_run'])
            self.assertEqual(result['purpose'],td.SOURCE_REAL)
            self.assertEqual(result['label_origin_counts'],{'human_review':samples-3,'source_annotation_derived':3})
            rows=[json.loads(s) for phase in ('probe','loop') for s in (output/phase/'train.jsonl').read_text().splitlines()]
            self.assertEqual([r['step'] for r in rows],list(range(1,updates+1)))


class ColabContinuationTests(unittest.TestCase):
    def test_saved_weights_and_learned_head_preserved_no_download(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'model.pt'; original=TinyVideo()
            settings={'test':'generated fixture'}
            torch.save({'model_name':'r3d_18','class_mapping':td.CLASS_MAPPING,
                        'config':{'preprocessing':settings},'model_state':original.state_dict()},path)
            with patch('torchvision.models.video.r3d_18',return_value=TinyVideo()) as factory, \
                 patch('torch.hub.download_url_to_file') as download:
                loaded=tr.build_saved_model(path,pp.sha256(path),settings)
            for k,v in original.state_dict().items():torch.testing.assert_close(v,loaded.state_dict()[k])
            self.assertFalse(loaded.features[1].training)
            self.assertTrue(loaded.fc.weight.requires_grad)
            factory.assert_called_once_with(weights=None,num_classes=2);download.assert_not_called()
            with self.assertRaisesRegex(td.TrainingError,'missing_or_changed'):
                tr.build_saved_model(path,'0'*64,settings)
            with self.assertRaisesRegex(td.TrainingError,'preprocessing_mismatch'):
                tr.build_saved_model(path,pp.sha256(path),{'changed':True})

    def test_cuda_scope_limits_and_existing_cli_gate(self):
        config=json.loads((ROOT/'configs/train_r3d18.colab-continue.json').read_text())
        for key,value in [('device','cpu'),('max_steps',41),('max_seconds',301),
                          ('initial_model_sha256','0'*64),('max_cuda_bytes',16*1024**3)]:
            with self.subTest(key=key),self.assertRaises(td.TrainingError):
                cli.colab_continuation_preflight(dict(config,**{key:value}),ROOT)
        with self.assertRaisesRegex(td.TrainingError,'explicit_colab_continuation'):
            td.source_real_preflight(config,ROOT)
        with self.assertRaisesRegex(td.TrainingError,'authorization_missing'):
            cli.colab_continuation_preflight(dict(config,authorization={}),ROOT)

    def test_warm_start_probe_resume_save_reload_and_missing_av(self):
        import torchvision
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'run';output.mkdir()
            config=json.loads((ROOT/'configs/train_r3d18.colab-continue.json').read_text())
            config['device']='cpu'  # Worker fixture; public Colab entry requires CUDA.
            dataset=[(torch.full((3,4,8,8),i/10),int(i<5),f'fixture_{i}') for i in range(8)]
            request={'config':config,'output':str(output),'provenance':{'fixture':True},
                'initial_model_sha256':'fixture','weights':'fixture',
                'prepared':[{'array_path':'unused','frames_path':'unused','target':int(i<5)} for i in range(8)]}
            get_version=cli.importlib.metadata.version
            def version(name):
                if name=='av':raise cli.importlib.metadata.PackageNotFoundError(name)
                return get_version(name)
            with patch.object(tr,'build_saved_model',return_value=TinyVideo()) as saved, \
                 patch.object(tr,'build_pretrained_model') as official, \
                 patch.object(tr,'SegmentDataset',return_value=dataset), \
                 patch.object(torchvision.models.video,'r3d_18',side_effect=lambda **kw:TinyVideo()), \
                 patch.object(cli.importlib.metadata,'version',side_effect=version):
                code=cli.run_limited_real_worker(request,torch,torchvision,tr)
            result=pp.read_json(output/'result.json')
            self.assertEqual(code,0,result);official.assert_not_called();saved.assert_called_once()
            self.assertTrue(result['previous_project_model_used_as_initialization'])
            self.assertEqual((result['probe']['steps_this_run'],result['loop']['steps_this_run']),(3,37))
            self.assertTrue(result['frozen_state']['unchanged'] and result['reload_verified'])
            self.assertEqual(result['initialization'],'project_model_fresh_optimizer')
            self.assertIn('not_required',result['versions']['av'])
            steps=[json.loads(s)['step'] for phase in ('probe','loop')
                   for s in (output/phase/'train.jsonl').read_text().splitlines()]
            self.assertEqual(steps,list(range(1,41)))

    def test_slow_probe_cannot_start_remaining_training(self):
        import torchvision
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'run';output.mkdir()
            config=json.loads((ROOT/'configs/train_r3d18.colab-continue.json').read_text())
            config.update(device='cpu',max_step_seconds=0)
            request={'config':config,'output':str(output),'provenance':{'fixture':True},
                'initial_model_sha256':'fixture','weights':'fixture',
                'prepared':[{'array_path':'unused','frames_path':'unused','target':0}]*8}
            with patch.object(tr,'build_saved_model',return_value=TinyVideo()), \
                 patch.object(tr,'SegmentDataset',return_value=[(torch.ones(3,4,8,8),0,'fixture')]*8):
                code=cli.run_limited_real_worker(request,torch,torchvision,tr)
            self.assertEqual(code,2)
            self.assertFalse((output/'loop').exists())
            self.assertIn('excessive_step_delay',pp.read_json(output/'result.json')['error'])


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        (self.data / "real_videos").mkdir(parents=True)
        self.config = json.loads((ROOT / "configs/train_r3d18.local.json").read_text())
        self.config.update(data_root="data", metadata_path="data/metadata.csv", segment_plan="plan.json",
                           split_manifest="splits.json", device="cpu", max_steps=2)
        self.config["preprocessing"].update(frame_count=4, image_size=32)
        review = {"status": "approved", "basis": "generated software fixture; not observed traffic",
                  "reviewer": "unit-test-fixture", "reviewed_at": "2026-09-18"}
        self.plan = {"schema_version": 1, "policy_version": "test-policy", "time_basis": "local_clip_seconds",
                     "frame_count": 4, "image_size": 32, "samples": []}
        self.split = {"schema_version": 1, "dataset_version": "fixture-1", "split_version": "fixture-1",
                      "official_split_column": "split_in_distribution", "review": review, "segments": []}
        rows = []
        for i in range(3):
            sid, relative = f"sample_{i+1:03}", f"real_videos/fixture_{i}.mp4"
            path = self.data / relative
            with av.open(str(path), "w") as container:
                stream = container.add_stream("mpeg4", rate=10)
                stream.width, stream.height, stream.pix_fmt = 64, 32, "yuv420p"
                for n in range(20):
                    pixels = np.full((32, 64, 3), 20 + i * 30 + n * 2, dtype=np.uint8)
                    frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                    frame.pts, frame.time_base = n, Fraction(1, 10)
                    for packet in stream.encode(frame):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            source_hash = pp.sha256(path)
            sample = {"sample_id": sid, "video_path": relative, "source_sha256": source_hash,
                      "start_seconds": 0.2, "end_seconds": 1.8, "window_basis": "fixture",
                      "label_review": {**review, "scope": "segment", "label": "accident" if i == 1 else "normal",
                                       "policy_version": "test-policy", "source_sha256": source_hash,
                                       "start_seconds": 0.2, "end_seconds": 1.8}}
            self.plan["samples"].append(sample)
            self.split["segments"].append({"sample_id": sid, "source_sha256": source_hash,
                                            "split": "train" if i < 2 else "validation",
                                            "source_group_id": f"fixture-group-{i}",
                                            "upstream_original_id": f"fixture-original-{i}",
                                            "event_id": f"fixture-event-{i}", "group_review": review,
                                            "training_authorized": i < 2})
            rows.append({"path": relative, "split_in_distribution": "train", "split_geo_aware": "train"})
        self.write_metadata(rows)
        self.write_manifests()

    def write_metadata(self, rows):
        with (self.data / "metadata.csv").open("w") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)

    def write_manifests(self):
        (self.root / "plan.json").write_text(json.dumps(self.plan))
        self.split["plan_sha256"] = pp.sha256(self.root / "plan.json")
        self.split["preprocessing_sha256"] = td.fingerprint(self.config["preprocessing"])
        (self.root / "splits.json").write_text(json.dumps(self.split))
        (self.root / "config.json").write_text(json.dumps(self.config))

    def ready(self):
        report = td.preflight(self.config, self.root)
        self.assertEqual(report["issues"], [])
        return report

    def dataset(self):
        report = self.ready()
        prepared = td.prepare_train_segments(self.config, self.root, report, self.root / "prepared")
        return tr.SegmentDataset(prepared, self.config["preprocessing"]), report["versions"]

    def test_current_real_samples_are_held_without_model_or_download(self):
        config = json.loads((ROOT / "configs/train_r3d18.local.json").read_text())
        report = td.preflight(config, ROOT)
        self.assertEqual(report["status"], "held")
        self.assertEqual(report["approved_segment_labels"], 0)
        self.assertIn("train_requires_reviewed_normal_and_accident", report["issues"])
        with patch.object(cli, "ROOT", self.root), patch.object(cli.td, "preflight", return_value=report), \
             patch.object(cli.td, "prepare_train_segments") as decode:
            result = cli.run(self.root / "config.json", self.root / "artifacts/held")
        self.assertFalse(result["training_run"])
        decode.assert_not_called()

    def test_missing_label_never_becomes_normal(self):
        self.plan["samples"][0]["label_review"] = {"status": "unreviewed", "label": None}
        self.write_manifests()
        report = td.preflight(self.config, self.root)
        self.assertEqual(report["status"], "held")
        self.assertIsNone(report["records"][0]["target"])

    def test_source_event_and_content_groups_cannot_cross_partitions(self):
        original = copy.deepcopy(self.split)
        for key in ("source_group_id", "upstream_original_id", "event_id"):
            self.split = copy.deepcopy(original)
            self.split["segments"][2][key] = self.split["segments"][0][key]
            self.write_manifests()
            self.assertIn("sample_003:cross_split_leakage:" + key, td.preflight(self.config, self.root)["issues"])
        self.split = original
        first, third = self.plan["samples"][0], self.plan["samples"][2]
        (self.data / third["video_path"]).write_bytes((self.data / first["video_path"]).read_bytes())
        third["source_sha256"] = third["label_review"]["source_sha256"] = first["source_sha256"]
        self.split["segments"][2]["source_sha256"] = first["source_sha256"]
        self.write_manifests()
        self.assertIn("sample_003:cross_split_leakage:source_sha256", td.preflight(self.config, self.root)["issues"])

    def test_official_test_cannot_move_to_train(self):
        rows = [{"path": s["video_path"], "split_in_distribution": "test", "split_geo_aware": "test"} for s in self.plan["samples"]]
        self.write_metadata(rows)
        self.assertIn("sample_001:official_split_conflict_or_unknown", td.preflight(self.config, self.root)["issues"])

    def test_reviewed_external_normal_does_not_need_fabricated_official_split(self):
        rows = [{"path": s["video_path"], "split_in_distribution": "" if i == 0 else "train",
                 "split_geo_aware": "" if i == 0 else "train"} for i, s in enumerate(self.plan["samples"])]
        self.write_metadata(rows)
        self.split["segments"][0].update(source_origin="external_real", external_source_review=self.split["review"])
        self.write_manifests()
        report = self.ready()
        self.assertEqual(report["records"][0]["official_split"], "")
        self.split["segments"][0]["external_source_review"] = {"status": "unreviewed"}
        self.write_manifests()
        self.assertEqual(td.preflight(self.config, self.root)["status"], "held")

    def test_external_flag_cannot_override_existing_official_test(self):
        rows = [{"path": s["video_path"], "split_in_distribution": "test", "split_geo_aware": "test"} for s in self.plan["samples"]]
        self.write_metadata(rows)
        self.split["segments"][0].update(source_origin="external_real", external_source_review=self.split["review"])
        self.write_manifests()
        self.assertIn("sample_001:official_split_conflict_or_unknown", td.preflight(self.config, self.root)["issues"])

    def test_approval_bound_to_plan_preprocessing_and_groups(self):
        self.ready()
        self.split["preprocessing_sha256"] = "bad"
        self.split["plan_sha256"] = "bad"
        self.split["segments"][0]["event_id"] = None
        (self.root / "splits.json").write_text(json.dumps(self.split))
        issues = td.preflight(self.config, self.root)["issues"]
        self.assertIn("split_plan_hash_mismatch", issues)
        self.assertIn("model_input_preprocessing_not_reviewed", issues)
        self.assertIn("sample_001:source_event_groups_unverified", issues)

    def test_invalid_config_limits(self):
        for key, value in (("max_steps", 21), ("max_seconds", 301), ("batch_size", 4),
                           ("num_workers", 1), ("device", "auto"), ("momentum", -0.5), ("learning_rate", float("nan"))):
            with self.subTest(key=key), self.assertRaises(td.TrainingError):
                td.validate_config({**self.config, key: value})

    def test_prepare_only_train_segments_and_preview_provenance(self):
        dataset, _ = self.dataset()
        self.assertEqual(len(dataset), 2)
        self.assertFalse((self.root / "prepared/sample_003").exists())
        self.assertEqual([dataset[i][1] for i in range(2)], [0, 1])
        for record in dataset.records:
            directory = Path(record["array_path"]).parent
            self.assertTrue((directory / "preview.png").exists())
            self.assertTrue((directory / "provenance.json").exists())
            provenance = json.loads((directory / "provenance.json").read_text())
            self.assertEqual(provenance["source_path"], record["source_path"])
            self.assertEqual(len(list((directory / "frames").glob("*.png"))), 4)
            self.assertEqual(dataset[0][0].shape, (3, 4, 32, 32))

    def test_tensor_transform_preserves_temporal_and_pixel_order(self):
        array = np.arange(4 * 32 * 32 * 3, dtype=np.uint8).reshape(4, 32, 32, 3)
        tensor = tr.tensor_from_array(array, self.config["preprocessing"])
        restored = ((tensor * torch.tensor(tr.STD).view(3, 1, 1, 1) + torch.tensor(tr.MEAN).view(3, 1, 1, 1)) * 255)
        np.testing.assert_array_equal(restored.permute(1, 2, 3, 0).round().byte().numpy(), array)
        with self.assertRaises(td.TrainingError):
            tr.tensor_from_array(array.astype(np.float32), self.config["preprocessing"])

    def test_missing_and_changed_prepared_input_are_errors(self):
        dataset, _ = self.dataset()
        path = Path(dataset.records[0]["array_path"])
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(td.TrainingError, "changed"):
            dataset[0]
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            dataset[0]

    def test_decode_timeout_and_corruption_abort(self):
        report = self.ready()
        with patch.object(td.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 45)):
            with self.assertRaisesRegex(td.TrainingError, "decode_timeout"):
                td.prepare_train_segments(self.config, self.root, report, self.root / "timeout")
        sample = self.plan["samples"][0]
        path = self.data / sample["video_path"]
        path.write_bytes(b"not video")
        sample["source_sha256"] = sample["label_review"]["source_sha256"] = pp.sha256(path)
        self.split["segments"][0]["source_sha256"] = pp.sha256(path)
        self.write_manifests()
        with self.assertRaises(td.TrainingError):
            td.prepare_train_segments(self.config, self.root, self.ready(), self.root / "corrupt")

    def test_head_updates_but_backbone_and_batchnorm_do_not(self):
        dataset, provenance = self.dataset()
        model = TinyVideo()
        # This test needs nonzero features; a random dead ReLU would make an
        # unchanged fc.weight valid even when fc.bias correctly updates.
        with torch.no_grad():
            model.features[0].weight.fill_(0.01)
            model.features[0].bias.fill_(1.0)
        before = copy.deepcopy(model.state_dict())
        result = tr.run_loop(model, dataset, self.config, provenance, self.root / "loop", torch.device("cpu"))
        self.assertEqual(result["progress"]["step"], 2)
        self.assertFalse(torch.equal(before["fc.weight"], model.fc.weight))
        for name, value in model.state_dict().items():
            if not name.startswith("fc."):
                self.assertTrue(torch.equal(before[name], value), name)

    def test_reload_and_resume_match_uninterrupted_training(self):
        dataset, provenance = self.dataset()
        base = TinyVideo()
        continuous, interrupted = copy.deepcopy(base), copy.deepcopy(base)
        tr.run_loop(continuous, dataset, self.config, provenance, self.root / "continuous", torch.device("cpu"))
        first = tr.run_loop(interrupted, dataset, self.config, provenance, self.root / "first", torch.device("cpu"), stop_after=1)
        self.assertEqual(first["progress"]["step"], 1)
        loaded = tr.reload_inference(self.root / "first/model.pt", TinyVideo, dataset[0][0].unsqueeze(0), torch.device("cpu"))
        interrupted.eval()
        torch.testing.assert_close(loaded, interrupted(dataset[0][0].unsqueeze(0)).detach())
        resumed = TinyVideo()
        second = tr.run_loop(resumed, dataset, self.config, provenance, self.root / "resumed", torch.device("cpu"),
                             resume=self.root / "first/checkpoint.pt")
        self.assertEqual(second["steps_this_run"], 1)
        for name, value in continuous.state_dict().items():
            torch.testing.assert_close(value, resumed.state_dict()[name], rtol=0, atol=0)

    def test_resume_rejects_changed_data_or_preprocessing(self):
        dataset, provenance = self.dataset()
        tr.run_loop(TinyVideo(), dataset, self.config, provenance, self.root / "first", torch.device("cpu"), stop_after=1)
        with self.assertRaisesRegex(td.TrainingError, "mismatch"):
            tr.run_loop(TinyVideo(), dataset, self.config, {**provenance, "dataset_version": "different"},
                        self.root / "bad", torch.device("cpu"), resume=self.root / "first/checkpoint.pt")

    def test_time_cap_can_stop_before_any_update(self):
        dataset, provenance = self.dataset()
        ticks = iter([0, 2, 3])
        result = tr.run_loop(TinyVideo(), dataset, {**self.config, "max_seconds": 1}, provenance,
                             self.root / "timed", torch.device("cpu"), clock=lambda: next(ticks))
        self.assertEqual(result["steps_this_run"], 0)
        self.assertEqual(result["stop_reason"], "time_limit")

    def test_deadline_after_backward_prevents_optimizer_update(self):
        dataset, provenance = self.dataset()
        model = TinyVideo()
        before = copy.deepcopy(model.state_dict())
        ticks = iter([0, 0.1, 0.2, 0.3, 2, 3])
        result = tr.run_loop(model, dataset, {**self.config, "max_seconds": 1}, provenance,
                             self.root / "before-update", torch.device("cpu"), clock=lambda: next(ticks))
        self.assertEqual(result["steps_this_run"], 0)
        self.assertEqual(result["stop_reason"], "time_limit_before_update")
        for name, value in model.state_dict().items():
            self.assertTrue(torch.equal(before[name], value), name)

    def test_reversed_frame_times_are_rejected_even_with_new_hash(self):
        dataset, _ = self.dataset()
        record = dataset.records[0]
        path = Path(record["frames_path"])
        details = json.loads(path.read_text())
        details["frames"].reverse()
        path.write_text(json.dumps(details))
        record["frames_sha256"] = pp.sha256(path)
        with self.assertRaisesRegex(td.TrainingError, "timing"):
            dataset[0]

    def test_existing_output_and_missing_weights_do_not_launch_training(self):
        with patch.object(cli, "ROOT", self.root), patch.object(cli, "supervise") as execute:
            held = cli.run(self.root / "config.json", self.root / "artifacts/no-weights")
            self.assertIn("official_weights_missing_download_requires_approval", held["issues"])
            execute.assert_not_called()
            with self.assertRaisesRegex(td.TrainingError, "exists"):
                cli.run(self.root / "config.json", self.root / "artifacts/no-weights")

    def test_resume_rejects_changed_class_mapping(self):
        dataset, provenance = self.dataset()
        tr.run_loop(TinyVideo(), dataset, self.config, provenance, self.root / "first", torch.device("cpu"), stop_after=1)
        path = self.root / "first/checkpoint.pt"
        state = torch.load(path, weights_only=True)
        state["class_mapping"] = {"normal": 1, "accident": 0}
        torch.save(state, path)
        with self.assertRaisesRegex(td.TrainingError, "mismatch"):
            tr.run_loop(TinyVideo(), dataset, self.config, provenance, self.root / "bad-map", torch.device("cpu"), resume=path)

    def test_hard_worker_deadline(self):
        result = cli.supervise([sys.executable, "-c", "import time; time.sleep(5)"], self.root, 0.1)
        self.assertEqual(result["status"], "worker_time_limit")
        self.assertFalse(result["checkpoint_may_exist"])

    def test_missing_official_weights_never_download(self):
        with patch("torch.hub.download_url_to_file") as download:
            with self.assertRaisesRegex(td.TrainingError, "approval"):
                tr.build_pretrained_model(self.root / "absent.pth", "head")
            download.assert_not_called()

    def test_official_loader_replaces_head_after_loading(self):
        source = TinyVideo(classes=400)
        torch.save(source.state_dict(), self.root / "mock-weights.pt")
        with patch.object(pp, "sha256", return_value="b3b3357e" + "0" * 56), \
             patch("torchvision.models.video.r3d_18", return_value=TinyVideo(classes=400)) as build:
            model = tr.build_pretrained_model(self.root / "mock-weights.pt", "head")
        build.assert_called_once_with(weights=None)
        self.assertEqual(model.fc.out_features, 2)
        torch.testing.assert_close(model.features[0].weight, source.features[0].weight)
        self.assertFalse(model.features[1].training)

    def test_actual_r3d_architecture_scope_without_forward_or_download(self):
        from torchvision.models.video import r3d_18
        with patch("torch.hub.download_url_to_file") as download:
            model = r3d_18(weights=None, num_classes=2)
        tr.set_training_scope(model, "head")
        self.assertEqual(sum(p.numel() for p in model.parameters() if p.requires_grad), 1026)
        self.assertTrue(all(not m.training for m in model.modules() if isinstance(m, torch.nn.BatchNorm3d)))
        tr.set_training_scope(model, "all")
        self.assertTrue(all(p.requires_grad for p in model.parameters()))
        download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
