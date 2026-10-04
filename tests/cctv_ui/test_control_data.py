import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from apps.cctv_ui import control_data as data


NEW_JOB = '9ef3054db80346c29225fa45eb7ac8ed'
NEW_REPORT = '50bb10ef-4e80-503f-80f5-4f9771e21c04'


class SavedControlDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = data.load_saved_snapshot()
        path = data.PROJECT_ROOT / cls.saved['metadata']['source']
        cls.receipt = json.loads(path.read_text())
        cls.raw = json.loads(cls.receipt['stdout'])

    def test_saved_receipt_source_hash_and_read_only_scope(self):
        source = data.PROJECT_ROOT / self.saved['metadata']['source']
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         self.saved['metadata']['source_sha256'])
        self.assertFalse(self.saved['metadata']['snapshot_is_live'])
        self.assertEqual(self.saved['metadata']['model_calls_by_reader'], 0)

    def test_selection_and_job_duplicates_do_not_inflate_statistics(self):
        snapshot = data.normalize_snapshot(self.raw['jobs'] * 2, self.raw['selections'] * 2)
        self.assertEqual(len(snapshot['jobs']), 6)
        self.assertEqual(snapshot['stats']['job_count'], 6)
        self.assertEqual(snapshot['stats']['completed_count'], 5)
        self.assertEqual(snapshot['stats']['attention_count'], 1)
        self.assertEqual(snapshot['stats']['report_count'], 6)

    def test_selection_tracking_is_preserved_without_replacing_saved_job_body(self):
        original = {'worker_job_id': 'a' * 32, 'reports': [{'report_id': 'stored'}]}
        selected = {'worker_job_id': 'a' * 32, 'reports': [],
                    'mlflow': {'model_id': 'r2plus1d_18', 'model_version': 'epoch7', 'status': 'pending'}}
        rows = data.unique_jobs([original], [{'jobs': [selected]}])
        self.assertEqual(rows[0]['mlflow'], selected['mlflow'])
        self.assertEqual(rows[0]['reports'], original['reports'])
        self.assertNotIn('mlflow', original)
        original['mlflow'] = {'status': 'recorded'}
        self.assertEqual(data.unique_jobs([original], [{'jobs': [selected]}])[0]['mlflow'], original['mlflow'])

    def test_submission_hours_use_kst_and_are_distinct_from_incident_time(self):
        stats = self.saved['stats']
        self.assertEqual(stats['hour_counts'], {10: 2, 14: 3, 15: 1})
        self.assertEqual(stats['unknown_submission_time_count'], 0)
        self.assertEqual(stats['timezone'], 'Asia/Seoul')
        self.assertTrue(all(case['incident_at'] is None for case in self.saved['cases']))

    def test_unknown_and_naive_times_are_not_assigned_a_timezone(self):
        job = {'worker_job_id': 'a' * 32, 'stage': 'no_candidates',
               'submitted_at': '2026-10-04T10:00:00'}
        stats = data.build_statistics([job])
        self.assertEqual(stats['hour_counts'], {})
        self.assertEqual(stats['unknown_submission_time_count'], 1)
        self.assertIsNone(data.format_kst('wrong'))

    def test_candidates_multiple_tags_and_reports_are_separate_units(self):
        stats = self.saved['stats']
        self.assertEqual(stats['candidate_event_count'], 6)
        self.assertEqual(stats['type_tags']['side_collision'], 2)
        self.assertEqual(stats['type_tags']['multi_vehicle'], 2)
        self.assertIsNone(stats['confirmed_accident_count'])
        self.assertIsNone(stats['connected_camera_count'])
        no_candidate = data.normalize_case({'worker_job_id': 'a' * 32, 'stage': 'no_candidates'})
        self.assertIsNone(no_candidate['event_id'])
        self.assertIn('정상 판정 아님', no_candidate['status_label'])

    def test_snapshot_preserves_all_reports_and_vlm_payloads_exactly(self):
        originals = {job['worker_job_id']: job for job in self.raw['jobs']}
        for job in self.saved['jobs']:
            self.assertEqual(job['reports'], originals[job['worker_job_id']]['reports'])
            self.assertEqual(job['descriptions'], originals[job['worker_job_id']]['descriptions'])

    def test_new_case_reports_original_facts_and_unknown_registration(self):
        case = next(case for case in self.saved['cases'] if case['worker_job_id'] == NEW_JOB)
        self.assertEqual(case['report']['report_id'], NEW_REPORT)
        self.assertEqual(len(case['observations']), 4)
        self.assertEqual(len(case['verification_needed']), 6)
        self.assertEqual(len(case['agency_responses']), 3)
        self.assertEqual(len(case['references']), 3)
        self.assertIsNone(case['location'])
        self.assertIsNone(case['camera_id'])
        self.assertEqual(case['human_review'], 'unreviewed')
        self.assertEqual(case['candidate_time_s'], 2.0)
        self.assertEqual((case['source_start_s'], case['source_end_s']), (0.0, 7.0))
        # The detector timestamp is not copied into the teammate scene facts.
        self.assertIsNone(case['scene']['candidate_time_s'])

    def test_team_report_is_selected_when_legacy_and_team_both_exist(self):
        job = next(job for job in self.saved['jobs'] if job['worker_job_id'].startswith('c2f5dc'))
        case = data.normalize_case(job)
        self.assertEqual(case['report']['state']['result']['schema_version'], data.TEAM_REPORT)
        self.assertEqual(len(case['reports']), 2)

    def test_legacy_description_and_report_remain_explicitly_selectable(self):
        job = next(job for job in self.saved['jobs'] if job['worker_job_id'].startswith('c2f5dc'))
        old = next(item for item in job['descriptions']
                   if item['payload']['schema_version'] == 'cctv-scene-v2')
        case = data.normalize_case(job, 'event_000', old['analysis_id'])
        self.assertEqual(case['description'], old)
        self.assertEqual(case['analysis_id'], old['analysis_id'])
        self.assertEqual(len(case['reports']), 1)
        self.assertEqual(case['report']['report_id'], 'abcbabdf-1d7e-59c4-b514-737997d803ff')
        for kind in ['clip', 'roi_clip', 'frame']:
            self.assertEqual(data.media_reference(job, 'event_000', kind)['status'], 'local_available')
        with self.assertRaises(ValueError):
            data.normalize_case(job, 'event_000', 'unrelated-analysis-id')

    def test_normalization_does_not_mutate_original_records(self):
        before = copy.deepcopy(self.raw)
        data.normalize_snapshot(self.raw['jobs'], self.raw['selections'])
        self.assertEqual(self.raw, before)

    def test_saved_api_envelope_and_public_snapshot_normalize_the_same_jobs(self):
        source = data.PROJECT_ROOT / self.saved['metadata']['source']
        envelope = data.load_saved_snapshot(source)
        self.assertEqual(envelope['stats'], self.saved['stats'])
        self.assertEqual(set(job['worker_job_id'] for job in envelope['jobs']),
                         set(job['worker_job_id'] for job in self.saved['jobs']))

    def test_three_new_case_videos_are_real_exact_matching_local_files(self):
        job = next(job for job in self.saved['jobs'] if job['worker_job_id'] == NEW_JOB)
        for kind in ['original', 'clip', 'roi_clip', 'clip_preview', 'roi_clip_preview']:
            reference = data.media_reference(job, 'event_000', kind)
            self.assertEqual(reference['status'], 'local_available', kind)
            self.assertTrue(Path(reference['local_path']).is_file())
        self.assertEqual(data.media_reference(job, 'event_000', 'original')['api_path'],
                         '/jobs/' + NEW_JOB + '/source')
        self.assertIsNone(data.media_reference(job, 'event_000', 'frame')['local_path'])

    def test_relative_path_traversal_and_symlink_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / 'video.mp4'
            file.write_bytes(b'video')
            outside = root.parent / ('outside-' + root.name + '.mp4')
            outside.write_bytes(b'video')
            try:
                (root / 'link.mp4').symlink_to(outside)
                self.assertEqual(data.safe_local_path('video.mp4', root), file.resolve())
                for value in ['../' + outside.name, 'link.mp4', 'https://example.com/clip.mp4',
                              str(outside), 'missing.mp4', 'bad\x00path']:
                    self.assertIsNone(data.safe_local_path(value, root), value)
            finally:
                outside.unlink()

    def test_tampered_file_is_not_promoted_to_local_available(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'original.mp4'
            path.write_bytes(b'video')
            job = {'worker_job_id': 'a' * 32,
                   'input_sha256': hashlib.sha256(b'video').hexdigest(), 'input_bytes': 5,
                   'local_media': {'source': {'path': 'original.mp4'}}}
            self.assertEqual(data.media_reference(job, None, 'source', root)['status'], 'local_available')
            path.write_bytes(b'other')
            reference = data.media_reference(job, None, 'source', root)
            self.assertEqual(reference['status'], 'integrity_failed')
            self.assertIsNone(reference['local_path'])

    def test_preview_requires_a_receipt_linking_the_original_asset(self):
        job = copy.deepcopy(next(job for job in self.saved['jobs'] if job['worker_job_id'] == NEW_JOB))
        job['local_media']['event_000']['clip_preview']['source_sha256'] = '0' * 64
        self.assertIsNone(data.media_reference(job, 'event_000', 'clip_preview')['local_path'])

    def test_absent_local_copy_is_distinct_from_a_tampered_file(self):
        with tempfile.TemporaryDirectory() as folder:
            job = {'worker_job_id': 'a' * 32, 'input_sha256': 'b' * 64,
                   'local_media': {'source': {'path': 'missing.mp4'}}}
            reference = data.media_reference(job, None, 'source', Path(folder))
            self.assertEqual(reference['status'], 'api_read_required')
            self.assertIsNone(reference['local_path'])

    def test_media_route_identifiers_are_validated_before_use(self):
        for identifier in ['../private', 'a' * 31, 'a' * 32 + '?x=1']:
            with self.assertRaises(ValueError):
                data.media_reference({'worker_job_id': identifier}, 'event_000', 'clip')
        with self.assertRaises(ValueError):
            data.media_reference({'worker_job_id': 'a' * 32}, '../event_000', 'frame')
        with self.assertRaises(ValueError):
            data.media_reference({'worker_job_id': 'a' * 32}, 'event_000', 'unknown')

    def test_list_thumbnails_are_source_linked_images_without_an_api_route(self):
        for job in self.saved['jobs']:
            reference = data.media_reference(job, None, 'thumbnail')
            self.assertEqual(reference['status'], 'local_available')
            self.assertIsNone(reference['api_path'])
            self.assertEqual(job['local_media']['thumbnail']['source_sha256'], job['input_sha256'])
            self.assertNotEqual(reference['sha256'], job['input_sha256'])
            self.assertNotEqual(reference['local_path'], data.media_reference(job, None, 'source')['local_path'])
        job = copy.deepcopy(self.saved['jobs'][0])
        job['local_media']['thumbnail']['source_sha256'] = '0' * 64
        self.assertEqual(data.media_reference(job, None, 'thumbnail')['status'], 'integrity_failed')

    def test_thumbnail_cannot_be_replaced_with_an_original_video_or_event_frame(self):
        job = copy.deepcopy(self.saved['jobs'][0])
        original = job['local_media']['source']
        job['local_media']['thumbnail'].update(path=original['path'], sha256=original['sha256'],
                                               bytes=original['bytes'])
        self.assertEqual(data.media_reference(job, None, 'thumbnail')['status'], 'integrity_failed')
        job = copy.deepcopy(self.saved['jobs'][0])
        job['local_media']['thumbnail']['role'] = 'annotated_frame'
        self.assertEqual(data.media_reference(job, None, 'thumbnail')['status'], 'integrity_failed')

    def test_historical_box_image_is_separate_from_current_vlm_frame_identity(self):
        job = next(job for job in self.saved['jobs'] if job['worker_job_id'] == NEW_JOB)
        result = data.historical_frame_reference(job, 'event_000')
        self.assertEqual(result['status'], 'local_available')
        self.assertFalse(result['provenance']['current_vlm_evidence'])
        self.assertNotEqual(result['sha256'], result['current_frame_sha256'])
        self.assertEqual(result['representative_source_time_s'], 6.16)
        self.assertFalse(result['image_pts_verified'])
        self.assertIsNone(result['analysis_started_at'])
        self.assertIsNone(data.media_reference(job, 'event_000', 'frame')['local_path'])

    def test_historical_reference_rejects_another_source_event_or_timing(self):
        source = next(job for job in self.saved['jobs'] if job['worker_job_id'] == NEW_JOB)
        for mutate in ['source', 'event', 'timing', 'worker']:
            job = copy.deepcopy(source)
            if mutate == 'source':
                job['input_sha256'] = '0' * 64
            elif mutate == 'event':
                job['historical_frame_references']['event_001'] = job['historical_frame_references']['event_000']
            elif mutate == 'timing':
                job['evidence_events'][0]['representative_source_time_s'] = 3.0
            else:
                job['worker_job_id'] = 'a' * 32
            result = data.historical_frame_reference(job, 'event_001' if mutate == 'event' else 'event_000')
            self.assertEqual(result['status'], 'identity_mismatch', mutate)
            self.assertIsNone(result['local_path'])

    def test_historical_reference_rejects_corrupted_image_or_manifest(self):
        source = next(job for job in self.saved['jobs'] if job['worker_job_id'] == NEW_JOB)
        for role in ['image', 'manifest']:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                job = copy.deepcopy(source)
                records = job['historical_frame_references']['event_000']['files']
                for key, record in records.items():
                    relative = record['path'].split('/output/', 1)[1]
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes((data.PROJECT_ROOT / record['path']).read_bytes())
                    record['path'] = relative
                altered = root / records[role]['path']
                blob = altered.read_bytes()
                altered.write_bytes(b'X' + blob[1:])
                result = data.historical_frame_reference(job, 'event_000', root)
                self.assertEqual(result['status'], 'integrity_failed', role)
                self.assertIsNone(result['local_path'])


if __name__ == '__main__':
    unittest.main()
