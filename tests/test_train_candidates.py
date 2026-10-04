"""Temporary CSV/name fixtures only: no real videos, labels, models or network."""
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
sys.path.insert(0, str(ROOT / "src"))
from boram3_cctv import preprocessing as pp
from boram3_cctv import train_candidates as tc

spec = importlib.util.spec_from_file_location("candidate_cli", ROOT / "scripts/check_train_candidates.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

FIELDS = ["path", "type", "accident_time", "weather", *tc.SPLIT_COLUMNS]


def row(name, iid="train", geo="train"):
    return ["real_videos/" + name, "t-bone", "1.5", "normal", iid, geo]


class TrainCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        (self.data / "real_videos").mkdir(parents=True)
        self.metadata = self.data / "metadata-real.csv"
        self.output = self.root / "artifacts/result"
        self.write([row("a.mp4"), row("b.mp4"), row("c.mp4"), row("d.mp4")])

    def write(self, rows, fields=FIELDS):
        with self.metadata.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows(rows)

    def reject(self, code, action=None):
        with self.assertRaisesRegex(pp.PreprocessingError, "^" + code + "$"):
            (action or (lambda: tc.inspect(self.data)))()

    def test_missing_candidates_are_unavailable_not_normal_or_corrupt(self):
        result = tc.inspect(self.data)
        self.assertEqual(result["status"], "candidate_list_prepared")
        self.assertEqual(result["selected_missing_count"], 3)
        self.assertEqual(result["unselected_metadata_video_checks"], {"status": "inspection_excluded", "count": 1})
        self.assertEqual([c["csv_row"] for c in result["candidates"]], [2, 3, 4])
        for c in result["candidates"]:
            self.assertEqual(c["local_status"], "missing")
            self.assertIsNone(c["target"])
            self.assertIsNone(c["approved_segment"])
            self.assertFalse(c["training_authorized"])

    def test_weather_collision_time_and_filename_do_not_create_labels_or_groups(self):
        self.write([row("same_1_00.mp4"), row("same_2_00.mp4")])
        result = tc.inspect(self.data)
        for c in result["candidates"]:
            self.assertEqual(c["source_annotations_raw"]["weather"], "normal")
            self.assertEqual(c["source_annotations_raw"]["accident_time"], "1.5")
            for key in ("target", "upstream_original_id", "event_id", "camera_id"):
                self.assertIsNone(c[key])
        self.assertFalse(result["selection_is_evaluation_protocol"])
        self.assertIsNone(result["chosen_evaluation_protocol"])

    def test_official_test_in_either_protocol_is_excluded(self):
        self.write([row("a.mp4", "test", "train"), row("b.mp4", "train", "test"),
                    row("c.mp4", "test", "test"), row("d.mp4")])
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            (self.data / "real_videos" / name).write_bytes(b"not-decoded")
        result = tc.inspect(self.data)
        self.assertEqual([c["path"] for c in result["candidates"]], ["real_videos/d.mp4"])
        self.assertTrue(all(i["reason"] == "official_test_protected" for i in result["local_inventory"]))

    def test_unknown_or_empty_split_is_unconfirmed_not_train(self):
        self.write([row("a.mp4", "", "train"), row("b.mp4", "train", "unknown")])
        result = tc.inspect(self.data)
        self.assertEqual(result["status"], "no_eligible_candidates")
        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["training_readiness"], "held")

    def test_existing_candidate_first_but_no_video_open(self):
        video = self.data / "real_videos/d.mp4"
        video.write_bytes(b"temporary-not-a-real-mp4")
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path.suffix == ".mp4":
                self.fail("video contents must not be read")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", guarded_open):
            result = tc.inspect(self.data)
        self.assertEqual([c["path"] for c in result["candidates"]],
                         ["real_videos/d.mp4", "real_videos/a.mp4", "real_videos/b.mp4"])
        self.assertEqual(result["candidates"][0]["local_status"], "present_not_decoded")
        self.assertEqual(result["video_files_opened"], 0)
        self.assertEqual(video.read_bytes(), b"temporary-not-a-real-mp4")

    def test_non_regular_file_is_not_available_video(self):
        (self.data / "real_videos/a.mp4").mkdir()
        self.assertEqual(tc.inspect(self.data)["candidates"][0]["local_status"], "not_regular_file")

    def test_synthetic_inputs_and_unrelated_files_are_not_read(self):
        (self.data / "metadata-synthetic.csv").write_text("invalid csv")
        (self.data / "synthetic_videos").mkdir()
        (self.data / "real_videos/.DS_Store").write_bytes(b"ignored")
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            self.assertNotIn("synthetic", str(path))
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", guarded_open):
            self.assertEqual(tc.inspect(self.data)["local_inventory"], [])

    def test_duplicate_metadata_path_is_rejected(self):
        self.write([row("a.mp4"), row("a.mp4", "test", "test")])
        self.reject("ambiguous_metadata_path")

    def test_missing_columns_and_bad_row_shape_are_rejected(self):
        self.write([["real_videos/a.mp4"]], ["path"])
        self.reject("required_metadata_columns_missing")
        self.write([row("a.mp4")[:-1]])
        self.reject("invalid_metadata_row")

    def test_missing_data_and_empty_metadata_are_errors(self):
        self.reject("data_root_missing", lambda: tc.inspect(self.root / "absent"))
        self.write([])
        self.reject("metadata_empty")
        self.metadata.unlink()
        self.reject("real_metadata_missing_or_symlink")

    def test_path_traversal_absolute_and_aliases_are_rejected(self):
        for name in ("real_videos/../../outside.mp4", "/outside.mp4", "synthetic_videos/a.mp4"):
            with self.subTest(name=name):
                bad = row("a.mp4"); bad[0] = name
                self.write([bad])
                self.reject("only_relative_real_video_paths_allowed")
        bad = row("a.mp4"); bad[0] = "real_videos/./a.mp4"
        self.write([bad])
        self.reject("noncanonical_video_path")

    def test_video_symlink_outside_root_is_rejected(self):
        outside = self.root / "outside.mp4"
        outside.write_bytes(b"untouched")
        (self.data / "real_videos/a.mp4").symlink_to(outside)
        self.reject("source_outside_allowed_directory")
        self.assertEqual(outside.read_bytes(), b"untouched")

    def test_candidate_size_row_and_directory_limits(self):
        for limit in (0, 4, True):
            self.reject("select_1_to_3_candidates", lambda: tc.inspect(self.data, limit))
        self.assertEqual(tc.inspect(self.data, 1)["selected_count"], 1)
        with patch.object(tc, "MAX_METADATA_BYTES", 1):
            self.reject("metadata_size_limit")
        with patch.object(tc, "MAX_ROWS", 1):
            self.reject("metadata_row_limit")
        (self.data / "real_videos/a.mp4").write_bytes(b"name-only")
        with patch.object(tc, "MAX_DIRECTORY_ENTRIES", 0):
            self.reject("directory_entry_limit")

    def test_input_change_during_read_is_rejected(self):
        with patch.object(pp, "sha256", side_effect=["before", "after"]):
            self.reject("metadata_changed_during_read")

    def test_output_protection_preserves_existing_files(self):
        self.output.mkdir(parents=True)
        marker = self.output / "keep.txt"
        marker.write_text("keep")
        self.reject("output_already_exists", lambda: cli.run(self.root, self.data, self.output, 3))
        self.assertEqual(marker.read_text(), "keep")
        self.reject("output_must_be_under_artifacts", lambda: cli.run(self.root, self.data, self.data / "result", 3))
        artifact_data = self.root / "artifacts/source"
        artifact_data.mkdir()
        self.reject("output_overlaps_input", lambda: cli.run(self.root, artifact_data, artifact_data / "result", 3))

    def test_worker_timeout_is_saved_as_error(self):
        with patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 30)):
            result = cli.run(self.root, self.data, self.output, 3)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "inspection_timeout")
        self.assertFalse(result["training_run"])
        self.assertEqual((self.output / "candidate_paths.txt").read_text(), "")

    def test_cli_real_subprocess_round_trip_and_no_source_change(self):
        before = self.metadata.read_bytes()
        report = cli.run(self.root, self.data, self.output, 2)
        self.assertEqual(report["selected_count"], 2)
        self.assertEqual(json.loads((self.output / "report.json").read_text()), report)
        self.assertEqual((self.output / "candidate_paths.txt").read_text(), "real_videos/a.mp4\nreal_videos/b.mp4\n")
        self.assertEqual(self.metadata.read_bytes(), before)
        self.assertNotIn(str(self.root), (self.output / "report.json").read_text())

    def test_worker_input_error_is_saved_and_not_reported_as_success(self):
        self.metadata.unlink()
        report = cli.run(self.root, self.data, self.output, 3)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["reason"], "real_metadata_missing_or_symlink")
        self.assertFalse(report["training_run"])


class ExpansionPlanningTests(unittest.TestCase):
    def rows(self, count=100):
        return {f'real_videos/source{i}_00.mp4': [
            {'csv_row': i+2, 'values': {'split_in_distribution': 'train',
                                      'accident_time': '3', 'duration': '10'}}]
                for i in range(count)}

    def test_disjoint_reproducible_proposals_without_execution_approval(self):
        rows = self.rows()
        result = tc.plan_expansion(rows, [], [])
        self.assertEqual(result, tc.plan_expansion(rows, [], []))
        self.assertEqual(result['counts'], {'training_candidate': 70,
                                          'development_review_reserved': 20})
        self.assertEqual(len({c['source_path'] for c in result['candidates']}), 90)
        self.assertTrue(all(c['target'] is None and c['split'] == 'unassigned'
                            and not c['training_authorized'] for c in result['candidates']))

    def test_official_test_family_existing_and_reserved_excluded(self):
        rows = self.rows(8)
        rows['real_videos/source0_01.mp4'] = [{'csv_row': 30, 'values': {
            'split_in_distribution': 'test', 'accident_time': '3', 'duration': '10'}}]
        result = tc.plan_expansion(rows, [{'source_path': 'real_videos/source1_00.mp4'}],
                                  [{'source_path': 'real_videos/source2_00.mp4'}],
                                  train_count=2, review_count=2)
        paths = {c['source_path'] for c in result['candidates']}
        self.assertFalse(paths & {f'real_videos/source{i}_00.mp4' for i in range(3)})
        self.assertNotIn('real_videos/source0_01.mp4', paths)

    def test_shortfall_and_invalid_times_not_silently_filled(self):
        rows = self.rows(2)
        rows['real_videos/source0_00.mp4'][0]['values']['accident_time'] = 'nan'
        result = tc.plan_expansion(rows, [], [], train_count=2, review_count=2)
        self.assertEqual(result['shortfalls'], {'training_candidate': 2,
                                               'development_review_reserved': 1})
        self.assertFalse(result['training_ready'])

    def test_bad_limits_rejected(self):
        for value in [0, 501, True, 1.5]:
            with self.assertRaises(pp.PreprocessingError):
                tc.plan_expansion({}, [], [], train_count=value)

    def test_200_candidate_plan_preserves_prior_70_and_review_20(self):
        rows = self.rows(250)
        before = tc.plan_expansion(rows, [], [])
        after = tc.plan_expansion(rows, [], [], train_count=200)
        self.assertEqual(after['candidates'][:90], before['candidates'])
        self.assertEqual(after['counts']['training_candidate'], 200)
        self.assertEqual(after['counts']['development_review_reserved'], 20)
        self.assertTrue(all(not x['training_authorized'] for x in after['candidates']))
        self.assertFalse(after['training_ready'])

    def test_500_plan_keeps_validation_and_reports_real_shortfall(self):
        rows = self.rows(450)
        before = tc.plan_expansion(rows, [], [], train_count=200)
        after = tc.plan_expansion(rows, [], [], train_count=500)
        self.assertEqual(after['candidates'][:220], before['candidates'])
        self.assertEqual(after['counts']['training_candidate'], 430)
        self.assertEqual(after['shortfalls']['training_candidate'], 70)
        self.assertEqual(len({r['source_path'] for r in after['candidates']}), 450)
        self.assertTrue(all(not r['training_authorized'] for r in after['candidates']))


if __name__ == "__main__":
    unittest.main()


class ExpansionReconciliationTests(unittest.TestCase):
    def inputs(self):
        c={'source_path':'real_videos/a_00.mp4','csv_row':2,'annotation_seconds':2.,
           'start_seconds':1.,'end_seconds':3.,'proposed_role':'training_candidate'}
        proposal={'purpose':'metadata_only_expansion_proposal','candidates':[c]}
        rows={c['source_path']:[{'csv_row':2,'values':{'split_in_distribution':'train',
              'split_geo_aware':'test','accident_time':'2','duration':'5'}}]}
        s={'status':'prepared_not_split','target':0,'label_origin':'user_assumed_normal',
           'sequence_id':'MVI_1','input':{'array_shape':[16,224,224,3],
           'strictly_increasing_timestamps':True},'start_seconds':0,'end_seconds':2,
           'frames_sha256':'fixture','official_split':None}
        return proposal, rows, [], [], {'status':'prepared_not_split','samples':[s]}

    def test_join_does_not_grant_split_or_invent_review(self):
        r=tc.reconcile_expansion_plan(*self.inputs())
        self.assertFalse(r['training_ready'])
        self.assertIsNone(r['records'][0]['target'])
        self.assertEqual(r['records'][0]['official_geo_split'],'test')
        self.assertEqual(r['records'][1]['target'],0)
        self.assertFalse(r['records'][1]['human_reviewed'])
        self.assertTrue(all(x['split']=='unassigned' for x in r['records']))

    def test_official_test_rejected(self):
        args=self.inputs();next(iter(args[1].values()))[0]['values']['split_in_distribution']='test'
        with self.assertRaises(pp.PreprocessingError):tc.reconcile_expansion_plan(*args)

    def test_reserved_source_rejected(self):
        args=self.inputs();args[3].append({'source_path':'real_videos/a_00.mp4'})
        with self.assertRaises(pp.PreprocessingError):tc.reconcile_expansion_plan(*args)

    def test_changed_annotation_rejected(self):
        args=self.inputs();args[0]['candidates'][0]['annotation_seconds']=9
        with self.assertRaises(pp.PreprocessingError):tc.reconcile_expansion_plan(*args)

    def test_family_overlap_between_roles_rejected(self):
        import copy
        args=self.inputs();c=copy.deepcopy(args[0]['candidates'][0]);c['proposed_role']='development_review_reserved'
        args[0]['candidates'].append(c)
        with self.assertRaises(pp.PreprocessingError):tc.reconcile_expansion_plan(*args)

class ColabExpansionRequestTests(unittest.TestCase):
    def request(self):
        candidates=[{'source_path':f'real_videos/clip{i}_00.mp4', 'csv_row':i+2,
                     'annotation_seconds':3.,'start_seconds':2.,'end_seconds':4.,
                     'proposed_role':'training_candidate'} for i in range(200)]
        return tc.colab_expansion_request({'purpose':'metadata_only_expansion_proposal','candidates':candidates},
            {'splits':{'train':[f'MVI_{i:05}' for i in range(60)],
                       'test':[f'MVI_{i:05}' for i in range(60,100)]}})

    def test_counts_provenance_reservations_and_no_execution(self):
        r=self.request()
        self.assertEqual(len(r['normal_candidates']),200)
        self.assertEqual(len(r['reserved_normal_sequences']),10)
        self.assertFalse({c['sequence_id'] for c in r['normal_candidates']} & set(r['reserved_normal_sequences']))
        self.assertTrue(all(c['label_origin']=='user_assumed_normal' and not c['human_reviewed'] for c in r['normal_candidates']))
        self.assertTrue(all(c['target']==1 for c in r['accident_candidates']))
        self.assertFalse(r['training_authorized'])

    def test_missing_files_and_official_test_blocked_no_decode(self):
        import zipfile
        r=self.request();r['accident_candidates']=r['accident_candidates'][:1];r['normal_candidates']=r['normal_candidates'][:1]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);md=p/'metadata.csv';z=p/'images.zip'
            md.write_text('path,accident_time,duration,split_in_distribution\nreal_videos/clip0_00.mp4,3,10,test\n')
            with zipfile.ZipFile(z,'w') as archive:archive.writestr('unrelated.txt','x')
            result=tc.inspect_colab_expansion(r,md,p,z)
            self.assertEqual(result['counts'],{'blocked':2})
            self.assertEqual(result['records'][0]['reason'],'official_test_or_unknown')
            self.assertEqual(result['decoded_frames'],0)

    def test_successful_file_connection_does_not_claim_prepared(self):
        import zipfile
        r=self.request();r['accident_candidates']=r['accident_candidates'][:1];r['normal_candidates']=r['normal_candidates'][:1]
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);md=p/'metadata.csv';z=p/'images.zip'
            md.write_text('path,accident_time,duration,split_in_distribution\nreal_videos/clip0_00.mp4,3,10,train\n')
            (p/'clip0_00.mp4').write_bytes(b'not a decoded video')
            with zipfile.ZipFile(z,'w') as archive:
                for n in range(1,51):archive.writestr(f'DETRAC-Images/MVI_00000/img{n:05d}.jpg',b'not decoded')
            result=tc.inspect_colab_expansion(r,md,p,z)
            self.assertEqual(result['counts'],{'file_connected_not_decoded':2})
            self.assertFalse(result['training_ready'])

class ReservedValidationTests(unittest.TestCase):
    def fixture(self):
        rows=[dict(source_path=f'real_videos/reserved{i}_00.mp4',csv_row=i+2,
                   proposed_role='development_review_reserved',start_seconds=1.,end_seconds=3.) for i in range(5)]
        md={r['source_path']:[dict(csv_row=r['csv_row'],values={'split_in_distribution':'train','accident_time':'2'})] for r in rows}
        splits={'splits':{'train':[f'MVI_{i:05d}' for i in range(60)],'test':['MVI_99999']}}
        return {'records':rows},{'records':[{'source_path':'real_videos/trained_00.mp4','sequence_id':None}]},splits,md

    def test_candidates_are_not_independent_or_approved(self):
        out=tc.connect_reserved_validation(*self.fixture())
        self.assertEqual(len(out['records']),10)
        self.assertFalse(out['independent_validation'])
        self.assertTrue(all(not r['evaluation_authorized'] for r in out['records']))
        self.assertTrue(all(r['target'] is None for r in out['records'][:5]))
        self.assertTrue(all(r['label_origin']=='user_assumed_normal' for r in out['records'][5:]))

    def test_training_overlap_rejected(self):
        a,t,s,m=self.fixture();t['records'][0]['source_path']=a['records'][0]['source_path']
        with self.assertRaises(pp.PreprocessingError):tc.connect_reserved_validation(a,t,s,m)

    def test_official_test_rejected(self):
        a,t,s,m=self.fixture();next(iter(m.values()))[0]['values']['split_in_distribution']='test'
        with self.assertRaises(pp.PreprocessingError):tc.connect_reserved_validation(a,t,s,m)


class DatasetRoleLedgerTests(unittest.TestCase):
    def md(self):
        return {f'real_videos/{name}_00.mp4': [dict(csv_row=i+2, values=dict(
            split_in_distribution=split, accident_time='2', duration='5'))]
            for i, (name, split) in enumerate([('used','train'),('dev','train'),('new','train'),('test','test')])}

    def test_roles_and_no_implicit_execution(self):
        m=self.md(); before=json.dumps(m)
        out=tc.dataset_role_ledger(m,['real_videos/used_00.mp4'],['real_videos/dev_00.mp4'])
        self.assertEqual([r['role'] for r in out['records']],
                         ['training_used','development_reference','training_candidate','official_test_protected'])
        self.assertTrue(all(not r['execution_authorized'] for r in out['records']))
        self.assertEqual(json.dumps(m),before)

    def test_history_conflicts_are_held(self):
        out=tc.dataset_role_ledger(self.md(),['real_videos/test_00.mp4','real_videos/dev_00.mp4'],['real_videos/dev_00.mp4'])
        self.assertEqual(out['counts']['hold'],2)

    def test_invalid_window_and_family_are_held(self):
        m=self.md(); m['real_videos/new_00.mp4'][0]['values']['accident_time']='nan'
        m['real_videos/test_01.mp4']=[dict(csv_row=8,values=dict(split_in_distribution='train',accident_time='2',duration='5'))]
        out=tc.dataset_role_ledger(m,[],[])
        self.assertEqual(out['counts']['hold'],2)

    def test_unknown_history_rejected(self):
        with self.assertRaises(pp.PreprocessingError):tc.dataset_role_ledger(self.md(),['missing'],[])


class LedgerRequestTests(unittest.TestCase):
    md = DatasetRoleLedgerTests.md
    def request_fixture(self):
        m=self.md();ledger=tc.dataset_role_ledger(m,['real_videos/used_00.mp4'],['real_videos/dev_00.mp4'])
        return m,ledger

    def test_only_new_train_window_connected(self):
        m,l=self.request_fixture();r=tc.expansion_request_from_ledger(l,m)
        self.assertEqual([x['source_path'] for x in r['accident_candidates']],['real_videos/new_00.mp4'])
        self.assertEqual(r['normal_candidates'],[])
        self.assertFalse(r['training_authorized'])

    def test_tampered_test_role_rejected(self):
        m,l=self.request_fixture();l['records'][-1]['role']='training_candidate'
        with self.assertRaises(pp.PreprocessingError):tc.expansion_request_from_ledger(l,m)

    def test_changed_annotation_rejected(self):
        m,l=self.request_fixture();m['real_videos/new_00.mp4'][0]['values']['accident_time']='2.5'
        with self.assertRaises(pp.PreprocessingError):tc.expansion_request_from_ledger(l,m)

    def test_accident_only_needs_no_detrac_archive(self):
        m,l=self.request_fixture();r=tc.expansion_request_from_ledger(l,m)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);md=root/'metadata.csv'
            md.write_text('path,accident_time,duration,split_in_distribution\nreal_videos/new_00.mp4,2,5,train\n')
            r['accident_candidates'][0]['csv_row']=2
            (root/'new_00.mp4').write_bytes(b'not decoded')
            out=tc.inspect_colab_expansion(r,md,root,root/'absent.zip')
            self.assertEqual(out['counts'],{'file_connected_not_decoded':1})
            self.assertEqual(out['decoded_frames'],0)
