"""Synthetic response only; no media, provider, or model execution."""
from copy import deepcopy
import unittest

from apps.scene_description.scene import validate_description


class ModelAssetTests(unittest.TestCase):
    def test_fixed_b_roi_reference_is_valid_for_b_and_r2_only(self):
        value = {'accident_suspicion': 'uncertain', 'incident_types': [],
                 'summary': '합성 연결 검사', 'uncertainties': ['모델 추론 아님'],
                 'observations': [{'text': '합성 관찰', 'clip_times_seconds': [1.],
                                   'asset_refs': ['roi_clip']}]}
        event = {'evidence_version': 'runpod-evidence-v2',
                 'source_start_s': 2., 'source_end_s': 5.}
        for version in ('B', 'R2'):
            result = validate_description(deepcopy(value), dict(event, version=version))
            self.assertEqual(result['observations'][0]['source_times_seconds'], [3.])
        for version in ('A', None):
            with self.assertRaisesRegex(ValueError, 'evidence reference'):
                validate_description(deepcopy(value), dict(event, version=version))


if __name__ == '__main__':
    unittest.main()
