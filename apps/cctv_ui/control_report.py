"""Presentation of stored reports. No retrieval, inference or persistence.

The six-section template is parsed only for layout. The export always receives
its untouched source; unfamiliar formats fall back to the full saved text.
"""
from html import escape
import hashlib
import re

import streamlit as st

from .report_view import source_link

TITLES = ('사고 개요', '영상 관찰 및 판단 불가 항목', '관제의 전달·요청 사항',
          '현장 대응 참고', '확인 필요 사항', '관련 근거')

_COPY = st.components.v2.component(
    'cctv_report_copy',
    html='''<button type="button" id="copy">원문 복사</button>
<div id="feedback" role="status" aria-live="polite"></div>
<div id="manual" hidden><label for="raw">저장 Markdown 원문 · 전체 선택 후 ⌘C / Ctrl+C</label>
<textarea id="raw" readonly></textarea><button type="button" id="close">원문 닫기</button></div>''',
    css='''
:host { font-family: var(--st-font, sans-serif); color: var(--st-text-color, #1d2b45); }
button { font: inherit; font-size: 12px; padding: 6px 10px; min-height: 30px;
  border: 1px solid #dde5ef; border-radius: 7px; background: #fff; color: #1d2b45; cursor: pointer; }
button:hover { border-color: #386cac; color: #386cac; }
button:focus-visible { outline: 2px solid #386cac; outline-offset: 2px; }
#feedback, label { font-size: 11px; line-height: 1.5; }
#feedback:not(:empty) { margin-top: 5px; }
#manual { margin-top: 8px; }
textarea { width: 100%; box-sizing: border-box; min-height: 210px; font: 12px/1.5 monospace; }
''',
    js='''
export default function ({data, parentElement}) {
  const copy = parentElement.querySelector('#copy');
  const feedback = parentElement.querySelector('#feedback');
  const manual = parentElement.querySelector('#manual');
  const raw = parentElement.querySelector('#raw');
  raw.value = data.markdown;
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(data.markdown);
      feedback.textContent = '저장 Markdown 원문을 복사했습니다.';
      manual.hidden = true;
    } catch (_) {
      manual.hidden = false;
      raw.focus(); raw.select();
      feedback.textContent = '브라우저가 자동 복사를 허용하지 않았습니다. 선택된 원문을 ⌘C / Ctrl+C로 복사하세요.';
    }
  };
  parentElement.querySelector('#close').onclick = () => { manual.hidden = true; copy.focus(); };
}
''',
)


def report_actions(markdown, worker_job_id, report_id, reason):
    """Call only after the selected report's identity has been validated."""
    available = isinstance(markdown, str) and bool(markdown.strip())
    token = hashlib.sha256((worker_job_id + ':' + report_id).encode()).hexdigest()[:20]
    title, copy, save = st.columns([6, 1.3, 1.6], gap='small', vertical_alignment='top')
    with title:
        st.header('보고서와 문서 근거', anchor=False)
    with copy:
        if available:
            _COPY(data={'markdown': markdown}, key='report_copy_' + token, width='stretch')
        else:
            st.button('원문 복사', disabled=True, help=reason, key='report_copy_disabled_' + token)
    with save:
        st.download_button('Markdown 저장', markdown if available else '',
            file_name='report-' + report_id + '.md', mime='text/markdown',
            disabled=not available, help=None if available else reason,
            key='control_report_md_' + token, on_click='ignore', icon=':material/download:')
    st.caption('복사·저장은 선택한 사건의 저장 Markdown 원문 전체를 사용합니다.' if available else reason)


def split_sections(markdown):
    matches = list(re.finditer(r'^## ([^\n]+)\n', markdown, re.M))
    if tuple(m.group(1) for m in matches) != TITLES:
        return None
    return [(m.group(1), markdown[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(markdown)])
            for i, m in enumerate(matches)]


def _blocks(text):
    """Keep any unexpected preamble instead of dropping unrecognized text."""
    matches = list(re.finditer(r'^### ([^\n]+)\n', text, re.M))
    if not matches:
        return [('', text)]
    result = [('', text[:matches[0].start()])] if text[:matches[0].start()].strip() else []
    return result + [(m.group(1), text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)])
                     for i, m in enumerate(matches)]


def report_html(markdown, worker_job_id, report_id, references=()):
    """Escape all saved text and create only local anchors / checked source URLs."""
    sections = split_sections(markdown)
    if sections is None:
        return None
    prefix = 'report-' + hashlib.sha256((worker_job_id + ':' + report_id).encode()).hexdigest()[:20]
    ref_ids = set(re.findall(r'^### \[(R\d+)\]', sections[5][1], re.M))

    def inline(value):
        text = escape(value)
        return re.sub(r'\[(R\d+)\]', lambda m: (
            f'<a class="cc-citation" href="#{prefix}-{m[1]}" target="_self" '
            f'aria-label="{m[1]} 문서 근거로 이동">{m[1]}</a>' if m[1] in ref_ids else m[0]), text)

    def prose(value):
        out = []
        for line in value.splitlines():
            line = line.strip()
            if not line:
                continue
            bullet = re.match(r'^(- |\d+\. )(.*)', line)
            if bullet:
                # Retain each explicit number; no auto-renumbering of statute clauses.
                out.append('<div class="cc-prose-item"><span>' + escape(bullet[1]) +
                           '</span><span>' + inline(bullet[2]) + '</span></div>')
            else:
                out.append('<p>' + inline(line) + '</p>')
        return ''.join(out)

    def fields(value):
        out = []
        for line in value.splitlines():
            line = line.strip().removeprefix('- ')
            if not line:
                continue
            label, sep, body = line.partition(': ')
            if not sep:
                out.append(prose(line))
                continue
            full = ' cc-field-full' if label in ('전달', '요청', '대응 참고 근거') else ''
            if label == '출처':
                url = source_link(body)
                content = (f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(body)}</a>'
                           if url else escape(body))
            else:
                content = inline(body)
            out.append(f'<div class="cc-report-field{full}"><dt>{escape(label)}</dt><dd>{content}</dd></div>')
        return '<dl class="cc-report-fields">' + ''.join(out) + '</dl>'

    nav, rendered = [], []
    for index, (title, body) in enumerate(sections, 1):
        anchor = f'{prefix}-section-{index}'
        nav.append(f'<a href="#{anchor}" target="_self"><span>{index}</span>{escape(title)}</a>')
        content = ''
        if index == 1:
            lines = body.splitlines()
            content = prose('\n'.join(x for x in lines if not x.startswith('- ')))
            content += fields('\n'.join(x for x in lines if x.startswith('- ')))
        elif index == 2:
            rows, extra = [], []
            for line in body.splitlines():
                if not line.strip():
                    continue
                match = re.fullmatch(r'- (.*?): (.*?) / 영상 근거: (.*)', line)
                if match:
                    status_class = ' class="cc-observed"' if match[2] == '관찰됨' else ''
                    rows.append(f'<tr><td>{inline(match[1])}</td><td{status_class}>{inline(match[2])}</td><td>{inline(match[3])}</td></tr>')
                else:
                    extra.append(line)
            content = ('<table class="cc-observation-table"><thead><tr><th>항목</th><th>영상 관찰 상태</th><th>영상 근거</th></tr></thead><tbody>'
                       + ''.join(rows) + '</tbody></table>' + prose('\n'.join(extra)))
        elif index in (3, 4):
            cards = []
            for heading, text in _blocks(body):
                agency, sep, status = heading.partition(' — ')
                badge = (f'<span class="cc-report-badge{" amber" if "조건부" in status else ""}">{escape(status)}</span>' if sep else '')
                cards.append('<article class="cc-report-agency"><h4>' + escape(agency) + '</h4>' + badge +
                             (fields(text) if index == 3 else prose(text)) + '</article>')
            content = '<div class="cc-agency-grid' + (' cc-action-grid' if index == 4 else '') + '">' + ''.join(cards) + '</div>'
        elif index == 5:
            checks = [f'<li><span class="cc-check-mark">!</span><span>{inline(x[2:])}</span></li>'
                      for x in body.splitlines() if x.startswith('- ')]
            content = '<ul class="cc-check-grid">' + ''.join(checks) + '</ul>'
            content += prose('\n'.join(x for x in body.splitlines() if not x.startswith('- ')))
        else:
            cards = []
            metadata = {r.get('reference_id'): r for r in references if isinstance(r, dict)}
            for heading, text in _blocks(body):
                match = re.match(r'^\[(R\d+)\]\s*(.*)', heading)
                rid, name = (match[1], match[2]) if match else ('', heading)
                lines = text.splitlines()
                # Only known metadata labels leave the statute body. Unknown bullets stay visible.
                meta = [x for x in lines if re.match(r'^(청크: |- (종류|본문 성격|원문 조치 주체|출처|보고서 용도): )', x)]
                statute = '\n'.join(x for x in lines if x not in meta)
                extra = metadata.get(rid, {})
                more = '\n'.join(f'{label}: {extra[key]}' for key, label in (
                    ('source_version', '저장 문서 판'), ('verification_status', '출처 확인 기록')) if extra.get(key))
                purpose = next((x.removeprefix('- 보고서 용도: ') for x in meta if x.startswith('- 보고서 용도: ')),
                               '행정규칙 · 원문에 명시된 주체·조건 확인')
                cards.append(f'<article class="cc-source-card" id="{prefix}-{rid}"><div class="cc-source-heading"><span class="cc-source-tag">{escape(rid)}</span><h4>{escape(name)}</h4></div>'
                             + '<span class="cc-source-purpose">' + escape(purpose) + '</span>'
                             + '<div class="cc-source-copy">' + prose(statute) + '</div>'
                             + '<div class="cc-source-meta">' + fields('\n'.join(meta) + '\n' + more) + '</div></article>')
            content = '<div class="cc-reference-grid">' + ''.join(cards) + '</div>'
        rendered.append(f'<section id="{anchor}" class="{"cc-report-wide" if index > 2 else ""}"><h3 class="cc-section-heading"><span class="cc-section-number">{index}</span>{escape(title)}</h3>{content}</section>')
    return (f'<div class="cc-stored-report" data-report-id="{escape(report_id, quote=True)}" data-worker-job-id="{escape(worker_job_id, quote=True)}">'
            + '<nav class="cc-report-nav" aria-label="보고서 절 바로가기">' + ''.join(nav) + '</nav>'
            + '<div class="cc-report-body">' + ''.join(rendered) + '</div></div>')
