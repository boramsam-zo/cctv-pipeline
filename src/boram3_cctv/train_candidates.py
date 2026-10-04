"""Read-only acquisition candidates, never a training manifest or label generator."""
from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
import math
import os

from . import preprocessing as pp

SPLIT_COLUMNS = ("split_in_distribution", "split_geo_aware")
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10_000
MAX_DIRECTORY_ENTRIES = 10_000


def connect_reserved_validation(allocation, training, split_inventory, metadata, *, all_reserved=False):
    """Freeze up to five candidates per class without inventing independence/labels."""
    trained = training['records']
    used = {r.get('source_path') for r in trained} - {None}
    families = {filename_family(p) for p in used}
    sequences = {r.get('sequence_id') for r in trained} - {None}
    splits = split_inventory['splits']
    require(not set(splits['train']) & set(splits['test']), 'official_split_overlap')
    reserved = [r for r in allocation['records']
                if r.get('proposed_role') == 'development_review_reserved']
    require(len({r['source_path'] for r in reserved}) == len(reserved), 'duplicate_reserved_source')
    rows = []
    for r in reserved:
        path = r['source_path']
        require(path not in used and filename_family(path) not in families, 'training_source_overlap')
        matches = metadata.get(path, [])
        require(len(matches) == 1 and matches[0]['csv_row'] == r['csv_row']
                and matches[0]['values'].get('split_in_distribution') == 'train', 'official_train_row_required')
        point = float(matches[0]['values']['accident_time'])
        require(math.isfinite(point) and math.isclose(r['start_seconds'], point-1, abs_tol=1e-8)
                and math.isclose(r['end_seconds'], point+1, abs_tol=1e-8), 'annotation_window_changed')
    for r in (reserved if all_reserved else reserved[:5]):
        rows.append(dict(r, sample_id=f"reserved_accident_{len(rows)+1:02d}", target=None,
                         requested_class='accident', model_score=None,
                         label_status='development_review_pending'))
    for sequence in sorted(splits['train'])[50:(60 if all_reserved else 55)]:
        require(sequence not in sequences, 'training_sequence_overlap')
        rows.append(dict(sample_id=f'reserved_normal_{sequence}', sequence_id=sequence,
                         requested_class='normal', start_seconds=0., end_seconds=2., target=0,
                         label_origin='user_assumed_normal', human_reviewed=False, model_score=None,
                         label_status='user_assumption_not_official_accident_label'))
    require(len(rows) == (30 if all_reserved else 10), 'reserved_candidate_count_mismatch')
    for r in rows:
        r.update(training_authorized=False, evaluation_authorized=False,
                 input_status='not_prepared', independence_status='unconfirmed',
                 split='validation_reserved', file_availability='not_checked_this_step')
    return dict(status='reserved_candidates_connected_not_evaluation_ready', records=rows,
                training_segments=len(trained), exact_source_and_filename_family_overlap=False,
                independent_validation=False, model_run=False, training_run=False,
                max_segments=10, max_inference_seconds=300,
                missing=['prepared_validation_inputs', 'accident_development_labels',
                         'source_event_camera_independence_evidence'],
                selection='First five existing accident reservations; first five of last ten official-train normal reservations. No model scores used.')


def colab_expansion_request(proposal, split_inventory):
    """Fixed metadata-only request; no camera independence or execution approval."""
    import re
    require(proposal.get('purpose') == 'metadata_only_expansion_proposal', 'invalid_expansion_proposal')
    splits = split_inventory['splits']
    train, test = sorted(splits['train']), sorted(splits['test'])
    require(len(train) == 60 and len(test) == 40 and
            len(set(train + test)) == 100 and
            all(re.fullmatch(r'MVI_\d+', s) for s in train + test), 'invalid_official_split_inventory')
    accidents = [dict(c) for c in proposal['candidates'] if c['proposed_role'] == 'training_candidate']
    reserved = [c['source_path'] for c in proposal['candidates'] if c['proposed_role'] == 'development_review_reserved']
    require(len(accidents) == 200 and len({c['source_path'] for c in accidents}) == 200,
            'expected_200_fixed_accident_candidates')
    require(not {filename_family(c['source_path']) for c in accidents} &
            {filename_family(p) for p in reserved}, 'reserved_family_overlap')
    for c in accidents:
        require(len(PurePosixPath(c['source_path']).parts) == 2 and
                PurePosixPath(c['source_path']).parts[0] == 'real_videos' and
                '..' not in PurePosixPath(c['source_path']).parts, 'noncanonical_video_path')
        point = float(c['annotation_seconds'])
        require(math.isfinite(point) and point >= 1 and
                abs(c['start_seconds']-(point-1)) < 1e-8 and
                abs(c['end_seconds']-(point+1)) < 1e-8, 'invalid_window')
        c.update(target=1, label_origin='source_annotation_derived', human_reviewed=False)
    # Hold out sequence IDs before looking at any model score. Not an independent split.
    normal = [{'sequence_id': s, 'start_seconds': start, 'end_seconds': start+2,
               'target': 0, 'label_origin': 'user_assumed_normal', 'human_reviewed': False}
              for s in train[:50] for start in (0, 2, 4, 6)]
    return {'purpose': 'colab_expansion_file_check_only', 'accident_candidates': accidents,
            'normal_candidates': normal, 'reserved_accident_paths': reserved,
            'reserved_normal_sequences': train[50:], 'official_normal_test': test,
            'normal_selection': 'sorted official train IDs: first50 x four nonoverlapping2s windows; last10 reserved',
            'normal_group': 'detrac-camera-unresolved', 'accident_group': 'accident-expansion-unresolved',
            'independent_validation': False, 'split': 'unassigned',
            'training_authorized': False, 'evaluation_authorized': False,
            'settings': {'seconds': 2, 'frames': 16, 'image_size': 224, 'fps_detrac': 25},
            'max_seconds': 60, 'max_decode_frames': 0}


def inspect_colab_expansion(request, metadata_path, accident_videos, detrac_zip):
    """Colab file/header check only: no media reads, extraction or model imports."""
    import zipfile
    import time
    started = time.monotonic()
    require(request.get('purpose') == 'colab_expansion_file_check_only' and
            request.get('training_authorized') is False, 'preparation_only_required')
    require(len(request['accident_candidates']) + len(request['normal_candidates']) <= 400 and len(request['normal_candidates']) <= 200,
            'candidate_count_exceeded')
    require(Path(metadata_path).stat().st_size <= MAX_METADATA_BYTES, 'metadata_size_limit')
    metadata = pp.read_metadata(Path(metadata_path))
    results = []
    def budget():
        require(time.monotonic()-started < 55, 'metadata_time_limit')
    for c in request['accident_candidates']:
        budget()
        matches = metadata.get(c['source_path'], [])
        path = Path(accident_videos) / PurePosixPath(c['source_path']).name
        reason = None
        if len(matches) != 1:
            reason = 'metadata_match_not_unique'
        else:
            row = matches[0]['values']
            if row['split_in_distribution'] != 'train': reason = 'official_test_or_unknown'
            elif matches[0]['csv_row'] != c['csv_row'] or float(row['accident_time']) != c['annotation_seconds']:
                reason = 'metadata_annotation_changed'
            elif float(row['duration']) < c['end_seconds']: reason = 'window_outside_duration'
        if reason is None and (not path.is_file() or path.is_symlink()): reason = 'missing_video_or_symlink'
        results.append(dict(c, status='blocked' if reason else 'file_connected_not_decoded', reason=reason))
    if request['normal_candidates']:
        with zipfile.ZipFile(detrac_zip) as z:
            infos = z.infolist()
            require(len(infos) <= 200000, 'zip_entry_limit')
            members = {}
            for item in infos:
                if item.filename in members: raise pp.PreprocessingError('duplicate_zip_member')
                members[item.filename] = item
            for c in request['normal_candidates']:
                budget()
                expected = [f"DETRAC-Images/{c['sequence_id']}/img{n:05d}.jpg"
                            for n in range(c['start_seconds']*25+1, c['end_seconds']*25+1)]
                missing = [name for name in expected if name not in members or members[name].is_dir()]
                results.append(dict(c, status='blocked' if missing else 'file_connected_not_decoded',
                                    reason='missing_frames' if missing else None, missing_frames=missing,
                                    compressed_bytes=sum(members[n].compress_size for n in expected if n in members)))
    return {'purpose': request['purpose'], 'records': results,
            'counts': dict(Counter(r['status'] for r in results)),
            'elapsed_seconds': time.monotonic()-started, 'decoded_frames': 0,
            'training_ready': False, 'evaluation_ready': False, 'model_run': False,
            'next_condition': 'Bounded decode and input receipt required; camera/event independence unresolved.'}


def filename_family(relative):
    """Conservative exclusion hint only; never claim this establishes an event."""
    return PurePosixPath(relative).stem.rsplit('_', 1)[0]


def select_training_expansion(rows, existing, reserved, limit=25):
    """Select fixed IID-train windows before decoding; no model scores or labels."""
    require(type(limit) is int and 1 <= limit <= 25, 'expansion_limit_1_to_25')
    used = {r.get('source_path') or r.get('video_path') for r in existing}
    used.discard(None)
    blocked = {r['source_path'] for r in reserved}
    families = {filename_family(p) for p in used | blocked}
    selected, exclusions = [], []
    for path, matches in sorted(rows.items(), key=lambda pair: pair[1][0]['csv_row']):
        reason = None
        raw = matches[0]['values']
        if len(matches) != 1:
            reason = 'ambiguous_metadata_path'
        elif raw.get('split_in_distribution') != 'train':
            reason = 'official_IID_nontrain_excluded'
        elif path in blocked:
            reason = 'reserved_comparison_source'
        elif path in used:
            reason = 'already_trained_source'
        elif filename_family(path) in families:
            reason = 'filename_family_conservatively_excluded'
        else:
            try:
                point, duration = float(raw['accident_time']), float(raw['duration'])
                if not (math.isfinite(point) and math.isfinite(duration) and point >= 1 and point+1 <= duration):
                    reason = 'centered_2s_window_outside_metadata_duration'
            except (KeyError, TypeError, ValueError):
                reason = 'invalid_annotation_time_or_duration'
        if reason:
            exclusions.append({'source_path': path, 'csv_row': matches[0]['csv_row'], 'reason': reason})
        elif len(selected) < limit:
            selected.append({'source_path': path, 'csv_row': matches[0]['csv_row'],
                             'start_seconds': point-1, 'end_seconds': point+1,
                             'annotation_seconds': point, 'target': None})
            families.add(filename_family(path))
    return {'selected': selected, 'exclusions': exclusions,
            'unselected_count': len(rows)-len(selected)-len(exclusions),
            'selection': 'CSV order; IID train; reserved/existing/family hints excluded; centered 2s',
            'family_is_event_identity': False, 'independent_event_count': None}


def require(condition, code):
    if not condition:
        raise pp.PreprocessingError(code)


def plan_expansion(rows, existing, reserved, *, train_count=70, review_count=20):
    """Metadata-only proposals, NOT an executable split or independent labels.

    Reserve review candidates first, before any model scores. Reuse the bounded
    selector in batches; its decoding/training limits remain unchanged.
    Filename families are exclusion hints, never evidence of separate events.
    """
    require(type(train_count) is int and 1 <= train_count <= 500
            and type(review_count) is int and 1 <= review_count <= 100,
            'planning_train_1_to_500_review_1_to_100')
    # Exclude hints related to official test as well as already-used sources.
    test_refs = [{'source_path': p} for p, matches in rows.items()
                 if any(m['values'].get('split_in_distribution') != 'train' for m in matches)]
    blocked = list(reserved) + test_refs
    used = list(existing)
    proposals = []
    for role, requested in [('development_review_reserved', review_count),
                            ('training_candidate', train_count)]:
        added = 0
        while added < requested:
            selected = select_training_expansion(rows, used, blocked,
                                                 limit=min(25, requested - added))['selected']
            if not selected:
                break
            for item in selected:
                proposals.append(dict(item, proposed_role=role,
                    filename_family_hint=filename_family(item['source_path']),
                    event_group=None, independence_status='unconfirmed',
                    split='unassigned', training_authorized=False,
                    evaluation_authorized=False, file_availability='not_checked',
                    label_origin='source_annotation_window_candidate_not_human_review'))
            used.extend(selected)
            added += len(selected)
    counts = dict(Counter(p['proposed_role'] for p in proposals))
    return {'schema_version': 1, 'purpose': 'metadata_only_expansion_proposal',
            'candidates': proposals, 'counts': counts,
            'requested': {'training_candidate': train_count,
                          'development_review_reserved': review_count},
            'shortfalls': {'training_candidate': train_count-counts.get('training_candidate', 0),
                          'development_review_reserved': review_count-counts.get('development_review_reserved', 0)},
            'selection': 'CSV order; review reservation first; no prediction scores',
            'independent_event_count': None, 'training_ready': False,
            'evaluation_ready': False, 'video_decode_run': False,
            'limitations': ['Filename families do not establish upstream event identity.',
                           'Actual files, normal data, event groups and final split remain pending.',
                           'CSV-point candidates are not human-reviewed development labels.']}


def read_candidates_metadata(root):
    """Reuse the existing CSV reader; retain exact strings and record numbers."""
    require(root.is_dir(), "data_root_missing")
    path = root / "metadata-real.csv"
    require(path.is_file() and not path.is_symlink(), "real_metadata_missing_or_symlink")
    require(path.stat().st_size <= MAX_METADATA_BYTES, "metadata_size_limit")
    before = pp.sha256(path)
    rows = pp.read_metadata(path)
    require(bool(rows), "metadata_empty")
    require(sum(map(len, rows.values())) <= MAX_ROWS, "metadata_row_limit")
    for relative, matches in rows.items():
        require(len(matches) == 1, "ambiguous_metadata_path")
        require(set(("path", "type", "accident_time", *SPLIT_COLUMNS))
                <= matches[0]["values"].keys(), "required_metadata_columns_missing")
        pp.source_path(root, relative)
        # No nested scan and no alias spelling such as real_videos/./a.mp4.
        require(len(PurePosixPath(relative).parts) == 2
                and relative == PurePosixPath(relative).as_posix(), "noncanonical_video_path")
    require(pp.sha256(path) == before, "metadata_changed_during_read")
    return rows, before


def local_inventory(root, rows):
    """List names only. Do not open video files or inspect synthetic data."""
    folder = root / "real_videos"
    require(folder.is_dir() and not folder.is_symlink(), "real_video_directory_missing_or_symlink")
    local = []
    with os.scandir(folder) as entries:
        for count, entry in enumerate(entries, 1):
            require(count <= MAX_DIRECTORY_ENTRIES, "directory_entry_limit")
            if Path(entry.name).suffix.lower() != ".mp4":
                continue
            relative = "real_videos/" + entry.name
            pp.source_path(root, relative)
            require(not entry.is_symlink(), "video_symlink_requires_review")
            matches = rows.get(relative, [])
            splits = {c: matches[0]["values"][c] for c in SPLIT_COLUMNS} if matches else None
            eligible = bool(splits and all(v == "train" for v in splits.values()))
            local.append({"path": relative, "csv_row": matches[0]["csv_row"] if matches else None,
                          "official_splits": splits, "is_regular_file": entry.is_file(follow_symlinks=False),
                          "candidate_eligible": eligible,
                          "reason": "both_official_splits_train" if eligible else
                          "metadata_match_missing" if not matches else
                          "official_test_protected" if "test" in splits.values() else
                          "official_split_unconfirmed",
                          "video_inspection": "inspection_excluded"})
    return sorted(local, key=lambda item: item["path"])


def inspect(root: Path, limit=3):
    """Choose up to three paths by availability then CSV order, without labels."""
    require(type(limit) is int and 1 <= limit <= 3, "select_1_to_3_candidates")
    rows, metadata_hash = read_candidates_metadata(root)
    inventory = local_inventory(root, rows)
    available = {item["path"] for item in inventory if item["is_regular_file"]}
    eligible = [matches[0] for matches in rows.values()
                if all(matches[0]["values"][c] == "train" for c in SPLIT_COLUMNS)]
    eligible.sort(key=lambda item: (item["values"]["path"] not in available, item["csv_row"]))
    candidates = []
    for record in eligible[:limit]:
        raw = record["values"]
        path = pp.source_path(root, raw["path"])
        present = path.is_file()
        candidates.append({
            "path": raw["path"], "csv_row": record["csv_row"],
            "official_splits": {c: raw[c] for c in SPLIT_COLUMNS},
            "local_status": "present_not_decoded" if present else
                            "not_regular_file" if path.exists() else "missing",
            "size_bytes": path.stat().st_size if present else None,
            "source_annotations_raw": {c: raw[c] for c in
                ("type", "accident_time", "accident_frame", "duration", "no_frames",
                 "height", "width", "weather", "quality") if c in raw},
            "upstream_original_id": None, "event_id": None, "camera_id": None,
            "group_evidence": "not_provided_by_metadata; filename_is_not_confirmed_event_id",
            "review_status": "unreviewed", "target": None,
            "approved_segment": None, "training_authorized": False,
            "video_inspection": "not_run",
        })
    split_counts = Counter(tuple(m[0]["values"][c] for c in SPLIT_COLUMNS) for m in rows.values())
    require(pp.sha256(root / "metadata-real.csv") == metadata_hash, "metadata_changed_during_check")
    return {
        "schema_version": 1, "status": "candidate_list_prepared" if candidates else "no_eligible_candidates",
        "purpose": "acquisition_candidates_only", "metadata_file": "metadata-real.csv",
        "metadata_sha256": metadata_hash, "metadata_rows": len(rows),
        "metadata_columns": list(next(iter(rows.values()))[0]["values"]),
        "split_row_counts": [{**dict(zip(SPLIT_COLUMNS, key)), "rows": value}
                             for key, value in sorted(split_counts.items())],
        "selection_rule": "both_splits_train; existing_regular_files_first; then_csv_record_order",
        "selection_is_evaluation_protocol": False, "chosen_evaluation_protocol": None,
        "eligible_metadata_rows": len(eligible), "selected_count": len(candidates),
        "selected_missing_count": sum(c["local_status"] == "missing" for c in candidates),
        "selected_present_count": sum(c["local_status"] == "present_not_decoded" for c in candidates),
        "local_inventory": inventory, "candidates": candidates,
        "unselected_metadata_video_checks": {"status": "inspection_excluded", "count": len(rows) - len(candidates)},
        "limits": {"candidates": limit, "metadata_bytes": MAX_METADATA_BYTES,
                   "metadata_rows": MAX_ROWS, "directory_entries": MAX_DIRECTORY_ENTRIES},
        "metadata_unchanged": True, "video_files_opened": 0, "decoded_frames": 0,
        "training_run": False, "training_readiness": "held", "model_created": False,
        "missing_requirements": ["selected_video_files_and_decode_check", "human_approved_segment_labels",
                                 "original_event_group_evidence", "approved_group_split_manifest",
                                 "independent_normal_sources", "input_preview_and_temporal_policy_review"],
    }


def reconcile_expansion_plan(proposal, metadata, existing, reserved, detrac):
    """Join existing evidence without granting executable splits or new labels."""
    require(proposal.get('purpose') == 'metadata_only_expansion_proposal', 'invalid_expansion_proposal')
    require(detrac.get('status') == 'prepared_not_split', 'detrac_preparation_incomplete')
    excluded = {r.get('source_path') or r.get('video_path') for r in existing}
    excluded.update(r['source_path'] for r in reserved)
    excluded.discard(None)
    blocked_families = {filename_family(p) for p in excluded}
    blocked_families.update(filename_family(p) for p, rows in metadata.items()
                            if any(r['values'].get('split_in_distribution') != 'train' for r in rows))
    seen = set()
    records = []
    for c in proposal['candidates']:
        path = c['source_path']
        matches = metadata.get(path, [])
        require(len(matches) == 1, 'metadata_match_not_unique')
        raw = matches[0]['values']
        require(raw.get('split_in_distribution') == 'train', 'official_test_cannot_be_assigned')
        require(path not in excluded and filename_family(path) not in blocked_families,
                'trained_reserved_or_test_related_source')
        require(filename_family(path) not in seen, 'candidate_family_overlap')
        seen.add(filename_family(path))
        require(c['proposed_role'] in ('training_candidate', 'development_review_reserved'), 'unknown_role')
        require(c['csv_row'] == matches[0]['csv_row'], 'metadata_row_changed')
        point = float(raw['accident_time'])
        require(math.isfinite(point) and abs(c['annotation_seconds']-point) < 1e-8,
                'annotation_changed')
        require(abs(c['start_seconds']-(point-1)) < 1e-8 and
                abs(c['end_seconds']-(point+1)) < 1e-8 and
                point >= 1 and point+1 <= float(raw['duration']), 'invalid_window')
        records.append({**c, 'official_iid_split': raw['split_in_distribution'],
                        'official_geo_split': raw.get('split_geo_aware'),
                        'target': None, 'split': 'unassigned',
                        'source_group_id': 'accident-expansion-unresolved',
                        'hold_reasons': ['upstream_event_relation_unconfirmed',
                                         'input_preparation_pending',
                                         'development_label_review_pending' if c['proposed_role']=='development_review_reserved'
                                         else 'source_annotation_policy_binding_pending'],
                        'training_authorized': False, 'evaluation_authorized': False})
    normal_seen = set()
    for s in detrac['samples']:
        require(s['status']=='prepared_not_split' and s['target']==0 and
                s['label_origin']=='user_assumed_normal', 'normal_assumption_evidence_missing')
        require(s['sequence_id'] not in normal_seen, 'duplicate_normal_sequence')
        normal_seen.add(s['sequence_id'])
        require(s['input']['array_shape']==[16,224,224,3] and
                s['input']['strictly_increasing_timestamps'] is True, 'normal_input_mismatch')
        records.append({'source_path':s['sequence_id'], 'proposed_role':'normal_prepared_unassigned',
                        'target':0, 'label_origin':s['label_origin'], 'human_reviewed':False,
                        'start_seconds':s['start_seconds'], 'end_seconds':s['end_seconds'],
                        'frames_sha256':s['frames_sha256'], 'source_group_id':'detrac-camera-unresolved',
                        'split':'unassigned', 'official_iid_split':None,
                        'official_split':s['official_split'], 'training_authorized':False,
                        'evaluation_authorized':False,
                        'hold_reasons':['official_sequence_split_evidence_missing',
                                        'camera_relation_unconfirmed']})
    return {'status':'joined_pending_split_evidence', 'purpose':'expansion_allocation_ledger_not_execution',
            'records':records, 'proposed_counts':dict(Counter(r['proposed_role'] for r in records)),
            'existing_training_segments':len(existing),
            'existing_class_counts':dict(Counter(str(r['target']) for r in existing)),
            'training_ready':False, 'evaluation_ready':False,
            'independent_event_count':None, 'model_run':False,
            'notes':['Unresolved groups are conservative holding groups, not claims of the same event.',
                     'No train/dev allocation is authorized by this ledger.',
                     'Reuse prepared inputs; do not repeat training or preprocessing.']}


def dataset_role_ledger(metadata, trained, reserved, exposed=()):
    """Allocate source files, not execution-ready segments or verified events."""
    trained, reserved, exposed = set(trained), set(reserved), set(exposed)
    require((trained | reserved | exposed) <= set(metadata), 'history_source_missing_from_metadata')
    protected = reserved | {p for p, m in metadata.items()
                            if m[0]['values']['split_in_distribution'] != 'train'}
    protected_families = {filename_family(p) for p in protected}
    records = []
    for path, matches in metadata.items():
        require(len(matches) == 1, 'ambiguous_metadata_path')
        row = matches[0]; values = row['values']
        iid = values['split_in_distribution']
        window = None
        try:
            point, duration = float(values['accident_time']), float(values['duration'])
            if math.isfinite(point) and math.isfinite(duration) and 1 <= point <= duration - 1:
                window = [point - 1, point + 1]
        except (KeyError, TypeError, ValueError):
            pass
        if path in trained and (path in reserved or iid != 'train'):
            role, reason = 'hold', 'historical_training_split_conflict'
        elif iid == 'test':
            role, reason = 'official_test_protected', 'never_train_or_tune'
        elif iid != 'train':
            role, reason = 'hold', 'unknown_official_split'
        elif path in reserved:
            role, reason = 'development_reference', 'existing_reservation_or_evaluation'
        elif path in trained:
            role, reason = 'training_used', 'historical_training_record'
        elif filename_family(path) in protected_families:
            role, reason = 'hold', 'filename_family_matches_protected_source'
        elif window is None:
            role, reason = 'hold', 'two_second_annotation_window_unavailable'
        else:
            role, reason = 'training_candidate', 'official_train_not_used_or_reserved'
        records.append(dict(source_path=path, csv_row=row['csv_row'], official_iid_split=iid,
                            official_geo_split=values.get('split_geo_aware'), role=role, reason=reason,
                            previously_trained=path in trained, development_reserved=path in reserved,
                            known_exposure=path in exposed, exposure_audit_complete=False,
                            filename_family_hint=filename_family(path), event_independence='unconfirmed',
                            candidate_window_seconds=window, label_scope='annotation_window_only_not_whole_video',
                            file_availability='not_checked_this_step', execution_authorized=False))
    return dict(purpose='source_role_ledger_not_execution_manifest', records=records,
                counts=dict(Counter(r['role'] for r in records)), training_run=False, model_run=False,
                independent_validation=False)


def expansion_request_from_ledger(ledger, metadata):
    """Connect only unused official-train windows; recheck against source metadata."""
    records = ledger['records']
    require(len({r['source_path'] for r in records}) == len(records), 'duplicate_ledger_source')
    require(set(metadata) == {r['source_path'] for r in records}, 'ledger_metadata_coverage_mismatch')
    protected = [r['source_path'] for r in records if r['role'] != 'training_candidate']
    candidates = []
    for r in records:
        if r['role'] != 'training_candidate':
            continue
        matches = metadata.get(r['source_path'], [])
        require(len(matches) == 1, 'metadata_match_not_unique')
        row = matches[0]; v = row['values']
        point, duration = float(v['accident_time']), float(v['duration'])
        require(v['split_in_distribution'] == 'train' and row['csv_row'] == r['csv_row']
                and not r['previously_trained'] and not r['development_reserved'], 'candidate_role_conflict')
        require(math.isfinite(point) and math.isfinite(duration) and 1 <= point <= duration-1
                and r['candidate_window_seconds'] == [point-1,point+1], 'annotation_window_changed')
        candidates.append(dict(source_path=r['source_path'], csv_row=row['csv_row'],
                               annotation_seconds=point, start_seconds=point-1, end_seconds=point+1,
                               target=1, label_origin='source_annotation_derived', human_reviewed=False))
    require(0 < len(candidates) <= 400, 'candidate_count_exceeded')
    return dict(purpose='colab_expansion_file_check_only', accident_candidates=candidates,
                normal_candidates=[], reserved_accident_paths=protected,
                reserved_normal_sequences=[], official_normal_test=[], training_authorized=False,
                evaluation_authorized=False, independent_validation=False,
                settings=dict(seconds=2,frames=16,image_size=224,fps_detrac=25),
                note='New accident inputs only. Existing normal/training arrays are not reprocessed. Strict sampling unchanged.')
