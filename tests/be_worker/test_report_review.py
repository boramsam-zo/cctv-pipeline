"""Public synthetic review sidecars: immutable sources, no models or services."""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from apps.be_worker import flow_service, report_review, team_jobs
from apps.be_worker.common import WorkerError, read_json
from apps.be_worker.report_jobs import fingerprint
from apps.scene_description import scene, team_scene
from apps.team_rag import control_rag as rag
from tests.be_worker.test_team_view import ReadOnlyReplies, read_view
from tests.team_rag.test_relevance_synthetic import report_fixture


WORKER = 'a' * 32
PROVIDER = 'b' * 32
REPORT = '11111111-1111-5111-8111-111111111111'
ANALYSIS = '22222222-2222-5222-8222-222222222222'


class ReportReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / WORKER
        self.directory.mkdir()
        raw_report = report_fixture()
        raw_report['input']['event_id'] = PROVIDER + '_event_000'
        raw_report['input']['candidate_time_s'] = None
        raw_report['event_id'] = raw_report['input']['event_id']
        corpus = self.root / 'corpus'
        (corpus / 'data').mkdir(parents=True)
        (corpus / 'data/chunks.jsonl').write_text('\n'.join(
            json.dumps(row, ensure_ascii=False) for row in raw_report['sources']))
        root_patch = patch.object(rag, 'ROOT', corpus)
        root_patch.start(); self.addCleanup(root_patch.stop)
        for target in ('apps.team_rag.control_rag.encoder', 'apps.team_rag.runtime.retrieve'):
            forbidden = patch(target, side_effect=AssertionError('model/retrieval forbidden'))
            forbidden.start(); self.addCleanup(forbidden.stop)
        self.network = patch('socket.create_connection', side_effect=AssertionError('network forbidden'))
        self.network.start(); self.addCleanup(self.network.stop)
        raw_report.update(report_format_version='2.1', verification_needed=[], agency_responses=[],
            references=[dict(source, reference_id=f'R{i}') for i, source in enumerate(raw_report['sources'], 1)])
        source = {'bucket': 'synthetic-test-bucket', 'region': 'ap-northeast-2',
            'manifest_key': f'cctv-evidence/{PROVIDER}/manifest.json', 'manifest_sha256': 'c' * 64,
            'source_start_s': 0, 'source_end_s': 7,
            'bundle_hashes': {name: 'c' * 64 for name in ('source.json', 'manifest.json', 'evidence.json', 'fetch.complete.json')},
            'assets': {kind: {'asset_id': f'{PROVIDER}/event_000/{kind}',
                'key': f'cctv-evidence/{PROVIDER}/evidence/event_000/{kind}.{extension}',
                'sha256': 'd' * 64, 'bytes': 1} for kind, extension in (('clip', 'mp4'), ('frame', 'png'))}}
        receipt = {'analysis_id': ANALYSIS, 'provider_job_id': PROVIDER, 'event_id': 'event_000',
            'team_event_id': PROVIDER + '_event_000', 'model': 'gemini-3.5-flash-lite',
            'prompt_version': team_scene.PROMPT_VERSION, 'request_sha256': 'e' * 64,
            'contract_sha256': dict(team_scene.CONTRACT_HASHES), 'input_sha256': 'f' * 64,
            'source': source, 'maximum_generation_calls': 1, 'automatic_retry': False}
        self.scene = team_scene._result(receipt, raw_report['input'], {'synthetic_fixture': True}, 0)
        # _result wraps invented observations; it never calls a provider.
        self.original = team_jobs.envelope({'pipeline_id': 'team-rag-v1',
            'report_json': raw_report, 'report_markdown': rag.markdown(raw_report)}, self.scene, REPORT, WORKER)
        self.report = {'report_id': REPORT, 'settings': {'analysis_id': ANALYSIS, 'report_id': REPORT},
            'state': {'status': 'report_stored', 'result': self.original,
                      'result_sha256': fingerprint(self.original)}}
        self.row = {'worker_job_id': WORKER, 'directory_id': WORKER,
            'created_at': '2026-10-05T00:00:00+09:00', 'updated_at': '2026-10-05T00:00:00+09:00',
            'state': {'status': 'handoff_ready', 'provider_job_id': PROVIDER,
                      'input_sha256': self.scene['input_sha256'], 'scene_events': {}, 'gemini_api_calls': 1}}
        originals = self.directory / 'scenes/event_000/report-team-v1'
        originals.mkdir(parents=True)
        (originals / 'report.json').write_text(json.dumps(self.original['report_json'], ensure_ascii=False))
        (originals / 'report.md').write_text(self.original['report_markdown'])

    def record(self):
        return report_review.build(self.original, self.scene)

    def persist(self, record=None):
        record = self.record() if record is None else record
        path = report_review.review_path(self.directory, self.original, record['review_id'])
        path.parent.mkdir(parents=True, exist_ok=True)
        report_review.save_review(path, record)
        return path

    def connection(self):
        descriptions = [{'analysis_id': ANALYSIS, 'event_id': 'event_000', 'payload': self.scene,
                         'result_sha256': team_scene.validate_result(self.scene)}]
        return ReadOnlyReplies(self.row, descriptions, [self.report])

    def source_patches(self, connection):
        stack = ExitStack()
        store = Mock(); store.rows.return_value = [deepcopy(self.row)]
        connection.execute.return_value.fetchone.return_value = deepcopy(self.report)
        stack.enter_context(patch('apps.be_worker.queue_store.Store', return_value=store))
        stack.enter_context(patch.object(flow_service, 'spool', return_value=self.root))
        stack.enter_context(patch('apps.scene_description.team_db.show', return_value=deepcopy(self.scene)))
        return stack

    def test_build_is_deterministic_and_preserves_original_scene_and_raw_trace(self):
        before = deepcopy((self.original, self.scene))
        first, second = self.record(), self.record()
        self.assertEqual(first, second)
        self.assertNotEqual(first['review_id'], REPORT)
        self.assertEqual(first['source_result_sha256'], fingerprint(self.original))
        self.assertEqual(first['result_sha256'], fingerprint(first['result']))
        self.assertEqual(first['excluded_count'], 1)
        self.assertEqual(first['result']['report_json']['input'], self.scene['team_event'])
        self.assertEqual(first['result']['report_json']['retrieval']['trace'],
                         self.original['report_json']['retrieval']['trace'])
        self.assertEqual(first['result']['report_json']['quality_review']['original_sources'],
                         self.original['report_json']['sources'])
        self.assertEqual(first['model_calls'], 0)
        self.assertFalse(first['retrieval_executed'])
        self.assertEqual((self.original, self.scene), before)

    def test_source_scene_identity_mismatch_is_rejected(self):
        for key, value in (('analysis_id', REPORT), ('provider_job_id', 'a' * 32),
                           ('event_id', 'event_001'), ('scene_sha256', '0' * 64)):
            with self.subTest(key=key):
                changed = deepcopy(self.original); changed[key] = value
                with self.assertRaisesRegex(WorkerError, 'review_source_scene_mismatch'):
                    report_review.build(changed, self.scene)

    def test_changed_input_facts_or_markdown_cannot_be_reviewed_as_original(self):
        for field in ('facts', 'markdown'):
            with self.subTest(field=field):
                changed = deepcopy(self.original)
                if field == 'facts': changed['report_json']['input']['description'] += ' 변경'
                else: changed['report_markdown'] += ' 변경'
                with self.assertRaises(WorkerError):
                    report_review.build(changed, self.scene)

    def test_save_is_exclusive_private_and_repeat_does_not_rewrite(self):
        record = self.record(); path = self.persist(record)
        before = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
        report_review.save_review(path, record)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino), before)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(path.parent.glob('.pending-*')))
        changed = deepcopy(record); changed['excluded_count'] += 1
        with self.assertRaisesRegex(WorkerError, 'exists_with_other_content'):
            report_review.save_review(path, changed)
        self.assertEqual(path.read_bytes(), before[0])

    def test_interrupted_atomic_link_never_replaces_existing_file(self):
        record = self.record(); path = self.persist(record)
        before = path.read_bytes()
        self.assertEqual(read_json(path), record)
        # A second publisher winning the link race must stay intact.
        target = path.with_name(str(uuid.uuid4()) + '.json')
        def winner(source, destination):
            Path(destination).write_bytes(b'other writer')
            raise FileExistsError('simulated competing writer')
        with patch.object(report_review.os, 'link', side_effect=winner), self.assertRaises(FileExistsError):
            report_review.save_review(target, record)
        self.assertEqual(target.read_bytes(), b'other writer')
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(path.parent.glob('.pending-*')))

    def test_symlink_file_or_parent_is_rejected_without_touching_target(self):
        record = self.record(); path = self.persist(record)
        target = self.root / 'unrelated'; target.write_bytes(b'preserve')
        link = path.with_name(str(uuid.uuid4()) + '.json'); link.symlink_to(target)
        with self.assertRaisesRegex(WorkerError, 'unsafe_report_review'):
            report_review.save_review(link, record)
        link.unlink()
        parent_link = self.root / 'linked'; parent_link.symlink_to(path.parent, target_is_directory=True)
        with self.assertRaisesRegex(WorkerError, 'unsafe_report_review'):
            report_review.save_review(parent_link / 'new.json', record)
        self.assertEqual(target.read_bytes(), b'preserve')

    def test_path_cannot_escape_event_or_uuid_namespace(self):
        for value in ('../outside', 'ABCDEFAB-1111-5111-8111-111111111111', 'not-a-uuid'):
            with self.subTest(value=value), self.assertRaises((WorkerError, ValueError)):
                report_review.review_path(self.directory, self.original, value)
        changed = dict(self.original, event_id='../event_000')
        with self.assertRaisesRegex(WorkerError, 'invalid_report_review_event'):
            report_review.review_path(self.directory, changed, REPORT)

    def test_load_preserves_parent_and_returns_one_same_event_revision(self):
        before = deepcopy(self.report)
        self.assertEqual(report_review.load_reviews(self.directory, self.report, self.scene), [])
        path = self.persist()
        raw = path.read_bytes()
        revisions = report_review.load_reviews(self.directory, self.report, self.scene)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]['settings']['analysis_id'], ANALYSIS)
        self.assertEqual(revisions[0]['settings']['source_report_id'], REPORT)
        self.assertEqual(revisions[0]['settings']['report_id'], revisions[0]['report_id'])
        self.assertEqual(revisions[0]['review']['model_calls'], 0)
        self.assertEqual(self.report, before)
        self.assertEqual(path.read_bytes(), raw)

    def test_changed_review_policy_or_result_is_rejected_even_if_rehashed(self):
        original = self.record(); path = self.persist(original)
        for field in ('policy', 'result', 'source'):
            with self.subTest(field=field):
                changed = deepcopy(original)
                if field == 'policy': changed['policy_sha256'] = '0' * 64
                elif field == 'source': changed['source_report_id'] = ANALYSIS
                else:
                    changed['result']['report_markdown'] += ' 변경'
                    changed['result_sha256'] = fingerprint(changed['result'])
                path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(WorkerError, 'identity_changed'):
                    report_review.load_reviews(self.directory, self.report, self.scene)

    def test_renamed_or_oversized_review_and_excessive_list_are_rejected(self):
        path = self.persist(); raw = path.read_bytes()
        renamed = path.with_name(str(uuid.uuid4()) + '.json'); path.rename(renamed)
        with self.assertRaisesRegex(WorkerError, 'filename_mismatch'):
            report_review.load_reviews(self.directory, self.report, self.scene)
        renamed.rename(path)
        path.write_bytes(b'x' * (report_review.LIMIT + 1))
        with self.assertRaisesRegex(WorkerError, 'unsafe_report_review_file'):
            report_review.load_reviews(self.directory, self.report, self.scene)
        path.write_bytes(raw)
        for _ in range(8): path.with_name(str(uuid.uuid4()) + '.json').write_bytes(raw)
        with self.assertRaisesRegex(WorkerError, 'too_many_report_reviews'):
            report_review.load_reviews(self.directory, self.report, self.scene)

    def test_api_read_uses_review_without_rewriting_parent_or_issuing_sql_writes(self):
        self.persist()
        before = deepcopy(self.report)
        conn = self.connection()
        with patch.object(flow_service, 'spool', return_value=self.root):
            result = read_view(conn)[0]['reports'][0]
        self.assertEqual(result['state'], before['state'])
        self.assertEqual(result['report_id'], REPORT)
        self.assertEqual(len(result['quality_reviews']), 1)
        self.assertEqual(self.report, before)
        self.assertTrue(all(sql.startswith('SELECT') for sql, _ in conn.queries))

    def test_api_broken_sidecar_returns_original_with_error(self):
        path = self.persist(); path.write_text('{"broken":true}')
        before = deepcopy(self.report)
        with patch.object(flow_service, 'spool', return_value=self.root):
            result = read_view(self.connection())[0]['reports'][0]
        self.assertEqual(result['state'], before['state'])
        self.assertEqual(result['report_id'], REPORT)
        self.assertTrue(result['quality_review_error'])
        self.assertEqual(result['quality_reviews'], [])

    def test_read_only_plan_matches_database_files_and_creates_no_review(self):
        before = {str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}
        conn = Mock()
        with self.source_patches(conn):
            summary, record, _ = report_review.plan(conn, WORKER, REPORT, fingerprint(self.original))
        self.assertEqual(summary['plan_sha256'], fingerprint(record))
        self.assertEqual(summary['database_writes'], 0)
        self.assertFalse(summary['already_present'])
        self.assertTrue(all(call.args[0].startswith('SELECT') for call in conn.execute.call_args_list))
        self.assertEqual({str(p): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}, before)
        self.assertFalse(report_review.review_path(self.directory, self.original, record['review_id']).parent.exists())

    def test_source_digest_file_and_input_identity_mismatches_are_rejected(self):
        conn = Mock()
        with self.source_patches(conn), self.assertRaisesRegex(WorkerError, 'review_source_report_changed'):
            report_review.source_context(conn, WORKER, REPORT, '0' * 64)
        self.row['state']['input_sha256'] = '0' * 64
        with self.source_patches(conn), self.assertRaisesRegex(WorkerError, 'review_source_job_changed'):
            report_review.source_context(conn, WORKER, REPORT, fingerprint(self.original))
        self.row['state']['input_sha256'] = self.scene['input_sha256']
        original_file = self.directory / 'scenes/event_000/report-team-v1/report.md'
        original_file.write_text('바뀐 원문')
        with self.source_patches(conn), self.assertRaisesRegex(WorkerError, 'database_file_mismatch'):
            report_review.source_context(conn, WORKER, REPORT, fingerprint(self.original))
        self.assertFalse((original_file.parent / 'reviews').exists())

    def test_unfinished_source_is_rejected_before_file_or_scene_read(self):
        conn = Mock(); self.row['state']['status'] = 'prepared'
        with self.source_patches(conn), self.assertRaisesRegex(WorkerError, 'completed_job_required'):
            report_review.source_context(conn, WORKER, REPORT, fingerprint(self.original))
        conn.execute.assert_not_called()

    def test_tracking_missing_or_changed_receipt_stays_pending_without_network(self):
        from apps.be_worker import mlflow_tracking
        record = self.record()
        self.assertEqual(report_review.tracking_status(self.directory, record), {'status': 'pending', 'run_id': None})
        folder = self.directory / 'mlflow-outbox' / record['review_id'].replace('-', '')
        folder.mkdir(parents=True)
        observation = {'worker_job_id': WORKER, 'execution_id': record['review_id'].replace('-', ''),
            'input_sha256': record['input_sha256'], 'result_refs': {'purpose': 'report_review',
                'result_sha256': record['result_sha256']}, 'config': {'policy_sha256': record['policy_sha256']}}
        digest = mlflow_tracking._hash(observation)
        observation_path = folder / (digest + '.json')
        observation_path.write_text(json.dumps(observation))
        receipt = {'status': 'recorded', 'recorded_observation': digest, 'run_id': '1' * 32}
        (folder / 'receipt.json').write_text(json.dumps(receipt))
        self.assertEqual(report_review.tracking_status(self.directory, record), {'status': 'recorded', 'run_id': '1' * 32})
        # Even a self-consistent hash for another input may not claim this review.
        observation['input_sha256'] = '0' * 64
        digest = mlflow_tracking._hash(observation)
        (folder / (digest + '.json')).write_text(json.dumps(observation))
        receipt['recorded_observation'] = digest
        (folder / 'receipt.json').write_text(json.dumps(receipt))
        self.assertEqual(report_review.tracking_status(self.directory, record), {'status': 'pending', 'run_id': None})


if __name__ == '__main__':
    unittest.main()
