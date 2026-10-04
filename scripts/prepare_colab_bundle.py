#!/usr/bin/env python3
"""Local allowlisted transfer archive; never uploads or downloads anything."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from boram3_cctv import preprocessing as pp
from boram3_cctv.evaluation import REFERENCE_PURPOSE, checked_file, preflight, relative_file
from boram3_cctv.training_data import TrainingError, connect_training_preparation

CODE_FILES = [f'src/boram3_cctv/{name}.py' for name in
              ('__init__', 'preprocessing', 'training_data', 'training', 'evaluation')]


def connect_reviewed_previews(root, manifest, output):
    """Recover three or fewer labelled arrays from saved review PNGs, without video I/O.

    Label approval and execution approval are separate. No review is manufactured here;
    the resulting manifest still goes through the unchanged evaluation preflight.
    """
    import numpy as np
    from PIL import Image

    selected = [r for r in manifest.get('reservations', []) if r.get('prepared_review')]
    if manifest.get('segments') or not 1 <= len(selected) <= 3:
        raise ValueError('select_1_to_3_reviewed_reservations_with_no_existing_segments')
    trained = pp.read_json(
        checked_file(root, manifest['training_inputs']['path'], manifest['training_inputs']['sha256']))
    train_hashes = {s['source_sha256'] for s in trained}
    train_groups = {s['management_group'] for s in trained}
    known_ids = {s.get(k) for s in trained for k in ('upstream_original_id', 'event_id')} - {None}
    prepared, evidence, seen, groups_by_source = [], [], set(), {}
    for reservation in selected:
        ref = reservation['prepared_review']
        plan_path = checked_file(root, ref['plan_path'], ref['plan_sha256'])
        parent_path = checked_file(root, ref['manifest_path'], ref['manifest_sha256'])
        plan, parent = pp.read_json(plan_path), pp.read_json(parent_path)
        pp.validate_plan(plan)
        sid = reservation['reservation_id']
        if sid in seen:
            raise ValueError('duplicate_selected_sample')
        seen.add(sid)
        sample = next((s for s in plan['samples'] if s['sample_id'] == sid), None)
        rows = [s for s in parent['samples'] if s['sample_id'] == sid]
        expected_settings = {k: plan[k] for k in ('policy_version', 'frame_count', 'image_size', 'time_basis')}
        if (sample is None or len(rows) != 1 or parent.get('inputs_unchanged') is not True
                or parent['plan_sha256'] != ref['plan_sha256'] or parent['settings'] != expected_settings
                or plan['frame_count'] != 16 or plan['image_size'] != 224
                or plan['policy_version'] != manifest['policy_version']):
            raise ValueError('review_parent_mismatch')
        row = rows[0]
        start, end = pp.validate_window(sample)
        source = sample['source_sha256']
        group = reservation.get('source_group_id')
        if (not math.isclose(end - start, 2, abs_tol=1e-6) or not group
                or source in train_hashes or group in train_groups
                or reservation.get('training_authorized') is not False
                or row.get('original_unchanged') is not True
                or row.get('status') not in ('unconfirmed', 'prepared_labeled')
                or reservation['source_sha256'] != source
                or reservation['metadata_path_value'] != sample['video_path']
                or reservation['approved_label_seconds'] != [start, end]
                or any(row.get(k) != sample.get(k) for k in ('source_sha256', 'start_seconds', 'end_seconds'))
                or row['source_path'] != sample['video_path']):
            raise ValueError('review_source_window_or_training_overlap')
        if (any(reservation.get(k) in known_ids for k in ('upstream_original_id', 'event_id'))
                or source in groups_by_source and groups_by_source[source] != group):
            raise ValueError('source_event_group_mismatch')
        groups_by_source[source] = group
        reviewed = dict(sample, label_review=reservation['label_review'])
        target = pp.approved_target(reviewed, manifest['policy_version'], source)
        if target is None or type(reservation.get('target')) is not int or reservation['target'] != target:
            raise ValueError('approved_label_required')
        metadata = ref['metadata']
        csv_path = checked_file(root, metadata['path'], metadata['sha256'])
        matches = pp.read_metadata(csv_path).get(sample['video_path'], [])
        if (parent['metadata_sha256'] != metadata['sha256'] or len(matches) != 1
                or matches[0]['csv_row'] != metadata['csv_row']
                or matches[0]['values'].get('split_in_distribution') != 'train'
                or row['official_splits'].get('split_in_distribution') != 'train'
                or reservation.get('official_iid') != 'train'):
            raise ValueError('official_iid_train_row_required')
        frames_path = relative_file(root, (parent_path.parent / row['frames_metadata_path']).relative_to(root).as_posix())
        preview = relative_file(root, (parent_path.parent / row['preview_path']).relative_to(root).as_posix())
        if pp.sha256(frames_path) != ref['frames_sha256'] or pp.sha256(preview) != ref['preview_sha256']:
            raise ValueError('reviewed_frames_or_preview_changed')
        details = pp.read_json(frames_path)
        times = details['frames']
        if (details != row['processed_frames'] or len(times) != 16
                or details['array_shape'] != [16, 224, 224, 3] or details['dtype'] != 'uint8'
                or details['array_layout'] != 'THWC' or details['model_normalization'] is not None
                or details['transform'].get('method') != 'letterbox' or details['transform'].get('crop') is not None
                or any(not start <= f['actual_seconds'] < end for f in times)
                or any(b['actual_seconds'] <= a['actual_seconds'] for a, b in zip(times, times[1:]))
                or any(not math.isclose(f['requested_seconds'], start+(i+.5)*2/16, abs_tol=1e-6)
                       for i, f in enumerate(times))):
            raise ValueError('reviewed_frame_configuration_mismatch')
        pixels, png_hashes = [], []
        inputs = {plan_path: ref['plan_sha256'], parent_path: ref['manifest_sha256'],
                  frames_path: ref['frames_sha256'], preview: ref['preview_sha256'],
                  csv_path: metadata['sha256']}
        with Image.open(preview) as sheet:
            if sheet.mode != 'RGB' or sheet.size != (896, 1100):
                raise ValueError('unexpected_review_sheet')
            for index in range(16):
                png = relative_file(root, (frames_path.parent / 'frames' / f'{index:03d}.png').relative_to(root).as_posix())
                inputs[png] = pp.sha256(png)
                with Image.open(png) as im:
                    if im.mode != 'RGB' or im.size != (224, 224) or im.format != 'PNG':
                        raise ValueError('review_png_format_mismatch')
                    array = np.array(im, dtype=np.uint8)
                x, y = index % 4 * 224, 44 + index // 4 * 264
                if not np.array_equal(array, np.asarray(sheet.crop((x, y, x+224, y+224)))):
                    raise ValueError('review_png_pixels_or_order_changed')
                pixels.append(array)
                png_hashes.append(inputs[png])
        if any(pp.sha256(path) != digest for path, digest in inputs.items()):
            raise ValueError('review_inputs_changed_during_connection')
        destination = output / 'inputs' / sid
        destination.mkdir(parents=True, exist_ok=False)
        np.save(destination / 'frames.npy', np.stack(pixels), allow_pickle=False)
        details = dict(details, target=target, model_input_written=True)
        pp.write_json(destination / 'frames.json', details)
        shutil.copyfile(preview, destination / 'preview.png')
        record = dict(sample_id=sid, dataset='ACCIDENT', source_path=sample['video_path'],
                      source_reference=reservation['source_url'], source_sha256=source,
                      management_group=group, group_scope=reservation.get('source_group_scope'),
                      upstream_original_id=reservation.get('upstream_original_id'), event_id=reservation.get('event_id'),
                      start_seconds=start, end_seconds=end, target=target, split='validation',
                      training_authorized=False, final_test=False, official_iid_split='train', metadata=metadata,
                      label_review=deepcopy(reservation['label_review']))
        for name in ('independence_review', 'usage_review', 'input_quality_review'):
            record[name] = deepcopy(reservation.get(name, {'status': 'pending'}))
        for kind, filename in [('array', 'frames.npy'), ('frames', 'frames.json'), ('preview', 'preview.png')]:
            path = destination / filename
            record[kind+'_path'], record[kind+'_sha256'] = path.relative_to(root).as_posix(), pp.sha256(path)
        prepared.append(record)
        evidence.append({'sample_id': sid, 'parent_plan_sha256': ref['plan_sha256'],
                         'parent_manifest_sha256': ref['manifest_sha256'], 'review_frames_sha256': ref['frames_sha256'],
                         'preview_sha256': ref['preview_sha256'], 'png_sha256_in_order': png_hashes,
                         'pixels_equal_review_sheet': True, 'timestamps_preserved': True,
                         'label_target': target, 'video_decoded': False})
    connected = deepcopy(manifest)
    connected['segments'] = prepared
    connected['input_connection'] = {'method': 'stack_existing_review_pngs_no_resampling', 'samples': evidence,
                                     'quality_or_independence_approval_inferred': False}
    return connected


def build(root, manifest_path, output, *, connect_previews=False):
    start = time.monotonic()
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(root) or output == root:
        raise ValueError('new_output_directory_inside_project_required')
    manifest = pp.read_json(relative_file(root, manifest_path))
    if connect_previews:
        output.mkdir(parents=True, exist_ok=False)
        manifest = connect_reviewed_previews(root, manifest, output)
        path = output / 'evaluation.json'
        pp.write_json(path, manifest)
        manifest_path = path.relative_to(root).as_posix()
    files = set(CODE_FILES + [manifest_path, 'notebooks/evaluate_r3d18_colab.ipynb'])
    for key in ('model', 'training_inputs'):
        ref = manifest[key]
        checked_file(root, ref['path'], ref['sha256'])
        files.add(ref['path'])
    # An empty manifest can be shipped for inspection, but cannot run evaluation.
    ready, blocked = False, None
    if manifest['segments']:
        try:
            preflight(root, manifest)
            ready = True
        except TrainingError as exc:
            # Preparation may carry explicitly pending reviews, never bypass execution.
            if not connect_previews or str(exc) not in (
                    'human_review_required', 'independence_unconfirmed',
                    'cloud_use_approval_required', 'input_quality_unconfirmed'):
                raise
            blocked = str(exc)
        for record in manifest['segments']:
            files.update(record[k + '_path'] for k in ('array', 'frames', 'preview'))
            if record.get('metadata'):
                files.add(record['metadata']['path'])
    for value in manifest.get('support_files', []):
        files.add(value)
    entries = []
    for value in sorted(files):
        path = relative_file(root, value)
        if any(part.startswith('.') for part in Path(value).parts) or path.suffix not in ('.py', '.json', '.csv', '.ipynb', '.pt', '.npy', '.png'):
            raise ValueError('non_transfer_file:' + value)
        entries.append({'path': value, 'bytes': path.stat().st_size, 'sha256': pp.sha256(path)})
    reference = manifest['purpose'] == REFERENCE_PURPOSE
    required_reviews = ('input_quality_review', 'usage_review') if reference else (
        'input_quality_review', 'independence_review', 'usage_review')
    receipt = {'schema_version': 1, 'purpose': 'manual_upload_only', 'evaluation_manifest': manifest_path,
               'evaluation_purpose': manifest['purpose'],
               'independent_validation': False if reference else ready,
               'independence_status': 'unconfirmed' if reference else ('reviewed' if ready else 'pending'),
               'evaluation_ready': ready, 'execution_blocked_reason': blocked, 'files': entries,
               'connected_segments': len(manifest['segments']),
               'class_counts': {name: sum(s['target'] == target for s in manifest['segments'])
                                for name, target in manifest['class_mapping'].items()},
               'source_files': len({s['source_sha256'] for s in manifest['segments']}),
               'pending_reviews': ([{'scope': 'run', 'field': 'evaluation_approval'}]
                   if manifest.get('evaluation_approval', {}).get('status') != 'approved' else []) + [
                       {'scope': s['sample_id'], 'field': key} for s in manifest['segments']
                       for key in required_reviews
                       if s.get(key, {}).get('status') != 'approved'],
               'payload_bytes': sum(e['bytes'] for e in entries),
               'missing': manifest.get('missing', []), 'raw_videos_included': False,
               'cloud_execution_verified': False}
    if not connect_previews:
        output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output / 'transfer_manifest.json', receipt)
    archive = output / 'colab-evaluation.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_STORED) as z:
        z.write(output / 'transfer_manifest.json', 'transfer_manifest.json')
        for entry in entries:
            z.write(root / entry['path'], entry['path'])
    pp.write_json(output / 'bundle_result.json', {'archive': archive.name, 'bytes': archive.stat().st_size,
        'sha256': pp.sha256(archive), 'elapsed_seconds': time.monotonic() - start,
        'file_count': len(entries), 'uploaded': False, 'evaluation_run': False})
    return receipt


def build_training_preparation(root, manifest_path, output):
    """Ship only eight or fewer existing arrays; no model or raw videos."""
    start = time.monotonic()
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(root) or output == root:
        raise ValueError('new_output_directory_inside_project_required')
    manifest = pp.read_json(relative_file(root, manifest_path))
    connected = connect_training_preparation(root, manifest)
    files = set(CODE_FILES) - {'src/boram3_cctv/evaluation.py'}
    for record in connected['records']:
        files.update(record[k+'_path'] for k in ('array', 'frames', 'preview'))
    entries = []
    for name in sorted(files):
        path = relative_file(root, name)
        if path.suffix not in ('.py', '.npy', '.json', '.png') or any(p.startswith('.') for p in Path(name).parts):
            raise ValueError('non_transfer_file:' + name)
        entries.append({'path': name, 'bytes': path.stat().st_size, 'sha256': pp.sha256(path)})
    output.mkdir(parents=True, exist_ok=False)
    pp.write_json(output/'training_inputs.json', connected)
    entries.append({'path': 'training_inputs.json', 'bytes': (output/'training_inputs.json').stat().st_size,
                    'sha256': pp.sha256(output/'training_inputs.json')})
    receipt = {'schema_version': 1, 'purpose': 'training_input_transfer_only', 'files': entries,
               'payload_bytes': sum(e['bytes'] for e in entries), 'class_counts': connected['class_counts'],
               'training_execution_authorized': False, 'raw_videos_included': False,
               'model_included': False, 'reviewer_aliases_included': True, 'uploaded': False}
    pp.write_json(output/'transfer_manifest.json', receipt)
    archive = output/'colab-training-inputs.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_STORED) as z:
        z.write(output/'transfer_manifest.json', 'transfer_manifest.json')
        for entry in entries:
            source = output/entry['path'] if entry['path'] == 'training_inputs.json' else root/entry['path']
            z.write(source, entry['path'])
    result = {'archive': archive.name, 'bytes': archive.stat().st_size, 'sha256': pp.sha256(archive),
              'elapsed_seconds': time.monotonic()-start, 'file_count': len(entries),
              'class_counts': connected['class_counts'], 'uploaded': False, 'training_run': False}
    pp.write_json(output/'bundle_result.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', default='configs/evaluation.colab.pending.json')
    p.add_argument('--output', required=True)
    p.add_argument('--connect-reviewed-previews', action='store_true',
                   help='connect 1–3 labelled saved PNG sets; pending execution reviews still block Colab')
    p.add_argument('--training-preparation', action='store_true',
                   help='transfer saved human/CSV-derived training inputs only; no model or execution approval')
    args = p.parse_args()
    if args.training_preparation:
        if args.connect_reviewed_previews:
            p.error('training preparation cannot run evaluation preview connection')
        print(json.dumps(build_training_preparation(ROOT, args.manifest, Path(args.output))))
        sys.exit(0)
    result = build(ROOT, args.manifest, Path(args.output), connect_previews=args.connect_reviewed_previews)
    print(json.dumps({'files': len(result['files']), 'payload_bytes': result['payload_bytes'],
                      'connected_segments': result['connected_segments'],
                      'evaluation_ready': result['evaluation_ready'],
                      'execution_blocked_reason': result['execution_blocked_reason'], 'uploaded': False}))
