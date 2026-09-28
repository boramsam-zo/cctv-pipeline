"""X3D-S factory matching PyTorchVideo's official hub defaults.

Use installed pytorchvideo==0.1.5 without fetching a mutable GitHub branch.
The received pipeline still selects its original 16 frames at 182 x 182.
Reference: pytorchvideo/models/hub/x3d.py, x3d_s.
"""

dependencies = ["torch"]


def x3d_s(pretrained=False, progress=True, **kwargs):
    if pretrained:
        raise ValueError("This bundle uses the included accident checkpoint only")
    from pytorchvideo.models.x3d import create_x3d

    return create_x3d(input_clip_length=13, input_crop_size=160, **kwargs)
