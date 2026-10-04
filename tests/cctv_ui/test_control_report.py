"""Lossless saved-report presentation and exact export boundaries."""
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from apps.cctv_ui.control_report import report_html, split_sections

ROOT = Path(__file__).resolve().parents[2]


def team_reports():
    snapshot = json.loads((ROOT / 'apps/cctv_ui/data/saved-control-room.json').read_text())
    return [(j['worker_job_id'], r['state']['result']) for j in snapshot['jobs']
            for r in j.get('reports', []) if r.get('state', {}).get('result', {}).get('report_markdown')]


class Text(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.parts = []; self.links = []; self.ids = []
        self.feed(html)
    def handle_data(self, value):
        self.parts.append(value)
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs: self.ids.append(attrs['id'])
        if tag == 'a': self.links.append(attrs['href'])


class ReportPresentationTests(unittest.TestCase):
    def test_both_real_reports_keep_each_fact_and_entire_statute(self):
        for job, result in team_reports():
            raw = result['report_markdown']
            html = report_html(raw, job, result['report_id'], result['report_json']['references'])
            doc = Text(html)
            visible = re.sub(r'[\s:—]+', '', ''.join(doc.parts))
            # Every line after the saved title survives layout. Formatting delimiters
            # (headings/list/field separators and citation brackets) alone may differ.
            for line in raw.splitlines()[1:]:
                line = re.sub(r'^(?:#{2,3} |- )', '', line)
                line = re.sub(r'\[(R\d+)\]', r'\1', line)
                line = line.replace(' / 영상 근거: ', '')
                self.assertIn(re.sub(r'[\s:—]+', '', line), visible)
            for ref in result['report_json']['references']:
                for line in ref['content'].splitlines():
                    self.assertIn(re.sub(r'[\s:—]+', '', line), visible)
            self.assertEqual(len(split_sections(raw)), 6)
            self.assertTrue(all(link[1:] in doc.ids for link in doc.links if link.startswith('#')))
            self.assertNotIn('R3', split_sections(raw)[3][1])
            self.assertEqual(raw, result['report_markdown'])

    def test_unfamiliar_template_falls_back_without_fake_sections(self):
        self.assertIsNone(report_html('# 과거 보고\n본문 유지', 'job', 'report'))
        raw = team_reports()[0][1]['report_markdown']
        self.assertIsNone(report_html(raw.replace('## 사고 개요', '## 다른 절'), 'job', 'report'))

    def test_unrecognized_lines_survive_and_saved_html_is_inert(self):
        job, report = team_reports()[0]
        raw = report['report_markdown'].replace('## 관련 근거', '추가 설명 <img src=x onerror=alert(1)>\n\n## 관련 근거')
        html = report_html(raw, job, report['report_id'])
        self.assertNotIn('<img', html)
        self.assertIn('&lt;img', html)
        self.assertIn('추가 설명', html)
        self.assertNotIn('<script', html)

    def test_case_specific_anchors_prevent_cross_report_links(self):
        reports = team_reports()
        docs = [Text(report_html(r['report_markdown'], j, r['report_id'])) for j, r in reports]
        self.assertTrue(set(docs[0].ids).isdisjoint(docs[1].ids))


if __name__ == '__main__':
    unittest.main()
