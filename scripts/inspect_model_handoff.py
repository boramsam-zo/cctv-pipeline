"""Read hashes and primitive checkpoint metadata without loading model code."""
import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path
import pickletools
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/contracts/received-model-metadata-v0.2.json"
PUTS = {"BINPUT", "LONG_BINPUT", "PUT", "MEMOIZE"}


def ops(path):
    with ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n == "data.pkl" or n.endswith("/data.pkl")]
        assert len(names) == 1
        # Opcode decoding does not execute GLOBAL/REDUCE or reconstruct objects.
        return [(op.name, arg, pos) for op, arg, pos in pickletools.genops(archive.read(names[0]))]


def class_map(operations):
    hits = [i for i, (op, arg, pos) in enumerate(operations) if arg == "names"]
    assert len(hits) == 1
    tail = [(op, arg) for op, arg, pos in operations[hits[0] + 1:] if op not in PUTS]
    assert tail[:2] == [("EMPTY_DICT", None), ("MARK", None)]
    result = {}
    i = 2
    while tail[i][0] != "SETITEMS":
        op, key = tail[i]
        next_op, value = tail[i + 1]
        assert op in {"BININT1", "BININT2", "BININT", "INT"} and isinstance(key, int)
        assert next_op in {"BINUNICODE", "SHORT_BINUNICODE", "UNICODE"} and isinstance(value, str)
        result[str(key)] = value
        i += 2
    return result


def scalar(operations, key):
    hits = [i for i, (op, arg, pos) in enumerate(operations) if arg == key]
    values = []
    for i in hits:
        tail = [(op, arg) for op, arg, pos in operations[i + 1:] if op not in PUTS]
        if tail and tail[0][0] in {"BININT1", "BININT2", "BININT", "BINFLOAT", "BINUNICODE"}:
            values.append(tail[0][1])
    return values


def constants(path):
    result = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    try:
                        result[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    source = parser.parse_args().source.resolve()
    manifest = json.loads((source / "manifest.json").read_text())
    receipts = []
    for name, entry in manifest.items():
        path = (source / name).resolve()
        assert path.is_relative_to(source)
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        assert len(data) == entry["bytes"] and digest == entry["sha256"], name
        receipts.append({"file": name, "bytes": len(data), "sha256": digest, "matches_manifest": True})
    yolo_ops = ops(source / "models/yolo11s_accident_150_e30_best.pt")
    x3d_ops = ops(source / "models/x3d_s_real_so_tad_epoch7_best_model.pt")
    names = class_map(yolo_ops)
    assert names == {"0": "Pedestrian", "1": "Car", "2": "Truck", "3": "Bus",
                     "4": "Motorcycle", "5": "Bicycle", "6": "Dynamic"}
    result = {
        "inspection_version": "static-handoff-inspection-v1", "date": "2026-09-26",
        "source_directory_name": source.name,
        "method": "stdlib SHA256, JSON, AST constants and pickletools opcode decoding; no pickle/torch deserialization or imported handoff code",
        "files": receipts,
        "yolo": {"class_map": names, "class_count": len(names),
                 "checkpoint_epoch_scalar": scalar(yolo_ops, "epoch"),
                 "dynamic_label_definition": "unconfirmed; retain class ID 6"},
        "x3d": {"epoch": scalar(x3d_ops, "epoch"), "threshold": scalar(x3d_ops, "threshold")},
        "pipeline_constants": constants(source / "pipeline.py"),
        "timeline_constants": constants(source / "timeline.py"),
        "not_verified": ["tensor shapes and state_dict loading", "model inference", "training history",
                         "reported SO-TAD 400 evaluation", "end-to-end latency", "source PTS alignment"],
    }
    content = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode()
    if OUT.exists():
        assert OUT.read_bytes() == content, "Refusing to replace inspection evidence."
    else:
        OUT.write_bytes(content)
    print(json.dumps({"files_matched": len(receipts), "yolo_classes": names,
                      "x3d_epoch": result["x3d"]["epoch"], "x3d_threshold": result["x3d"]["threshold"],
                      "output": OUT.relative_to(ROOT).as_posix()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
