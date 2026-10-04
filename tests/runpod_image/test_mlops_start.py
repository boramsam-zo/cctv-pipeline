"""Exercise the Pod entry script with temporary private files and exec intercepted."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class Started(BaseException):
    pass


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.envfile = self.root / 'runtime.env'
        token, creds = self.root / 'token', self.root / 'credentials'
        token.write_text('synthetic-token-' + 'x' * 32)
        creds.write_text('[default]\naws_access_key_id = synthetic-key\naws_secret_access_key = synthetic-secret\n')
        self.envfile.write_text(f'export CCTV_API_TOKEN_FILE={token}\n'
                                f'export AWS_SHARED_CREDENTIALS_FILE={creds}\n'
                                'export S3_BUCKET=synthetic-evidence\n'
                                'export CCTV_MODEL_S3_BUCKET=synthetic-models\n'
                                'export CCTV_R2_MODEL_S3_KEY=models/candidate.pt\n')
        for path in (token, creds, self.envfile):
            path.chmod(0o600)
        source = (Path(__file__).resolve().parents[2] / 'apps/runpod_image/start-model-session.sh').read_text()
        self.source = source.split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0].replace(
            '/workspace/cctv-image-state/service', str(self.root / 'service'))
        self.args = ['synthetic-script', 'mlops-session-20261005-01', str(self.envfile)]

    def run_start(self):
        with patch.object(sys, 'argv', self.args), patch('builtins.print'), \
                patch.object(os, 'execve', side_effect=Started) as execute:
            with self.assertRaises(Started):
                exec(compile(self.source, 'synthetic-startup', 'exec'), {})
            return execute.call_args.args

    def test_exact_limits_private_env_no_model_and_old_name_preserved(self):
        command, argv, env = self.run_start()
        self.assertEqual(command, '/bin/bash')
        self.assertEqual(argv, ['bash', 'apps/runpod_image/start-service.sh'])
        self.assertEqual((env['CCTV_GPU_MAX_JOBS'], env['CCTV_GPU_JOB_SECONDS'], env['CCTV_GPU_TOTAL_SECONDS']), ('2', '300', '900'))
        self.assertEqual(env['CCTV_EXPECTED_GPU'], 'auto')
        self.assertEqual(env['CCTV_ANALYSIS_DEPLOYMENT'], 'mlops-20261004-v1')
        self.assertEqual(env['CCTV_MODEL_S3_BUCKET'], 'synthetic-models')
        self.assertNotIn('synthetic-secret', repr(env))
        with patch.object(sys, 'argv', self.args), patch('builtins.print'), patch.object(os, 'execve') as execute:
            with self.assertRaises(SystemExit):
                exec(compile(self.source, 'synthetic-startup', 'exec'), {})
            execute.assert_not_called()

    def test_executable_env_line_rejected_and_not_executed(self):
        self.envfile.write_text(self.envfile.read_text() + 'echo private-value\n')
        with patch.object(sys, 'argv', self.args), patch('builtins.print'), patch.object(os, 'execve') as execute:
            with self.assertRaises(SystemExit):
                exec(compile(self.source, 'synthetic-startup', 'exec'), {})
            execute.assert_not_called()
        self.assertFalse((self.root / 'service').exists())


if __name__ == '__main__':
    unittest.main()
