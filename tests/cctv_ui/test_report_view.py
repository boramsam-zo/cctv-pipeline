"""Saved-report display guards and metadata fallback without network access."""
import unittest

from apps.cctv_ui.client import APIError
from apps.cctv_ui.report_view import display_markdown, report_pipeline, source_link


class ReportViewTests(unittest.TestCase):
    def test_only_missing_metadata_endpoint_retains_legacy_date_support(self):
        def missing(path):
            self.assertEqual(path, '/report-pipeline')
            raise APIError('not_found', 404)
        self.assertTrue(report_pipeline(missing)['document_reference_date_supported'])
        def denied(path):
            raise APIError('read_failed', 503)
        with self.assertRaises(APIError):
            report_pipeline(denied)

    def test_malformed_capability_is_not_treated_as_date_filter_support(self):
        for metadata in ({}, {'pipeline_id':'team-rag-v1','document_reference_date_supported':'false'}, []):
            with self.subTest(metadata=metadata), self.assertRaises(RuntimeError):
                report_pipeline(lambda path: metadata)

    def test_markdown_display_does_not_change_original_download_text(self):
        raw='## 사고 개요\n<script>x</script>\n![image](https://example.com/pixel)'
        shown=display_markdown(raw)
        self.assertIn('&lt;script&gt;', shown)
        self.assertIn(r'\![image]', shown)
        self.assertIn('<script>',raw)

    def test_source_control_accepts_only_explicit_http_sources(self):
        url='https://example.com/law?article=4#paragraph-3'
        self.assertEqual(source_link(url),url)
        for value in ('javascript:alert(1)', '//example.com', '/path', 'https://name:key@example.com',
                      'https://example.com:bad', 'https://example.com/\nfoo', None):
            with self.subTest(value=value):
                self.assertIsNone(source_link(value))


if __name__ == '__main__':
    unittest.main()
