"""Linux image build checks: imports and synthetic codecs, never model inference."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import tempfile


def main():
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise RuntimeError('Build the reviewed linux/amd64 image')
    if not Path('/start.sh').is_file() or not os.access('/start.sh', os.X_OK):
        raise RuntimeError('Expected the official base image startup script')
    import av
    import cv2
    import numpy as np
    import torch
    import torchvision
    from pytorchvideo.models.x3d import create_x3d  # Import only; do not construct.
    from ultralytics import YOLO  # Import only; do not load weights.

    if torch.version.cuda != '12.8':
        raise RuntimeError('Expected the existing cu128 torch wheel')
    checks = []
    with tempfile.TemporaryDirectory(prefix='cctv-image-codecs-') as name:
        folder = Path(name)
        pixels = np.zeros((48, 64, 3), dtype=np.uint8)
        pixels[:, :32, 1] = 128
        h264 = folder / 'h264.mp4'
        with av.open(str(h264), 'w') as output:
            stream = output.add_stream('libx264', rate=25)
            stream.width, stream.height, stream.pix_fmt = 64, 48, 'yuv420p'
            for _ in range(2):
                for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format='rgb24')):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        mp4v = folder / 'mp4v.mp4'
        writer = cv2.VideoWriter(str(mp4v), cv2.VideoWriter_fourcc(*'mp4v'), 25, (64, 48))
        if not writer.isOpened():
            raise RuntimeError('B mp4v encoder unavailable')
        try:
            for _ in range(2):
                writer.write(pixels)
        finally:
            writer.release()
        for path, expected in ((h264, 'h264'), (mp4v, 'mpeg4')):
            with av.open(str(path)) as source:
                frames = list(source.decode(video=0))
                codec = source.streams.video[0].codec_context.name
                if len(frames) != 2 or codec != expected:
                    raise RuntimeError('Synthetic clip roundtrip failed')
                if any((f.width, f.height) != (64, 48) for f in frames):
                    raise RuntimeError('Synthetic clip shape changed')
                checks.append({'codec': codec, 'frames': len(frames)})
        # A writes PNG with PyAV; B writes JPEG with OpenCV.
        png = folder / 'frame.png'
        with av.open(str(png), 'w', format='image2') as output:
            stream = output.add_stream('png', rate=1)
            stream.width, stream.height, stream.pix_fmt = 64, 48, 'rgb24'
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format='rgb24')):
                output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        for suffix in ('png', 'jpg'):
            path = folder / ('frame.' + suffix)
            if suffix == 'jpg' and not cv2.imwrite(str(path), pixels):
                raise RuntimeError('Still image write failed')
            with av.open(str(path)) as source:
                if len(list(source.decode(video=0))) != 1:
                    raise RuntimeError('Still image decode failed')
            checks.append({'format': suffix, 'frames': 1})
    root = Path(__file__).resolve().parent
    document = {
        'status': 'image_import_and_synthetic_codec_checks_passed',
        'architecture': platform.machine(), 'python': platform.python_version(),
        'cuda_runtime': torch.version.cuda, 'codec_roundtrips': checks,
        'active_distribution_versions': {
            name: importlib.metadata.version(name)
            for name in sorted({d.metadata['Name'] for d in importlib.metadata.distributions()})},
        'installed_distributions': sorted(
            [{'name': d.metadata['Name'], 'version': d.version, 'location': str(d.locate_file(''))}
             for d in importlib.metadata.distributions()], key=lambda d: (d['name'], d['location'])),
        'image_manifest_sha256': hashlib.sha256((root / 'image-manifest.json').read_bytes()).hexdigest(),
        'model_objects_created': 0, 'checkpoint_loads': 0, 'inference_calls': 0,
        'gpu_available_tested': False,
        'original_environment_full_freeze_available': False,
        'note': 'Actual build environment recorded; GPU and A/B real video validation still required.',
    }
    with (root / 'build-verification.json').open('x') as target:
        json.dump(document, target, indent=2)
        target.write('\n')
    print(json.dumps({k: v for k, v in document.items()
                      if k not in ('installed_distributions', 'active_distribution_versions')}))


if __name__ == '__main__':
    main()
