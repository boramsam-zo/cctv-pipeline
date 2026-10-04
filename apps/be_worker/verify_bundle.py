"""Verify an extracted CPU worker bundle without network or model imports."""
import hashlib
import json
from pathlib import Path, PurePosixPath

root = Path(__file__).resolve().parents[2]
manifest = json.loads((root / 'be-bundle-manifest.json').read_text())
for name, digest in manifest['files'].items():
    relative = PurePosixPath(name)
    path = (root / name).resolve()
    if relative.is_absolute() or '..' in relative.parts or not path.is_relative_to(root):
        raise ValueError('Invalid bundle path')
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Bundle hash mismatch')
print(f"Bundle verified: {len(manifest['files'])} files; no API/model calls")
