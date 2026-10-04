"""Generated JSON/byte fixtures only; no traffic labels, video decoding, or training."""
from copy import deepcopy
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("record_segment_reviews", ROOT / "scripts/record_segment_reviews.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
pp = review.pp


class SegmentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".review-test-", dir=ROOT / "artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = {"schema_version": 1, "policy_version": "fixture-policy", "time_basis": "local_clip_seconds",
                     "frame_count": 16, "image_size": 512, "samples": []}
        rows = []
        for n in range(1, 4):
            sid = f"sample_{n:03}"
            sha = str(n) * 64
            self.plan["samples"].append({"sample_id": sid, "video_path": f"real_videos/{n}.mp4",
                "source_sha256": sha, "start_seconds": 1.0, "end_seconds": 3.0,
                "source_group_id": None, "event_id": None, "upstream_original_id": None,
                "label_review": {"status": "unreviewed", "label": None}})
            (self.root / f"{sid}.png").write_bytes(b"fixture bytes, hash only")
            rows.append({"sample_id": sid, "source_path": f"real_videos/{n}.mp4", "source_sha256": sha,
                "start_seconds": 1.0, "end_seconds": 3.0, "status": "unconfirmed", "target": None,
                "original_unchanged": True, "training_authorized": False, "preview_path": f"{sid}.png",
                "official_splits": {"split_in_distribution": "train" if n > 1 else "test", "split_geo_aware": "test"}})
        self.plan_path = self.root / "plan.json"
        self.manifest_path = self.root / "manifest.json"
        pp.write_json(self.plan_path, self.plan)
        self.manifest = {"status": "completed_with_unconfirmed", "inputs_unchanged": True,
            "plan_sha256": pp.sha256(self.plan_path),
            "settings": {k: self.plan[k] for k in ("policy_version", "frame_count", "image_size", "time_basis")},
            "samples": rows}
        pp.write_json(self.manifest_path, self.manifest)
        self.template = review.pending_answers(self.plan, self.manifest, pp.sha256(self.plan_path),
            pp.sha256(self.manifest_path), {r["sample_id"]: pp.sha256(self.root / r["preview_path"]) for r in rows})

    def answered(self, decision="accident"):
        answers = deepcopy(self.template)
        for row in answers["responses"]:
            row.update(decision=decision, basis="Fixture human statement, not real annotation.", reviewer="fixture-reviewer",
                reviewed_at="2026-09-21", response_source="human_completed_file", preview_visible=True,
                crop_or_missing_problem=False, order_problem=False, needs_more_context=False,
                whole_segment_viewed=True)
        return answers

    def apply(self, answers):
        return review.apply_answers(self.plan, self.manifest, self.template, answers)

    def test_blank_responses_never_become_normal(self):
        plan, records = self.apply(self.template)
        self.assertEqual([r["target"] for r in records], [None] * 3)
        self.assertEqual([r["status"] for r in records], ["unreviewed"] * 3)
        self.assertEqual(plan["samples"][0]["human_submission"]["reviewer"], None)

    def test_explicit_labels_use_existing_mapping_and_preserve_scope(self):
        for decision, target in [("normal", 0), ("accident", 1)]:
            with self.subTest(decision=decision):
                before = deepcopy(self.plan)
                result, records = self.apply(self.answered(decision))
                self.assertEqual([r["target"] for r in records], [target] * 3)
                self.assertEqual(self.plan, before)
                for sample, original in zip(result["samples"], self.manifest["samples"]):
                    self.assertEqual(sample["official_splits"], original["official_splits"])
                    self.assertFalse(sample["training_authorized"])
                    self.assertFalse(sample["model_input_human_approval"])
                    self.assertIsNone(sample["event_id"])

    def test_undetermined_is_not_third_training_class(self):
        _, records = self.apply(self.answered("undetermined"))
        self.assertEqual([r["status"] for r in records], ["undetermined"] * 3)
        self.assertTrue(all(r["target"] is None for r in records))

    def test_label_and_unanswered_quality_are_separate(self):
        answers = self.answered()
        answers["responses"][0].update(order_problem=None, needs_more_context=None)
        result, records = self.apply(answers)
        self.assertEqual(records[0]["target"], 1)
        self.assertEqual(records[0]["quality_unresolved"], ["order_problem_unresolved", "needs_more_context_unresolved"])
        self.assertIsNone(result["samples"][0]["human_submission"]["order_problem"])
        self.assertFalse(result["samples"][0]["model_input_human_approval"])

    def test_reported_quality_problem_is_not_cleared(self):
        answers = self.answered()
        answers["responses"][0]["crop_or_missing_problem"] = True
        result, _ = self.apply(answers)
        self.assertEqual(result["samples"][0]["input_quality_review"]["status"], "pending")

    def test_partial_label_information_is_held(self):
        for key in ("basis", "reviewer", "reviewed_at", "response_source", "preview_visible"):
            with self.subTest(key=key):
                answers = self.answered()
                answers["responses"][0][key] = None
                _, records = self.apply(answers)
                self.assertIsNone(records[0]["target"])
                self.assertEqual(records[0]["status"], "hold")

    def test_normal_requires_entire_segment_confirmation(self):
        answers = self.answered("normal")
        answers["responses"][0]["whole_segment_viewed"] = None
        _, records = self.apply(answers)
        self.assertIsNone(records[0]["target"])
        self.assertIn("normal_requires_whole_segment_review", records[0]["missing_or_unresolved"])

    def test_invalid_values_are_not_coerced(self):
        for key, value in [("decision", "t-bone"), ("preview_visible", 1), ("order_problem", "false"),
                           ("reviewed_at", "260921"), ("reviewed_at", "2026-02-30"), ("basis", 1),
                           ("response_source", "model_prediction")]:
            with self.subTest(key=key, value=value):
                answers = self.answered(); answers["responses"][0][key] = value
                with self.assertRaises(pp.PreprocessingError):
                    self.apply(answers)

    def test_answer_order_does_not_mix_samples(self):
        answers = self.answered()
        answers["responses"][0]["decision"] = "normal"
        answers["responses"].reverse()
        _, records = self.apply(answers)
        self.assertEqual([r["target"] for r in records], [0, 1, 1])

    def test_duplicate_missing_and_foreign_ids_rejected(self):
        for mode in ("duplicate", "missing", "foreign"):
            answers = self.answered()
            if mode == "missing": answers["responses"].pop()
            else: answers["responses"][0]["sample_id"] = "sample_002" if mode == "duplicate" else "sample_999"
            with self.assertRaises(pp.PreprocessingError): self.apply(answers)

    def test_source_window_and_preview_mismatch_rejected(self):
        for key, value in [("source_sha256", "0" * 64), ("start_seconds", 2), ("end_seconds", 4),
                           ("preview_sha256", "0" * 64), ("video_path", "real_videos/other.mp4")]:
            answers = self.answered(); answers["responses"][0][key] = value
            with self.assertRaisesRegex(pp.PreprocessingError, "answer_identity_mismatch"): self.apply(answers)

    def test_stale_answer_parent_hash_rejected(self):
        answers = self.answered(); answers["parent_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(pp.PreprocessingError, "stale_answer_version"): self.apply(answers)

    def test_changed_preview_cannot_reuse_old_response(self):
        path = self.root / "answers.json"; pp.write_json(path, self.answered())
        (self.root / "sample_001.png").write_bytes(b"changed preview")
        with self.assertRaisesRegex(pp.PreprocessingError, "answer_identity_mismatch"):
            review.run(self.plan_path, self.manifest_path, self.root / "out", path)
        self.assertFalse((self.root / "out").exists())

    def test_boolean_or_nonfinite_interval_rejected(self):
        for value in (True, float("nan"), float("inf")):
            answers = self.answered(); answers["responses"][0]["start_seconds"] = value
            with self.assertRaisesRegex(pp.PreprocessingError, "invalid_time_value"):
                self.apply(answers)

    def test_invalid_parent_manifest_rejected(self):
        for mode in ("hash", "duplicate", "error", "split", "time", "source"):
            plan, manifest = deepcopy(self.plan), deepcopy(self.manifest)
            if mode == "hash": manifest["plan_sha256"] = "0" * 64
            if mode == "duplicate": manifest["samples"][1] = manifest["samples"][0]
            if mode == "error": manifest["samples"][0]["status"] = "error"
            if mode == "split": manifest["samples"][0]["official_splits"] = {}
            if mode == "time": plan["samples"][0]["end_seconds"] = -1
            if mode == "source": plan["samples"][0]["video_path"] = "../other.mp4"
            with self.subTest(mode=mode), self.assertRaises(pp.PreprocessingError):
                review.pending_answers(plan, manifest, pp.sha256(self.plan_path), pp.sha256(self.manifest_path), {"sample_001": "0" * 64})

    def test_run_blank_without_video_access_and_no_overwrite(self):
        output = self.root / "out"
        with patch.object(pp, "sample_frames", side_effect=AssertionError("must not decode")):
            report = review.run(self.plan_path, self.manifest_path, output)
        self.assertEqual(report["counts"], {"unreviewed": 3})
        self.assertFalse(report["source_video_rechecked"])
        self.assertEqual(report["video_frames_decoded"], 0)
        self.assertFalse(list(output.rglob("*.npy")))
        with self.assertRaisesRegex(pp.PreprocessingError, "output_already_exists"):
            review.run(self.plan_path, self.manifest_path, output)

    def test_cli_answered_roundtrip_preserves_raw_text(self):
        answers = self.answered()
        answers["responses"][0].update(raw_response="fixture: 없는 것 같아", reviewed_at_as_reported="260921")
        path = self.root / "answers.json"; pp.write_json(path, answers)
        cmd = [sys.executable, str(ROOT / "scripts/record_segment_reviews.py"), "--plan", str(self.plan_path),
               "--manifest", str(self.manifest_path), "--answers", str(path), "--output", str(self.root / "out")]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["counts"], {"approved": 3})
        saved = pp.read_json(self.root / "out/reviewed_plan.json")
        self.assertEqual(saved["samples"][0]["human_submission"]["raw_response"], "fixture: 없는 것 같아")
        self.assertNotIn(str(self.root), proc.stdout)

    def test_output_outside_artifacts_and_input_limits(self):
        with self.assertRaisesRegex(pp.PreprocessingError, "output_must_be_under_project_artifacts"):
            review.run(self.plan_path, self.manifest_path, ROOT / "data/local/new-review")
        with patch.object(review, "MAX_JSON_BYTES", 5), self.assertRaisesRegex(pp.PreprocessingError, "oversized"):
            review.run(self.plan_path, self.manifest_path, self.root / "out")
        with self.assertRaisesRegex(pp.PreprocessingError, "missing"):
            review.run(self.root / "absent.json", self.manifest_path, self.root / "out")

    def test_missing_changed_or_escaping_preview_rejected(self):
        (self.root / "sample_001.png").unlink()
        with self.assertRaisesRegex(pp.PreprocessingError, "missing_or_oversized_preview"):
            review.run(self.plan_path, self.manifest_path, self.root / "out")
        self.manifest["samples"][0]["preview_path"] = "../outside.png"
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(pp.PreprocessingError, "unsafe_preview_path"):
            review.run(self.plan_path, self.manifest_path, self.root / "out")

    def test_changed_input_during_recording_releases_no_output(self):
        original = review.apply_answers
        def modify(*args):
            result = original(*args)
            self.plan_path.write_text('{}')
            return result
        with patch.object(review, "apply_answers", side_effect=modify):
            with self.assertRaisesRegex(pp.PreprocessingError, "input_changed_during_recording"):
                review.run(self.plan_path, self.manifest_path, self.root / "out")
        self.assertFalse((self.root / "out").exists())


class InputQualityReviewTests(unittest.TestCase):
    """Existing approved labels + generated 224px images; never real user answers."""
    answered = SegmentReviewTests.answered

    def setUp(self):
        from PIL import Image
        SegmentReviewTests.setUp(self)
        self.plan, _ = review.apply_answers(self.plan, self.manifest, self.template, self.answered())
        self.plan['image_size'] = 224
        self.plan_path.write_text(json.dumps(self.plan))
        rows, checks = [], []
        for sample in self.plan['samples']:
            sid = sample['sample_id']
            frames = [{'image': Image.new('RGB', (32, 16), (i * 12, 20, 30)),
                       'actual_seconds': 1.0625 + i / 8, 'requested_seconds': 1.0625 + i / 8,
                       'pts': 17 + 2 * i, 'pts_time_base': '1/16'} for i in range(16)]
            folder = self.root / 'prepared' / sid
            details = pp.save_frames(folder, frames, 224, 1, sid)
            row = {**deepcopy(sample), 'source_video_id': 'sha256:' + sample['source_sha256'],
                   'time_basis': self.plan['time_basis'], 'target': 1,
                   'preview_path': f'{sid}/preview.png', 'frames_metadata_path': f'{sid}/frames.json',
                   'input_path': f'{sid}/frames.npy', 'processed_frames': details}
            hashes = {'preview_sha256': pp.sha256(folder/'preview.png'),
                      'frames_sha256': pp.sha256(folder/'frames.json'), 'array_sha256': pp.sha256(folder/'frames.npy')}
            rows.append(row); checks.append({'sample_id': sid, 'target': 1, 'status': 'passed', **hashes})
        self.prepared_manifest = self.root/'prepared/manifest.json'
        pp.write_json(self.prepared_manifest, {'samples': rows, 'plan_sha256': pp.sha256(self.plan_path), 'inputs_unchanged': True})
        self.link_path = self.root/'link.json'
        self.link = {'purpose': 'configured_size_input_link_only', 'status': 'passed', 'training_run': False,
                     'configured_training_input_verified': True, 'derived_plan_sha256': pp.sha256(self.plan_path), 'samples': checks}
        pp.write_json(self.link_path, self.link)
        self.kind = 'accident'
        self.refresh_template()

    def refresh_template(self):
        _, self.template, _ = review.quality_inputs(self.plan_path, self.link_path, self.kind)

    def quality_answers(self):
        answer = deepcopy(self.template)
        for row in answer['responses']:
            row.update(reviewer='fixture-only', reviewed_at='2026-09-21', response_source='human_completed_file',
                       raw_response='Generated software-test answer, not a person inspecting traffic.')
            row['checks'] = {k: k == 'preview_visible' for k in row['checks']}
        return answer

    def apply(self, answers):
        return review.apply_quality_answers(self.plan, self.template, answers)

    def as_qmul(self):
        # Same test pixels, different existing report schema and approved normal labels.
        manifest = pp.read_json(self.prepared_manifest)
        self.plan.update(time_basis='local_file_pts_seconds', class_order=pp.CLASS_ORDER)
        for sample, row in zip(self.plan['samples'], manifest['samples']):
            sample['label_review']['label'] = 'normal'
            sample['official_split'] = None
            sample['source_group_id'] = 'fixture-same-file'
            row.update(target=0, label_review=deepcopy(sample['label_review']), official_split=None,
                       source_group_id='fixture-same-file', time_basis=self.plan['time_basis'])
            folder = self.root/'prepared'/sample['sample_id']
            details = pp.read_json(folder/'frames.json'); details['target'] = 0
            (folder/'frames.json').write_text(json.dumps(details))
            row['frames_sha256'] = pp.sha256(folder/'frames.json')
            row['array_sha256'] = pp.sha256(folder/'frames.npy')
        self.plan_path.write_text(json.dumps(self.plan))
        self.link_path = self.root/'prepared/qmul-report.json'
        pp.write_json(self.link_path, {'purpose':'external_reviewed_input_label_link_only', 'status':'passed',
                      'training_run':False, 'inputs_unchanged':True, 'preprocessing':{'frame_count':16,'image_size':224},
                      'input_sha256':{'review':pp.sha256(self.plan_path)}, 'samples':manifest['samples'],
                      'verification':[{'sample_id':s['sample_id'],'target':0,'status':'passed'} for s in self.plan['samples']]})
        self.kind = 'qmul'; self.refresh_template()

    def test_blank_keeps_all_existing_labels_and_quality_pending(self):
        records = self.apply(self.template)
        self.assertEqual([r['approved_target'] for r in records], [1, 1, 1])
        self.assertEqual([r['quality_status'] for r in records], ['awaiting_review'] * 3)
        for row, sample in zip(records, self.plan['samples']):
            self.assertEqual(row['original_segment_record'], sample)
            self.assertFalse(row['training_authorized'])

    def test_accident_asks_visibility_only_without_relabel_or_context_question(self):
        self.assertEqual(set(self.template['responses'][0]['checks']), {'preview_visible'})
        before = deepcopy(self.plan)
        records = self.apply(self.quality_answers())
        self.assertEqual([r['quality_status'] for r in records], ['confirmed'] * 3)
        self.assertEqual(self.plan, before)
        self.assertEqual(records[0]['original_segment_record']['official_splits']['split_in_distribution'], 'test')

    def test_visibility_issue_does_not_revoke_approved_accident(self):
        answers = self.quality_answers(); answers['responses'][0]['checks']['preview_visible'] = False
        records = self.apply(answers)
        self.assertEqual(records[0]['quality_status'], 'reported_issue')
        self.assertEqual(records[0]['approved_target'], 1)
        self.assertEqual(records[0]['original_segment_record']['label_review']['status'], 'approved')

    def test_qmul_preserves_normal_and_requires_own_context_answers(self):
        self.as_qmul()
        answers = self.quality_answers(); answers['responses'][1]['checks']['needs_more_context'] = None
        records = self.apply(answers)
        self.assertEqual([r['approved_target'] for r in records], [0, 0, 0])
        self.assertEqual([r['quality_status'] for r in records], ['confirmed', 'awaiting_review', 'confirmed'])
        self.assertEqual(records[0]['original_segment_record']['source_group_id'], 'fixture-same-file')
        self.assertIsNone(records[0]['original_segment_record']['official_split'])

    def test_partial_reply_and_reordered_ids_do_not_fill_other_samples(self):
        answers = deepcopy(self.template); answers['responses'][0] = self.quality_answers()['responses'][0]
        answers['responses'].reverse()
        self.assertEqual([r['quality_status'] for r in self.apply(answers)], ['confirmed','awaiting_review','awaiting_review'])

    def test_reviewer_date_and_raw_response_cannot_be_inferred(self):
        for key in ('reviewer', 'reviewed_at', 'response_source', 'raw_response'):
            with self.subTest(key=key):
                answers = self.quality_answers(); answers['responses'][0][key] = None
                self.assertEqual(self.apply(answers)[0]['quality_status'], 'awaiting_review')

    def test_wrong_bool_date_source_and_extra_label_rejected(self):
        for key, value in [('checks',{'preview_visible':1}), ('reviewed_at','260921'),
                           ('reviewed_at','2026-02-30'), ('response_source','model_prediction'), ('target',0)]:
            answers = self.quality_answers(); answers['responses'][0][key] = value
            with self.subTest(key=key), self.assertRaises(pp.PreprocessingError): self.apply(answers)

    def test_wrong_preview_interval_or_parent_and_duplicate_ids_rejected(self):
        for key, value in [('preview_sha256','0'*64), ('end_seconds',4), ('sample_id','sample_002')]:
            answers = self.quality_answers(); answers['responses'][0][key] = value
            with self.subTest(key=key), self.assertRaises(pp.PreprocessingError): self.apply(answers)
        answers = self.quality_answers(); answers['parent_report_sha256'] = '0'*64
        with self.assertRaisesRegex(pp.PreprocessingError,'stale_quality_answer_version'): self.apply(answers)

    def test_changed_preview_rejected_before_writing(self):
        (self.root/'prepared/sample_001/preview.png').write_bytes(b'changed')
        with self.assertRaisesRegex(pp.PreprocessingError,'checked_preview_changed'):
            review.run_quality(self.plan_path,self.link_path,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_qmul_sheet_without_old_hash_is_compared_to_checked_array(self):
        from PIL import Image
        self.as_qmul()
        path = self.root/'prepared/sample_001/preview.png'
        with Image.open(path) as sheet:
            sheet.paste('red',(0,44,224,268));sheet.save(path)
        with self.assertRaisesRegex(pp.PreprocessingError,'preview_does_not_match_checked_input'):
            review.run_quality(self.plan_path,self.link_path,self.root/'out',kind='qmul')

    def test_changed_input_or_missing_file_is_not_quality_approval(self):
        (self.root/'prepared/sample_001/frames.npy').write_bytes(b'changed')
        with self.assertRaisesRegex(pp.PreprocessingError,'checked_input_changed'): self.refresh_template()
        (self.root/'prepared/sample_001/frames.npy').unlink()
        with self.assertRaisesRegex(pp.PreprocessingError,'missing_or_oversized_array'): self.refresh_template()

    def test_wrong_plan_failed_report_or_escaping_path_rejected(self):
        for change in ({'status':'needs_attention'}, {'derived_plan_sha256':'0'*64}):
            self.link_path.write_text(json.dumps({**self.link,**change}))
            with self.assertRaises(pp.PreprocessingError): self.refresh_template()
        self.link_path.write_text(json.dumps(self.link))
        manifest=pp.read_json(self.prepared_manifest);manifest['samples'][0]['preview_path']='../escape.png'
        self.prepared_manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(pp.PreprocessingError,'artifact_path_outside_output'): self.refresh_template()

    def test_blank_run_no_decode_model_or_existing_output_overwrite(self):
        with patch.object(pp,'sample_frames',side_effect=AssertionError('no decode')):
            report=review.run_quality(self.plan_path,self.link_path,self.root/'out')
        self.assertEqual(report['counts'],{'awaiting_review':3})
        self.assertEqual(report['video_frames_decoded'],0);self.assertFalse(report['labels_changed'])
        self.assertFalse(report['model_created']);self.assertFalse(report['training_run'])
        with self.assertRaisesRegex(pp.PreprocessingError,'output_already_exists'):
            review.run_quality(self.plan_path,self.link_path,self.root/'out')

    def test_quality_cli_roundtrip_with_generated_complete_answers(self):
        path=self.root/'quality-answers.json';pp.write_json(path,self.quality_answers())
        proc=subprocess.run([sys.executable,str(ROOT/'scripts/record_segment_reviews.py'),'--input-quality','accident',
            '--plan',str(self.plan_path),'--manifest',str(self.link_path),'--answers',str(path),'--output',str(self.root/'out')],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['counts'],{'confirmed':3})
        saved=pp.read_json(self.root/'out/quality_review.json')
        self.assertEqual(saved['samples'][0]['quality_submission']['raw_response'],self.quality_answers()['responses'][0]['raw_response'])

    def test_change_during_recording_releases_no_output(self):
        original=review.apply_quality_answers
        def change(*args):
            result=original(*args);self.plan_path.write_text('{}');return result
        with patch.object(review,'apply_quality_answers',side_effect=change):
            with self.assertRaisesRegex(pp.PreprocessingError,'input_changed_during_recording'):
                review.run_quality(self.plan_path,self.link_path,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_quality_output_and_file_size_limits(self):
        with self.assertRaisesRegex(pp.PreprocessingError,'output_must_be_under_project_artifacts'):
            review.run_quality(self.plan_path,self.link_path,ROOT/'data/local/no')
        with patch.object(review,'MAX_PREVIEW_BYTES',10),self.assertRaisesRegex(pp.PreprocessingError,'oversized_preview'):
            self.refresh_template()

    def test_check_only_pending_preserves_all_files_and_labels(self):
        before = {p: pp.sha256(p) for p in self.root.rglob('*') if p.is_file()}
        with patch.object(pp, 'write_json', side_effect=AssertionError('read only')):
            report = review.run_quality(self.plan_path, self.link_path, check_only=True)
        self.assertEqual(report['status'], 'awaiting_review')
        self.assertEqual([r['approved_target'] for r in report['samples']], [1, 1, 1])
        self.assertTrue(report['check_only']); self.assertFalse(report['output_written'])
        self.assertFalse(report['training_run'])
        self.assertEqual(before, {p: pp.sha256(p) for p in self.root.rglob('*') if p.is_file()})

    def test_check_only_cli_distinguishes_pending_ready_and_issue(self):
        path = self.root / 'readonly-answers.json'
        for state, code, expected_status in [('pending', 3, 'awaiting_review'), ('clear', 0, 'ready_to_record'),
                                              ('issue', 2, 'needs_attention')]:
            answer = deepcopy(self.template) if state == 'pending' else self.quality_answers()
            if state == 'issue': answer['responses'][0]['checks']['preview_visible'] = False
            path.write_text(json.dumps(answer))
            before = {p: pp.sha256(p) for p in self.root.rglob('*') if p.is_file()}
            proc = subprocess.run([sys.executable, str(ROOT/'scripts/record_segment_reviews.py'),
                '--input-quality', 'accident', '--check-only', '--plan', str(self.plan_path),
                '--manifest', str(self.link_path), '--answers', str(path)], capture_output=True, text=True, timeout=10)
            with self.subTest(state=state):
                self.assertEqual(proc.returncode, code, proc.stdout+proc.stderr)
                result = json.loads(proc.stdout)
                self.assertEqual(result['status'], expected_status)
                self.assertFalse(result['output_written']); self.assertFalse(result['training_run'])
                self.assertEqual([r['approved_target'] for r in result['samples']], [1, 1, 1])
                self.assertEqual(before, {p: pp.sha256(p) for p in self.root.rglob('*') if p.is_file()})

    def test_check_only_rejects_output_and_recording_requires_one(self):
        with self.assertRaisesRegex(pp.PreprocessingError, 'check_only_does_not_accept_output'):
            review.run_quality(self.plan_path, self.link_path, self.root/'new', check_only=True)
        self.assertFalse((self.root/'new').exists())
        with self.assertRaisesRegex(pp.PreprocessingError, 'output_required_for_recording'):
            review.run_quality(self.plan_path, self.link_path)

    def test_check_only_cli_rejects_wrong_mode_and_missing_recording_output(self):
        base = [sys.executable, str(ROOT/'scripts/record_segment_reviews.py'),
                '--plan', str(self.plan_path), '--manifest', str(self.link_path)]
        for args, reason in [(['--check-only'], 'check_only_requires_input_quality'),
                             ([], 'output_required_for_recording')]:
            proc = subprocess.run(base+args, capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 2, proc.stdout+proc.stderr)
            self.assertEqual(json.loads(proc.stdout)['reason'], reason)

    def test_check_only_qmul_pending_and_changed_input_are_distinct(self):
        self.as_qmul()
        report = review.run_quality(self.plan_path, self.link_path, kind='qmul', check_only=True)
        self.assertEqual(report['status'], 'awaiting_review')
        self.assertEqual([r['approved_target'] for r in report['samples']], [0, 0, 0])
        (self.root/'prepared/sample_001/frames.npy').write_bytes(b'changed')
        with self.assertRaisesRegex(pp.PreprocessingError, 'checked_input_changed'):
            review.run_quality(self.plan_path, self.link_path, kind='qmul', check_only=True)


class SourceAnnotationTests(unittest.TestCase):
    """Explicit CSV-derived labels never become fabricated human approvals."""
    def setUp(self):
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory(prefix=".annotation-test-", dir=ROOT / "artifacts")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.metadata = self.data / "metadata-real.csv"
        self.raw = {"path": "real_videos/fixture.mp4", "type": "single", "weather": "normal",
                    "accident_time": "2.0", "duration": "5.0", "split_in_distribution": "train",
                    "split_geo_aware": "test"}
        self.sample = {"sample_id": "sample_010", "video_path": self.raw["path"], "source_sha256": "a"*64,
                       "start_seconds": 1.0, "end_seconds": 3.0,
                       "source_group_id": "unresolved", "event_id": None, "upstream_original_id": None,
                       "label_review": {"status": "unreviewed", "label": None, "basis": None}}
        self.plan = {"schema_version": 1, "policy_version": "fixture", "frame_count": 16,
                     "image_size": 224, "time_basis": "local_clip_seconds", "samples": [self.sample]}
        self.saved = self.root / "previews/sample_010"
        frames = [{"image": Image.new("RGB", (224,224), (i, i*2, i*3)),
                   "actual_seconds": 1+(i+.5)*2/16, "requested_seconds": 1+(i+.5)*2/16} for i in range(16)]
        self.details = pp.save_frames(self.saved, frames, 224, None, "sample_010")
        self.row = {**self.sample, "source_path": self.sample["video_path"], "status": "unconfirmed",
                    "target": None, "original_unchanged": True, "training_authorized": False,
                    "preview_path": "sample_010/preview.png", "frames_metadata_path": "sample_010/frames.json",
                    "media": {"duration_seconds": 5.0}, "processed_frames": self.details}
        self.manifest = {"preprocessing_version": pp.VERSION, "class_order": pp.CLASS_ORDER,
                         "status": "completed_with_unconfirmed", "inputs_unchanged": True,
                         "settings": {k:self.plan[k] for k in ("policy_version","frame_count","image_size","time_basis")},
                         "samples": [self.row]}
        self.plan_path, self.manifest_path = self.root/"plan.json", self.root/"previews/manifest.json"
        self.policy_path, self.reserved_path = self.root/"policy.json", self.root/"reserved.json"
        self.reserved = {"segments": [{"source_path": "real_videos/reserved.mp4", "source_sha256": "b"*64}]}
        self.policy = {"schema_version": 1, "policy_version": review.ANNOTATION_POLICY,
                       "purpose": "source_annotation_training_inputs_only", "window_seconds": 2,
                       "time_basis": "local_clip_seconds", "training_execution_authorized": False,
                       "sample_ids": ["sample_010"], "metadata_path": str(self.metadata.relative_to(ROOT)),
                       "reserved_manifest": str(self.reserved_path.relative_to(ROOT)),
                       "authorization": {"source":"user_message", "use_source_annotations":True,
                                         "raw_response":"Fixture authorization, not real user review."}}
        self.output = self.root/"result"
        self.sync()

    def sync(self):
        with self.metadata.open("w", newline="") as f:
            w=csv.DictWriter(f, fieldnames=self.raw.keys()); w.writeheader(); w.writerow(self.raw)
        self.row["official_splits"] = {k:self.raw[k] for k in ("split_in_distribution","split_geo_aware")}
        self.row["source_annotation"] = {"csv_row":2,"values":deepcopy(self.raw)}
        self.plan_path.write_text(json.dumps(self.plan))
        self.manifest.update(plan_sha256=pp.sha256(self.plan_path),metadata_sha256=pp.sha256(self.metadata))
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.reserved_path.write_text(json.dumps(self.reserved))
        self.policy.update(parent_plan_sha256=pp.sha256(self.plan_path),parent_manifest_sha256=pp.sha256(self.manifest_path),
                           metadata_sha256=pp.sha256(self.metadata),reserved_manifest_sha256=pp.sha256(self.reserved_path))
        self.policy_path.write_text(json.dumps(self.policy))

    def run_source(self):
        return review.run_source_annotations(self.plan_path,self.manifest_path,self.policy_path,self.output)

    def reject(self, reason):
        with self.assertRaisesRegex(pp.PreprocessingError, reason):
            self.run_source()
        self.assertFalse(self.output.exists())

    def test_csv_positive_reuses_pixels_preserves_human_status_and_loader_reads(self):
        import numpy as np
        from boram3_cctv.training import SegmentDataset
        before = {p:pp.sha256(p) for p in self.root.rglob('*') if p.is_file()}
        result = self.run_source()
        row = result['samples'][0]
        self.assertEqual(row['target'],1)  # weather=normal does not create target=0
        self.assertEqual(row['label_review'],self.sample['label_review'])
        self.assertFalse(row['label_provenance']['human_reviewed'])
        self.assertEqual(row['label_provenance']['annotation_field'],'accident_time')
        self.assertFalse(row['label_provenance']['binary_label_supplied_by_dataset'])
        self.assertFalse(row['training_execution_authorized'])
        self.assertFalse(row['evaluation_authorized'])
        self.assertEqual(result['counts']['normal_created'],0)
        rec={**row, **{k:ROOT/row[k] for k in ('array_path','frames_path')}}
        tensor,target,sid=SegmentDataset([rec],{'frame_count':16,'image_size':224})[0]
        self.assertEqual(tuple(tensor.shape),(3,16,224,224))
        self.assertEqual((target,sid),(1,'sample_010'))
        arr=np.load(ROOT/row['array_path'],allow_pickle=False)
        np.testing.assert_array_equal(arr[:,0,0,0],np.arange(16))
        self.assertEqual(before,{p:pp.sha256(p) for p in before})
        self.assertIsNone(pp.approved_target(self.sample,'fixture','a'*64))

    def test_explicit_source_policy_required(self):
        self.policy['authorization']['use_source_annotations']=False; self.sync()
        self.reject('source_annotation_authorization_required')

    def test_changed_parent_and_csv_rejected(self):
        self.plan_path.write_text(self.plan_path.read_text()+' ')
        self.reject('annotation_parent_changed')
        self.sync()
        self.metadata.write_text(self.metadata.read_text()+'\n')
        self.reject('annotation_metadata_changed')

    def test_iid_test_protected_while_geographic_test_is_allowed(self):
        self.raw['split_in_distribution']='test'; self.sync()
        self.reject('annotation_IID_test_forbidden')

    def test_reserved_path_hash_and_event_protected(self):
        for field,value in [('source_path',self.sample['video_path']),('source_sha256','a'*64),('event_id','known-event')]:
            with self.subTest(field=field):
                self.reserved={'segments':[{field:value}]}
                self.sample['event_id']='known-event'; self.sync()
                self.reject('annotation_reserved_source_forbidden')

    def test_invalid_point_never_becomes_normal(self):
        for value in ('', 'NaN','inf','-1','99'):
            with self.subTest(value=value):
                self.raw['accident_time']=value; self.sync()
                self.reject('annotation_invalid_time_or_duration|annotation_time_out_of_video')

    def test_short_video_or_wrong_window_not_silently_clipped(self):
        self.row['media']['duration_seconds']=2.5; self.sync()
        self.reject('time_out_of_video_range')
        self.row['media']['duration_seconds']=5.0
        self.sample['start_seconds']=0.5; self.row['start_seconds']=0.5; self.sync()
        self.reject('annotation_requires_existing_centered_2s_window')

    def test_changed_csv_row_and_selected_sample_rejected(self):
        self.row['source_annotation']['csv_row']=9
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.policy['parent_manifest_sha256']=pp.sha256(self.manifest_path)
        self.policy_path.write_text(json.dumps(self.policy))
        self.reject('annotation_csv_row_mismatch')
        self.policy['sample_ids']=['sample_011']; self.sync()
        self.reject('annotation_selection_mismatch')

    def test_missing_png_and_reordered_pixels_rejected(self):
        png=self.saved/'frames/000.png'; original=png.read_bytes(); png.unlink()
        self.reject('annotation_frame_missing_or_oversized')
        png.write_bytes((self.saved/'frames/001.png').read_bytes())
        self.reject('annotation_preview_pixel_or_order_mismatch')
        png.write_bytes(original)

    def test_out_of_order_pts_rejected(self):
        self.details['frames'][1]['actual_seconds']=self.details['frames'][0]['actual_seconds']
        (self.saved/'frames.json').write_text(json.dumps(self.details)); self.sync()
        self.reject('annotation_frame_order_or_time_mismatch')

    def test_no_overwrite(self):
        self.output.mkdir(); (self.output/'existing.txt').write_text('keep')
        with self.assertRaisesRegex(pp.PreprocessingError,'output_already_exists'):
            self.run_source()
        self.assertEqual((self.output/'existing.txt').read_text(),'keep')

    def test_cli_source_mode_uses_explicit_policy(self):
        cmd=[sys.executable,str(ROOT/'scripts/record_segment_reviews.py'),'--plan',str(self.plan_path),
             '--manifest',str(self.manifest_path),'--source-annotation-policy',str(self.policy_path),'--output',str(self.output)]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=10,cwd=ROOT)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['counts']['source_annotation_accident'],1)


if __name__ == "__main__":
    unittest.main()
