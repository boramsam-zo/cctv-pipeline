"""Pinned lastconv631 epoch-3 R3D-18 inference; importing never loads a model."""
from pathlib import Path

from apps.runpod_image.classifier_adapter import summarize_logits
from apps.runpod_image.model_artifact import verify_file


MODEL_ID = 'r3d_18'
MODEL_VERSION = 'lastconv631-epoch3'
CHECKPOINT_SHA256 = '6139ac8597f4c6e6a3fd5a632fc490e636e7188d076739e29f841f3ca3da0558'
CHECKPOINT_BYTES = 132769867
PARENT_SHA256 = '7dbd352a3992e2c788ba517522698f5dd1353f5df6c46f4247a74820c59b891c'
CLASS_MAPPING = {'normal': 0, 'accident': 1}
PREPROCESSING = {
    'version': 'r3d-letterbox-kinetics-v1', 'frame_count': 16, 'image_size': 224,
    'sampling': 'nearest_PTS_to_uniform_bin_centres', 'spatial': 'letterbox_no_crop',
    'normalization': 'KINETICS400_V1', 'layout': 'CTHW', 'dtype': 'float32',
}
MEAN = [0.43216, 0.394666, 0.37645]
STD = [0.22803, 0.22145, 0.216989]


def validate_checkpoint_metadata(checkpoint):
    """Validate the original experiment format, not the later R2 checkpoint schema."""
    if not isinstance(checkpoint, dict):
        raise ValueError('lastconv_checkpoint_dictionary_required')
    experiment = checkpoint.get('experiment', {})
    config = checkpoint.get('config', {})
    if (checkpoint.get('format_version') != 1
            or checkpoint.get('model_name') != MODEL_ID
            or checkpoint.get('class_mapping') != CLASS_MAPPING
            or checkpoint.get('parent_model_sha256') != PARENT_SHA256
            or checkpoint.get('experiment_progress') != {'updates': 1893, 'completed_epochs': 3}
            or checkpoint.get('purpose') != 'experimental_lastconv631_not_adopted'
            or not isinstance(checkpoint.get('model_state'), dict)
            or not isinstance(experiment, dict) or experiment.get('name') != 'lastconv631_v1'
            or experiment.get('baseline_model_sha256') != PARENT_SHA256
            or not isinstance(config, dict) or config.get('train_scope') != 'last_conv_and_head'):
        raise ValueError('lastconv_checkpoint_identity_mismatch')
    if (config.get('preprocessing') != PREPROCESSING
            or experiment.get('preprocessing') != PREPROCESSING):
        raise ValueError('lastconv_checkpoint_preprocessing_mismatch')
    return checkpoint


class LastConvClassifier:
    """Load the full saved binary state only in an explicit CUDA job."""
    def __init__(self, checkpoint, device='cuda', *, torch_module=None, model_factory=None):
        checkpoint = verify_file(Path(checkpoint), CHECKPOINT_SHA256, CHECKPOINT_BYTES)
        if device != 'cuda':
            raise ValueError('lastconv_requires_explicit_cuda_job')
        if torch_module is None:
            import torch as torch_module
        if model_factory is None:
            from torchvision.models.video import r3d_18 as model_factory
        self.torch, self.device = torch_module, device
        saved = torch_module.load(checkpoint, map_location='cpu', weights_only=True)
        validate_checkpoint_metadata(saved)
        model = model_factory(weights=None)
        if model.fc.in_features != 512:
            raise ValueError('lastconv_feature_dimension_mismatch')
        model.fc = torch_module.nn.Linear(512, 2)
        model.load_state_dict(saved['model_state'], strict=True)
        self.model = model.to(device).eval()
        self.model.requires_grad_(False)
        self.mean = torch_module.tensor(MEAN, device=device).view(1, 3, 1, 1, 1)
        self.std = torch_module.tensor(STD, device=device).view(1, 3, 1, 1, 1)

    def predict(self, arrays):
        import numpy as np
        if not arrays:
            return []
        for array in arrays:
            if array.dtype != np.uint8 or array.shape != (16, 224, 224, 3):
                raise ValueError('lastconv_preprocessed_shape_mismatch')
        x = self.torch.from_numpy(np.stack(arrays)).permute(0, 4, 1, 2, 3).to(self.device)
        x = (x.float().div_(255.0) - self.mean) / self.std
        with self.torch.inference_mode():
            logits = self.model(x).float().cpu().tolist()
        if len(logits) != len(arrays):
            raise ValueError('lastconv_output_count_mismatch')
        return summarize_logits(logits)
