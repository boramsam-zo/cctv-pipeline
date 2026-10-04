"""Real Streamlit AppTest; explicitly skipped until isolated viewer install approval."""
import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
HAS_STREAMLIT=importlib.util.find_spec('streamlit') is not None
# First chart/library initialization can exceed six seconds. The external test
# runner still enforces the approved 60-second total budget, including retries.
APP_TIMEOUT_SECONDS=25


@unittest.skipUnless(HAS_STREAMLIT,'Streamlit not installed; UI execution is NOT verified')
class ViewerAppTests(unittest.TestCase):
    def test_saved_views_switch_without_exception(self):
        from streamlit.testing.v1 import AppTest
        from boram3_cctv.results_viewer import PAGES,load_view
        hashes={}
        for key in PAGES:hashes.update(load_view(ROOT,key)['source_sha256'])
        app=AppTest.from_file(str(ROOT/'apps/results_viewer.py'),default_timeout=APP_TIMEOUT_SECONDS).run()
        self.assertEqual(len(app.exception),0)
        for key in PAGES:
            app.radio(key='view').set_value(key).run()
            self.assertEqual(len(app.exception),0,key)
            self.assertEqual(len(app.error),0,key)
            self.assertGreater(len(app.dataframe),0,key)
        for path,digest in hashes.items():
            self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),digest,path)

    def test_preview_switch_and_rerun_keeps_saved_results(self):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(ROOT/'apps/results_viewer.py'),default_timeout=APP_TIMEOUT_SECONDS).run()
        self.assertEqual(app.radio(key='view').value,'midterm')
        app.radio(key='view').set_value('latest').run()
        app.selectbox(key='media_latest').set_value(32).run()
        self.assertEqual(len(app.error),0)
        app.radio(key='view').set_value('training').run()
        app.selectbox(key='media_training').set_value(4).run()
        self.assertEqual(len(app.exception),0)
        app.radio(key='view').set_value('review').run()
        app.selectbox(key='media_review').set_value(2).run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.error),0)
        app.run()
        self.assertEqual(len(app.exception),0)


if __name__ == '__main__':unittest.main()
