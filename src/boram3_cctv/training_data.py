"""Training readiness and bounded, approved segment preparation. No torch import."""
from __future__ import annotations

from datetime import date
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

from . import preprocessing as pp

CLASS_MAPPING = {"normal": 0, "accident": 1}
VERSION = "r3d-training-0.1"
LIMITED_REAL = "limited_real_training_no_validation"
SOURCE_REAL = "source_annotation_limited_training_no_validation"
EXPANDED_REAL = "expanded_source_training_no_validation"


class TrainingError(Exception):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def connect_training_preparation(root, manifest, *, max_records=8):
    """Reuse saved human/CSV-derived inputs for transfer, never authorize a run."""
    from copy import deepcopy
    root = Path(root).resolve()

    def require(ok, code):
        if not ok:
            raise TrainingError(code)

    def checked(ref):
        path = resolve_path(root, ref['path'])
        require(not Path(ref['path']).is_absolute() and path.is_relative_to(root)
                and path.is_file(), 'preparation_reference_missing_or_outside_root')
        require(pp.sha256(path) == ref['sha256'], 'preparation_reference_changed')
        return path

    require(manifest.get('purpose') == 'training_input_transfer_only'
            and manifest.get('training_execution_authorized') is False
            and manifest.get('validation_segments') == [] and manifest.get('test_segments') == [],
            'preparation_only_scope_required')
    require(manifest.get('preprocessing') == {
        'version': 'r3d-letterbox-kinetics-v1', 'frame_count': 16, 'image_size': 224,
        'sampling': 'nearest_PTS_to_uniform_bin_centres', 'spatial': 'letterbox_no_crop',
        'normalization': 'KINETICS400_V1', 'layout': 'CTHW', 'dtype': 'float32'},
        'preparation_input_settings_changed')
    require(max_records in (8, 33), 'unsupported_source_input_limit')
    extras = manifest.get('additional_annotation_batches', [])
    require(isinstance(extras, list) and len(extras) <= 25
            and (not extras or max_records == 33), 'additional_annotations_require_expansion_scope')
    refs = deepcopy(manifest['references'])
    for index, batch in enumerate(extras):
        for kind in ('annotation_report', 'annotation_policy'):
            refs[f'{kind}_{index}'] = batch[kind]
    paths = {key: checked(ref) for key, ref in refs.items()}
    old = pp.read_json(paths['previous_inputs'])
    human = {s['sample_id']: s for s in pp.read_json(paths['human_receipt'])['samples']}
    annotation = pp.read_json(paths['annotation_report'])
    policy = pp.read_json(paths['annotation_policy'])
    reserved = pp.read_json(paths['reserved_manifest'])['segments']
    metadata = pp.read_metadata(paths['metadata'])
    require(annotation.get('status') == 'source_annotation_inputs_prepared'
            and annotation.get('training_execution_authorized') is False
            and policy.get('authorization', {}).get('use_source_annotations') is True
            and policy.get('training_execution_authorized') is False,
            'source_annotation_receipt_required')
    records = deepcopy(old + annotation['samples'])
    require(len(records) <= 8, 'base_preparation_limit')
    policy_by_id = {r['sample_id']: refs['annotation_policy']['sha256'] for r in annotation['samples']}
    extra_count = 0
    for index, batch in enumerate(extras):
        report = pp.read_json(paths[f'annotation_report_{index}'])
        extra_policy = pp.read_json(paths[f'annotation_policy_{index}'])
        require(report.get('status') == 'source_annotation_inputs_prepared'
                and report.get('training_execution_authorized') is False
                and report.get('inputs_unchanged') is True
                and extra_policy.get('authorization', {}).get('use_source_annotations') is True
                and extra_policy.get('authorization', {}).get('source') == 'user_message'
                and bool(extra_policy.get('authorization', {}).get('raw_response'))
                and extra_policy.get('training_execution_authorized') is False
                and extra_policy.get('metadata_sha256') == refs['metadata']['sha256']
                and extra_policy.get('reserved_manifest_sha256') == refs['reserved_manifest']['sha256']
                and 1 <= len(report.get('samples', [])) <= 3
                and extra_policy.get('sample_ids') == [r['sample_id'] for r in report['samples']],
                'additional_annotation_receipt_invalid')
        extra_count += len(report['samples'])
        for record in report['samples']:
            policy_by_id[record['sample_id']] = refs[f'annotation_policy_{index}']['sha256']
        records.extend(deepcopy(report['samples']))
    require(extra_count <= 25, 'additional_annotation_limit')
    require(2 <= len(records) <= max_records and len({r['sample_id'] for r in records}) == len(records)
            and [r['sample_id'] for r in records] == manifest['sample_ids'],
            'preparation_selection_mismatch')
    old_ids = {r['sample_id'] for r in old}
    groups = {}
    for record in records:
        sid = record['sample_id']
        require(record.get('split') == 'train' and bool(record.get('management_group')),
                'preparation_train_only_required')
        source = record['source_sha256']
        require(groups.setdefault(source, record['management_group']) == record['management_group'],
                'same_original_must_keep_same_group')
        start, end = pp.validate_window(record)
        if sid in old_ids:
            evidence = human[sid]
            require(all(record[k] == evidence[k] for k in
                        ('target', 'source_sha256', 'start_seconds', 'end_seconds', 'label_review'))
                    and evidence['input_quality_status'] == 'confirmed'
                    and pp.approved_target(evidence, evidence['label_review']['policy_version'], source)
                    == record['target'], 'preparation_human_evidence_mismatch')
            record.update(source_path=evidence['video_path'], source_kind=evidence['source_kind'],
                          input_quality_status='confirmed',
                          label_provenance={'kind': 'human_review', 'human_reviewed': True})
        else:
            provenance = record['label_provenance']
            require(type(record['target']) is int and record['target'] == 1
                    and provenance.get('kind') == 'source_annotation_derived'
                    and provenance.get('human_reviewed') is False
                    and provenance.get('policy_version') == 'accident-point-window-v1'
                    and provenance.get('policy_authorization_sha256') == policy_by_id[sid]
                    and record['label_review'].get('status') == 'unreviewed',
                    'preparation_source_label_mismatch')
            record.update(source_path=record['video_path'], source_kind='accident')
        require(type(record['target']) is int and record['target'] in (0, 1), 'unconfirmed_target')
        for blocked in reserved:
            require(not (source == blocked.get('source_sha256')
                         or record['source_path'] and record['source_path'] == blocked.get('source_path')
                         or any(record.get(k) and record[k] == blocked.get(k)
                                for k in ('event_id', 'upstream_original_id'))),
                    'preparation_reserved_source_forbidden')
        if record['source_kind'] == 'accident':
            matches = metadata.get(record['source_path'], [])
            require(len(matches) == 1 and matches[0]['values']['split_in_distribution'] == 'train'
                    and record['official_splits']['split_in_distribution'] == 'train',
                    'official_IID_test_cannot_train')
            if sid not in old_ids:
                entry, provenance = matches[0], record['label_provenance']
                t = pp.number(float(entry['values']['accident_time']))
                require(provenance['metadata_sha256'] == refs['metadata']['sha256']
                        and provenance['csv_row'] == entry['csv_row']
                        and provenance['annotation_seconds'] == t
                        and math.isclose(start, t-1, abs_tol=1e-6)
                        and math.isclose(end, t+1, abs_tol=1e-6), 'preparation_annotation_window_mismatch')
        else:
            require(record['source_kind'] == 'qmul' and record['target'] == 0,
                    'preparation_unknown_normal_source')
        for kind in ('array', 'frames'):
            checked({'path': record[kind+'_path'], 'sha256': record[kind+'_sha256']})
        if not record.get('preview_path'):
            preview = (Path(record['array_path']).parent / 'preview.png').as_posix()
            record.update(preview_path=preview, preview_sha256=pp.sha256(root / preview))
        checked({'path': record['preview_path'], 'sha256': record['preview_sha256']})
        record.update(training_execution_authorized=False, evaluation_authorized=False)
    require({r['target'] for r in records} == {0, 1}, 'preparation_both_classes_required')
    return {'purpose': manifest['purpose'], 'records': records,
            'class_mapping': CLASS_MAPPING, 'preprocessing': manifest['preprocessing'],
            'class_counts': {k: sum(r['target'] == v for r in records) for k, v in CLASS_MAPPING.items()},
            'label_origin_counts': dict(Counter(r['label_provenance']['kind'] for r in records)),
            'temporal_seconds_by_class': {k: sorted({round(r['end_seconds']-r['start_seconds'], 6)
                                         for r in records if r['target'] == v})
                                         for k, v in CLASS_MAPPING.items()},
            'normal_source_files': len({r['source_sha256'] for r in records if r['target'] == 0}),
            'independent_event_count': None, 'independent_validation': False,
            'training_execution_authorized': False, 'training_run': False,
            'new_video_decode': False, 'references': deepcopy(refs)}


def match_colab_source_windows(prepared, reported):
    """Match a user-run CSV list to the existing saved pixels, without new labels."""
    if (reported.get('purpose') != 'source_annotation_window_list_only'
            or reported.get('errors') != [] or reported.get('training_run') is not False
            or reported.get('training_ready') is not False
            or reported.get('class_mapping') != CLASS_MAPPING):
        raise TrainingError('colab_window_list_incomplete_or_wrong_purpose')
    expected = {r['sample_id']: r for r in prepared['records']
                if r['label_provenance']['kind'] == 'source_annotation_derived'}
    rows = reported.get('segments', [])
    if len(rows) != len(expected) or {r['sample_id'] for r in rows} != set(expected):
        raise TrainingError('colab_window_selection_mismatch')
    for row in rows:
        ref = expected[row['sample_id']]
        provenance = ref['label_provenance']
        if (type(row.get('target')) is not int or row['target'] != 1
                or any(row.get(k) != ref[k] for k in ('source_path', 'start_seconds', 'end_seconds'))
                or row.get('csv_row') != provenance['csv_row']
                or row.get('metadata_sha256') != provenance['metadata_sha256']
                or row.get('official_iid_split') != 'train'
                or row.get('label_provenance', {}).get('kind') != 'source_annotation_derived'
                or row['label_provenance'].get('human_reviewed') is not False
                or row['label_provenance'].get('annotation_seconds') != provenance['annotation_seconds']):
            raise TrainingError('colab_window_differs_from_saved_input')
    return {'matched_samples': sorted(expected), 'raw_drive_video_content_checked': False,
            'uses_saved_preprocessed_pixels': True, 'training_run': False}


def resolve_path(root, value):
    if not isinstance(value, str) or not value.strip():
        raise TrainingError("path_not_configured")
    path = Path(value).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def validate_config(config):
    if config.get("schema_version") != 1 or config.get("device") not in ("cpu", "mps", "cuda"):
        raise TrainingError("invalid_config_or_device")
    expanded = config.get("purpose") == EXPANDED_REAL
    source_run = config.get("purpose") == SOURCE_REAL
    source_limit = config.get("source_input_limit", 8)
    if (type(source_limit) is not int or source_limit not in (8, 33)
            or not source_run and source_limit != 8):
        raise TrainingError("invalid_source_input_limit")
    limited = config.get("purpose") in (LIMITED_REAL, SOURCE_REAL, EXPANDED_REAL)
    if config.get("purpose") not in (None, LIMITED_REAL, SOURCE_REAL, EXPANDED_REAL):
        raise TrainingError("unknown_training_purpose")
    if source_run and config.get("initialization") not in (
            "official_pretrained_fresh_head", "project_model_fresh_optimizer"):
        raise TrainingError("source_training_initialization_required")
    if limited and (type(config.get("max_epochs")) is not int or not 1 <= config["max_epochs"] <= 5
                    or config.get("train_scope") != "head" or config.get("batch_size") != 1):
        raise TrainingError("invalid_limited_real_scope")
    for key, low, high in (("batch_size", 1, 3), ("max_steps", 1, (3015 if config.get("execution_scope") == "fixed_603_v1" else 1910) if expanded else source_limit*5 if source_run else 25 if limited else 20),
                           ("max_seconds", 1, 1800 if limited and not source_run else 300),
                           ("cpu_threads", 1, 4), ("seed", 0, 2**32-1)):
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise TrainingError("invalid_" + key)
    if config.get("train_scope") not in ("head", "all") or config.get("num_workers") != 0:
        raise TrainingError("invalid_scope_or_workers")
    for key, low, high in (("learning_rate", 0, 0.1), ("momentum", 0, 1)):
        value = config.get(key)
        if (type(value) not in (int, float) or not math.isfinite(value) or
                not low <= value < high or (key == "learning_rate" and value == 0)):
            raise TrainingError("invalid_" + key)
    settings = config.get("preprocessing", {})
    fixed = {"version": "r3d-letterbox-kinetics-v1", "sampling": "nearest_PTS_to_uniform_bin_centres",
             "spatial": "letterbox_no_crop", "normalization": "KINETICS400_V1",
             "layout": "CTHW", "dtype": "float32"}
    if any(settings.get(key) != value for key, value in fixed.items()):
        raise TrainingError("unsupported_preprocessing")
    for key, low, high in (("frame_count", 2, 32), ("image_size", 32, 224)):
        if type(settings.get(key)) is not int or not low <= settings[key] <= high:
            raise TrainingError("invalid_" + key)
    if limited:
        if settings["frame_count"] != 16 or settings["image_size"] != 224:
            raise TrainingError("limited_real_requires_reviewed_input_shape")
        for key, high in (("probe_steps", 3), ("probe_max_seconds", 60),
                          ("max_step_seconds", 30), ("max_process_rss_bytes", 8 * 1024**3),
                          ("max_mps_driver_bytes", 8 * 1024**3)):
            if type(config.get(key)) is not int or not 1 <= config[key] <= high:
                raise TrainingError("invalid_" + key)


def source_real_preflight(config, root):
    """Explicit bounded execution over the existing mixed-origin input receipt.

    Keep the original five-sample human-only and independent-evaluation paths
    intact. Source annotations are training supervision, not visual approvals.
    """
    validate_config(config)
    if config.get("purpose") != SOURCE_REAL:
        raise TrainingError("source_training_purpose_required")
    if config.get("initialization") != "official_pretrained_fresh_head":
        raise TrainingError("project_model_requires_explicit_colab_continuation")
    root = Path(root).resolve()

    def checked(name, digest=None):
        path = resolve_path(root, name)
        if Path(name).is_absolute() or not path.is_relative_to(root) or not path.is_file():
            raise TrainingError("source_training_reference_invalid")
        if digest is not None and pp.sha256(path) != digest:
            raise TrainingError("source_training_reference_changed")
        return path

    manifest_path = checked(config["train_manifest"])
    manifest = pp.read_json(manifest_path)
    if (manifest.get("schema_version") != 1 or manifest.get("purpose") != SOURCE_REAL
            or manifest.get("official_split_column") != "split_in_distribution"
            or manifest.get("validation_segments") != [] or manifest.get("test_segments") != []):
        raise TrainingError("source_training_train_only_IID_required")
    auth = manifest.get("authorization", {})
    if (auth.get("source") != "user_message" or auth.get("training_authorized") is not True
            or not auth.get("raw_response") or auth.get("config_sha256") != fingerprint(config)
            or auth.get("conservative_groups_authorized") is not True
            or any(auth.get(k) != config[k] for k in ("max_epochs", "max_steps", "max_seconds", "probe_steps", "probe_max_seconds"))):
        raise TrainingError("source_training_execution_authorization_missing_or_changed")
    prepared_path = checked(manifest["preparation_manifest"], manifest["preparation_manifest_sha256"])
    preparation = pp.read_json(prepared_path)
    source_limit = config.get("source_input_limit", 8)
    joined = connect_training_preparation(root, preparation, max_records=source_limit)
    records = joined["records"]
    if (config["preprocessing"] != joined["preprocessing"]
            or manifest.get("sample_ids") != [r["sample_id"] for r in records]
            or not 2 <= len(records) <= source_limit
            or config["max_steps"] != len(records)*config["max_epochs"]
            or not 1 <= config["probe_steps"] < config["max_steps"]
            or not 1 <= joined["label_origin_counts"].get("source_annotation_derived", 0) <= source_limit-5):
        raise TrainingError("source_training_selection_or_limits_mismatch")
    # All unresolved accident sources stay train-only in one conservative group.
    # Preserve the earlier grouping as evidence, never claim they are one event.
    for record in records:
        record["previous_management_group"] = record["management_group"]
        if record["source_kind"] == "accident":
            record["management_group"] = "accident_training_unresolved"
        record.update(training_execution_authorized=True, evaluation_authorized=False,
                      grouping_note="conservative training group; event identity not established")
    versions = {"training_version": VERSION, "purpose": SOURCE_REAL,
                "manifest_sha256": pp.sha256(manifest_path),
                "preparation_manifest_sha256": pp.sha256(prepared_path),
                "references": joined["references"], "class_mapping": CLASS_MAPPING,
                "initialization": config["initialization"], "preprocessing_sha256": fingerprint(config["preprocessing"])}
    return {"status": "ready", "issues": [], "purpose": SOURCE_REAL,
            "selected_segments": len(records), "train_segments": len(records),
            "approved_segment_labels": joined["label_origin_counts"].get("human_review", 0),
            "source_annotation_labels": joined["label_origin_counts"].get("source_annotation_derived", 0),
            "class_counts": joined["class_counts"], "label_origin_counts": joined["label_origin_counts"],
            "temporal_seconds_by_class": joined["temporal_seconds_by_class"],
            "normal_source_files": joined["normal_source_files"], "records": records, "prepared": records,
            "versions": versions, "generalization_evaluation": False,
            "validation_segments": 0, "test_segments": 0, "independent_event_count": None,
            "existing_evaluation_preflight_unchanged": True}


def limited_real_preflight(config, root):
    """Train-only purpose with explicit conservative groups; no independent eval.

    Reuse the immutable reviewed-input receipt. This does not weaken preflight()
    for independent train/validation experiments or invent upstream/event IDs.
    """
    validate_config(config)
    if config.get("purpose") != LIMITED_REAL:
        raise TrainingError("limited_real_purpose_required")
    root = root.resolve()

    def local(value):
        path = resolve_path(root, value)
        if not path.is_relative_to(root):
            raise TrainingError("limited_real_reference_outside_project")
        return path

    manifest_path = local(config["train_manifest"])
    manifest = pp.read_json(manifest_path)
    if (manifest.get("schema_version") != 1 or manifest.get("purpose") != LIMITED_REAL
            or manifest.get("official_split_column") != "split_in_distribution"
            or manifest.get("validation_segments") != [] or manifest.get("test_segments") != []):
        raise TrainingError("limited_real_train_only_IID_required")
    auth = manifest.get("authorization", {})
    if (auth.get("source") != "user_message" or auth.get("training_authorized") is not True
            or auth.get("conservative_groups_authorized") is not True
            or auth.get("max_epochs") != 5 or auth.get("max_loop_seconds") != 1800
            or not auth.get("basis")):
        raise TrainingError("limited_real_authorization_missing")
    receipt_path = local(manifest["reviewed_inputs_report"])
    if pp.sha256(receipt_path) != manifest["reviewed_inputs_sha256"]:
        raise TrainingError("reviewed_inputs_receipt_changed")
    receipt = pp.read_json(receipt_path)
    if receipt.get("chosen_evaluation_protocol") != "split_in_distribution" or receipt.get("inspection_status") != "completed":
        raise TrainingError("reviewed_IID_receipt_required")
    # File integrity is checked at use time; human judgements and decoder tests
    # are reused. Unselected raw videos/arrays are not opened here.
    for name, digest in receipt["input_sha256"].items():
        if Path(name).suffix in (".mp4", ".npy") or name.endswith("/frames.json"):
            continue
        if pp.sha256(local(name)) != digest:
            raise TrainingError("reviewed_evidence_changed:" + name)
    available = {s["sample_id"]: s for s in receipt["samples"]}
    entries = manifest.get("segments", [])
    if (not 2 <= len(entries) <= 5 or len({e["sample_id"] for e in entries}) != len(entries)
            or config["max_steps"] != len(entries) * config["max_epochs"]):
        raise TrainingError("limited_real_epoch_or_segment_count")
    prepared, records, source_groups, accident_groups = [], [], {}, set()
    for entry in entries:
        sid = entry["sample_id"]
        if sid not in available or entry.get("split") != "train" or not entry.get("management_group"):
            raise TrainingError("limited_real_only_selected_train")
        sample = available[sid]
        review = sample["label_review"]
        if (sample["input_quality_status"] != "confirmed" or type(sample["target"]) is not int
                or pp.approved_target(sample, review["policy_version"], sample["source_sha256"]) != sample["target"]):
            raise TrainingError("limited_real_label_or_quality_unconfirmed")
        if sample["source_kind"] == "accident":
            if sample["csv_official_splits"]["split_in_distribution"] != "train":
                raise TrainingError("official_IID_test_cannot_train")
            source = pp.source_path(local(config["data_root"]), sample["video_path"])
            if pp.sha256(source) != sample["source_sha256"]:
                raise TrainingError("training_original_changed")
            if not sample.get("upstream_original_id") or not sample.get("event_id"):
                accident_groups.add(entry["management_group"])
        elif sample["source_kind"] != "qmul" or sample["csv_official_splits"] is not None or sample["target"] != 0:
            raise TrainingError("unsupported_external_training_source")
        group = entry["management_group"]
        if source_groups.setdefault(sample["source_sha256"], group) != group:
            raise TrainingError("same_original_must_keep_same_group")
        item = {k: sample[k] for k in ("sample_id", "target", "source_sha256", "start_seconds", "end_seconds", "label_review")}
        for key in ("array_path", "frames_path"):
            path = local(sample["prepared_input"][key])
            digest = sample["prepared_input"][key.replace("_path", "_sha256")]
            if pp.sha256(path) != digest:
                raise TrainingError("approved_prepared_input_changed")
            item[key] = str(path.relative_to(root))
            item[key.replace("_path", "_sha256")] = digest
        item.update(management_group=group, split="train", upstream_original_id=sample.get("upstream_original_id"),
                    event_id=sample.get("event_id"), official_splits=sample["csv_official_splits"])
        prepared.append(item)
        records.append({**sample, "assigned_split": "train", "training_authorized": True,
                        "management_group": group, "authorization_source": "current_user_message"})
    if len(accident_groups) > 1:
        raise TrainingError("unresolved_accidents_must_keep_together")
    if {p["target"] for p in prepared} != {0, 1}:
        raise TrainingError("train_requires_reviewed_normal_and_accident")
    versions = {"training_version": VERSION, "purpose": LIMITED_REAL,
                "manifest_sha256": pp.sha256(manifest_path), "reviewed_inputs_sha256": pp.sha256(receipt_path),
                "preprocessing_sha256": fingerprint(config["preprocessing"]),
                "metadata_sha256": receipt["input_sha256"][config["metadata_path"]],
                "class_mapping": CLASS_MAPPING}
    return {"status": "ready", "issues": [], "purpose": LIMITED_REAL,
            "selected_segments": len(entries), "approved_segment_labels": len(entries), "train_segments": len(entries),
            "records": records, "prepared": prepared, "versions": versions,
            "generalization_evaluation": False, "validation_segments": 0, "test_segments": 0,
            "independent_event_count": None, "existing_evaluation_preflight_unchanged": True}


def approved_review(review):
    if not isinstance(review, dict) or review.get("status") != "approved":
        return False
    if any(not isinstance(review.get(k), str) or not review[k].strip() for k in ("basis", "reviewer", "reviewed_at")):
        return False
    try:
        date.fromisoformat(review["reviewed_at"])
    except ValueError:
        return False
    return True


def reviewed_input_readiness(config, batches, metadata, *, protocol_selection=None):
    """Describe checked review records, never assign partitions or permit training.

    The CLI binds each batch to its current plan, answers, and checked input files
    before calling this function. This diagnostic does not replace preflight().
    """
    validate_config(config)
    columns = ("split_in_distribution", "split_geo_aware")
    selected_column = None
    if protocol_selection is not None:
        if (not isinstance(protocol_selection, dict)
                or protocol_selection.get("column") not in columns
                or protocol_selection.get("source") != "user_message"
                or protocol_selection.get("task") != "binary_segment_classification"
                or not isinstance(protocol_selection.get("raw_response"), str)
                or not protocol_selection["raw_response"].strip()
                or protocol_selection.get("training_execution_authorized") is not False):
            raise TrainingError("invalid_protocol_selection")
        selected_column = protocol_selection["column"]
    if not 1 <= len(batches) <= 2 or len({b['kind'] for b in batches}) != len(batches):
        raise TrainingError("select_distinct_review_batches")
    records, seen, windows = [], set(), set()
    issues = {"group_split_requires_separate_approval", "official_protocol_not_selected",
              "training_config_and_mixed_source_integration_require_review"}
    if selected_column:
        issues.remove("official_protocol_not_selected")
    if not config.get("split_manifest"):
        issues.add("approved_split_manifest_missing")
    if not config.get("pretrained_weights_path"):
        issues.add("official_weights_not_configured")
    for batch in batches:
        kind, quality = batch["kind"], batch["quality"]
        if kind not in ("accident", "qmul") or not 1 <= len(quality["samples"]) <= 3:
            raise TrainingError("invalid_review_batch")
        for item in quality["samples"]:
            sample = item["original_segment_record"]
            sid, source_hash = sample["sample_id"], sample["source_sha256"]
            pp.validate_window(sample)
            window = (source_hash, sample["start_seconds"], sample["end_seconds"])
            if sid in seen or window in windows:
                raise TrainingError("duplicate_reviewed_segment")
            seen.add(sid); windows.add(window)
            target = pp.approved_target(sample, batch["policy_version"], source_hash)
            if target is None or type(item["approved_target"]) is not int or target != item["approved_target"]:
                raise TrainingError("approved_review_target_mismatch")
            problems = []
            if item["quality_status"] != "confirmed":
                problems.append("input_quality_not_confirmed")
            declared = sample.get("official_splits") if kind == "accident" else sample.get("official_split")
            current, csv_row, annotation = None, None, None
            if kind == "accident":
                matches = metadata.get(sample["video_path"], [])
                if len(matches) != 1:
                    problems.append("metadata_not_unique")
                else:
                    raw = matches[0]["values"]
                    current = {c: raw.get(c) for c in columns}
                    csv_row = matches[0]["csv_row"]
                    annotation = {k: raw.get(k) for k in ("accident_time", "accident_frame", "type")}
                    if current != declared:
                        problems.append("official_split_record_csv_mismatch")
                    if any(current[c] not in ("train", "test") for c in columns):
                        problems.append("official_split_value_unknown")
            elif declared is not None:
                problems.append("unexpected_external_official_split")
            if not sample.get("upstream_original_id") or (target == 1 and not sample.get("event_id")):
                problems.append("upstream_or_event_relationship_unverified")
            issues.update(sid + ":" + problem for problem in problems)
            records.append({
                "sample_id": sid, "source_kind": kind, "source_sha256": source_hash,
                "video_path": sample.get("video_path"),
                "start_seconds": sample["start_seconds"], "end_seconds": sample["end_seconds"],
                "duration_seconds": round(sample["end_seconds"] - sample["start_seconds"], 6),
                "target": target, "label_review": sample["label_review"],
                "input_quality_status": item["quality_status"],
                "quality_reviewer": item["quality_submission"]["reviewer"],
                "quality_reviewed_at": item["quality_submission"]["reviewed_at"],
                "recorded_official_splits": declared, "csv_official_splits": current,
                "csv_row": csv_row, "source_annotations_raw": annotation,
                "source_group_id": sample.get("source_group_id"),
                "upstream_original_id": sample.get("upstream_original_id"),
                "event_id": sample.get("event_id"), "assigned_split": None,
                "training_authorized": False, "issues": problems,
            })
    # These are known equal identifiers, not newly inferred event identities.
    constraints = []
    for key in ("source_sha256", "source_group_id", "upstream_original_id", "event_id"):
        groups = defaultdict(list)
        for row in records:
            if row[key]:
                groups[row[key]].append(row["sample_id"])
        constraints.extend({"evidence_key": key, "value": value, "sample_ids": ids,
                            "rule": "keep_in_one_partition"}
                           for value, ids in sorted(groups.items()) if len(ids) > 1)
    normal_sources = {r["source_sha256"] for r in records if r["target"] == 0}
    if len(normal_sources) < 2:
        issues.add("independent_normal_sources_insufficient_for_train_validation")
    durations = sorted({r["duration_seconds"] for r in records})
    if len(durations) > 1:
        issues.add("different_segment_durations_need_temporal_policy")
    comparison = {}
    for column in columns:
        comparison[column] = {
            "official_train_only_not_authorized": [r["sample_id"] for r in records
                if r["csv_official_splits"] and r["csv_official_splits"].get(column) == "train"],
            "official_test_excluded_from_train_and_tuning": [r["sample_id"] for r in records
                if r["csv_official_splits"] and r["csv_official_splits"].get(column) == "test"],
            "external_without_official_assignment": [r["sample_id"] for r in records if r["source_kind"] == "qmul"],
            "unknown_official_value": [r["sample_id"] for r in records if r["source_kind"] == "accident"
                and (r["csv_official_splits"] or {}).get(column) not in ("train", "test")],
        }
    result = {"schema_version": 1, "purpose": "reviewed_input_readiness_only", "status": "held",
            "inspection_status": "completed", "issues": sorted(issues), "samples": records,
            "selected_segments": len(records), "approved_label_counts": dict(Counter(
                pp.CLASS_ORDER[r["target"]] for r in records)),
            "quality_counts": dict(Counter(r["input_quality_status"] for r in records)),
            "distinct_recorded_file_hashes": len({r["source_sha256"] for r in records}),
            "normal_distinct_recorded_file_hashes": len(normal_sources),
            "independent_event_count": None, "independence_verified": False,
            "keep_together_constraints": constraints, "protocol_comparison": comparison,
            "chosen_evaluation_protocol": selected_column, "assigned_train_segments": 0,
            "assigned_validation_segments": 0, "segment_durations_seconds": durations,
            "split_manifest_configured": bool(config.get("split_manifest")),
            "weights_path_configured": bool(config.get("pretrained_weights_path")),
            "training_gate_replaced": False, "training_run": False,
            "generalization_evaluation": False, "source_video_rechecked": False,
            "video_frames_decoded": 0, "model_created": False}
    if selected_column:
        eligible = []
        for row in records:
            official = (row["csv_official_splits"] or {}).get(selected_column)
            row["selected_official_split"] = official
            # Unknown relationships prohibit independent evaluation; they do not
            # automatically erase reviewed train-only computational inputs.
            record_issues = [p for p in row["issues"] if p != "upstream_or_event_relationship_unverified"]
            allowed = not record_issues and (official == "train" or row["source_kind"] == "qmul")
            row["proposed_use"] = "A_train_only_candidate" if allowed else (
                "official_test_protected" if official == "test" else "hold_for_evidence")
            if allowed:
                eligible.append(row)
        requested_ids = protocol_selection.get("smoke_sample_ids", [])
        if (not isinstance(requested_ids, list) or not 1 <= len(requested_ids) <= 3
                or any(not isinstance(s, str) for s in requested_ids) or len(set(requested_ids)) != len(requested_ids)):
            raise TrainingError("select_1_to_3_smoke_candidates")
        eligible_by_id = {r["sample_id"]: r for r in eligible}
        if not set(requested_ids) <= eligible_by_id.keys():
            raise TrainingError("smoke_candidate_not_eligible_or_official_test")
        both_classes = {eligible_by_id[s]["target"] for s in requested_ids} == {0, 1}
        a_conditions = ["approved_A_train_only_manifest_and_mixed_source_input_connection",
                        "existing_group_gate_needs_explicit_A_contract_no_fake_event_ids",
                        "explicit_training_execution_approval"]
        if not config.get("pretrained_weights_path"):
            a_conditions.append("verified_official_weights_file_not_configured")
        if not both_classes:
            a_conditions.append("A_requires_reviewed_normal_and_accident")
        result.update(protocol_selection=protocol_selection,
            benchmark_scope={"our_task":"reviewed_binary_segment_classification",
                             "official_time_location_type_benchmark_reproduced":False,
                             "geographic_generalization_evaluated":False},
            A_short_training_check={
                "data_status":"reviewed_candidates_available" if both_classes else "both_classes_missing",
                "candidate_sample_ids":[r["sample_id"] for r in eligible],
                "proposed_smoke_sample_ids":requested_ids,
                "proposed_limits":{"max_steps":3,"training_worker_max_seconds":60,
                                   "overall_proposed_max_seconds":180,"batch_size":1,
                                   "frame_count":16,"image_size":224,"train_scope":"head"},
                "execution_status":"held_pending_A_setup_and_approval", "remaining_conditions":a_conditions,
                "blocked_by_B_data_shortage":False, "independent_evaluation_claimed":False,
                "existing_training_gate_unchanged":True},
            B_independent_evaluation={
                "status":"not_ready", "assigned_evaluation_segments":0,
                "issues": ["reviewed_independent_train_validation_sources_and_event_relations_missing",
                           "unexposed_final_test_not_established",
                           "normal_source_diversity_and_class_source_confounding",
                           "common_temporal_policy_and_binary_metrics_not_established"]},
            unresolved_accident_keep_together={
                "sample_ids":[r["sample_id"] for r in eligible if r["target"]==1],
                "rule":"do_not_split_between_train_and_evaluation_without_relationship_review",
                "confirmed_same_event":None})
    return result


def compare_candidate_history(history, metadata, metadata_hash, records, reviewed_metadata_hash):
    """Compare file keys/CSV evidence, not unavailable video contents or events."""
    if (history.get("purpose") != "acquisition_candidates_only"
            or not isinstance(history.get("candidates"), list) or not 1 <= len(history["candidates"]) <= 3):
        raise TrainingError("invalid_candidate_history")
    columns = ("split_in_distribution", "split_geo_aware")
    rows = []
    for index, item in enumerate(history["candidates"], 1):
        matches = metadata.get(item["path"], [])
        current = matches[0] if len(matches)==1 else None
        rows.append({"record_kind":"previous_acquisition_candidate", "candidate_number":index,
            "sample_id":None, "path":item["path"], "recorded_csv_row":item["csv_row"],
            "recorded_official_splits":item["official_splits"],
            "recorded_metadata_sha256":history.get("metadata_sha256"),
            "current_csv_row":current["csv_row"] if current else None,
            "current_official_splits":{c:current["values"].get(c) for c in columns} if current else None,
            "current_metadata_sha256":metadata_hash})
    for item in records:
        if item["source_kind"] != "accident":
            continue
        rows.append({"record_kind":"reviewed_segment", "sample_id":item["sample_id"], "path":item["video_path"],
            "recorded_csv_row":item["csv_row"], "recorded_official_splits":item["recorded_official_splits"],
            "recorded_metadata_sha256":reviewed_metadata_hash, "current_csv_row":item["csv_row"],
            "current_official_splits":item["csv_official_splits"], "current_metadata_sha256":metadata_hash})
    old_paths = {r["path"] for r in rows if r["record_kind"]=="previous_acquisition_candidate"}
    new_paths = {r["path"] for r in rows if r["record_kind"]=="reviewed_segment"}
    unchanged = history.get("metadata_sha256")==metadata_hash==reviewed_metadata_hash
    matching_rows = all(r["recorded_csv_row"]==r["current_csv_row"] and
                       r["recorded_official_splits"]==r["current_official_splits"] for r in rows)
    return {"rows":rows, "overlapping_file_keys":sorted(old_paths & new_paths),
            "same_metadata_bytes":unchanged, "recorded_rows_match_current":matching_rows,
            "conclusion":"different_file_keys_same_metadata_bytes" if unchanged and matching_rows and not old_paths & new_paths
                         else "requires_row_and_version_review",
            "upstream_or_event_identity_deduced":False, "official_release_version":None}


def preflight(config, root):
    """Inspect declared files only; missing approvals never become labels."""
    validate_config(config)
    plan_path = resolve_path(root, config["segment_plan"])
    plan = pp.read_json(plan_path)
    pp.validate_plan(plan)
    metadata_path = resolve_path(root, config["metadata_path"])
    metadata = pp.read_metadata(metadata_path)
    problems, records = [], []
    for key in ("frame_count", "image_size"):
        if config["preprocessing"][key] != plan[key]:
            problems.append("preprocessing_plan_mismatch:" + key)
    split = None
    if config.get("split_manifest"):
        split = pp.read_json(resolve_path(root, config["split_manifest"]))
    if not isinstance(split, dict):
        problems.append("approved_split_manifest_missing")
        split = {}
    if split.get("schema_version") != 1 or not approved_review(split.get("review")):
        problems.append("split_review_not_approved")
    if split.get("plan_sha256") != pp.sha256(plan_path):
        problems.append("split_plan_hash_mismatch")
    if split.get("preprocessing_sha256") != fingerprint(config["preprocessing"]):
        problems.append("model_input_preprocessing_not_reviewed")
    if any(not split.get(key) for key in ("dataset_version", "split_version")):
        problems.append("data_or_split_version_missing")
    column = split.get("official_split_column")
    if column not in ("split_in_distribution", "split_geo_aware"):
        problems.append("official_split_protocol_missing")
    entries = split.get("segments", [])
    if not isinstance(entries, list):
        raise TrainingError("invalid_split_segments")
    assignment = {s.get("sample_id"): s for s in entries if isinstance(s, dict)}
    if len(assignment) != len(entries) or set(assignment) != {s["sample_id"] for s in plan["samples"]}:
        problems.append("split_must_cover_selected_plan_once")
    data_root = resolve_path(root, config["data_root"])
    # Separate maps avoid accidental equal IDs across different ID namespaces.
    groups = {key: {} for key in ("source_sha256", "source_group_id", "upstream_original_id", "event_id")}
    for sample in plan["samples"]:
        sid = sample["sample_id"]
        entry = assignment.get(sid, {})
        matches = metadata.get(sample["video_path"], [])
        target, source_hash = None, None
        try:
            pp.validate_window(sample)
            source = pp.source_path(data_root, sample["video_path"])
            source_hash = pp.sha256(source)
            if source_hash != sample.get("source_sha256"):
                raise TrainingError("source_hash_mismatch")
            target = pp.approved_target(sample, plan["policy_version"], source_hash)
            if target is None:
                problems.append(sid + ":segment_label_unconfirmed")
        except (pp.PreprocessingError, TrainingError) as error:
            problems.append(sid + ":" + str(error))
        except OSError:
            problems.append(sid + ":source_unreadable")
        if len(matches) != 1:
            problems.append(sid + ":metadata_not_unique")
        row = matches[0]["values"] if len(matches) == 1 else {}
        partition = entry.get("split")
        if partition not in ("train", "validation", "test"):
            problems.append(sid + ":split_missing")
        if entry.get("source_sha256") != source_hash:
            problems.append(sid + ":split_source_hash_mismatch")
        if not approved_review(entry.get("group_review")) or not all(entry.get(k) for k in ("source_group_id", "upstream_original_id", "event_id")):
            problems.append(sid + ":source_event_groups_unverified")
        official = row.get(column)
        external_without_official_split = (
            entry.get("source_origin") == "external_real"
            and not any(row.get(k) for k in ("split_in_distribution", "split_geo_aware"))
            and approved_review(entry.get("external_source_review"))
        )
        if not external_without_official_split and (
                official not in ("train", "test") or
                (partition in ("train", "validation") and official != "train") or
                (partition == "test" and official != "test")):
            problems.append(sid + ":official_split_conflict_or_unknown")
        if partition == "train" and entry.get("training_authorized") is not True:
            problems.append(sid + ":training_not_authorized")
        for key, mapping in groups.items():
            value = source_hash if key == "source_sha256" else entry.get(key)
            if value:
                if value in mapping and mapping[value] != partition:
                    problems.append(sid + ":cross_split_leakage:" + key)
                mapping[value] = partition
        records.append({"sample_id": sid, "target": target, "split": partition,
                        "official_split": official, "sample": sample, "assignment": entry,
                        "metadata_entry": matches[0] if len(matches) == 1 else None})
    train = [r for r in records if r["split"] == "train"]
    if {r["target"] for r in train} != {0, 1}:
        problems.append("train_requires_reviewed_normal_and_accident")
    if config["batch_size"] > len(train):
        problems.append("batch_exceeds_train_segments")
    versions = {"training_version": VERSION, "plan_sha256": pp.sha256(plan_path),
                "metadata_sha256": pp.sha256(metadata_path), "split_sha256": fingerprint(split),
                "preprocessing_sha256": fingerprint(config["preprocessing"]),
                "dataset_version": split.get("dataset_version"), "split_version": split.get("split_version"),
                "policy_version": plan["policy_version"], "class_mapping": CLASS_MAPPING}
    return {"status": "ready" if not problems else "held", "issues": sorted(set(problems)),
            "selected_segments": len(records), "approved_segment_labels": sum(r["target"] is not None for r in records),
            "train_segments": len(train), "records": records, "versions": versions,
            "generalization_evaluation": False}


def prepare_train_segments(config, root, report, output):
    """Reuse original bounded decoder in subprocesses, for train segments only."""
    if report["status"] != "ready":
        raise TrainingError("data_not_ready")
    script = Path(__file__).resolve().parents[2] / "scripts/preprocess_samples.py"
    settings = {"policy_version": report["versions"]["policy_version"], "time_basis": "local_clip_seconds",
                "frame_count": config["preprocessing"]["frame_count"], "image_size": config["preprocessing"]["image_size"]}
    output.mkdir(parents=True, exist_ok=False)
    prepared = []
    for record in report["records"]:
        if record["split"] != "train":
            continue
        with tempfile.TemporaryDirectory(prefix=".decode-", dir=output) as temporary:
            temp = Path(temporary)
            request = {"data_root": str(resolve_path(root, config["data_root"])),
                       "directory": str(temp / "result"), "sample": record["sample"], "settings": settings,
                       "metadata_entry": record["metadata_entry"]}
            pp.write_json(temp / "request.json", request)
            try:
                proc = subprocess.run([sys.executable, str(script), "--worker", str(temp / "request.json")],
                                      capture_output=True, text=True, timeout=45)
                result = json.loads(proc.stdout)
            except subprocess.TimeoutExpired:
                raise TrainingError(record["sample_id"] + ":decode_timeout") from None
            except (OSError, ValueError):
                raise TrainingError(record["sample_id"] + ":decode_worker_failed") from None
            if proc.returncode or result.get("status") != "prepared_labeled" or result.get("target") != record["target"]:
                raise TrainingError(record["sample_id"] + ":" + result.get("reason", "decode_or_label_failure"))
            destination = output / record["sample_id"]
            (temp / "result").rename(destination)
            item = {"sample_id": record["sample_id"], "target": record["target"],
                    "array_path": str(destination / "frames.npy"), "frames_path": str(destination / "frames.json"),
                    "array_sha256": pp.sha256(destination / "frames.npy"),
                    "frames_sha256": pp.sha256(destination / "frames.json"),
                    "source_sha256": result["source_sha256"], "source_video_id": result["source_video_id"],
                    "source_path": result["source_path"], "official_splits": result["official_splits"],
                    "time_basis": result["time_basis"], "start_seconds": result["start_seconds"],
                    "end_seconds": result["end_seconds"], "label_review": result["label_review"],
                    "split_assignment": record["assignment"]}
            pp.write_json(destination / "provenance.json", {k:v for k,v in item.items() if k not in ("array_path", "frames_path")})
            prepared.append(item)
    return prepared


def connect_expansion_inputs(manifest, request, metadata, normal_splits, *, previous_root, current_root):
    """Build a train-only proposal from received receipts, without remote I/O or authorization."""
    expected = request['accident_candidates'] + request['normal_candidates']
    rows = manifest['records']
    def check(ok, reason):
        if not ok: raise TrainingError(reason)
    check(manifest.get('purpose') == 'expanded_inputs_not_training' and len(rows) == len(expected) <= 400,
          'expansion_manifest_mismatch')
    check(previous_root != current_root and all(Path(p).is_absolute() for p in (previous_root,current_root)),
          'explicit_distinct_input_roots_required')
    records, held = [], []
    for index,(c,r) in enumerate(zip(expected,rows)):
        check(r['sample_id']==f'expansion_{index:04d}' and all(r.get(k)==c.get(k) for k in
              ('source_path','sequence_id','start_seconds','end_seconds','label_origin')), 'expansion_record_changed')
        check(r['declared_target']==c['target'], 'expansion_target_changed')
        if r['status'] != 'prepared_not_split':
            check(r['target'] is None, 'failed_input_has_target')
            held.append({'sample_id':r['sample_id'],'status':r['status'],'reason':r.get('reason')})
            continue
        check(r['target']==c['target'] and r['input']['target']==c['target'] and
              r['input']['array_shape']==[16,224,224,3], 'expansion_input_mismatch')
        if 'source_path' in c:
            matches=metadata.get(c['source_path'],[])
            check(len(matches)==1 and matches[0]['values']['split_in_distribution']=='train', 'official_test_or_unknown')
            match=matches[0]; point=float(match['values']['accident_time'])
            check(match['csv_row']==c['csv_row'] and point==c['annotation_seconds'] and
                  math.isclose(c['start_seconds'],point-1,rel_tol=0,abs_tol=1e-9) and
                  math.isclose(c['end_seconds'],point+1,rel_tol=0,abs_tol=1e-9), 'annotation_changed')
            check(c['source_path'] not in request['reserved_accident_paths'] and c['target']==1 and
                  c['label_origin']=='source_annotation_derived','reserved_or_wrong_label')
            group='accident-expansion-unresolved'
        else:
            check(c['sequence_id'] in normal_splits['train'] and c['sequence_id'] not in
                  normal_splits['test'] + request['reserved_normal_sequences'] and c['target']==0 and
                  c['label_origin']=='user_assumed_normal', 'reserved_or_wrong_normal_label')
            group='detrac-camera-unresolved'
        origin=r.get('input_origin')
        check(origin in ('previous','current'),'unknown_input_origin')
        root=Path(previous_root if origin=='previous' else current_root)
        if origin=='previous':check(r.get('input_base')==previous_root,'previous_input_base_changed')
        rel=Path(r['input_path'])
        check(rel.as_posix()==r['sample_id']+'/frames.npy','unsafe_array_reference')
        details=r['input'];times=[f['actual_seconds'] for f in details['frames']]
        check(len(times)==16 and all(c['start_seconds']<=t<c['end_seconds'] for t in times) and
              all(a<b for a,b in zip(times,times[1:])), 'invalid_input_timestamps')
        records.append({'sample_id':r['sample_id'],'source_path':c.get('source_path'),
                        'sequence_id':c.get('sequence_id'),'start_seconds':c['start_seconds'],
                        'end_seconds':c['end_seconds'],'target':c['target'],
                        'label_origin':c['label_origin'],'human_reviewed':False,
                        'source_group_id':group,'group_independence':'unconfirmed_conservative_group',
                        'proposed_split':'train','array_path':str(root/rel),
                        'array_sha256':r['frames_sha256'],
                        'frames_path':str(root/rel.parent/'frames.json'),
                        'frames_sha256':hashlib.sha256((json.dumps(details,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()).hexdigest(),
                        'preview_path':str(root/rel.parent/'preview.png'),
                        'remote_files_verified_this_run':False})
    return {'purpose':'expanded_training_input_proposal_not_authorized','records':records,'held':held,
            'class_mapping':CLASS_MAPPING,'class_counts':dict(Counter(r['target'] for r in records)),
            'validation_segments':[],'test_segments':[],'independent_validation':False,
            'training_execution_authorized':False,'status':'paths_connected_remote_verification_pending',
            'notes':['Conservative groups stay train-only; no random segment split.',
                     'Normal labels are user assumptions, not official accident-free annotations.']}


def verify_expansion_input_files(proposal):
    """Hash-check files before existing SegmentDataset use; never start a model."""
    if proposal.get('purpose')!='expanded_training_input_proposal_not_authorized':
        raise TrainingError('expansion_proposal_required')
    for r in proposal['records']:
        for prefix in ('array','frames'):
            path=Path(r[prefix+'_path'])
            if not path.is_file() or pp.sha256(path)!=r[prefix+'_sha256']:
                raise TrainingError('expanded_input_missing_or_changed')
    return proposal['records']


def validate_expansion_execution(config, proposal, *, require_authorization=True):
    validate_config(config)
    expanded603 = config.get('execution_scope') == 'fixed_603_v1'
    if expanded603 and config.get('input_proposal_sha256') != '26493f831fbc5068b452abf3f7c4e1de93f008edd1065945d6d4855ab74274ae':
        raise TrainingError('fixed603_proposal_changed')
    fixed={'purpose':EXPANDED_REAL,'device':'cuda','train_scope':'head','batch_size':1,
           'num_workers':0,'max_epochs':5,'max_steps':3015 if expanded603 else 1910,'max_seconds':1800,
           'probe_steps':3,'probe_max_seconds':60,'initialization':'official_pretrained_fresh_head',
           'learning_rate':0.001,'momentum':0.9}
    if any(config.get(k)!=v for k,v in fixed.items()):
        raise TrainingError('expansion_execution_scope_changed')
    if config['preprocessing']['frame_count']!=16 or config['preprocessing']['image_size']!=224:
        raise TrainingError('expansion_input_shape_changed')
    if require_authorization and config.get('training_execution_authorized') is not True:
        raise TrainingError('expansion_training_requires_explicit_approval')
    if (proposal.get('purpose')!='expanded_training_input_proposal_not_authorized' or
        proposal.get('validation_segments')!=[] or proposal.get('test_segments')!=[] or
        proposal.get('independent_validation') is not False):
        raise TrainingError('expansion_train_only_proposal_required')
    rows=proposal['records']
    expected_count = 603 if expanded603 else 382
    expected_classes = {1:403,0:200} if expanded603 else {1:182,0:200}
    if len(rows)!=expected_count or len({r['sample_id'] for r in rows})!=expected_count or Counter(r['target'] for r in rows)!=expected_classes:
        raise TrainingError('expansion_fixed_class_counts_changed')
    for r in rows:
        expected='source_annotation_derived' if r['target']==1 else 'user_assumed_normal'
        group='accident-expansion-unresolved' if r['target']==1 else 'detrac-camera-unresolved'
        if r.get('label_origin')!=expected or r.get('source_group_id')!=group or r.get('proposed_split')!='train':
            raise TrainingError('expansion_label_or_group_changed')


def merge_expansion_proposals(existing, additional, *, namespace):
    """Combine received inputs without granting execution or resetting old run guards."""
    import copy
    import re
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,40}', namespace):
        raise TrainingError('invalid_input_namespace')
    for p in (existing, additional):
        if (p.get('purpose') != 'expanded_training_input_proposal_not_authorized'
                or p.get('validation_segments') != [] or p.get('test_segments') != []):
            raise TrainingError('train_only_proposals_required')
    rows=copy.deepcopy(existing['records']); ids={r['sample_id'] for r in rows}
    if len(ids)!=len(rows):raise TrainingError('duplicate_input_id')
    old_sources={r.get('source_path') for r in rows}-{None}
    keys={(r.get('source_path'),r.get('sequence_id'),r['start_seconds'],r['end_seconds']) for r in rows}
    if len(keys)!=len(rows):raise TrainingError('duplicate_input_window')
    for original in additional['records']:
        r=copy.deepcopy(original)
        key=(r.get('source_path'),r.get('sequence_id'),r['start_seconds'],r['end_seconds'])
        if key in keys or r.get('source_path') in old_sources:raise TrainingError('existing_source_overlap')
        r['upstream_sample_id']=r['sample_id'];r['sample_id']=namespace+'_'+r['sample_id']
        if r['sample_id'] in ids:raise TrainingError('duplicate_input_id')
        ids.add(r['sample_id']);keys.add(key);rows.append(r)
    return dict(purpose='expanded_training_input_proposal_not_authorized',records=rows,
                held=copy.deepcopy(additional.get('held',[])),class_mapping=CLASS_MAPPING,
                class_counts=dict(Counter(r['target'] for r in rows)),validation_segments=[],test_segments=[],
                independent_validation=False,training_execution_authorized=False,
                status='merged_paths_remote_verification_pending',
                note='Input IDs namespaced; original paths unchanged. Historical execution guards remain unchanged.')


def connect_reviewed_normal_proposal(existing, manifests, roots):
    """Candidate list only. Existing execution guards deliberately remain unchanged."""
    import copy
    if len(existing['records']) != 603 or Counter(r['target'] for r in existing['records']) != {1:403,0:200}:
        raise TrainingError('expected_existing_603')
    if len(manifests)!=2 or len(roots)!=2:
        raise TrainingError('two_preparation_receipts_required')
    rows=copy.deepcopy(existing['records']);old_sources={r.get('source_path') for r in rows}
    seen={r['sample_id'] for r in rows};extras=[]
    for manifest,root in zip(manifests,roots):
        if not Path(root).is_absolute():raise TrainingError('absolute_preparation_root_required')
        for r in manifest['records']:
            if r['status'] not in ('prepared_not_split','prepared_policy_review_only'):continue
            if (r['sample_id'] in seen or r['source_path'] not in old_sources or r['target']!=0
                or r['label_origin']!='user_segment_review' or r['start_seconds']!=0 or r['end_seconds']!=2):
                raise TrainingError('reviewed_normal_receipt_mismatch')
            seen.add(r['sample_id']);repeat=r.get('sampling_policy')=='nearest-bin-repeat-v1'
            d=r['input'];ts=[f['actual_seconds'] for f in d['frames']]
            if (len(ts)!=16 or d['array_shape']!=[16,224,224,3] or any(not 0<=t<2 for t in ts)
                or not all(b>=a if repeat else b>a for a,b in zip(ts,ts[1:]))
                or d['target']!=(None if repeat else 0)):
                raise TrainingError('reviewed_normal_input_mismatch')
            sid=r['sample_id']
            if sid not in ('train_normal_review_01','train_normal_review_02','train_normal_review_03'):
                raise TrainingError('unexpected_reviewed_normal')
            extras.append(dict(sample_id=sid,source_path=r['source_path'],start_seconds=0,end_seconds=2,
                target=0,label_origin='user_segment_review',human_reviewed=True,
                source_group_id=next(x['source_group_id'] for x in rows if x.get('source_path')==r['source_path']),
                proposed_split='train',sampling_policy='nearest-bin-repeat-v1' if repeat else 'strict-distinct-v1',
                array_path=str(Path(root)/sid/'frames.npy'),array_sha256=r.get('array_sha256',r.get('frames_sha256')),
                frames_path=str(Path(root)/sid/'frames.json'),
                frames_sha256=hashlib.sha256((json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()).hexdigest(),
                reviewed_input_details=d,input_quality_review='assistant_preview_checked_user_confirmation_pending',
                remote_files_verified_this_run=False,training_execution_authorized=False))
    if {r['sample_id'] for r in extras}!={'train_normal_review_01','train_normal_review_02','train_normal_review_03'}:
        raise TrainingError('three_reviewed_normals_required')
    return dict(purpose='reviewed_normal_606_proposal_not_authorized',records=rows+sorted(extras,key=lambda r:r['sample_id']),
        class_counts={'accident':403,'normal':203},training_execution_authorized=False,
        validation_segments=[],test_segments=[],independent_validation=False,
        status='candidate_connected_remote_arrays_and_quality_pending')


def validate_reviewed_normal_details(record, details, *, inspection_only=False):
    """Explicit adapter for reviewed normal input; original strict path is untouched."""
    if (record.get('label_origin')!='user_segment_review' or record.get('target')!=0
        or (not inspection_only and record.get('input_quality_review')!='approved') or record.get('proposed_split')!='train'
        or record.get('start_seconds')!=0 or record.get('end_seconds')!=2
        or details != record.get('reviewed_input_details')):
        raise TrainingError('reviewed_normal_quality_or_binding_required')
    times=[f['actual_seconds'] for f in details['frames']]
    repeat=record.get('sampling_policy')=='nearest-bin-repeat-v1'
    if record.get('sampling_policy') not in ('nearest-bin-repeat-v1','strict-distinct-v1'):
        raise TrainingError('unknown_reviewed_sampling_policy')
    if (len(times)!=16 or details['array_shape']!=[16,224,224,3]
        or details['target']!=(None if repeat else 0) or any(not 0<=t<2 for t in times)
        or not all(b>=a if repeat else b>a for a,b in zip(times,times[1:]))):
        raise TrainingError('reviewed_normal_timing_mismatch')


def validate_provisional_normal_details(record, details):
    """Narrow opt-in for the authorized 22 weak labels; never human review."""
    held = {5, 9, 12, 17, 24, 26, 27, 30}
    allowed = {f'normal_batch_{i:02d}' for i in range(1, 31) if i not in held}
    if (record.get('sample_id') not in allowed
        or record.get('label_origin') != 'assistant_observation_provisional_normal'
        or record.get('human_reviewed') is not False
        or record.get('label_status') != 'provisional_user_authorized'
        or record.get('target') != 0 or record.get('proposed_split') != 'train'
        or record.get('input_quality_review') != 'assistant_preview_only_not_user_segment_review'
        or record.get('start_seconds') != 0 or record.get('end_seconds') != 2
        or record.get('sampling_policy') != 'nearest-bin-repeat-v1'
        or details != record.get('reviewed_input_details')):
        raise TrainingError('provisional_normal_scope_or_binding_required')
    times = [f['actual_seconds'] for f in details['frames']]
    if (len(times) != 16 or details['array_shape'] != [16, 224, 224, 3]
        or details['target'] is not None or any(not 0 <= t < 2 for t in times)
        or not all(b >= a for a, b in zip(times, times[1:]))):
        raise TrainingError('provisional_normal_timing_mismatch')


def validate_pre_accident_normal_details(record, details):
    """Explicit v1 assumed-normal adapter; never official/human normal truth."""
    if (record.get('label_origin') != 'user_assumed_pre_accident_normal'
        or record.get('human_reviewed') is not False or record.get('target') != 0
        or record.get('proposed_split') != 'train' or record.get('official_iid_split') != 'train'
        or record.get('policy_version') != 'pre-point-minus4-assumed-normal-v1'
        or record.get('start_seconds') != 0 or record.get('end_seconds') != 2
        or not isinstance(record.get('annotation_seconds'), (int, float))
        or not math.isfinite(record['annotation_seconds']) or record['annotation_seconds'] < 6
        or record.get('sampling_policy') != 'nearest-bin-repeat-v1'
        or details != record.get('reviewed_input_details')):
        raise TrainingError('pre_accident_normal_scope_required')
    times = [f['actual_seconds'] for f in details['frames']]
    if (len(times) != 16 or details['array_shape'] != [16,224,224,3]
        or details['target'] is not None or any(not 0 <= t < 2 for t in times)
        or not all(b >= a for a,b in zip(times,times[1:]))):
        raise TrainingError('pre_accident_normal_input_mismatch')
