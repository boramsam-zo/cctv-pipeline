"""R(2+1)D epoch-7 adapter for the pinned, previously trained candidate.

Import is standard-library only. Construction is a model operation, reserved for
the bounded Linux GPU job. The original X3D loader and transform stay unchanged.
"""
import math
from pathlib import Path

from apps.runpod_image.model_artifact import verify_file


MODEL_ID = 'r2plus1d_18'
MODEL_VERSION = 'sotad-head-epoch7'
CHECKPOINT_SHA256 = 'c40c150ea70cd422edc546c9d110b138c87cba67a277c5c4d65353ba04478b46'
CHECKPOINT_BYTES = 125379501
CLASS_MAPPING = {'normal': 0, 'accident': 1}
PREPROCESSING = {
    'frames': 16, 'size': 224, 'spatial': 'letterbox',
    'mean': [0.43216, 0.394666, 0.37645],
    'std': [0.22803, 0.22145, 0.216989],
}


def validate_checkpoint_metadata(checkpoint):
    if not isinstance(checkpoint, dict):
        raise ValueError('r2_checkpoint_dictionary_required')
    if (checkpoint.get('model_name') != MODEL_ID
            or checkpoint.get('class_mapping') != CLASS_MAPPING
            or checkpoint.get('progress', {}).get('completed_epochs') != 7
            or not isinstance(checkpoint.get('model_state'), dict)):
        raise ValueError('r2_checkpoint_identity_mismatch')
    preprocessing = checkpoint.get('config', {}).get('preprocessing')
    experiment = checkpoint.get('experiment', {})
    if (not isinstance(preprocessing, dict)
            or any(preprocessing.get(k) != v for k, v in PREPROCESSING.items())
            or experiment.get('preprocessing') != preprocessing
            or experiment.get('class_mapping') != CLASS_MAPPING):
        raise ValueError('r2_checkpoint_preprocessing_mismatch')
    return checkpoint


def summarize_logits(rows):
    """Keep raw logits; the trained binary argmax rule sends exact ties to normal."""
    result = []
    for row in rows:
        if len(row) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in row):
            raise ValueError('r2_logits_invalid')
        a, b = map(float, row)
        maximum = max(a, b)
        score = math.exp(b - maximum) / (math.exp(a - maximum) + math.exp(b - maximum))
        result.append({'logits': [a, b], 'collision_probability': score,
                       'predicted_class': int(b > a), 'is_positive': b > a})
    return result


def letterbox_geometry(width, height, size=224):
    if any(type(value) is not int or value <= 0 for value in (width, height, size)):
        raise ValueError('r2_image_dimensions_invalid')
    ratio = min(size / width, size / height)
    new_width = max(1, min(size, round(width * ratio)))
    new_height = max(1, min(size, round(height * ratio)))
    return new_width, new_height, (size - new_width) // 2, (size - new_height) // 2


def letterbox(image):
    from PIL import Image
    width, height, left, top = letterbox_geometry(*image.size)
    canvas = Image.new('RGB', (224, 224), (0, 0, 0))
    canvas.paste(image.convert('RGB').resize((width, height), Image.Resampling.BILINEAR), (left, top))
    return canvas


class R2Classifier:
    """Strict full-state loading, independent of pretrained weight downloads."""
    def __init__(self, checkpoint, device='cuda', *, torch_module=None, model_factory=None):
        checkpoint = verify_file(Path(checkpoint), CHECKPOINT_SHA256, CHECKPOINT_BYTES)
        if device != 'cuda':
            raise ValueError('r2_requires_explicit_cuda_job')
        if torch_module is None:
            import torch as torch_module
        if model_factory is None:
            from torchvision.models.video import r2plus1d_18 as model_factory
        self.torch, self.device = torch_module, device
        saved = torch_module.load(checkpoint, map_location='cpu', weights_only=True)
        validate_checkpoint_metadata(saved)
        model = model_factory(weights=None)
        if model.fc.in_features != 512:
            raise ValueError('r2_feature_dimension_mismatch')
        model.fc = torch_module.nn.Linear(512, 2)
        model.load_state_dict(saved['model_state'], strict=True)
        self.model = model.to(device).eval()
        self.model.requires_grad_(False)
        self.mean = torch_module.tensor(PREPROCESSING['mean'], device=device).view(1, 3, 1, 1, 1)
        self.std = torch_module.tensor(PREPROCESSING['std'], device=device).view(1, 3, 1, 1, 1)

    def predict(self, arrays):
        import numpy as np
        if not arrays:
            return []
        for array in arrays:
            if array.dtype != np.uint8 or array.shape != (16, 224, 224, 3):
                raise ValueError('r2_preprocessed_shape_mismatch')
        x = self.torch.from_numpy(np.stack(arrays)).permute(0, 4, 1, 2, 3).to(self.device)
        x = (x.float().div_(255.0) - self.mean) / self.std
        # Preserve the candidate's float32 evaluation, not X3D's autocast policy.
        with self.torch.inference_mode():
            logits = self.model(x).float().cpu().tolist()
        if len(logits) != len(arrays):
            raise ValueError('r2_output_count_mismatch')
        return summarize_logits(logits)
