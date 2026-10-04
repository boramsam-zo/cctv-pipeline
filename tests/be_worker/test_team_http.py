"""HTTP handler boundary checks through request doubles; no sockets/cloud."""
from contextlib import nullcontext
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from apps.be_worker import web_api as api, team_settings


def handler(path, body=b'x'):
    value=object.__new__(api.Handler)
    value.path=path
    value.headers={'Content-Type':'video/mp4','Content-Length':str(len(body))}
    value.rfile=BytesIO(body)
    value.send=lambda data,status=200,**kwargs: replies.append((status,data))
    replies=[]
    return value,replies


class TeamHttpTests(unittest.TestCase):
    def test_metadata_does_not_open_database_or_runpod(self):
        req,replies=handler('/report-pipeline')
        with patch.dict(os.environ,{'BE_REPORT_PIPELINE':team_settings.PIPELINE}), \
                patch.object(api,'connect') as conn,patch.object(api,'connection_status') as provider:
            req.do_GET()
        self.assertEqual(replies,[(200,{'pipeline_id':'team-rag-v1','document_reference_date_supported':False})])
        conn.assert_not_called();provider.assert_not_called()

    def test_old_metadata_reports_original_date_support(self):
        req,replies=handler('/report-pipeline')
        with patch.dict(os.environ,{},clear=True):req.do_GET()
        self.assertEqual(replies[0][1]['pipeline_id'],'legacy')
        self.assertTrue(replies[0][1]['document_reference_date_supported'])

    def test_team_explicit_date_is_rejected_before_upload_or_db(self):
        for suffix in ('&reference_date=2026-10-03','&reference_date='):
            req,replies=handler('/selections?selection=B&purpose=full_report'+suffix)
            with patch.dict(os.environ,{'BE_REPORT_PIPELINE':team_settings.PIPELINE}),patch.object(api,'connect') as conn:
                req.do_POST()
            self.assertEqual(replies,[(400,{'error':'team_date_filter_unsupported'})])
            conn.assert_not_called();self.assertEqual(req.rfile.tell(),0)

    def test_team_no_date_reaches_admission_as_none(self):
        req,replies=handler('/selections?selection=B&purpose=full_report')
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'BE_REPORT_PIPELINE':team_settings.PIPELINE}), \
                patch.object(api.flow,'spool',return_value=Path(root)), \
                patch.object(api,'connect',return_value=nullcontext(object())),patch.object(api,'Store'), \
                patch.object(api.flow,'admit_selection',return_value={'accepted':True}) as admit:
            req.do_POST()
        self.assertEqual(replies,[(202,{'accepted':True})])
        self.assertEqual(admit.call_args.args[2:],(None,'B','full_report'))

    def test_legacy_full_report_still_requires_date(self):
        req,replies=handler('/selections?selection=B&purpose=full_report')
        with patch.dict(os.environ,{},clear=True),patch.object(api,'connect') as conn:req.do_POST()
        self.assertNotEqual(replies[0][0],202);conn.assert_not_called()


if __name__=='__main__':unittest.main()
