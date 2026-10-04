"""Build the 2026-09-26 report from saved evidence only; no model computation.

Run with the existing document runtime (ReportLab). Rasterize the new SVG with
the existing Sharp runtime, then use --package to check and archive the report.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/reports/cctv-project-20260926'
DOC = ROOT / 'docs/model_experiment_report.md'
OLD = 'artifacts/analysis/service-results-631-20260926-01/'
NEW = 'artifacts/colab/new100-two-models-20260926-01/'
TRAIN = 'artifacts/colab/train-631-20260924-01/'
LAST = 'artifacts/colab/lastconv631-20260926-01/'
AUDIT = 'artifacts/analysis/training-diagnostic-audit-20260926-01/'
SOURCES = {
    'S01': TRAIN + 'received/result.json',
    'S02': TRAIN + 'received/summary.json',
    'S03': TRAIN + 'received/epoch-results.json',
    'S04': AUDIT + 'model-metadata.json',
    'S05': OLD + 'metrics.json',
    'S06': LAST + 'received/epoch-results.json',
    'S07': LAST + 'received-verification.json',
    'S08': NEW + 'received-verification.json',
    'S09': NEW + 'received/comparison.json',
    'S10': NEW + 'received/execution.json',
    'S11': NEW + 'unknown-timestamp-audit.json',
    'S12': AUDIT + 'audit.json',
    'S13': 'artifacts/model-archive/two-models-20260926.json',
    'S14': NEW + 'received/preservation-receipt.json',
    'S15': 'artifacts/data-review/new100-two-models-20260926-01/role-allocation.json',
}
FIGURES = {
    '02-development-models': OLD + 'figures/01-model-comparison',
    '03-finetuning-context': OLD + 'figures/04-training-context',
    '04-new100-errors': NEW + 'review-figures/01-primary-comparison',
    '05-normal-windows': NEW + 'review-figures/02-normal-windows',
    '06-evaluation-coverage': NEW + 'review-figures/03-coverage',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def pct(value):
    return '미측정' if value is None else f'{value:.2%}'


def inline(value):
    text = html.escape(str(value))
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    return re.sub(r'`(.+?)`', r'<code>\1</code>', text)


def baseline_figure(epochs):
    from reportlab.graphics import renderSVG
    from reportlab.graphics.charts.linecharts import HorizontalLineChart
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font = 'ReportKorean'
    pdfmetrics.registerFont(TTFont(font, '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'))
    drawing = Drawing(1080, 430)
    drawing.add(Rect(0, 0, 1080, 430, fillColor=colors.white, strokeColor=None))

    def label(x, y, text, size=12, color='#24394e'):
        drawing.add(String(x, y, text, fontName=font, fontSize=size, fillColor=colors.HexColor(color)))

    label(28, 393, '원본631 학습 경과: 온라인 평균 loss', 22)
    label(28, 367, '631구간 × 5 epoch = 3,155회 업데이트 · 분류층만 학습 · batch size 1', 12, '#54677b')
    chart = HorizontalLineChart()
    chart.x, chart.y, chart.width, chart.height = 95, 105, 880, 220
    values = [e['online_mean_loss'] for e in epochs]
    chart.data = [values]
    chart.categoryAxis.categoryNames = [f'{e["epoch"]} epoch' for e in epochs]
    chart.categoryAxis.labels.fontName = chart.valueAxis.labels.fontName = font
    chart.categoryAxis.labels.fontSize = chart.valueAxis.labels.fontSize = 12
    chart.valueAxis.valueMin, chart.valueAxis.valueMax, chart.valueAxis.valueStep = 0, .6, .1
    chart.valueAxis.visibleGrid = True
    chart.valueAxis.gridStrokeColor = colors.HexColor('#e4eaf0')
    chart.lines[0].strokeColor = colors.HexColor('#235e9c')
    chart.lines[0].strokeWidth = 3
    drawing.add(chart)
    # The category axis places points in the centres of its equal-width bins.
    for i, v in enumerate(values):
        label(95 + 880 * (i + .5) / 5 - 24, 105 + 220 * v / .6 + 16, f'{v:.4f}', 12)
    label(28, 51, '학습 중 계속 달라지는 모델의 loss입니다. 최종 고정모델의 정확도나 검증 loss가 아닙니다.', 12)
    label(28, 25, '원본631의 epoch별 검증 F1·Recall은 미측정이므로 곡선을 임의로 추가하지 않았습니다.', 11, '#54677b')
    renderSVG.drawToFile(drawing, str(OUT / 'figures/01-baseline-loss.svg'))


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'figures').mkdir(exist_ok=True)
    evidence = {}
    source_manifest = {}
    for sid, name in SOURCES.items():
        path = ROOT / name
        evidence[sid] = json.loads(path.read_text())
        source_manifest[sid] = {'path': name, 'sha256': sha(path), 'bytes': path.stat().st_size}
    training, summary, epochs, metadata = [evidence[f'S{i:02d}'] for i in range(1, 5)]
    legacy, last_epochs, last_check, new = [evidence[f'S{i:02d}'] for i in range(5, 9)]
    original, experimental = new['models']['original631'], new['models']['lastconv631_epoch3']
    totals = new['totals']
    assert training['training_updates'] == 631 * 5 == 3155
    assert training['class_counts'] == {'accident': 403, 'normal': 228}
    assert sum(e['recorded_updates'] for e in epochs) == 3155
    assert all(e['epoch_complete'] and e['validation_f1'] is None for e in epochs)
    assert training['frozen_state']['unchanged'] and training['reload_verified']
    assert metadata['model_sha256'] == legacy['model_sha256']
    assert totals['scheduled_windows'] == totals['predicted_windows_per_model'] + totals['unclassified_windows']
    assert totals['processed_sources'] == totals['video_scored_sources'] + totals['partially_or_fully_unclassified_sources']
    for model in (original, experimental):
        c = model['confusion_matrix']
        assert sum(c.values()) == 81 and c['tp'] + c['fn'] == 61 and c['tn'] + c['fp'] == 20
        assert abs(model['metrics']['video_accident_recall'] - c['tp'] / 61) < 1e-10
        assert abs(model['metrics']['video_accident_precision'] - c['tp'] / (c['tp'] + c['fp'])) < 1e-10
        assert abs(model['metrics']['video_accident_f1'] - 2*c['tp'] / (2*c['tp'] + c['fp'] + c['fn'])) < 1e-10
        assert abs(model['metrics']['video_accuracy'] - (c['tp'] + c['tn']) / 81) < 1e-10
        assert sum(model['normal_windows'].values()) == 1170
    assert original['confusion_matrix'] == {'tp': 61, 'tn': 0, 'fp': 20, 'fn': 0}
    assert experimental['confusion_matrix'] == {'tp': 60, 'tn': 2, 'fp': 18, 'fn': 1}
    assert original['normal_windows']['1'] - experimental['normal_windows']['1'] == 453
    assert [e['updates'] for e in last_epochs] == [631, 1262, 1893]
    assert evidence['S12']['connection_mismatch_found'] is False

    baseline_figure(epochs)
    for name, source in FIGURES.items():
        for suffix in ('.svg', '.png'):
            shutil.copyfile(ROOT / (source + suffix), OUT / 'figures' / (name + suffix))
    save_json(OUT / 'source-manifest.json', source_manifest)
    save_json(OUT / 'report-data.json', {
        'as_of': '2026-09-26', 'report_generation_training_run': False,
        'report_generation_model_run': False, 'numeric_acceptance_thresholds': None,
        'baseline': {k: training[k] for k in ['training_segments', 'class_counts', 'label_origin_counts', 'training_updates', 'completed_epochs', 'timing', 'versions']},
        'baseline_config': metadata['config'], 'baseline_epochs': epochs,
        'legacy_groups': legacy['groups'], 'legacy_models': legacy['models'],
        'lastconv_epochs': [{k: e[k] for k in ['epoch', 'updates', 'online_unweighted_loss', 'dev_fn', 'dev_fp', 'bellevue_fp', 'model_sha256']} for e in last_epochs],
        'new100_totals': totals, 'new100_models': new['models'],
        'model_sha256': {'original631': metadata['model_sha256'], 'lastconv631_epoch3': last_epochs[-1]['model_sha256']},
    })

    md, body, toc = [], [], []

    def h(title):
        anchor = f'section-{len(toc) + 1:02d}'
        toc.append((anchor, title))
        md.extend(['', '## ' + title, ''])
        body.append(f'<h2 id="{anchor}"><span>{len(toc):02d}</span>{inline(title)}</h2>')

    def p(text):
        md.extend([text, ''])
        body.append('<p>' + inline(text) + '</p>')

    def note(text):
        md.extend(['> ' + text, ''])
        body.append('<aside>' + inline(text) + '</aside>')

    def ul(items):
        md.extend(['- ' + t for t in items] + [''])
        body.append('<ul>' + ''.join('<li>' + inline(t) + '</li>' for t in items) + '</ul>')

    def table(headers, rows):
        md.extend(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'])
        md.extend('| ' + ' | '.join(str(x).replace('|', '\\|') for x in row) + ' |' for row in rows)
        md.append('')
        body.append('<div class="table-wrap"><table><thead><tr>' + ''.join('<th>' + inline(x) + '</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + inline(x) + '</td>' for x in row) + '</tr>' for row in rows) + '</tbody></table></div>')

    def code(text):
        md.extend(['```text', text, '```', ''])
        body.append('<pre>' + html.escape(text) + '</pre>')

    def figure(name, caption):
        md.extend([f'![{caption}](figures/{name}.png)', '', caption, ''])
        # Standalone HTML uses SVG if the generated PNG has not been rasterized yet.
        path = OUT / 'figures' / (name + '.png')
        if not path.exists():
            path = path.with_suffix('.svg')
        mime = 'image/png' if path.suffix == '.png' else 'image/svg+xml'
        uri = f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode()
        body.append(f'<figure><img src="{uri}" alt="{html.escape(caption)}"><figcaption>{inline(caption)}</figcaption></figure>')

    title = 'R3D-18 기반 도로 CCTV 사고탐지'
    subtitle = '개발·실험 결과 보고서 · 2026-09-26'
    md.extend(['# ' + title, '', subtitle, ''])
    p('**목표:** 학습에 사용하지 않은 녹화 도로 CCTV에서 충돌·전복 의심 구간을 찾고, 원본 시각과 영상 근거를 제시한다. 사고 미탐 감소를 우선하면서 정상 통행의 오탐과 탐지 지연을 함께 평가한다.')
    note('현재 상태는 모델 개발·비교평가 단계다. 원본631은 비교 기준이며 운영 합격 모델이 아니다. 미래 영상에서 미탐 0을 보장하지 않는다. 이 보고서는 기존 결과를 정리한 것으로 새 학습·새 예측을 실행하지 않았다.')

    h('핵심 결과 요약')
    ul([
        '**원본631 학습 완료:** 사고 403 + 정상 228구간으로 분류층만 5 epoch, 3,155회 업데이트했다. 특징 추출부와 BatchNorm 상태 보존, 저장·재개·재로딩 검사 기록을 확인했다. [S01–S04]',
        '**학습 경과 기록:** 온라인 평균 loss는 0.4675 → 0.2577로 감소했다. 원본631의 epoch별 검증 지표는 미측정이며, loss 감소를 일반화 성능 개선으로 해석하지 않는다. [S03]',
        '**개선 실험 비교:** 자료 보강, 균형추출, 분류층 추가학습, 마지막 합성곱 미세조정을 비교했다. 작은 고정 개발목록에서는 원본631을 교체할 조건을 충족하지 못했다. [S05–S07]',
        '**새 원본100 비교 완료:** 사고 80영상 + 정상 20연속 이미지 시퀀스를 두 모델로 처리했다. 주 지표는 미분류가 없는 동일 81원본에서 계산했다. [S08–S11]',
        '**개선과 손실을 함께 확인:** 마지막 실험모델의 정상창 오탐은 1,015 → 562로 줄었지만 사고 원본 미탐이 0 → 1로 늘었다. 두 모델을 별도 보존하고 교체하지 않았다. [S08, S13–S14]',
    ])

    h('구현 범위와 분석 흐름')
    code('녹화 CCTV 영상 / 연속 JPG 시퀀스\n  → 파일·시간정보·정답 근거·사용 이력 확인\n  → 2초 구간에서 16프레임 선택 / 224×224 letterbox\n  → RGB 정규화 → R3D-18 → 정상·사고 logits\n  → 고정 판정 기준 → 원본 시간축에 예측 연결\n  → 미탐·오탐·미분류 집계 → JSON·그래프·HTML 보고서')
    table(['구성', '현재 역할', '상태'], [
        ['자료·전처리 모듈', '원본/구간 목록, 해시, 시각, 입력 배열·미리보기', '구현·사용'],
        ['R3D-18', '최근 구간의 정상/사고 이진 분류', '학습·평가 실행'],
        ['연속영상 분석', '2초 입력을 1초 간격으로 이동하며 판정 시각 저장', '녹화영상에서 실행'],
        ['결과 전달', 'Colab 출력, Drive 결과, JSON·HTML·그래프', '사용 중'],
        ['실시간 CCTV 서비스', '상시 스트림·운영 경보·웹 통합', '운영 성능 미검증·통합 서비스 미완료'],
        ['차량 탐지·추적', '차량 바운딩박스·ID·궤적', '현재 R3D-18 실험 범위에 없음'],
    ])
    p('R3D-18은 영상 구간의 공간·시간 특징을 처리하는 분류 모델이다. 현재 구현이 사고 차량의 위치를 직접 검출하거나 차량을 추적하는 것은 아니다. 실시간 서비스 목표와 현재 완료한 녹화영상 실험을 구분한다.')

    h('데이터 구성과 정답 근거')
    p('원본631의 숫자 631은 **학습에 사용한 구간 수**다. 631개의 독립 CCTV나 631 epoch를 뜻하지 않는다. 원본 파일 수·구간 수·독립 사건/카메라 수를 별도로 관리한다. [S01]')
    table(['학습 구성', '구간 수', '정답 근거'], [
        ['ACCIDENT 사고', 403, 'metadata-real.csv 사고 시점에서 파생한 라벨'],
        ['DETRAC 정상', 200, '사용자 정상 가정'],
        ['ACCIDENT 정상', 6, '사용자가 정상으로 확인한 구간'],
        ['ACCIDENT 임시 정상', 22, '관찰 기반 임시 라벨; 공식 CSV 정상·사람 검수 확정과 구분'],
        ['합계', 631, '사고 403 / 정상 228'],
    ])
    p('631 학습에는 새100 비교자료를 넣지 않았다. 신규 비교자료는 사고 80 MP4 + DETRAC 정상 20시퀀스로, 100개 원본을 따로 확보했다. 같은 11개 영상을 잘라 100개로 표시한 것이 아니다. 다만 서로 다른 파일이 서로 다른 카메라·사건을 보장하지는 않는다. [S08, S15]')
    p('공식 test 일부는 사용자 승인으로 개발 비교에 배정했다. 사용분은 이후 학습이나 미노출 최종시험으로 재사용하지 않는다. 남은 사고 1,432파일/1,354파일명 묶음과 정상 20시퀀스는 최종시험 후보로 보호한다. 파일명 묶음이 독립 사건 수라는 뜻은 아니다. [S15]')

    h('데이터 점검과 전처리')
    ul([
        '원본 사용 이력, 알려진 학습·평가 중복, 파일 해시, 디코딩, 프레임 시각과 구간 경계를 점검했다.',
        '학습631의 사고·정상 출처와 주야간 분포를 살펴보는 목적별 데이터 탐색·진단을 진행했다. 모든 현장 조건을 대표하는 포괄적 EDA가 완료됐다는 뜻은 아니다.',
        '사고 시점 주석에서 파생한 구간 정답과 사람이 확인한 정답을 분리했다. 사고 이후·주석이 없는 모든 구간을 자동 정상으로 바꾸지 않았다.',
    ])
    table(['항목', '사용 규격'], [
        ['시간 입력', '2초 구간 / 16프레임; 원본 시각에 가까운 프레임 선택'],
        ['공간 입력', '224×224 letterbox; 종횡비 유지와 패딩, 일괄 상단 잘라내기 없음'],
        ['저장 배열', '[16, 224, 224, 3], RGB uint8'],
        ['모델 입력', '[3, 16, 224, 224], float32; batch 축 별도'],
        ['정규화', '0–255를 0–1로 변환; mean [0.43216, 0.394666, 0.37645], std [0.22803, 0.22145, 0.216989]'],
        ['클래스·판정', 'normal=0 / accident=1; 큰 logit 선택, 동률이면 정상'],
        ['연속 판정', '직전 2초를 사용해 1초마다 판정; 판정 뒤의 미래 프레임 사용 안 함'],
    ])
    note('저FPS 처리 정책은 실험별 기록을 따른다. 과거 일부 준비자료에는 반복프레임을 허용했지만, 이번 새100 연속평가는 16개의 서로 다른 프레임과 최대 시각 오차 0.125초를 요구했다. 조건에 못 미치는 창은 미분류로 남겼다. 과거 참고30과 입력정책·채점단위가 동일하다고 주장하지 않는다. [S04, S08, S11–S12]')
    p('사고 점수는 두 클래스 점수에서 계산한 값이며 검증된 실제 사고 발생 확률이 아니다. 처리불가와 정상 판정도 별개다.')

    h('원본631 모델과 학습 설정')
    cfg = metadata['config']
    table(['항목', '실제 기록'], [
        ['모델 / 초기화', 'R3D-18 / Kinetics-400 사전학습 + 새 2클래스 분류층'],
        ['학습 범위', 'fc 분류층 1,026개 파라미터; 특징 추출부·BatchNorm 고정'],
        ['학습량', f'{training["completed_epochs"]} epoch / {training["training_updates"]:,}회 업데이트'],
        ['Batch / DataLoader workers', f'{cfg["batch_size"]} / {cfg["num_workers"]}'],
        ['Optimizer / learning rate', f'SGD, momentum {cfg["momentum"]} / {cfg["learning_rate"]}'],
        ['Loss', '비가중 이진 클래스 cross entropy'],
        ['학습 순서', f'seed {cfg["seed"]}; 한 번 섞은 631구간 순서를 5회 반복'],
        ['종료 기준', '사전에 정한 5 epoch / 3,155회 완료; 최적 epoch 탐색·early stopping 아님'],
        ['장치 / 환경', 'Colab Tesla T4 / torch 2.11.0+cu128 / torchvision 0.26.0+cu128'],
        ['원본631 epoch별 검증', '미실행; 원본 로그에 validation_f1/recall=null'],
    ])
    p('3회 시험 업데이트 뒤 저장 상태를 복원해 나머지 3,152회를 수행했다. 원본631은 이전 프로젝트 모델의 이어학습이 아니라 공식 사전학습 가중치에서 새 분류층으로 시작한 실행이다. 이후 warm631/lastconv631은 원본631에서 출발한 별도 실험이다. [S01, S04]')
    table(['Epoch', '업데이트', '온라인 평균 학습 loss', '검증 F1 / Recall'], [[e['epoch'], e['recorded_updates'], f'{e["online_mean_loss"]:.6f}', '미측정'] for e in epochs])
    figure('01-baseline-loss', '원본631의 온라인 학습 loss. 검증 성능이나 과적합 여부는 이 곡선만으로 확정할 수 없다. [S03]')
    table(['시간 범위', '실측'], [
        ['입력 준비', f'{summary["staging"]["staging_seconds"]:.2f}초'],
        ['학습 worker 전체 — 모델 로딩·저장·검사 포함', f'{training["timing"]["worker_total_seconds"]:.2f}초'],
        ['부모 실행 — 준비와 worker 등 포함', f'{summary["wall_seconds"]:.2f}초 (약 {summary["wall_seconds"]/60:.1f}분)'],
    ])
    p('시간은 해당 실행 범위의 기록이며 Drive 연결·업로드·사용자 대기 등을 모두 포함하는 사용자 체감시간과 같지 않다. 학습 완료는 서비스 품질 합격과도 다르다. [S01–S02]')

    h('참고평가와 개발평가에서 드러난 차이')
    rows = []
    for g in legacy['groups']:
        m = g['metrics']; has_accident = bool(m['accident_segments'])
        rows.append([g['label'], f'{m["accident_segments"]} / {m["normal_segments"]}', f'{m["fn"]} / {m["fp"]}' if has_accident else f'— / {m["fp"]}', pct(m['recall']) if has_accident else '정상 전용', pct(m['f1']) if has_accident else '정상 전용'])
    table(['평가 목록', '사고 / 정상 구간', '미탐 / 오탐', 'Recall', 'F1'], rows)
    p('참고30의 100%는 작고 반복 사용한 목록의 결과다. 다른 정상 출처와 같은 배경의 사고 이전 구간을 확인하자 오탐이 드러났다. 목록별 정답 근거와 난이도가 다르므로 합산하거나 성능 상승·하락률로 비교하지 않는다. [S05]')
    ul([
        '검토8은 4원본의 정상/사고 2구간씩이다. 이름의 “검토”가 모든 정상 라벨의 사람 검수 확정을 뜻하지 않는다.',
        '개발11은 사고 10 + 정상 가정 1구간이다. 원본631은 11구간을 모두 사고로 답했다.',
        'Bellevue3은 정상으로 확인된 1원본/카메라의 2초 구간 3개, 합계 6초다. 독립 카메라 3대가 아니다.',
    ])

    h('개선 실험 비교 — 같은 개발목록')
    recipes = {
        '631': '분류층 학습 기준 모델', '711': '기존631 + 사고 이전 정상 가정80',
        'balanced631': '같은631에서 사고/출처별 정상 균형추출',
        '707': '정상 가정80 중4개 보류 → 기존631+76',
        'warm631': '원본631 복사본의 분류층 추가3 epoch',
        'lastconv631': '원본631 복사본의 마지막 Conv3d 1개 + fc, 추가3 epoch',
    }
    table(['모델', '변경 내용', '사고 미탐 /10', '개발 정상 오탐 /1', 'Bellevue 오탐 /3'], [[m['label'], recipes[m['id']], m['development11']['fn'], m['development11']['fp'], m['bellevue3']['fp']] for m in legacy['models']])
    figure('02-development-models', '동일한 개발11·Bellevue3에서 비교한 결과. 두 목록의 분모는 합치지 않는다. [S05]')
    p('711·707·균형631은 일부 정상 판정을 개선하거나 유지했지만 사고 미탐이 늘었다. warm631과 lastconv631은 이 목록의 사고 미탐을 늘리지 않았으나 정상 오탐도 줄이지 못했다. 데이터 확대나 epoch 증가가 자동으로 개선을 보장하지 않는다는 점을 확인했다.')
    p('균형631의 5회차는 전체631개를 매번 한 번씩 보는 일반 epoch와 다르다. 사고315·ACCIDENT 정상158·DETRAC 정상158회씩 추출했으며 정상 원본의 반복 노출이 있다. 실험별 자료·초기화·손실·일부 환경 조건이 달라 결과 차이를 하나의 원인으로 단정하지 않는다.')

    h('오탐 진단과 제한적 특징층 실험')
    p('원본631으로 학습 정상228구간을 다시 판정했을 때 46구간이 오탐이었다. 그중 사람이 정상으로 재확인한6구간도 모두 오탐이었다. 학습자료에 대한 적합도 진단이며 독립 개발성능에 합산하지 않는다. [S05, S12]')
    p('Grad-CAM으로 정상6구간의 사고 클래스에 기여하는 영역을 확인했다. 일부 지도에서 나무·하늘·원경 등 배경이 강조됐다. 이는 사고 차량의 바운딩박스도, 실제 사고 확률 지도도 아니며 배경이 오탐의 원인이라는 인과 증명은 아니다.')
    p('이후 실제 학습 코드와 진단 코드를 대조해 모델·코드 해시, 입력·정규화·프레임 순서, 라벨/클래스, 학습 순서, 저장·로딩, 고정 층을 확인했다. 확보된 근거 범위에서 연결 불일치는 발견하지 못했다. 정상28 입력은 로컬 재해시했고, DETRAC200 배열·일부 과거 실행환경 등 미확인 범위는 별도로 남겼다. [S12]')
    table(['lastconv631 설정', '내용'], [
        ['초기화', '원본631 가중치 복사본'],
        ['갱신 범위', 'layer4.1.conv2.0.weight 한 개 + fc.weight / fc.bias'],
        ['학습 가능 파라미터', '7,078,914개; 모든 BN과 다른 파라미터 고정'],
        ['학습량 / 학습률', '631×3 = 1,893회 / fc 1e-4, 마지막 conv 1e-5'],
        ['손실', '사고 2배·정상 1배 가중; 보고 표는 별도 기록한 비가중 CE'],
        ['평가', '매 epoch 개발11·Bellevue3·학습 정상228 별도 집계'],
    ])
    table(['추가 epoch', '온라인 비가중 CE', '학습 정상 오탐 /228', '개발 미탐 /10', 'Bellevue 오탐 /3'], [[e['epoch'], f'{e["online_unweighted_loss"]:.6f}', [24, 26, 21][i], e['dev_fn'], e['bellevue_fp']] for i, e in enumerate(last_epochs)])
    figure('03-finetuning-context', '학습 정상 오탐46→21, 사람 정상6은6→6. 학습 적합도와 개발 성능은 다르다. [S05–S07]')
    p('마지막 실험의 준비·학습·평가 포함 최종저장 전 시간은 약29분10초다. 과거 warm631의 동일 환경과 학습 정상228 진단이 없어, 개선분 전체를 마지막 합성곱 한 개의 단독 효과로 단정하지 않는다. 기존 개발목록에서 채택 조건을 충족하지 못했지만, 사용자 요청으로 모델을 보존하고 새100원본 비교까지 수행했다.')

    h('새 원본100에서 두 고정모델 비교')
    table(['항목', '실제 평가 범위'], [
        ['원본', '사고80 MP4 + 정상20 연속 이미지 시퀀스 = 100'],
        ['전체 재생 길이', '약51.35분; 정상 약19.96분'],
        ['공통 채점 원본', '81개 = 사고61 + 정상20'],
        ['미분류 포함 원본', '사고19개: 18개 일부 예측, 1개는29창 모두 미분류'],
        ['시간창', '예정2,949 / 모델별 예측2,820 / 공통 미분류129'],
        ['모델 판정 규칙', '가중치·argmax 동점 정상 고정; 학습·임계값 조정 없음'],
        ['실행', f'Tesla T4 / 최종저장 전 {new["launcher_seconds"]:.2f}초 (약26분)'],
    ])
    figure('06-evaluation-coverage', '100원본 처리와81원본 주채점을 구분한다. 미분류를 정상으로 세지 않는다. [S08–S11]')
    p('이번 주 지표에서 영상은 **한 번이라도 사고 판정이 있으면 사고 영상**으로 집계했다. 이는 실제 충돌 시각을 찾아냈다는 의미가 아니다. 사건/카메라 독립성은 미확인이고, 정상20의 정답은 기존 사용자 가정이다.')
    rows = []
    for label, model in [('원본631', original), ('lastconv631 epoch3', experimental)]:
        c, m = model['confusion_matrix'], model['metrics']
        rows.append([label, c['tp'], c['tn'], c['fp'], c['fn'], pct(m['video_accident_recall']), pct(m['video_accident_precision']), pct(m['video_accident_f1']), pct(m['video_accuracy'])])
    table(['모델', 'TP', 'TN', 'FP', 'FN', 'Recall', 'Precision', 'F1', 'Accuracy'], rows)
    p('TP=사고를 사고로, TN=정상을 정상으로, FP=정상을 사고로, FN=사고를 정상으로 판정한 원본 수다. Recall=TP/(TP+FN), Precision=TP/(TP+FP), F1은 두 값의 조화평균이다. [S08–S09]')
    figure('04-new100-errors', '동일81원본에서 미탐0→1, 오탐20→18. 작은 Accuracy/F1 상승만으로 모델을 채택하지 않는다. [S08]')
    note('원본631은 채점한81원본을 모두 사고로 판정했다. 영상 Recall100%는 이 자료에서 충돌을 잘 구별했다는 증거가 아니다. 제외된19원본을 포함한 전체 사고 검출률도 이 표로 확정할 수 없다.')
    table(['정상20시퀀스의 겹치는1,170창', '원본631', 'lastconv631 epoch3'], [
        ['오탐 창', original['normal_windows']['1'], experimental['normal_windows']['1']],
        ['정상 판정 창', original['normal_windows']['0'], experimental['normal_windows']['0']],
        ['정상창 중 오탐 비율', pct(original['normal_positive_fraction']), pct(experimental['normal_positive_fraction'])],
    ])
    figure('05-normal-windows', '같은 정상창453개가 사고→정상으로 바뀌었다. 겹치는453창이 독립된453사건은 아니다. [S08]')
    p('새 정상창 오탐은 0개였고, 원본 전체에서 오탐이 사라진 정상 시퀀스는 MVI_40775와 MVI_40864 두 개다. 나머지 정상 원본에는 경보가 남았다. 오탐 감소 효과는 확인했으나 새 미탐 때문에 실험모델을 교체 채택하지 않았다.')

    h('새로 놓친 사례와 미분류의 의미')
    table(['원본', '관찰된 결과', '해석·다음 확인'], [
        ['p7GtPUgji9w_00.mp4', 'CSV 2.958초; 실험모델29창 모두 정상', '현재 주석 기준 영상 미탐1개. 실제 충돌 장면과 주석 대조 예정'],
        ['xaZ2WJAHrgY_00.mp4', 'CSV 0.627178초; 0–2초 정상, 첫 사고판정3초', '영상 전체는 양성. 주석시점 창 놓침과 초기2초 입력 경계를 구분'],
    ])
    p('CSV 사고 시점을 포함한 창만 별도로 참고 비교하면 채점 가능한76사고원본의 놓침이 0→2다. 영상 단위 사고61개의 미탐0→1과 분모·정의가 다르다. 두 수치를 더하거나 정식 사건Recall로 바꾸지 않는다. [S08]')
    p('미분류129창은 원본 프레임 시각을 대조해 재현했다. 97창은 서로 다른16프레임을 고를 수 없었고, 나머지32창은 시각 오차 초과도 동반했다. 이 검사는 원본 시간정보와 해시를 재사용한 감사이며 재예측·입력정책 수정은 하지 않았다. [S11]')

    h('실시간 처리와 서비스 품질: 측정한 것과 남은 것')
    c = legacy['continuous']
    table(['항목', '현재 근거', '발표 시 해석'], [
        ['기존 연속11원본', f'{c["predicted_windows"]}예측 / {c["unclassified_windows"]}미분류, 예측창 전부 사고', '사고 순간을 구분하는 능력이 확인된 것은 아님'],
        ['기존113회 모델 호출 평균', f'{c["mean_model_call_seconds"]*1000:.1f}ms', '해당 실행의 모델 호출시간; 전체 서비스 지연 아님'],
        ['새100 평가 실행', '두 모델·복사·디코딩·검사 등 포함 약26분', '실시간 스트림 End-to-End FPS가 아님'],
        ['시간당 오경보', '정식 운영 지표 미확정', '정상 시간 정답과 경보 병합 규칙 필요'],
        ['사고 발생→경보 지연', '검증된 지연 분포 미확정', '확정 사고 시각·사건 매칭·입력 수집시간 포함 필요'],
        ['미노출 최종시험 / 운영 합격', '미실행 / 미판정', '개발자료에서의 모델 비교와 구분'],
    ])
    p('정상20시퀀스에서 양성창을 겹침/접촉 기준으로 묶으면 39→56묶음, 양성 합집합 길이는 1,068→644초다. 긴 경보가 짧게 나뉘어 개수는 늘 수 있으므로, 이를 검증된 서비스 오경보율로 발표하지 않는다. [S08]')
    p('성공 기준은 “허용 오경보와 지연 안에서 사고 미탐을 최대한 줄이는 것”이다. 최소 사건Recall, 허용 오경보/시간, 허용 지연의 숫자는 아직 합의하지 않았다. 대화에서 든 예시 숫자를 승인된 합격선으로 기재하지 않는다.')

    h('문제 발견·대응·현재 결론')
    table(['문제', '수행한 대응', '확인한 결과 / 남은 한계'], [
        ['참고30에서는100%지만 다른 정상 장면 오탐', '정상 출처·같은 배경 구간·개발목록 확대', '참고목록 점수와 새 CCTV 성능 차이를 확인'],
        ['정상 보강 후 사고 미탐 증가', '711/707/균형631을 같은 개발목록에 비교', '개선 조건 미충족; 기존 결과 보존'],
        ['학습 정상6도 계속 오탐', '사용자 재검수·Grad-CAM·학습/진단 연결 감사', '정상 확인 유지·연결 불일치 발견 없음; 원인은 확정 못함'],
        ['고정 특징의 적응 부족 가능성', '분류층 대조 실험과 마지막 Conv3d+fc 제한 학습', '학습 정상46→21, 기존 개발 오탐 불변'],
        ['새 평가에서 개선과 악화가 동시에 발생', '새100원본·고정 두 모델 비교', '정상창453개 개선·영상 미탐1개 증가; 미채택'],
        ['저FPS 구간을 정상으로 잘못 집계할 위험', '미분류를 별도 저장·PTS 재대조', '129창 미분류 유지; 성능 분모에서 명시'],
    ])
    p('실험의 성과는 높은 숫자 하나를 얻은 것에 한정되지 않는다. 입력과 모델 버전을 고정해 재현 가능한 비교를 만들고, 미탐·오탐의 교환관계와 현재 실패 범위를 수치로 확인했다. 정상 통행 구분과 사건 시간평가는 여전히 개선 대상이다.')

    h('모델 보존·재현·실행 기록')
    table(['모델', '역할', 'SHA-256'], [
        ['원본631', '변경하지 않는 비교 기준', metadata['model_sha256']],
        ['lastconv631 epoch3', '별도 보존한 실험 후보; 운영 교체 미채택', last_epochs[-1]['model_sha256']],
    ])
    ul([
        '원본631 로컬 백업과 두 모델의 별도 Drive 보존 기록을 유지했다. 동일 파일에 덮어쓰지 않았다. [S13–S14]',
        '코드 ZIP·실제 실행셀·설정·환경 버전·입력 목록/해시·모델 해시·원본 JSON/로그·검증 결과를 연결했다.',
        '새100 수신 결과에서 29,714개 행별 검증과 지표 재계산을 완료했다. 이 수는 독립 시험 영상 수나 모델 성능 시험 횟수가 아니다. [S08]',
        '원격 모델/입력 배열 일부는 결과 ZIP에 없으므로, 해당 불변성은 검증된 Colab 코드와 실행기록 근거다. 로컬에서 모두 재해시한 것으로 표현하지 않는다.',
    ])
    table(['실행물', '위치 / 용도'], [
        ['원본631 학습', TRAIN + ' — Colab 셀·코드ZIP·수신 학습결과'],
        ['특징층 실험', LAST + ' — 제한 학습·매epoch 평가·모델 식별'],
        ['새100 두 모델 평가', NEW + ' — 고정목록·실제 코드·수신 결과·감사'],
        ['이 보고서 생성', 'scripts/build_project_report.py — 저장 결과만 읽고 표·그래프·문서 생성'],
    ])
    p('실제 수치의 원본은 아래 출처 목록과 source-manifest.json의 해시로 찾을 수 있다. 모델 가중치와 원본 영상은 이 보고서 공유 ZIP에 포함하지 않는다.')

    h('현재 완료 상태와 다음 작업')
    table(['항목', '상태'], [
        ['입력 준비·정답 출처 구분·재현 기록', '완료한 범위의 근거 보존'],
        ['원본631 및 승인된 개선 실험', '실행·수신·검증 완료'],
        ['새100 원본 비교', '완료; 주 채점81·미분류19 구분'],
        ['새 미탐 관련2원본의 주석/장면 대조', '기존 TODO로 남음'],
        ['운영 합격선·사건 시간 정답·매칭', '미확정'],
        ['보호 자료의 미노출 최종시험', '미실행'],
        ['추가 학습·판정기준 변경·차량추적·서비스 통합', '이번 보고서 작업에서 실행하지 않음'],
    ])
    p('바로 다음 작업은 새 미탐 관련2원본의 실제 충돌 장면과 CSV 시각을 대조하는 것이다. 기존 저장 예측을 재사용하며 새 학습이나 임계값 변경은 하지 않는다. 이후 개선 실험은 원본631을 보존하고 별도 실행안과 승인에 따라 결정한다.')
    p('동료의 R(2+1)D-18 보고서는 정리 형식의 참고자료다. 합성 클립 결과와 본 보고서의 실제 영상 결과를 직접 비교해 모델 우위를 주장하지 않는다. 공동 평가를 하려면 양쪽 학습 사용 이력과 같은 영상·정답·채점 규칙을 먼저 확인해야 한다.')

    h('발표·보고서에 사용할 결론')
    note('R3D-18 기반 CCTV 사고탐지 모델의 입력 준비, 학습, 연속영상 예측, 결과 검증 흐름을 구현하였다. 원본631은 사고403·정상228구간으로 분류층을5epoch 학습한 비교 기준 모델이다. 자료 보강과 학습 범위 변경 실험 후 새100원본에 두 고정모델을 적용했고, 동일81원본에서 원본631의 미탐0/오탐20과 마지막 실험모델의 미탐1/오탐18을 확인하였다. 마지막 모델은 정상 시간창 오탐을 줄였지만 사고 미탐이 증가해 교체하지 않았다. 원본631 역시 정상 원본 모두에 경보하여 운영 합격으로 볼 수 없다. 현재 성과는 재현 가능한 실험과 실패 범위의 확인이며, 사건 단위 검출·오경보·지연 기준과 미노출 최종시험이 남아 있다.')
    p('**30초 설명:** “사고 영상에서만 잘 나오는 모델인지 확인하려고 실제 정상 CCTV까지 평가했습니다. 추가 학습으로 정상 오탐은 줄었지만 사고를 새로 놓치는 문제가 생겼습니다. 그래서 두 모델과 기록을 보존하고, 정확한 사고 시각과 오경보 부담을 기준으로 다음 개선을 결정하는 단계입니다.”')

    h('근거 자료')
    table(['번호', '원본 경로'], [[sid, name] for sid, name in SOURCES.items()])
    p('각 원본의 SHA-256은 source-manifest.json, 보고서에서 사용하는 요약 수치는 report-data.json, 산출물 검사는 verification.json에 있다. 표의 수치는 원본 JSON에서 읽거나 혼동행렬로 재계산했다. 기존 실험 결과를 덮어쓰지 않았다.')

    markdown = '\n'.join(md).rstrip() + '\n'
    (OUT / 'report.md').write_text(markdown)
    DOC.write_text(markdown.replace('](figures/', '](../artifacts/reports/cctv-project-20260926/figures/'))
    nav = ''.join(f'<a href="#{anchor}">{inline(name)}</a>' for anchor, name in toc)
    css = '''
    :root{--ink:#172a3c;--muted:#536777;--line:#dce5eb;--blue:#1b587d;--paper:#fff;--accent:#a66013}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f1f5f7;color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;line-height:1.8}
    .cover{background:#173e55;color:white;padding:62px max(28px,calc((100vw - 1080px)/2)) 42px}.eyebrow{letter-spacing:.16em;font-size:12px;color:#9dd4de}.cover h1{font-size:clamp(28px,4vw,43px);line-height:1.3;margin:16px 0}.cover p{color:#cbe0e9;margin:0}.chips{display:flex;gap:10px;flex-wrap:wrap;margin-top:23px}.chip{border:1px solid #52798e;border-radius:30px;padding:5px 13px;font-size:13px}
    .layout{max-width:1420px;margin:auto;display:grid;grid-template-columns:235px minmax(0,1fr);gap:30px;padding:30px 24px}nav{position:sticky;top:20px;align-self:start;max-height:95vh;overflow:auto;font-size:12px}nav b{display:block;margin-bottom:12px;color:var(--muted)}nav a{display:block;color:var(--ink);text-decoration:none;padding:7px 10px;border-left:2px solid #d4e1e8}nav a:hover{background:#dfebf2;border-color:var(--blue)}main{background:var(--paper);padding:34px 42px 55px;min-width:0;border:1px solid var(--line)}h2{font-size:24px;line-height:1.5;margin:50px 0 20px;padding-top:16px;border-top:1px solid var(--line);scroll-margin-top:20px}h2 span{font-size:12px;color:var(--blue);display:block;letter-spacing:.14em;margin-bottom:7px}p{margin:15px 0;font-size:15px}strong{font-weight:700}li{font-size:15px;margin:8px 0}ul{padding-left:23px}aside{background:#fff5e7;border-left:4px solid var(--accent);padding:19px 22px;margin:22px 0;font-size:15px}.table-wrap{overflow-x:auto;margin:18px 0 23px}table{border-collapse:collapse;width:100%;font-size:13px}th{text-align:left;background:#edf3f6;color:#234759;font-weight:650;white-space:nowrap}th,td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}td{min-width:70px;overflow-wrap:anywhere}tr:nth-child(even) td{background:#fafcfd}pre{font-family:inherit;font-size:14px;line-height:1.9;background:#f0f5f8;padding:22px;white-space:pre-wrap;border:1px solid var(--line)}code{font-size:.9em;background:#edf3f6;padding:1px 4px;overflow-wrap:anywhere}figure{margin:27px 0 30px}figure img{width:100%;height:auto;display:block;border:1px solid var(--line)}figcaption{font-size:12px;color:var(--muted);padding:9px 3px}footer{border-top:1px solid var(--line);padding-top:20px;margin-top:36px;font-size:12px;color:var(--muted)}
    @media(max-width:950px){.layout{display:block;padding:14px}nav{position:static;max-height:180px;margin-bottom:20px}main{padding:22px}h2{font-size:21px}.cover{padding:35px 25px}}
    @media print{body{background:white}.cover{background:white;color:#172a3c;padding:20px}.cover p,.eyebrow{color:#536777}.chip{border-color:#aaa}.layout{display:block;padding:0}nav{display:none}main{border:0;padding:0}h2{break-after:avoid}figure,table,aside{break-inside:avoid}.table-wrap{overflow:visible}th,td{padding:7px;font-size:10px}p,li{font-size:11px}}
    '''
    document = f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} · 개발 결과 보고서</title><style>{css}</style></head><body><header class="cover"><div class="eyebrow">BORAM 3 / MODEL EXPERIMENT REPORT</div><h1>{title}</h1><p>{subtitle}</p><div class="chips"><span class="chip">녹화영상 실험</span><span class="chip">원본631 보존</span><span class="chip">새100원본 비교 완료</span><span class="chip">운영 품질 검증 진행 중</span></div></header><div class="layout"><nav aria-label="보고서 목차"><b>CONTENTS / {len(toc)}개 항목</b>{nav}</nav><main>{"".join(body)}<footer>기존 실행 결과를 재사용한 보고서 · 이 문서 생성 중 모델 연산·학습 없음 · 모든 그림을 포함한 오프라인 HTML</footer></main></div></body></html>'
    (OUT / 'report.html').write_text(document)
    for sid, receipt in source_manifest.items():
        assert sha(ROOT / receipt['path']) == receipt['sha256'], sid
    print(json.dumps({'status': 'report_built', 'sections': len(toc), 'figures': 6, 'source_files': len(SOURCES), 'new_model_runs': 0, 'output': str(OUT.relative_to(ROOT))}, ensure_ascii=False))


def package():
    from html.parser import HTMLParser
    from PIL import Image

    class Inspector(HTMLParser):
        def __init__(self):
            super().__init__(); self.images = []; self.ids = set(); self.hrefs = []; self.tables = 0
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'id' in attrs: self.ids.add(attrs['id'])
            if tag == 'a': self.hrefs.append(attrs.get('href', ''))
            if tag == 'img': self.images.append(attrs)
            if tag == 'table': self.tables += 1

    parser = Inspector(); parser.feed((OUT / 'report.html').read_text())
    assert len(parser.images) == 6 and all(i.get('alt') for i in parser.images)
    assert all(i['src'].startswith('data:image/png;base64,') for i in parser.images)
    assert all(h.startswith('#') and h[1:] in parser.ids for h in parser.hrefs)
    assert len(parser.ids) == 16
    pngs = sorted((OUT / 'figures').glob('*.png')); assert len(pngs) == 6
    for p in pngs:
        with Image.open(p) as im: im.verify()
    manifest = json.loads((OUT / 'source-manifest.json').read_text())
    assert all(sha(ROOT / item['path']) == item['sha256'] for item in manifest.values())
    md = (OUT / 'report.md').read_text()
    assert DOC.read_text() == md.replace('](figures/', '](../artifacts/reports/cctv-project-20260926/figures/')
    assert all((OUT / name).exists() for name in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', md))
    assert '/Users/' not in md and '/Volumes/' not in md
    for name, source in FIGURES.items():
        for suffix in ('.png', '.svg'):
            assert sha(OUT / 'figures' / (name + suffix)) == sha(ROOT / (source + suffix))
    readme = '''# 보고서 사용 안내\n\n- report.html: 그림 포함 단일 HTML. 인터넷 연결 없이 열 수 있습니다.\n- report.md: Notion/문서 편집용 Markdown. 그림은 figures 폴더와 함께 사용합니다.\n- figures/: PNG(삽입용), SVG(확대용) 6쌍.\n- report-data.json / source-manifest.json: 보고 수치·원본 경로와 SHA-256.\n- verification.json: 산출물 검사 결과.\n- build_project_report.py: 원본 저장소에서 실행하는 생성 코드. 이 ZIP만으로 모델 학습이나 원본 실험 재실행을 할 수 없습니다.\n\n원본631·실험모델·영상은 포함하지 않습니다. 현재 상태는 운영 합격이 아닌 개발 비교 단계입니다.\n\n## 저장소에서 문서 재생성\n\n기존 ReportLab/Pillow Python과 Sharp Node 환경을 사용합니다. 모델 연산은 없습니다.\n\n1. `python scripts/build_project_report.py` — JSON으로 문서·SVG 생성.\n2. Sharp에서 새 `figures/01-baseline-loss.svg`를 density144의 PNG로 변환.\n3. `python scripts/build_project_report.py` — PNG를 포함한 HTML 재생성.\n4. 그림을 육안 확인하고 `figure-visual-qa.json`의 SHA를 갱신.\n5. `python scripts/build_project_report.py --package` — 원본 보존·문서/이미지 검사 후 ZIP 생성.\n\nHTML 전체 브라우저 화면 검사는 수행하지 않았으며, 그래프6개 육안·이미지 디코딩·목차 연결·표/수치·ZIP 무결성을 확인했습니다.\n'''
    (OUT / 'README.md').write_text(readme)
    shutil.copyfile(Path(__file__), OUT / 'build_project_report.py')
    visual_path = OUT / 'figure-visual-qa.json'
    visual = json.loads(visual_path.read_text()) if visual_path.exists() else {}
    visually_checked = [p.name for p in pngs if visual.get(p.name, {}).get('sha256') == sha(p)
                        and visual[p.name].get('status') == 'visually_checked']
    save_json(OUT / 'verification.json', {
        'status': 'passed', 'source_files_unchanged': len(manifest), 'sections': len(parser.ids),
        'tables': parser.tables, 'embedded_png_figures': len(parser.images),
        'source_figure_pairs_reused_unchanged': len(FIGURES), 'new_training_loss_figure': 1,
        'standalone_html_no_external_assets': True, 'markdown_figure_paths_valid': True,
        'canonical_markdown_equals_packaged': True, 'metric_arithmetic_checked': True,
        'new_model_runs': 0, 'new_training_runs': 0,
        'visually_checked_png_figures': visually_checked,
        'full_browser_visual_inspection': False,
    })
    archive = OUT / 'cctv-project-report-20260926.zip'
    files = [p for p in OUT.rglob('*') if p.is_file() and p.suffix != '.zip']
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(files): z.write(p, 'cctv-project-report/' + str(p.relative_to(OUT)))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
    print(json.dumps({'status': 'packaged', 'files': len(files), 'bytes': archive.stat().st_size, 'zip_sha256': sha(archive)}, ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--package', action='store_true')
    args = ap.parse_args()
    package() if args.package else build()
