"""Pinned accident classifiers; release/session policy controls availability."""
from copy import deepcopy
import hashlib
import json

SCHEMA = 'cctv-classifier-contract-v1'
DEPLOYMENT = 'mlops-20261004-v1'
VERSIONS = ('B', 'R2', 'LC')
_COMMON = dict(schema_version=SCHEMA, class_mapping={'normal': 0, 'accident': 1})
_CONTRACTS = {
    'B': dict(_COMMON, model_id='x3d_s', model_version='operating-epoch7',
        weights_sha256='ca2db4a25c9cd2d859a7143932d911a3351beacc5193de3ada16f3115bea09bf',
        weights_bytes=36344683, checkpoint_key='model_state_dict',
        preprocessing=dict(frames=16, height=182, width=182, color='RGB',
            resize='cv2_inter_area_stretch', mean=[.45, .45, .45], std=[.225, .225, .225],
            temporal_sampling='operating_window_start_uniform', window_seconds=2, stride_seconds=1,
            window_coverage='include_shifted_final', short_video='pad_existing'),
        decision_policy=dict(method='accident_score_gte', threshold=.5, tie='accident',
                             event_merge_gap_seconds=4)),
    'R2': dict(_COMMON, model_id='r2plus1d_18', model_version='sotad-head-epoch7',
        weights_sha256='c40c150ea70cd422edc546c9d110b138c87cba67a277c5c4d65353ba04478b46',
        weights_bytes=125379501, checkpoint_key='model_state',
        preprocessing=dict(frames=16, height=224, width=224, color='RGB',
            resize='pil_bilinear_letterbox_black', mean=[.43216, .394666, .37645],
            std=[.22803, .22145, .216989], temporal_sampling='pts_bin_centers_no_future_boundary',
            window_seconds=2, stride_seconds=1, window_coverage='complete_windows_only',
            short_video='reject_less_than_window'),
        decision_policy=dict(method='argmax', threshold=.5, tie='normal',
                             event_merge_gap_seconds=4)),
    'LC': dict(_COMMON, model_id='r3d_18', model_version='lastconv631-epoch3',
        weights_sha256='6139ac8597f4c6e6a3fd5a632fc490e636e7188d076739e29f841f3ca3da0558',
        weights_bytes=132769867, checkpoint_key='model_state',
        preprocessing=dict(frames=16, height=224, width=224, color='RGB',
            resize='pil_bilinear_letterbox_black', mean=[.43216, .394666, .37645],
            std=[.22803, .22145, .216989], temporal_sampling='pts_bin_centers_no_future_boundary',
            window_seconds=2, stride_seconds=1, window_coverage='complete_windows_only',
            short_video='reject_less_than_window'),
        decision_policy=dict(method='argmax', threshold=.5, tie='normal',
                             event_merge_gap_seconds=4)),
}


def contract_for_version(version):
    if not isinstance(version, str) or version not in _CONTRACTS:
        raise ValueError('classifier_version_not_supported')
    return deepcopy(_CONTRACTS[version])


def contract_sha256(contract):
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_contract(contract, version):
    expected = contract_for_version(version)
    if contract_sha256(contract) != contract_sha256(expected):
        raise ValueError('classifier_contract_mismatch')
    return expected


def is_b_pipeline(version):
    return version in VERSIONS
