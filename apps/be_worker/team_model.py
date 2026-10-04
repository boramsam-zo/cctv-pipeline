"""Validate the supplied E5 files before CPU admission; never load a model."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from .common import WorkerError

MANIFEST = Path(__file__).resolve().parents[1]/'team_rag/e5-model-manifest.json'


@lru_cache(maxsize=2)
def _verify(root, manifest_bytes, inventory):
    spec=json.loads(manifest_bytes)
    base=Path(root)
    for name,item in spec['files'].items():
        path=base/name
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                h.update(block)
        if path.stat().st_size != item['bytes'] or h.hexdigest() != item['sha256']:
            raise WorkerError('team_model_file_identity_changed')
    return True


def preflight(path):
    if not path or not Path(path).is_absolute() or not Path(path).is_dir():
        raise WorkerError('team_rag_local_model_not_prepared')
    base=Path(path)
    try:
        raw=MANIFEST.read_bytes()
        if (base/'model-inventory.json').read_bytes() != raw:
            raise WorkerError('team_model_inventory_changed')
        files=json.loads(raw)['files']
        inventory=[]
        for name in files:
            selected=base/name
            if selected.is_symlink() or any(p.is_symlink() for p in selected.parents):
                raise WorkerError('team_model_symlink_invalid')
            stat=selected.stat()
            inventory.append((name,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns))
        return _verify(str(base),raw,tuple(inventory))
    except (OSError,ValueError,KeyError):
        raise WorkerError('team_rag_local_model_not_prepared') from None
