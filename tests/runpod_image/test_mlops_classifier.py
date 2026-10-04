"""Contract/PTS/orchestration tests; no torch, checkpoints, or GPU is loaded."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from apps.be_worker.model_catalog import contract_for_version
from apps.runpod_image import classifier_adapter as adapter
from apps.runpod_image import classifier_pipeline as pipeline


def checkpoint():
    spec = deepcopy(adapter.PREPROCESSING)
    return dict(model_name='r2plus1d_18', class_mapping={'normal': 0, 'accident': 1},
                progress={'completed_epochs': 7}, model_state={'fake': 'synthetic'},
                config={'preprocessing': spec},
                experiment={'preprocessing': spec, 'class_mapping': {'normal': 0, 'accident': 1}})


class ClassifierTests(unittest.TestCase):
    def test_strict_metadata_and_distinct_operating_preprocessing(self):
        adapter.validate_checkpoint_metadata(checkpoint())
        for field, value in (('frames', 13), ('size', 182), ('spatial', 'stretch'), ('mean', [.45] * 3)):
            bad = checkpoint()
            bad['config']['preprocessing'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'preprocessing'):
                adapter.validate_checkpoint_metadata(bad)
        for key, value in (('model_name', 'x3d_s'), ('class_mapping', {'accident': 0, 'normal': 1}),
                           ('progress', {'completed_epochs': 10})):
            bad = checkpoint()
            bad[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identity'):
                adapter.validate_checkpoint_metadata(bad)

    def test_loader_builds_binary_head_and_uses_full_strict_state(self):
        fake = Mock()
        fake.fc.in_features = 512
        fake.to.return_value.eval.return_value = fake
        factory = Mock(return_value=fake)
        tensor = Mock()
        tensor.view.return_value = tensor
        torch = types.SimpleNamespace(load=Mock(return_value=checkpoint()),
                    nn=types.SimpleNamespace(Linear=Mock()), tensor=Mock(return_value=tensor))
        with patch.object(adapter, 'verify_file', return_value=Path('/synthetic.pt')) as check:
            obj = adapter.R2Classifier('/synthetic.pt', torch_module=torch, model_factory=factory)
        check.assert_called_once_with(Path('/synthetic.pt'), adapter.CHECKPOINT_SHA256, adapter.CHECKPOINT_BYTES)
        torch.load.assert_called_once_with(Path('/synthetic.pt'), map_location='cpu', weights_only=True)
        factory.assert_called_once_with(weights=None)
        torch.nn.Linear.assert_called_once_with(512, 2)
        fake.load_state_dict.assert_called_once_with({'fake': 'synthetic'}, strict=True)
        self.assertIs(obj.model, fake)

    def test_stable_scores_and_ties_normal(self):
        rows = adapter.summarize_logits([[0., 0.], [1000., 1001.], [-1000., -1001.]])
        self.assertFalse(rows[0]['is_positive'])
        self.assertEqual(rows[0]['predicted_class'], 0)
        self.assertAlmostEqual(rows[1]['collision_probability'], 1 / (1 + math.exp(-1)))
        self.assertTrue(rows[1]['is_positive'])
        for value in ([[0, float('nan')]], [[0]], [[True, 1]]):
            with self.assertRaises(ValueError):
                adapter.summarize_logits(value)

    def test_letterbox_keeps_aspect_ratio_with_black_padding_geometry(self):
        self.assertEqual(adapter.letterbox_geometry(640, 480), (224, 168, 0, 28))
        self.assertEqual(adapter.letterbox_geometry(480, 640), (168, 224, 28, 0))

    def test_pts_bin_centres_exclude_boundary_and_tail(self):
        buffer = pipeline.WindowBuffer()
        windows = []
        for i in range(100):
            t = i / 30
            windows.extend(buffer.advance(t))
            buffer.push(t, i)
        windows.extend(buffer.advance(100 / 30))
        self.assertEqual([row[0] for row in windows], [0, 1])
        for start, end, selected, targets in windows:
            self.assertEqual(len({t for t, _ in selected}), 16)
            self.assertTrue(all(start <= t < end for t, _ in selected))
            self.assertEqual(targets[0], start + .0625)
        self.assertEqual(pipeline.window_starts(3.3), [0., 1.])
        with self.assertRaises(ValueError):
            pipeline.window_starts(1.99)

    def test_sparse_input_fails_instead_of_normal_prediction(self):
        buffer = pipeline.WindowBuffer()
        buffer.push(0., 'only frame')
        with self.assertRaisesRegex(ValueError, 'insufficient'):
            list(buffer.advance(2.))

    def test_classification_keeps_evidence_handoff_and_contract(self):
        def source(path, checked):
            for start in (0., 1.):
                yield {'start_s': start, 'end_s': start + 2}, 'synthetic tensor'
        predictor = types.SimpleNamespace(predict=lambda arrays: adapter.summarize_logits([[0, 0], [0, 1]]))
        def group(rows):
            self.assertEqual(len(rows), 1)  # Tied first window must not become an event.
            row = rows[0]
            return [dict(start_s=1., end_s=3., candidate_time_s=2., available_after_video_time_s=3.,
                         first_window_start_s=1., first_window_end_s=3.,
                         first_window_probability=row['collision_probability'])]
        context = Mock(return_value={'event_id': 'event_000', 'files': {'full_clip': 'fixture.mp4'}})
        with tempfile.TemporaryDirectory() as folder:
            result = pipeline.analyze_video(Path('/synthetic.mp4'), folder, predictor, object(),
                checked={'duration_s': 3., 'fps': 30.}, contract=contract_for_version('R2'),
                context_builder=context, group_alerts=group, window_source=source)
            self.assertEqual(result, json.loads((Path(folder) / 'result.json').read_text()))
            self.assertEqual(result['classifier']['model_id'], 'r2plus1d_18')
            self.assertNotIn('x3d', result)
            handoff = result['vlm_handoffs'][0]
            self.assertTrue(handoff['classifier_collision_candidate'])
            self.assertEqual(handoff['candidate_time_s'], 2.)
            self.assertEqual(handoff['incident_context']['files']['full_clip'], 'fixture.mp4')
            context.assert_called_once()

    def test_saved_r2_tie_output_passes_be_window_contract_without_x3d_alias(self):
        from apps.be_worker.ab_evidence import _windows
        def source(path, checked):
            samples = [(i + .5) * 2 / 16 for i in range(16)]
            yield {'start_s': 0., 'end_s': 2., 'sample_times_s': samples,
                   'requested_sample_times_s': samples}, 'synthetic tensor'
        predictor = types.SimpleNamespace(predict=lambda arrays: adapter.summarize_logits([[0, 0]]))
        with tempfile.TemporaryDirectory() as folder:
            result = pipeline.analyze_video(Path('/synthetic.mp4'), folder, predictor, object(),
                checked={'duration_s': 2., 'fps': 30.}, contract=contract_for_version('R2'),
                context_builder=Mock(side_effect=AssertionError('no event should create evidence')),
                group_alerts=lambda rows: [], window_source=source)
            normalized = {'version': 'R2', 'status': 'no_candidates', 'window_count': 1}
            _windows(result, normalized, 2.)
            self.assertEqual(result['classifier']['windows'][0]['predicted_class'], 0)

    def test_generic_context_label_is_identical_in_file_and_handoff(self):
        def source(path, checked):
            yield {'start_s': 0., 'end_s': 2.}, 'synthetic tensor'
        predictor = types.SimpleNamespace(predict=lambda arrays: adapter.summarize_logits([[0, 1]]))
        def group(rows):
            return [dict(start_s=0., end_s=2., candidate_time_s=1., available_after_video_time_s=2.,
                         first_window_start_s=0., first_window_end_s=2.,
                         first_window_probability=rows[0]['collision_probability'])]
        def context(video, candidate, folder, detector, **kwargs):
            folder.mkdir()
            result = {'event_id': kwargs['event_id'], 'display_frame_reason': 'x3d_candidate_time'}
            (folder / (kwargs['event_id'] + '_context.json')).write_text(json.dumps(result))
            return result
        with tempfile.TemporaryDirectory() as folder:
            result = pipeline.analyze_video(Path('/synthetic.mp4'), folder, predictor, object(),
                checked={'duration_s': 2., 'fps': 30.}, contract=contract_for_version('R2'),
                context_builder=context, group_alerts=group, window_source=source)
            stored = json.loads((Path(folder) / 'event_000/event_000_context.json').read_text())
            self.assertEqual(stored, result['vlm_handoffs'][0]['incident_context'])
            self.assertEqual(stored['display_frame_reason'], 'classifier_candidate_time')


if __name__ == '__main__':
    unittest.main()
