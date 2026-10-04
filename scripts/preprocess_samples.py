"""Preprocess up to three explicitly selected segments; never train a model."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import importlib.metadata
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from boram3_cctv import preprocessing as pp


def worker(request_path):
    request = pp.read_json(request_path)
    try:
        result = pp.process_sample(Path(request["data_root"]), request["sample"], request["settings"],
                                   request["metadata_entry"], Path(request["directory"]))
    except pp.PreprocessingError as exc:
        result = {"status": "error", "reason": str(exc), "target": None}
    except FileNotFoundError:
        result = {"status": "error", "reason": "file_missing", "target": None}
    except PermissionError:
        result = {"status": "error", "reason": "access_denied", "target": None}
    except ImportError:
        result = {"status": "error", "reason": "dependency_missing", "target": None}
    except Exception as exc:
        result = {"status": "error", "reason": "media_or_processing_failed", "error_type": type(exc).__name__, "target": None}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


def run(data_root, plan_path, output, timeout=45, *, metadata_path=None):
    if type(timeout) is not int or not 1 <= timeout <= 45:
        raise pp.PreprocessingError("worker_timeout_must_be_1_to_45")
    plan = pp.read_json(plan_path)
    pp.validate_plan(plan)
    if output.resolve().is_relative_to(data_root.resolve()) or data_root.resolve().is_relative_to(output.resolve()):
        raise pp.PreprocessingError("output_overlaps_original_data")
    if output.exists():
        raise pp.PreprocessingError("output_already_exists_choose_new_directory")
    if metadata_path is None:
        metadata_path = data_root / "metadata-real.csv"
        if not metadata_path.resolve().is_relative_to(data_root.resolve()):
            raise pp.PreprocessingError("metadata_outside_data_root")
    else:
        metadata_path = Path(metadata_path)  # Explicit read-only metadata selection, no copy into originals.
    if any(p.resolve().is_relative_to(output.resolve()) for p in (metadata_path, plan_path)):
        raise pp.PreprocessingError("output_overlaps_manifest_input")
    plan_hash, metadata_hash = pp.sha256(plan_path), pp.sha256(metadata_path)
    metadata = pp.read_metadata(metadata_path)
    output.mkdir(parents=True, exist_ok=False)
    settings = {key: plan[key] for key in ("policy_version", "frame_count", "image_size", "time_basis")}
    records = []
    for sample in plan["samples"]:
        base = {"sample_id": sample["sample_id"], "source_path": sample.get("video_path"),
                "start_seconds": sample.get("start_seconds"), "end_seconds": sample.get("end_seconds"),
                "target": None}
        matches = metadata.get(sample.get("video_path"), [])
        if len(matches) != 1:
            records.append({**base, "status": "error", "reason": "metadata_missing" if not matches else "metadata_ambiguous"})
            continue
        with tempfile.TemporaryDirectory(prefix=".sample-", dir=output) as temporary:
            temp = Path(temporary)
            request = {"data_root": str(data_root.resolve()), "directory": str((temp / "result").resolve()),
                       "sample": sample, "settings": settings, "metadata_entry": matches[0]}
            pp.write_json(temp / "request.json", request)
            try:
                proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(temp / "request.json")],
                                      capture_output=True, text=True, timeout=timeout)
                item = json.loads(proc.stdout)
                if proc.returncode or not isinstance(item, dict) or item.get("status") not in ("error", "unconfirmed", "prepared_labeled"):
                    raise ValueError
            except subprocess.TimeoutExpired:
                item = {"status": "error", "reason": "processing_timeout", "target": None}
            except (OSError, ValueError):
                item = {"status": "error", "reason": "worker_failed", "target": None}
            item = {**base, **item}
            if item["status"] != "error":
                kind = "labeled" if item["status"] == "prepared_labeled" else "unconfirmed"
                relative = Path(kind) / sample["sample_id"]
                destination = output / relative
                destination.parent.mkdir(exist_ok=True)
                (temp / "result").rename(destination)
                item["preview_path"] = (relative / "preview.png").as_posix()
                item["frames_metadata_path"] = (relative / "frames.json").as_posix()
                item["input_path"] = (relative / "frames.npy").as_posix() if kind == "labeled" else None
            records.append(item)
    unchanged = pp.sha256(plan_path) == plan_hash and pp.sha256(metadata_path) == metadata_hash
    if not unchanged:
        # Keep diagnostic previews but release no supervised input references.
        for record in records:
            if record.get("status") == "prepared_labeled":
                record.update(status="error", reason="input_manifest_changed", target=None, input_path=None)
    counts = Counter(record["status"] for record in records)
    report = {
        "preprocessing_version": pp.VERSION, "created_utc": datetime.now(timezone.utc).isoformat(),
        "code_sha256": {"preprocessing.py": pp.sha256(Path(pp.__file__)), "preprocess_samples.py": pp.sha256(Path(__file__))},
        "plan_sha256": plan_hash, "metadata_sha256": metadata_hash, "inputs_unchanged": unchanged,
        "metadata_selection": "explicit_path" if metadata_path != data_root / "metadata-real.csv" else "data_root_default",
        "worker_timeout_seconds": timeout,
        "settings": settings, "class_order": pp.CLASS_ORDER,
        "dependencies": {name: importlib.metadata.version(name) for name in ("av", "numpy", "Pillow")},
        "counts": {key: counts[key] for key in ("prepared_labeled", "unconfirmed", "error")},
        "status": "needs_attention" if counts["error"] or not unchanged else "completed_with_unconfirmed" if counts["unconfirmed"] else "completed",
        "samples": records, "training_run": False,
    }
    pp.write_json(output / "manifest.json", report)
    pp.write_json(output / "labeled.json", {"class_order": pp.CLASS_ORDER, "samples": [r for r in records if r["status"] == "prepared_labeled"]})
    pp.write_json(output / "unconfirmed.json", {"samples": [r for r in records if r["status"] == "unconfirmed"]})
    pp.write_json(output / "errors.json", {"samples": [r for r in records if r["status"] == "error"]})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--detrac-sequences", nargs='+', help="1–3 explicit JPEG sequence IDs; first 2s, preparation only")
    parser.add_argument("--training-expansion", type=Path,
                        help="approved max-25 source-annotation preparation request; no training")
    parser.add_argument("--metadata", type=Path, help="explicit existing CSV, independent of the video root")
    parser.add_argument("--worker-timeout", type=int, default=45, help="per-video limit in seconds (1..45)")
    parser.add_argument("--output", type=Path, required=True, help="new output directory outside the original data")
    args = parser.parse_args()
    try:
        if args.detrac_sequences:
            if args.plan or args.training_expansion or args.metadata:
                raise pp.PreprocessingError('detrac_mode_conflict')
            report = pp.prepare_detrac_preview(args.data_root, args.detrac_sequences, args.output)
            print(json.dumps({k: report[k] for k in ('status', 'elapsed_seconds', 'training_run', 'model_run')}, ensure_ascii=False))
            print('Report:', args.output / 'report.html')
            return 0 if report['status'] == 'prepared_not_split' else 2
        if args.training_expansion:
            if args.plan or args.metadata:
                raise pp.PreprocessingError("expansion_request_mode_conflict")
            report = run_training_expansion(args.data_root, args.training_expansion, args.output)
            print(json.dumps({k: report[k] for k in ('status', 'selected_count', 'prepared_count',
                                                     'failed_count', 'elapsed_seconds')}, ensure_ascii=False))
            return 0 if report['prepared_count'] else 2
        if args.plan is None:
            raise pp.PreprocessingError("plan_required")
        report = run(args.data_root, args.plan, args.output, args.worker_timeout, metadata_path=args.metadata)
        print(json.dumps({key: report[key] for key in ("status", "counts", "inputs_unchanged", "training_run")}, ensure_ascii=False, indent=2))
        return 2 if report["status"] == "needs_attention" else 0
    except pp.PreprocessingError as exc:
        reason = str(exc)
    except FileNotFoundError:
        reason = "required_input_missing"
    except PermissionError:
        reason = "access_denied"
    except (OSError, ValueError, TypeError, KeyError):
        reason = "invalid_or_unreadable_input"
    print(json.dumps({"status": "invalid_request", "reason": reason}, ensure_ascii=False))
    return 2


def run_training_expansion(data_root, request_path, output):
    """Reuse single-segment workers and CSV policy; preserve every failure row."""
    from copy import deepcopy
    import importlib.util
    from boram3_cctv import train_candidates as tc, training_data as td
    root = Path(__file__).resolve().parents[1]
    started = time.monotonic()
    request = pp.read_json(request_path)
    auth = request.get('authorization', {})
    tc.require(request.get('purpose') == 'bounded_source_training_expansion_preparation'
               and request.get('max_new_videos') == 25 and request.get('max_seconds') == 300
               and auth.get('source') == 'user_message' and auth.get('use_source_annotations') is True
               and bool(auth.get('raw_response')), 'explicit_expansion_scope_required')

    def reference(ref):
        path = td.resolve_path(root, ref['path'])
        tc.require(path.is_relative_to(root) and path.is_file()
                   and pp.sha256(path) == ref['sha256'], 'expansion_reference_changed')
        return path

    base_path = reference(request['base_manifest'])
    policy_path = reference(request['annotation_policy'])
    base = pp.read_json(base_path)
    prior = td.connect_training_preparation(root, base)
    tc.require(len(prior['records']) == 8, 'expansion_requires_existing_8_inputs')
    template_policy = pp.read_json(policy_path)
    metadata_path = reference(base['references']['metadata'])
    reserved_path = reference(base['references']['reserved_manifest'])
    metadata, digest = tc.read_candidates_metadata(metadata_path.parent)
    reserved = pp.read_json(reserved_path)['segments']
    selection = tc.select_training_expansion(metadata, prior['records'], reserved, request['max_new_videos'])
    output = output.resolve()
    tc.require(output.is_relative_to(root / 'artifacts') and output != root / 'artifacts'
               and not output.is_relative_to(data_root.resolve()), 'invalid_expansion_output')
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / 'selection.json', selection)
    pp.write_json(output / 'request.json', request)
    spec = importlib.util.spec_from_file_location('expansion_reviews', root/'scripts/record_segment_reviews.py')
    reviews = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reviews)
    joined = deepcopy(base)
    joined.update(additional_annotation_batches=[], scope_note='Existing 8 inputs plus at most 25 new CSV-point inputs; train-only preparation.')
    known_hashes = {r['source_sha256'] for r in prior['records'] + reserved}
    rows = []
    for i, chosen in enumerate(selection['selected'], 13):
        row = {**chosen, 'sample_id': f'sample_{i:03d}', 'status': 'held', 'target': None}
        # Keep enough time for one <=20s worker and its saved-input binding.
        if time.monotonic()-started > request['max_seconds']-25:
            row['reason'] = 'preparation_time_budget'; rows.append(row); continue
        folder = output / row['sample_id']; folder.mkdir()
        try:
            path = pp.source_path(data_root, chosen['source_path'])
            if path.is_symlink():
                raise pp.PreprocessingError('video_symlink_requires_review')
            source_hash = pp.sha256(path)
            tc.require(source_hash not in known_hashes, 'source_content_duplicate_or_reserved')
            sample = {'sample_id': row['sample_id'], 'video_path': chosen['source_path'],
                      'source_sha256': source_hash, 'start_seconds': chosen['start_seconds'],
                      'end_seconds': chosen['end_seconds'], 'event_id': None, 'upstream_original_id': None,
                      'source_group_id': 'accident_training_unresolved',
                      'window_basis': {'purpose': 'source_annotation_training_candidate',
                                       'accident_time_seconds': chosen['annotation_seconds']},
                      'label_review': {'status': 'unreviewed', 'label': None, 'basis': None}}
            plan = {'schema_version': 1, 'policy_version': 'draft-0.1', 'time_basis': 'local_clip_seconds',
                    'frame_count': 16, 'image_size': 224, 'samples': [sample]}
            plan_path = folder / 'plan.json'; pp.write_json(plan_path, plan)
            report = run(data_root, plan_path, folder/'previews', timeout=20, metadata_path=metadata_path)
            if report['samples'][0]['status'] == 'error':
                raise pp.PreprocessingError(report['samples'][0]['reason'])
            manifest_path = folder/'previews/manifest.json'
            policy = deepcopy(template_policy)
            policy.update(sample_ids=[row['sample_id']], parent_plan_sha256=pp.sha256(plan_path),
                          parent_manifest_sha256=pp.sha256(manifest_path), metadata_sha256=digest,
                          authorization=deepcopy(auth), scope_note='One selected source-annotation window in the authorized max-25 expansion; not human review.')
            new_policy_path = folder/'annotation-policy.json'; pp.write_json(new_policy_path, policy)
            derived = reviews.run_source_annotations(plan_path, manifest_path, new_policy_path, folder/'prepared')
            record = derived['samples'][0]
            joined['sample_ids'].append(row['sample_id'])
            joined['additional_annotation_batches'].append({
                'annotation_report': {'path': (folder/'prepared/report.json').relative_to(root).as_posix(),
                                      'sha256': pp.sha256(folder/'prepared/report.json')},
                'annotation_policy': {'path': new_policy_path.relative_to(root).as_posix(),
                                      'sha256': pp.sha256(new_policy_path)}})
            known_hashes.add(source_hash)
            row.update(status='prepared', target=1, source_sha256=source_hash,
                       array_path=record['array_path'], preview_path=record['preview_path'],
                       label_origin='source_annotation_derived', human_reviewed=False)
        except (pp.PreprocessingError, OSError, ValueError, KeyError, TypeError) as error:
            row.update(status='failed', reason=str(error) if isinstance(error, pp.PreprocessingError) else type(error).__name__)
        rows.append(row)
        pp.write_json(folder/'outcome.json', row)
    inputs = td.connect_training_preparation(root, joined, max_records=33)
    pp.write_json(output/'preparation-manifest.json', joined)
    pp.write_json(output/'prepared-inputs.json', inputs['records'])
    prepared = sum(r['status'] == 'prepared' for r in rows)
    report = {'status': 'prepared' if prepared == len(rows) else 'partial' if prepared else 'held',
              'selected_count': len(rows), 'prepared_count': prepared, 'failed_count': len(rows)-prepared,
              'samples': rows, 'total_training_segments': len(inputs['records']),
              'class_counts': inputs['class_counts'], 'label_origin_counts': inputs['label_origin_counts'],
              'independent_event_count': None, 'normal_source_files': inputs['normal_source_files'],
              'training_run': False, 'elapsed_seconds': time.monotonic()-started,
              'limits': {'new_videos':25, 'frames_per_video':16, 'worker_seconds':20, 'whole_command_seconds':300}}
    pp.write_json(output/'report.json', report)
    return report


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(Path(sys.argv[2]))
    elif '--detrac-sequences' in sys.argv and '--detrac-child' not in sys.argv:
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                     '--detrac-child', *sys.argv[1:]], timeout=90)
            raise SystemExit(result.returncode)
        except subprocess.TimeoutExpired:
            print(json.dumps({'status': 'error', 'reason': 'detrac_command_timeout',
                              'training_run': False, 'model_run': False}))
            raise SystemExit(2)
    else:
        if '--detrac-child' in sys.argv:
            sys.argv.remove('--detrac-child')
        raise SystemExit(main())
