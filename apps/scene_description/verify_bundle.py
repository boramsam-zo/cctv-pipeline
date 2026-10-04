"""Verify the code bundle before execution; does not load models or call APIs."""
import hashlib
import json
from pathlib import Path, PurePosixPath

root=Path(__file__).resolve().parent
manifest=json.loads((root/'bundle-manifest.json').read_text())
for name, digest in manifest['files'].items():
    relative=PurePosixPath(name)
    path=(root/name).resolve()
    if relative.is_absolute() or '..' in relative.parts or not path.is_relative_to(root):
        raise ValueError('Invalid bundle path')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise ValueError(f'Bundle hash mismatch: {name}')
print(f"Bundle verified: {len(manifest['files'])} files; no API/model calls")
