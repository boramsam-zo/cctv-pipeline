"""Retirement driver guards; no SSH, signals, API calls or actual subprocesses."""
import ast
from copy import deepcopy
import hashlib
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

DRIVER=Path(__file__).resolve().parents[2]/'artifacts/mlops/public-demo-20261006-01/prepared/run_pod_demo.py'


def definitions():
    spec=importlib.util.spec_from_file_location('pod_demo_driver',DRIVER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    tree=ast.parse(module.REMOTE)
    tree.body=[node for node in tree.body if isinstance(node,(ast.Import,ast.ImportFrom,ast.FunctionDef))]
    namespace={};exec(compile(tree,'<local-driver-guard>','exec'),namespace)
    return namespace


class DemoDriverGuardTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name).resolve();root.chmod(0o700)
        helper=root/'helper.py';helper.write_bytes(b'fixture');helper.chmod(0o600)
        self.namespace=definitions()
        self.existing={'present':True,'authenticated_health':True,'expired':True,'exhausted':True,
            'in_flight_jobs':0,'processes':[{'pid':123}],'limits':{'max_jobs':1},'admissions':1,'deadline_at':1}
        self.plan={'plan_sha256':'a'*64,'existing_api':self.existing}
        self.namespace.update(ACTION='retire-expired',PRIVATE=str(root),REMOTE_HELPER=str(helper),POD='fixturepod123',
            HELPER_SHA=hashlib.sha256(b'fixture').hexdigest(),SESSION='demo-session-20261006-01',
            REVIEWED=deepcopy(self.plan),plan=lambda:self.plan)
        original=Path.read_bytes
        self.read=lambda path: b'RUNPOD_POD_ID=fixturepod123\0' if str(path)=='/proc/1/environ' else original(path)
    def test_non_idle_unknown_unexpired_or_changed_process_is_never_signalled(self):
        cases=({'in_flight_jobs':1},{'in_flight_jobs':None},{'authenticated_health':False},
               {'expired':False,'exhausted':False},{'processes':[]},{'processes':[{'pid':456}]},
               {'deadline_at':2},{'admissions':0})
        for change in cases:
            self.plan['existing_api']=dict(self.existing,**change)
            with self.subTest(change=change),patch.object(Path,'read_bytes',self.read),\
                 patch.object(os,'pidfd_open',create=True) as fd:
                with self.assertRaises(ValueError):self.namespace['main']()
                fd.assert_not_called()
    def test_old_review_hash_mismatch_rejects_before_process_handle(self):
        self.plan['plan_sha256']='b'*64
        with patch.object(Path,'read_bytes',self.read),patch.object(os,'pidfd_open',create=True) as fd:
            with self.assertRaisesRegex(ValueError,'reviewed_plan_changed'):self.namespace['main']()
            fd.assert_not_called()
