"""A/B settings constructor contracts only; no providers or media are opened."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from apps.be_worker import analysis_profiles, generation_settings as settings, report_jobs
from apps.be_worker.common import WorkerError
from apps.scene_description import ab_scene, scene


class ABGenerationSettingsTests(unittest.TestCase):
    def test_distinct_ab_request_contracts_keep_legacy_unchanged(self):
        old=settings.legacy_settings()
        a=settings.from_environment({},analysis_profile=analysis_profiles.profile('A'))
        b=settings.from_environment({},analysis_profile=analysis_profiles.profile('B'))
        self.assertEqual(old,settings.from_environment({}))
        for version,value in (('A',a),('B',b)):
            self.assertEqual(value['schema_version'],'be-generation-settings-ab-v1')
            self.assertEqual(value['evidence_version'],'runpod-evidence-v2')
            self.assertEqual(value['analysis_version'],version)
            self.assertEqual(value['scene_prompt_version'],'cctv-scene-ab-v1')
            self.assertEqual(settings.validate(json.loads(json.dumps(value))),value)
            for key in ('report_prompt_sha256','report_prompt_version','query_sha256','embedding_profile'):
                self.assertEqual(value[key],old[key])
        self.assertNotEqual(a['scene_prompt_sha256'],b['scene_prompt_sha256'])
        self.assertNotEqual(a['scene_prompt_sha256'],old['scene_prompt_sha256'])

    def test_saved_models_frozen_across_environment_changes_without_secrets(self):
        with patch.object(settings,'read_generation_key',side_effect=AssertionError('no key')):
            a=settings.from_environment({settings.SCENE_MODEL_ENV:'gemini-first','GEMINI_API_KEY':'not-read'},
                                        analysis_profile=analysis_profiles.profile('A'))
            b=settings.from_environment({settings.SCENE_MODEL_ENV:'gemini-second'},
                                        analysis_profile=analysis_profiles.profile('A'))
            self.assertEqual(settings.validate(a)['scene_model'],'gemini-first')
        self.assertNotEqual(a['scene_model'],b['scene_model'])
        self.assertNotIn('not-read',json.dumps(a))
        self.assertFalse(any('key' in key or 'secret' in key for key in a))

    def test_cross_version_legacy_unknown_and_tampered_profile_rejected(self):
        a=settings.from_environment({},analysis_profile=analysis_profiles.profile('A'))
        bad=analysis_profiles.profile('A');bad['execution_settings']['batch_size']=2
        for snapshot,profile in [(a,analysis_profiles.profile('B')),(a,bad),
                                 (settings.legacy_settings(),analysis_profiles.profile('A'))]:
            with self.subTest(snapshot=snapshot['schema_version']),self.assertRaises(WorkerError):
                settings.validate(snapshot,analysis_profile=profile)
        for field,value in [('analysis_version','C'),('evidence_version','runpod-evidence-v1'),
                            ('scene_prompt_version','cctv-scene-v2'),('scene_fps',4.0)]:
            changed=dict(a,**{field:value})
            with self.subTest(field=field),self.assertRaises(WorkerError):settings.validate(changed)
        with self.assertRaises(WorkerError):settings.from_environment({},analysis_profile={})

    def test_actual_ab_prompt_schema_constructor_drift_rejected(self):
        snapshot=settings.from_environment({},analysis_profile=analysis_profiles.profile('B'))
        for name,value in [('SYSTEM',ab_scene.SYSTEM+' changed'),('PROMPT_VERSION','future')]:
            with self.subTest(name=name),patch.object(ab_scene,name,value),self.assertRaises(WorkerError):
                settings.validate(snapshot)
        schema=deepcopy(scene.RESPONSE_SCHEMA);schema['properties']['summary']['description']='changed'
        with patch.object(scene,'RESPONSE_SCHEMA',schema),self.assertRaises(WorkerError):settings.validate(snapshot)
        with patch.object(ab_scene,'build_request',return_value=b'changed'),self.assertRaises(WorkerError):
            settings.validate(snapshot)

    def test_ab_uses_actual_common_report_query_contracts(self):
        snapshot=settings.from_environment({},analysis_profile=analysis_profiles.profile('A'))
        for name,value in [('report_request',{'changed':True}),('query_from_description','changed')]:
            with patch.object(report_jobs,name,return_value=value),self.assertRaises(WorkerError):
                settings.validate(snapshot)

    def test_extra_missing_fields_and_boolean_fps_rejected(self):
        snapshot=settings.from_environment({},analysis_profile=analysis_profiles.profile('B'))
        for changed in (dict(snapshot,extra='no'),dict(snapshot,scene_fps=True),
                        {k:v for k,v in snapshot.items() if k!='analysis_version'}):
            with self.assertRaises(WorkerError):settings.validate(changed)


if __name__=='__main__':unittest.main()
