"""Build the allowlisted scene CLI. Excludes all videos, keys, results and Terraform state."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
NAMES=('scene.py','db.py','db.sh','compose.tools.yaml','schema.sql','sample.json','verify_bundle.py','README.md')

def main(output):
    files={name:(ROOT/'apps/scene_description'/name).read_bytes() for name in NAMES}
    manifest={'schema_version':'scene-code-bundle-v1',
              'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    files['bundle-manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():
            info=zipfile.ZipInfo(name)
            info.external_attr=0o100644<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        for name,data in files.items(): assert archive.read(name)==data
    print(json.dumps({'files':len(files),'bytes':output.stat().st_size,
                     'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'file':str(output)}))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
