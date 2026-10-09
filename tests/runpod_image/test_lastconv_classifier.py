"""LC integration boundaries using metadata and fakes; no model imports or execution."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from apps.runpod_image import lastconv_classifier as adapter, model_prepare
from apps.runpod_image.runtime import image_runtime, job


def checkpoint():
    # This mirrors the saved payload's distinct experiment_progress field.
    return dict(format_version=1, model_name='r3d_18', class_mapping={'normal': 0, 'accident': 1},
        model_state={'synthetic': 'state'}, parent_model_sha256=adapter.PARENT_SHA256,
        progress={'completed_epochs': 8}, experiment_progress={'updates': 1893, 'completed_epochs': 3},
        purpose='experimental_lastconv631_not_adopted',
        config={'train_scope': 'last_conv_and_head', 'preprocessing': deepcopy(adapter.PREPROCESSING)},
        experiment={'name': 'lastconv631_v1', 'baseline_model_sha256': adapter.PARENT_SHA256,
                    'preprocessing': deepcopy(adapter.PREPROCESSING)})


class LastConvTests(unittest.TestCase):
    def setUp(self):
        self.blocked = patch.dict(sys.modules, {name: None for name in
            ('torch', 'torchvision', 'pytorchvideo', 'ultralytics', 'cv2', 'av', 'numpy', 'boto3')})
        self.blocked.start()
        self.addCleanup(self.blocked.stop)
        network = patch('socket.socket', side_effect=AssertionError('No network'))
        network.start()
        self.addCleanup(network.stop)

    def test_original_payload_metadata_accepted_wrong_epoch_model_transform_rejected(self):
        saved = checkpoint()
        adapter.validate_checkpoint_metadata(saved)
        received = Path(__file__).resolve().parents[2] / 'artifacts/colab/lastconv631-20260926-01/received/experiment.json'
        if received.exists():
            self.assertEqual(adapter.PREPROCESSING, json.loads(received.read_text())['preprocessing'])
        for field, value in (('model_name', 'r2plus1d_18'),
                             ('experiment_progress', {'updates': 1262, 'completed_epochs': 2}),
                             ('parent_model_sha256', '0' * 64)):
            bad = deepcopy(saved)
            bad[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity'):
                adapter.validate_checkpoint_metadata(bad)
        bad = deepcopy(saved)
        bad['config']['preprocessing']['image_size'] = 182
        with self.assertRaisesRegex(ValueError, 'preprocessing'):
            adapter.validate_checkpoint_metadata(bad)

    def test_full_state_strict_load_without_pretrained_download_and_cuda_required(self):
        model = Mock()
        model.fc.in_features = 512
        model.to.return_value.eval.return_value = model
        factory = Mock(return_value=model)
        tensor = Mock()
        tensor.view.return_value = tensor
        torch = types.SimpleNamespace(load=Mock(return_value=checkpoint()),
            nn=types.SimpleNamespace(Linear=Mock()), tensor=Mock(return_value=tensor))
        with patch.object(adapter, 'verify_file', return_value=Path('/synthetic.pt')) as checked:
            loaded = adapter.LastConvClassifier('/synthetic.pt', torch_module=torch, model_factory=factory)
            checked.assert_called_once_with(Path('/synthetic.pt'), adapter.CHECKPOINT_SHA256, adapter.CHECKPOINT_BYTES)
            with self.assertRaisesRegex(ValueError, 'explicit_cuda'):
                adapter.LastConvClassifier('/synthetic.pt', device='cpu', torch_module=torch, model_factory=factory)
        factory.assert_called_once_with(weights=None)
        torch.load.assert_called_once_with(Path('/synthetic.pt'), map_location='cpu', weights_only=True)
        model.load_state_dict.assert_called_once_with({'synthetic': 'state'}, strict=True)
        model.requires_grad_.assert_called_once_with(False)
        self.assertIs(loaded.model, model)

    def test_lc_source_is_opt_in_missing_key_never_imports_sdk_or_downloads(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(model_prepare, 'CACHE_ROOT', Path(folder)), \
                patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'source_not_configured'):
                model_prepare.prepare_lc()
        with patch.object(model_prepare, 'verify_file', return_value=Path('/verified.pt')) as verify:
            self.assertEqual(model_prepare.cached_lc(), Path('/verified.pt'))
            self.assertEqual(verify.call_args.args[1:], (adapter.CHECKPOINT_SHA256, adapter.CHECKPOINT_BYTES))

    def test_old_two_model_image_cannot_admit_lc_even_when_runtime_implements_it(self):
        manifest = {'supported_versions': ['B', 'R2']}
        with patch.object(image_runtime, 'inspect_image', return_value=(manifest, 'synthetic')):
            with self.assertRaisesRegex(ValueError, 'not enabled'):
                image_runtime.execute({'jobs': [{'version': 'LC'}]}, {}, Path('/synthetic-image'),
                    Path('/synthetic-state'), 'LC', Mock(), 0)

    def test_lc_tie_remains_normal_at_output_validation_boundary(self):
        row = {'start_s': 0., 'end_s': 2., 'collision_probability': .5,
               'is_positive': False, 'logits': [0., 0.]}
        result = {'duration_s': 2., 'fps': 30.,
                  'classifier': {'model_id': 'r3d_18', 'windows': [row]},
                  'events': [], 'vlm_handoffs': []}
        self.assertEqual(job.validate_result(result, {'duration_s': 2., 'fps': 30.},
                         lambda duration: [0.]), 'no_candidates')
        row['is_positive'] = True
        with self.assertRaisesRegex(ValueError, 'threshold'):
            job.validate_result(result, {'duration_s': 2., 'fps': 30.}, lambda duration: [0.])

    def test_lc_release_requires_verified_local_weights_before_service_start(self):
        from apps.runpod_image import service_job
        runtime = service_job.Runtime()
        with patch.object(runtime, 'verify_service'), \
                patch.object(service_job.runtime, 'inspect_image', return_value=(
                    {'supported_versions': ['B', 'R2', 'LC']}, 'synthetic')), \
                patch.object(service_job, 'supported_versions', return_value=('B', 'R2', 'LC')), \
                patch.object(model_prepare, 'cached_lc', side_effect=ValueError('model_artifact_size_mismatch')), \
                patch.object(service_job.subprocess, 'run') as process:
            with self.assertRaisesRegex(ValueError, 'model_artifact_size_mismatch'):
                runtime.startup()
            process.assert_not_called()


if __name__ == '__main__':
    unittest.main()
