"""New classifier identity/report routing; no models, DB or external requests."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from apps.be_worker import analysis_profiles as p, model_catalog as c
from apps.be_worker import flow_service, generation_settings, team_settings
from apps.be_worker.runpod_api import RunpodAPI
from apps.scene_description import ab_scene


class ModelProfilesTests(TestCase):
    def test_previous_model_release_survives_new_catalog_and_contract_change(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            original=json.loads(p._RELEASE_CATALOGS[p.MLOPS_DEPLOYMENT].read_text())
            original['classifiers']={v:c.contract_for_version(v) for v in ('B','R2')}
            current=root/'current.json';current.write_text(json.dumps(original))
            archived=root/'model_releases';archived.mkdir()
            raw=json.dumps(original,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
            (archived/(hashlib.sha256(raw).hexdigest()+'.json')).write_bytes(raw)
            with patch.object(p,'__file__',str(root/'analysis_profiles.py')), \
                    patch.dict(p._RELEASE_CATALOGS,{p.MLOPS_DEPLOYMENT:current}):
                old=p.profile('R2',deployment=p.MLOPS_DEPLOYMENT)
                altered=deepcopy(original)
                altered['execution_adapter_sha256']='8'*64
                altered['classifiers']['R2']['preprocessing']['height']=226
                current.write_text(json.dumps(altered))
                self.assertEqual(p.validate_profile(old),old)
                self.assertEqual(p.companion_profiles(old)['R2'],old)
                self.assertNotEqual(p.profile('R2',deployment=p.MLOPS_DEPLOYMENT),old)
                current.write_text('{}')
                self.assertEqual(p.validate_profile(old),old)

    def test_two_classifiers_keep_the_same_detector_and_separate_reuse_keys(self):
        b = p.profile('B', deployment=p.MLOPS_DEPLOYMENT)
        r = p.profile('R2', deployment=p.MLOPS_DEPLOYMENT)
        self.assertEqual(p.supported_versions(p.MLOPS_DEPLOYMENT), ('B', 'R2'))
        self.assertEqual({k:v for k,v in b['model_files'].items() if 'yolo' in k},
                         {k:v for k,v in r['model_files'].items() if 'yolo' in k})
        self.assertNotEqual(p.gpu_identity('a'*64, b)['gpu_key'], p.gpu_identity('a'*64, r)['gpu_key'])
        self.assertEqual(p.deployment_for_profile(r), p.MLOPS_DEPLOYMENT)
        self.assertEqual(r['classifier'], c.contract_for_version('R2'))
        with self.assertRaises(ValueError):
            p.profile('R2', deployment=p.LEGACY_DEPLOYMENT)

    def test_tampered_preprocessing_and_weights_are_rejected(self):
        for field in ('weights_sha256', 'preprocessing'):
            item = p.profile('R2', deployment=p.MLOPS_DEPLOYMENT)
            item['classifier'][field] = 'changed'
            with self.assertRaises(ValueError):
                p.validate_profile(item)

    def test_health_requires_both_exact_current_profiles(self):
        snapshot = p.profile('R2', deployment=p.MLOPS_DEPLOYMENT)
        health = dict(status='ok', api_schema='runpod-ab-api-v1', storage='s3', gpu_concurrency=1,
            supported_versions=['B','R2'], profiles={v:p.gpu_identity('0'*64,
                p.profile(v,deployment=p.MLOPS_DEPLOYMENT))['profile_sha256'] for v in ('B','R2')})
        api = RunpodAPI('https://example.invalid', 'test-token')
        with patch.object(api, 'request', return_value=health):
            self.assertEqual(api.health(snapshot)['supported_versions'], ['B','R2'])
        health['profiles']['R2'] = health['profiles']['B']
        with patch.object(api, 'request', return_value=health), self.assertRaises(Exception):
            api.health(snapshot)

    def test_r2_keeps_roi_context_and_frame_assets_for_report(self):
        self.assertEqual(ab_scene.asset_kinds({'version':'R2'}), ('clip','frame','roi_clip','context'))
        self.assertEqual(ab_scene.visual_kinds({'version':'R2'}), ('clip','frame','roi_clip'))

    def test_r2_full_report_reaches_scene_and_report_and_comparison_does_not(self):
        snapshot=p.profile('R2',deployment=p.MLOPS_DEPLOYMENT)
        settings=generation_settings.from_environment({},analysis_profile=snapshot)
        row={'worker_job_id':'a'*32,'state':{'analysis_profile':snapshot},
             'handoff':{'scene_configs':['scene-event_000.json']}}
        queue=Mock();queue.store.rows.return_value=[row]
        scenes,reports,previews,emit=Mock(),Mock(),Mock(),Mock()
        with patch.object(flow_service.trial_limits,'check_events'):
            flow_service.process_events(queue,row,'2026-10-04',emit,scenes,reports,
                                        settings=settings,previews=previews)
        scenes.fetch.assert_called_once();scenes.describe.assert_called_once()
        reports.run.assert_called_once();previews.assert_called_once()
        self.assertEqual(emit.call_args.args,('completed',))
        scenes.reset_mock();reports.reset_mock()
        with patch.object(flow_service.trial_limits,'check_events'):
            flow_service.process_events(queue,row,None,emit,scenes,reports,
                                        purpose='gpu_comparison',previews=previews)
        scenes.describe.assert_not_called();reports.run.assert_not_called()
        self.assertEqual(emit.call_args.args,('comparison_completed',))
