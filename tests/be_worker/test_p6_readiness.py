"""Readiness helper command/output checks with Docker/SQL doubles only."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('p6_readiness',ROOT/'scripts/read_p6_readiness.py')
readiness=importlib.util.module_from_spec(spec);spec.loader.exec_module(readiness)
TABLES=['cctv_jobs.queue','cctv_jobs.flows','cctv_scene.descriptions','cctv_scene.report_runs',
        'cctv_rag.chunks','cctv_jobs.analysis_tasks','cctv_jobs.analysis_selections']


class ReadinessTests(unittest.TestCase):
    def run_readiness(self,new_tables):
        present={name:i<5 or new_tables for i,name in enumerate(TABLES)}
        receipts={name:{'count':[2,1,2,2,2,0,0][i],'md5':str(i)*32} for i,name in enumerate(TABLES) if present[name]}
        commands=[]
        def run(args,**kwargs):
            commands.append((list(args),kwargs.get('input')))
            self.assertEqual(kwargs['timeout'],30)
            if args[0:2]==['docker','compose']:
                sql=kwargs['input']
                self.assertIn('BEGIN READ ONLY;',sql);self.assertIn('COMMIT;',sql)
                self.assertIsNone(re.search(r'\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|GRANT|REVOKE|COPY|CALL)\b',sql,re.I))
                if 'to_regclass' in sql:output=present
                elif 'md5' in sql:
                    table=next(name for name in TABLES if 'FROM '+name+' t;' in sql);output=receipts[table]
                elif "state->>'status'" in sql:output=[{'status':'handoff_ready','jobs':2}]
                else:output=[{'status':'completed','paused':False,'finished':True,'jobs':1}] if 'cctv_jobs.flows' in sql else []
                return subprocess.CompletedProcess(args,0,json.dumps(output),'')
            if args[0:2]==['docker','ps']:
                self.assertEqual(args[2:],['-aq','--filter','label=com.docker.compose.project=cctv'])
                return subprocess.CompletedProcess(args,0,'worker-id\nother-id','')
            self.assertEqual(args[0:2],['docker','inspect'])
            service='be-worker' if args[2]=='worker-id' else 'unrelated'
            value={'Name':'/cctv-be-worker-1','Image':'sha256:cached-image',
                   'State':{'Status':'running'},
                   'Config':{'Labels':{'com.docker.compose.service':service},'Image':'cctv-be-queue:pg-v1',
                             'Env':['GEMINI_API_KEY=synthetic-secret-value']},
                   'Mounts':[{'Destination':'/code/apps/be_worker','Source':'/opt/cctv-be-p5/apps/be_worker'},
                             {'Destination':'/spool','Source':'/srv/cctv/be/spool'},
                             {'Destination':'/run/secrets/be_gemini_api_key','Source':'/private/synthetic-secret-path'}]}
            return subprocess.CompletedProcess(args,0,json.dumps([value]),'')
        output=io.StringIO()
        with patch.object(readiness.subprocess,'run',side_effect=run),contextlib.redirect_stdout(output):
            readiness.main()
        raw=output.getvalue();result=json.loads(raw)
        self.assertEqual(result['table_receipts'],receipts)
        self.assertEqual(result['tables'],present)
        self.assertEqual(result['task_states']['queue'],[{'status':'handoff_ready','jobs':2}])
        self.assertEqual(len(result['services']),1)
        self.assertTrue(result['services'][0]['gemini_secret_mounted'])
        self.assertEqual(result['services'][0]['code_and_spool'],{'/code/apps/be_worker':'/opt/cctv-be-p5/apps/be_worker','/spool':'/srv/cctv/be/spool'})
        self.assertNotIn('synthetic-secret',raw)
        self.assertTrue(all(result[key]==0 for key in ('model_calls','s3_requests','database_writes','configuration_changes')))
        return commands,result

    def test_existing_five_tables_with_absent_new_tables_queries_present_only(self):
        commands,result=self.run_readiness(False)
        self.assertNotIn('cctv_jobs.analysis_tasks',result['task_states'])
        queries=[text for args,text in commands if text and 'to_regclass' not in text]
        self.assertFalse(any('analysis_tasks' in q or 'analysis_selections' in q for q in queries))

    def test_zero_new_tasks_and_groups_are_preserved_in_output(self):
        _,result=self.run_readiness(True)
        self.assertEqual(result['table_receipts']['cctv_jobs.analysis_tasks']['count'],0)
        self.assertEqual(result['table_receipts']['cctv_jobs.analysis_selections']['count'],0)
        self.assertEqual(result['task_states']['cctv_jobs.analysis_tasks'],[])

    def test_command_failure_does_not_expose_stderr_or_credentials(self):
        failed=subprocess.CompletedProcess([],1,'','synthetic-secret-from-docker-error')
        with patch.object(readiness.subprocess,'run',return_value=failed):
            with self.assertRaisesRegex(RuntimeError,'^readiness_command_failed$'):
                readiness.main()


if __name__=='__main__':unittest.main()
