"""Pure startup guards with temporary private fixtures; no API/model/process launch."""
import ast
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from scripts import start_runpod_demo as start

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL = ROOT / 'apps/runpod_image/start-model-session.sh'
TOKEN = 'not-a-real-token-' + 'x' * 40


class DemoStartTests(unittest.TestCase):
    def config(self):return start.configuration('demo-session-20261006-01',8,5)
    def pins(self):
        return dict(start.release_pins(), official_launcher_sha256=start.sha(OFFICIAL.read_bytes()))
    def code(self):return start.embedded_launcher(OFFICIAL.read_bytes(),self.config(),self.pins())
    def test_default_pins_preserve_active_release_while_new_source_needs_explicit_pins(self):
        from scripts import prepare_runpod_ci
        payloads, _ = prepare_runpod_ci.payloads_for_release(ROOT)
        catalog_path = 'apps/be_worker/mlops-20261004-v1.json'
        current = (ROOT / catalog_path).read_bytes()
        history = (ROOT / 'apps/be_worker/model_releases').glob('*.json')
        self.assertTrue(any(start.sha(path.read_bytes()) == start.CATALOG_SHA for path in history),
                        'The legacy launcher catalog must remain in immutable release history')
        self.assertEqual(payloads['service/' + catalog_path], current)
        self.assertEqual(set(json.loads(current)['classifiers']), {'B', 'R2'})
        self.assertEqual(start.release_pins()['supported_versions'], ['B', 'R2'])
        self.assertNotEqual(start.sha(payloads['service/' + catalog_path]), start.CATALOG_SHA)
        if self.pins()['official_launcher_sha256'] != start.OFFICIAL_SHA:
            with self.assertRaisesRegex(start.LaunchError, 'official_launcher_changed'):
                start.embedded_launcher(OFFICIAL.read_bytes(), self.config())
    def test_policy_names_dates_and_finite_caps(self):
        config=self.config()
        self.assertEqual(config['limits'],{'max_jobs':5,'per_job_seconds':300.0,'whole_service_seconds':28800.0})
        twenty=start.configuration('demo-session-20261006-01',8,20)
        self.assertEqual(twenty['limits'],{'max_jobs':20,'per_job_seconds':300.0,'whole_service_seconds':28800.0})
        self.assertIn("CCTV_GPU_MAX_JOBS='20'",start.embedded_launcher(OFFICIAL.read_bytes(),twenty,self.pins()))
        for name,hours,jobs in (('p6-hour-session-20261006-01',8,5),('demo-session-20260230-01',8,5),
                ('demo-session-20261006-00',8,5),('demo-session-20261006-01',True,5),
                ('demo-session-20261006-01',25,5),('demo-session-20261006-01',0,5),
                ('demo-session-20261006-01',8,21),('demo-session-20261006-01',8,False)):
            with self.subTest(name=name,hours=hours,jobs=jobs),self.assertRaises(start.LaunchError):
                start.configuration(name,hours,jobs)
    def test_only_four_pinned_launcher_substitutions(self):
        raw=OFFICIAL.read_bytes();before=raw.decode().split("<<'PY'\n",1)[1][:-4]
        code=self.code()
        restored=code.replace("r'demo-session-[0-9]{8}-[0-9]{2}'","r'mlops-session-[0-9]{8}-[0-9]{2}'")
        restored=restored.replace("CCTV_GPU_MAX_JOBS='5'","CCTV_GPU_MAX_JOBS='2'")
        restored=restored.replace("CCTV_GPU_TOTAL_SECONDS='28800'","CCTV_GPU_TOTAL_SECONDS='900'")
        restored=restored.replace('Starting B/R2 demo API: 5 jobs, 300 seconds/job, 28800 seconds/session;',
                                  'Starting B/R2 API: 2 jobs, 300 seconds/job, 900 seconds/session;')
        self.assertEqual(restored,before)
        self.assertEqual(OFFICIAL.read_bytes(),raw)
        with self.assertRaisesRegex(start.LaunchError,'official_launcher_changed'):
            start.embedded_launcher(raw+b'\n',self.config(),self.pins())
    def test_lastconv_pins_bind_versions_and_launcher_without_starting(self):
        pins = dict(self.pins(), supported_versions=['B', 'R2', 'LC'], files_verified=26)
        code = start.embedded_launcher(OFFICIAL.read_bytes(), self.config(), pins)
        self.assertIn('Starting B/R2/LC demo API:', code)
        self.assertEqual(start.release_pins()['supported_versions'], ['B', 'R2'])
        for change in ({'supported_versions': ['LC']}, {'files_verified': True},
                       {'verifier_sha256': 'invalid'}, {'unexpected': 'value'}):
            with self.assertRaisesRegex(start.LaunchError, 'release_pins_invalid'):
                start.release_pins(dict(pins, **change))
    def test_validation_prefix_cannot_create_or_launch(self):
        prefix=start.validation_code(self.code())
        calls=[node.func.attr for node in ast.walk(ast.parse(prefix))
               if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)]
        self.assertFalse(set(calls)&{'mkdir','execve','Popen','unlink','write_text','write_bytes'})
        with self.assertRaises(start.LaunchError):start.validation_code('not the official boundary')
    def private_fixture(self,root):
        root.chmod(0o700)
        token=root/'token';token.write_text(TOKEN);token.chmod(0o600)
        creds=root/'credentials';creds.write_text('[default]\naws_access_key_id = TESTFIXTURE\naws_secret_access_key = TESTFIXTURESECRET\n');creds.chmod(0o600)
        runtime=root/'runtime.env'
        runtime.write_text(f'export CCTV_API_TOKEN_FILE={token}\nexport AWS_SHARED_CREDENTIALS_FILE={creds}\n'
            'export S3_BUCKET=test-fixture-bucket\nexport CCTV_MODEL_S3_BUCKET=test-fixture-bucket\n'
            'export CCTV_R2_MODEL_S3_KEY=models/candidate.pt\n')
        runtime.chmod(0o600)
        return runtime
    def test_original_private_validation_keeps_permissions_and_shell_rejection(self):
        with TemporaryDirectory() as directory:
            root=Path(directory).resolve();runtime=self.private_fixture(root)
            original={p.name:p.read_bytes() for p in root.iterdir()}
            self.assertEqual(start.validate_private_without_start(self.code(),self.config()['name'],runtime),TOKEN)
            self.assertEqual({p.name:p.read_bytes() for p in root.iterdir()},original)
            runtime.chmod(0o644)
            with self.assertRaisesRegex(start.LaunchError,'private_runtime_validation_failed'):
                start.validate_private_without_start(self.code(),self.config()['name'],runtime)
            runtime.chmod(0o600)
            runtime.write_text(runtime.read_text()+'export AWS_REGION=$(whoami)\n')
            with self.assertRaises(start.LaunchError):
                start.validate_private_without_start(self.code(),self.config()['name'],runtime)
    def test_health_summary_marks_expiry_exhaustion_without_echoing_extras(self):
        value={'limits':self.config()['limits'],'admissions':5,'deadline_at':10,'token':TOKEN}
        summary=start.health_summary(value,20)
        self.assertTrue(summary['expired']);self.assertTrue(summary['exhausted'])
        self.assertNotIn(TOKEN,json.dumps(summary))
        for change in ({'deadline_at':float('inf')},{'admissions':True},{'admissions':6}):
            with self.assertRaises(start.LaunchError):start.health_summary(dict(value,**change),20)
    def test_existing_api_plan_never_stops_process_and_reads_only_status_counts(self):
        with TemporaryDirectory() as directory:
            base=Path(directory).resolve();proc=base/'proc';proc.mkdir();entry=proc/'123';entry.mkdir()
            jobs=base/'sessions'/'demo-session-20261006-00'/'jobs';jobs.mkdir(parents=True)
            (entry/'cmdline').write_bytes(b'python\0apps.runpod_image.service:create_app\0')
            (entry/'environ').write_bytes(('CCTV_SERVICE_ROOT='+str(jobs)+'\0AWS_SECRET_ACCESS_KEY='+TOKEN+'\0').encode())
            for n,status in enumerate(('succeeded','failed','running')):
                job=jobs/str(n);job.mkdir();(job/'status.json').write_text(json.dumps({'status':status,'secret':TOKEN}))
            health={'limits':self.config()['limits'],'admissions':5,'deadline_at':0}
            with patch.object(start,'SESSION_BASE',base/'sessions'),patch.object(start.socket,'create_connection') as socket,\
                 patch.object(start,'local_health',return_value=health),patch.object(start.subprocess,'Popen') as spawn:
                socket.return_value.__enter__.return_value=None
                result=start.existing_api(TOKEN,proc=proc)
            self.assertTrue(result['present']);self.assertTrue(result['expired']);self.assertTrue(result['exhausted'])
            self.assertEqual(result['in_flight_jobs'],1);self.assertFalse(result['automatic_stop'])
            self.assertNotIn(TOKEN,json.dumps(result));spawn.assert_not_called()
    def test_apply_refuses_existing_api_before_writing_or_launching(self):
        with patch.object(start,'inspect',return_value=({'can_apply':False},'',TOKEN)),\
             patch.object(start.subprocess,'Popen') as spawn,patch.object(start,'write_exclusive') as write:
            with self.assertRaisesRegex(start.LaunchError,'existing_api_preserved'):
                start.apply(self.config(),'/private/runtime.env')
        spawn.assert_not_called();write.assert_not_called()
    def test_exclusive_private_files_never_overwrite_or_follow_symlink(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);path=root/'intent.json'
            start.write_exclusive(path,{'attempt':1})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            with self.assertRaises(FileExistsError):start.write_exclusive(path,{'attempt':2})
            self.assertEqual(json.loads(path.read_text()),{'attempt':1})
            link=root/'alias.json';link.symlink_to(path)
            with self.assertRaises(FileExistsError):start.write_exclusive(link,{'attempt':2})
