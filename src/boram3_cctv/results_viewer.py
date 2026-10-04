"""Read a fixed catalogue of saved results. No model, decoder, subprocess or writes."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path, PurePosixPath

TRAIN = 'artifacts/training/limited-real-iid-20260921-01'
REVIEW = 'artifacts/training-review/limited-real-iid-20260921-01'
LOSSES = 'artifacts/training-review/continuous-20260921-01/loss-explanation.json'
SEQUENTIAL = 'artifacts/inference/008-sequential-20260921-01'
EVENTS = 'artifacts/events/m3-synthetic-20260922-01/demo-final'
RECEIPT = 'artifacts/data-review/t9-devval-20260922-01'
SIGNED = 'artifacts/data-review/t9-devval-20260922-02'
LATEST = 'artifacts/training/t9-expansion-20260922-01'
EXPANSION = 'artifacts/training/expansion-intake-20260922-01'
PAGES = {'midterm': '중간보고 · 382구간 / 5 epoch', 'latest': '이전 학습 · T9 33구간', 'training': '이전 학습 · 5구간', 'continuous': '연속 영상 결과',
         'review': '검증 후보 검수', 'events': '시험용 이벤트', 'validation': '독립 검증 상태',
         'expansion': '확대 계획 · 500개 요청 / 실제 후보',
         'colab_predictions': '이전 Colab · 10구간 실제 예측'}


class ViewerError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ViewerError(reason)


class ArtifactReader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.reads = {}

    def path(self, name, *, suffixes, limit):
        require(isinstance(name, str) and '\\' not in name, 'unsafe_artifact_path')
        rel = PurePosixPath(name)
        require(not rel.is_absolute() and '..' not in rel.parts and len(rel.parts) > 1
                and rel.parts[0] == 'artifacts', 'only_project_artifacts_allowed')
        path = (self.root / str(rel)).resolve()
        require(path.is_relative_to(self.root / 'artifacts') and path.suffix in suffixes,
                'unsafe_artifact_path_or_type')
        require(path.is_file(), 'artifact_missing: ' + name)
        require(path.stat().st_size <= limit, 'artifact_too_large: ' + name)
        return path

    def bytes(self, name, *, suffixes=('.json', '.jsonl'), limit=2*1024*1024):
        try:
            path = self.path(name, suffixes=suffixes, limit=limit)
            with path.open('rb') as stream:
                data = stream.read(limit + 1)
        except OSError:
            raise ViewerError('artifact_read_failed: ' + name) from None
        require(len(data) <= limit, 'artifact_too_large: ' + name)
        self.reads[name] = hashlib.sha256(data).hexdigest()
        return data

    def json(self, name):
        try:
            return json.loads(self.bytes(name), parse_constant=lambda value: _bad_json())
        except (UnicodeError, ValueError) as error:
            if isinstance(error, ViewerError):
                raise
            raise ViewerError('invalid_json: ' + name) from None

    def jsonl(self, name, max_rows=100):
        try:
            lines = self.bytes(name).decode('utf-8').splitlines()
            require(len(lines) <= max_rows, 'too_many_saved_rows')
            return [json.loads(line, parse_constant=lambda value: _bad_json()) for line in lines if line.strip()]
        except (UnicodeError, ValueError) as error:
            if isinstance(error, ViewerError):
                raise
            raise ViewerError('invalid_jsonl: ' + name) from None

    def media(self, name):
        return self.bytes(name, suffixes=('.png', '.mp4'), limit=16*1024*1024)


def _bad_json():
    raise ViewerError('nonfinite_json_not_supported')


def records(value, limit=100):
    require(isinstance(value, list) and len(value) <= limit and all(isinstance(r, dict) for r in value),
            'invalid_saved_rows')
    return value


def index(rows, key):
    rows = records(rows)
    require(all(isinstance(r.get(key), str) and r[key] for r in rows), 'missing_record_id')
    result = {r[key]: r for r in rows}
    require(len(result) == len(rows), 'duplicate_saved_id')
    return result


def number(value):
    require(type(value) in (int, float) and math.isfinite(value), 'invalid_saved_number')
    return value


def label(value):
    require(value is None or (type(value) is int and value in (0, 1)), 'invalid_class')
    return {None: '미확정', 0: '정상', 1: '사고'}[value]


def frame_rows(frames):
    rows = records(frames, limit=16)
    times = [number(r.get('actual_seconds')) for r in rows]
    require(all(a < b for a, b in zip(times, times[1:])), 'saved_frame_order_invalid')
    return [{'프레임': i+1, '원본 시각(초)': t, '목표 시각(초)': row.get('requested_seconds')}
            for i, (row, t) in enumerate(zip(rows, times))]


def view(title, scope, notice):
    return {'title': title, 'scope': scope, 'notice': notice, 'tables': [], 'charts': [],
            'media': [], 'details': [], 'user_review_status': 'pending'}


def latest_training(reader):
    base = LATEST + '/run-mps'
    result = reader.json(base + '/result.json')
    config = reader.json(base + '/config.json')
    inputs = index(reader.json(base + '/prepared_inputs.json'), 'sample_id')
    mapping = reader.json(base + '/class_mapping.json')
    require(result['status'] == 'completed_limited_real_training' and result['validation_run'] is False
            and mapping == {'normal': 0, 'accident': 1} and config['batch_size'] == 1
            and 1 <= len(inputs) <= 33 and result['training_segments'] == len(inputs),
            'latest_training_scope_mismatch')
    rows = reader.jsonl(base + '/probe/train.jsonl', 165) + reader.jsonl(base + '/loop/train.jsonl', 165)
    require(len(rows) == result['training_updates'] and len(rows) <= 165
            and [r['step'] for r in rows] == list(range(1, len(rows)+1)),
            'duplicate_or_unordered_loss_step')
    out = view(PAGES['latest'], '실제 학습 기록 · 새 모델 성능 평가는 미실행',
               '저장된 학습 입력과 손실을 표시합니다. 정확도·Precision·Recall은 아직 미측정이며 독립 검증 결과가 아닙니다.')
    table = []
    for sid, item in inputs.items():
        origin = item['label_provenance']['kind']
        require(origin in ('human_review', 'source_annotation_derived'), 'unknown_label_origin')
        frames = reader.json(item['frames_path'])
        require(reader.reads[item['frames_path']] == item['frames_sha256'], 'saved_frame_hash_mismatch')
        times = frame_rows(frames['frames'])
        require(len(times) == 16, 'saved_training_frame_count_mismatch')
        table.append({'구간 ID':sid, '원본':item.get('source_path') or 'QMUL 동일 원본',
                      '시작(초)':item['start_seconds'], '끝(초)':item['end_seconds'], '정답':label(item['target']),
                      '정답 출처':'사람 검수' if origin == 'human_review' else 'CSV 사고 시점 파생',
                      '프레임 수':16, '새 모델 예측':'미실행'})
        out['media'].append({'id':sid, 'path':item['preview_path'], 'kind':'image', 'times':times,
            'caption':'실제 학습 입력 16장 · 왼쪽→오른쪽, 위→아래. CSV 파생 자료의 UNCONFIRMED 표시는 사람 미검수 상태이며 학습 정답은 목록에 별도로 기록합니다.'})
    log_rows=[]
    for row in rows:
        require(len(row['sample_ids']) == 1 and row['sample_ids'][0] in inputs, 'unknown_training_log_sample')
        log_rows.append({'step':row['step'], '구간 ID':row['sample_ids'][0], '학습 loss':number(row['loss'])})
    epochs=[]
    for offset in range(0, len(rows), len(inputs)):
        subset=rows[offset:offset+len(inputs)]
        complete=len(subset)==len(inputs) and {r['sample_ids'][0] for r in subset}==set(inputs)
        values={'epoch':offset//len(inputs)+1, '기록 완전함':complete, '기록된 step':len(subset)}
        for name,target in [('전체',None),('사고',1),('정상',0)]:
            losses=[number(r['loss']) for r in subset if target is None or inputs[r['sample_ids'][0]]['target']==target]
            values[name+' 평균 loss']=sum(losses)/len(losses) if complete and losses else None
        epochs.append(values)
    out['tables']=[('학습 입력과 정답 근거',table),('epoch별 학습 손실',epochs),
                   ('평가 지표 — 0점이 아니라 미측정',[
                       {'지표':name,'상태':'미측정'} for name in ('Accuracy','사고 Precision','사고 Recall','사고 F1')])]
    out['charts']=[{'title':'epoch 평균 loss · 학습 중 기록', 'rows':epochs,'x':'epoch',
                    'y':['전체 평균 loss','사고 평균 loss','정상 평균 loss']},
                   {'title':'저장된 전체 업데이트 손실','rows':log_rows,'x':'step','y':'학습 loss'}]
    outer=reader.json(LATEST+'/training-execution.json')
    preparation=reader.json(LATEST+'/preparation-execution.json')
    out['details']=[('실제 학습과 처리시간',{'장치':result['actual_device'],'epoch':result['completed_epochs'],
        '업데이트':result['training_updates'],'구간 수':result['class_counts'],
        '준비 전체(초)':preparation['whole_command_seconds'],'학습 명령 전체(초)':outer['whole_command_seconds'],
        '재로딩 확인':result['reload_verified'],'모델 파일':base+'/loop/model.pt'}),
        ('해석과 다음 단계',{'loss':'클래스 가중치 없는 cross_entropy, batch1. epoch 평균은 가중치가 변하는 각 step의 평균. 정확도로 환산하지 않음.',
         '한계':'정상 QMUL 한 원본·사고30/정상3 불균형·정상6초/사고2초 차이·사건 독립성 미확인',
         '다음':'학습에 쓰지 않은 자료의 평가 목록을 확정한 뒤 예측과 정답 비교. 이 화면은 모델 실행 기능이 아님.'})]
    return out


def training(reader):
    saved = reader.json(REVIEW + '/predictions.json')
    result = reader.json(TRAIN + '/result.json')
    inputs = index(reader.json(TRAIN + '/prepared_inputs.json'), 'sample_id')
    loss = reader.json(LOSSES)
    require(saved['purpose'] == 'review_existing_training_inputs_only'
            and saved['generalization_evaluation'] is False
            and saved['class_mapping'] == {'normal': 0, 'accident': 1}, 'training_review_scope_mismatch')
    require(reader.reads[REVIEW + '/predictions.json'] == loss['fixed_predictions_sha256'],
            'saved_loss_prediction_hash_mismatch')
    fixed = index(loss['fixed_model_training_inputs']['rows'], 'sample_id')
    predictions = index(saved['rows'], 'sample_id')
    require(set(inputs) == set(predictions) == set(fixed), 'training_sample_set_mismatch')
    out = view(PAGES['training'], '학습 자료 재확인', '학습에 사용한 자료의 결과입니다. 독립 검증 성능이 아닙니다.')
    table = []
    for sid, row in predictions.items():
        item = inputs[sid]
        require(item['target'] == row['truth'] == fixed[sid]['truth'] and
                item['label_review']['status'] == 'approved', 'approved_truth_mismatch')
        require(row['correct'] is (row['truth'] == row['prediction']), 'saved_correctness_mismatch')
        score = number(row['class_scores']['accident'])
        require(0 <= score <= 1, 'saved_score_out_of_range')
        frames = reader.json(item['frames_path'])
        require(reader.reads[item['frames_path']] == item['frames_sha256'] == row['frames_sha256'],
                'saved_frame_hash_mismatch')
        times = frame_rows(frames['frames'])
        require(len(times) == 16, 'saved_training_frame_count_mismatch')
        table.append({'구간 ID': sid, '검수 시작(초)': item['start_seconds'], '검수 끝(초)': item['end_seconds'],
            '정답': label(row['truth']), '모델 예측': label(row['prediction']), '사고 점수': score,
            '맞음/틀림': '맞음' if row['correct'] else '틀림', '고정 모델 손실': fixed[sid]['loss'],
            '프레임 수': len(times), '입력 첫 시각(초)': times[0]['원본 시각(초)'],
            '입력 끝 시각(초)': times[-1]['원본 시각(초)']})
        out['media'].append({'id': sid, 'path': str(PurePosixPath(item['frames_path']).with_name('preview.png')),
            'kind': 'image', 'caption': '저장된 학습 입력의 미리보기 · 16장/224 · 왼쪽부터 시간순', 'times': times})
    logs = records(loss['training_log_summary']['rows'])
    steps = [r['step'] for r in logs]
    require(all(type(n) is int and n > 0 for n in steps) and steps == sorted(set(steps)), 'duplicate_or_unordered_loss_step')
    log_rows = [{'전역 step': r['step'], '실행 구간': r['phase'], '배치 손실': number(r['loss'])} for r in logs]
    epochs = [{'epoch': e['epoch'], '기록 완전함': e['complete'], '저장된 step 수': len(e['steps']),
               'epoch 평균 손실': e['epoch_mean'] if e['complete'] else None}
              for e in records(loss['training_log_summary']['epochs'])]
    out['tables'] = [('정답과 저장 예측', table), ('학습 로그 — 기록된 값만', log_rows), ('epoch 기록', epochs)]
    out['charts'] = [{'title': '학습 중 배치 손실 · 모델이 업데이트되는 동안의 값',
                      'rows': log_rows, 'x': '전역 step', 'y': '배치 손실'}]
    out['details'] = [('입력·점수 의미', {'입력 출처': saved['input_origin'], '판정 규칙': saved['rule'],
        '점수': 'softmax 사고 클래스 점수이며 보정된 실제 사고 확률이 아님',
        '시간 규칙': '기존 사고 검수2초·정상 검수6초에서 각각16장. 이번에 변경하지 않음',
        '모델 SHA256': saved['model_sha256']}),
        ('손실 평균의 구분', {'손실 함수': loss['function'], '클래스 가중치': loss['class_weight'],
        'reduction': loss['reduction'], 'batch_size': loss['batch_size'],
        '학습 중 기록 평균': loss['training_log_summary']['observed_run_mean'],
        '최종 고정 모델의 학습 자료 평균': loss['fixed_model_training_inputs']['mean_loss'],
        '설명': '이번 batch_size=1의 배치 손실은 한 구간의 손실. loss를 정확도%로 환산하지 않음'}),
        ('기존 실행 기록', {'장치': result['actual_device'], 'epoch': result['completed_epochs'],
        '업데이트': result['training_updates'], '학습 구간수': result['class_counts'],
        '누적 학습루프 초': result['loop']['progress']['training_seconds'],
        'worker 내부 총시간 초': result['timing']['worker_total_seconds'],
        '시간 범위': '기존 모델 실행 로그. 현재 화면 로딩 시간과 다름; 부모 전체 시간은 아님'})]
    return out


def continuous(reader):
    result = reader.json(SEQUENTIAL + '/result.json')
    config = reader.json(SEQUENTIAL + '/config.json')
    command = reader.json(SEQUENTIAL + '/command.json')
    rows = records(reader.jsonl(SEQUENTIAL + '/predictions.jsonl'), 60)
    index(rows, 'window_id')
    require(result['independent_validation'] is False, 'continuous_scope_mismatch')
    out = view(PAGES['continuous'], '학습 원본의 연속 분석',
               '008 학습 원본의 겹치는 구간입니다. 새 구간 정답이 없어 정확도·F1을 계산하지 않습니다.')
    table, score_rows = [], []
    for row in rows:
        require(all(row.get(k) is None for k in ('truth', 'correct', 'loss')), 'unexpected_continuous_truth')
        predicted = row['status'] == 'predicted'
        table.append({'구간 ID': row['window_id'], '시작(초)': row['start_seconds'], '끝(초)': row['end_seconds'],
            '판단 원본 시각(초)': row['decision_source_time_sec'], '처리 상태': row['status'],
            '정답': '미확정', '예측': label(row.get('prediction')) if predicted else '미판정',
            '사고 점수': row.get('accident_score') if predicted else None})
        if predicted:
            score = number(row['accident_score']);require(0 <= score <= 1, 'saved_score_out_of_range')
            score_rows.append({'원본 판단 시각(초)': row['decision_source_time_sec'], '사고 점수': score})
        if row.get('preview'):
            rel = PurePosixPath(row['preview'])
            require(len(rel.parts) == 1, 'unsafe_preview_reference')
            out['media'].append({'id': row['window_id'], 'path': SEQUENTIAL + '/' + row['preview'], 'kind': 'image',
                'caption': '당시 저장한 연속 입력 미리보기 · 새로 추출하지 않음', 'times': frame_rows(row['frames'])})
    out['tables'] = [('모든 저장 구간', table)]
    out['charts'] = [{'title': '원본 시각별 사고 점수 · 정답선 없음', 'rows': score_rows,
                      'x': '원본 판단 시각(초)', 'y': '사고 점수'}]
    out['details'] = [('기존 실행·시간 범위', {'장치': result['actual_device'], '입력 구간 초': config['window_seconds'],
        '호출 간격 초': config['stride_seconds'], '판정': config['decision_rule'],
        '실제 관측 원본 끝 초': result['video']['observed_coverage_end_seconds'],
        '모델 호출 합계 초': result['timing']['inference_seconds'], 'worker 내부 초': result['timing']['worker_seconds'],
        '자식 실행을 감싼 초': command['wall_seconds'],
        '설명': '약15.10초는 자식을 감싼 시간. 부모 준비·최종 기록을 포함한 전체 처리시간이 아님',
        '모델 SHA256': result['model_sha256']})]
    return out


def reviews(reader):
    signed = reader.json(SIGNED + '/recorded/reviewed_plan.json')
    report = reader.json(SIGNED + '/recorded/report.json')
    manifest = reader.json(RECEIPT + '/prepared/manifest.json')
    require(reader.reads[RECEIPT + '/prepared/manifest.json'] == report['parent_manifest_sha256'], 'review_manifest_mismatch')
    rows = index(manifest['samples'], 'sample_id')
    out = view(PAGES['review'], '검증 후보 검수', '사고 정답 승인과 추가 맥락·독립성 확인은 별개입니다. 모델 평가는 미실행입니다.')
    table = []
    for sample in records(signed['samples'], 3):
        sid = sample['sample_id'];require(sid in rows, 'review_sample_missing')
        old = rows[sid];review = sample['label_review']
        require(sample['source_sha256'] == old['source_sha256'] and
                sample['start_seconds'] == old['start_seconds'] and sample['end_seconds'] == old['end_seconds'],
                'review_identity_mismatch')
        preview = RECEIPT + '/prepared/' + old['preview_path']
        require(hashlib.sha256(reader.media(preview)).hexdigest() == report['preview_sha256'][sid], 'review_preview_hash_mismatch')
        table.append({'구간 ID': sid, '원본 파일': sample['video_path'], '시작(초)': sample['start_seconds'],
            '끝(초)': sample['end_seconds'], '정답 상태': review['status'], '정답': review['label'],
            '검수자': review['reviewer'], '검수 날짜': review['reviewed_at'],
            '입력 품질': sample['input_quality_review']['status'],
            '추가 장면 필요': sample['human_submission']['needs_more_context'],
            '원본/사건 관계': sample['human_submission']['related_source_note'], '근거': review['basis']})
        frames = reader.json(RECEIPT + '/prepared/' + old['frames_metadata_path'])
        out['media'].append({'id': sid, 'path': preview, 'kind': 'image',
            'caption': '기존 검수 미리보기 · 이미지의 UNCONFIRMED는 생성 당시 표시이며 위 최신 정답 기록을 따름',
            'times': frame_rows(frames['frames'])})
    context = reader.json(SIGNED + '/context/manifest.json')
    seg = context['segments'][0]
    out['media'].append({'id': '후보 2 앞 0.5초 참고', 'path': SIGNED + '/context/' + seg['preview_path'],
        'kind': 'video', 'caption': '참고 영상의 0초 = 원본0.750초. 참고 범위[0.750,1.250), 정답 미지정·사용자 추가 확인 대기.',
        'times': [{'원본 시각(초)': r['source_seconds'], '참고 영상 시각(초)': r['preview_seconds']} for r in seg['frame_mapping']]})
    out['tables'] = [('최신 사용자 검수 기록', table)]
    out['details'] = [('남은 확인', {'후보 1': '추가 장면 방향·길이 선택 대기', '후보 2': '앞0.5초 참고 영상 사용자 확인 대기',
        '평가 자료': '두 사고 후보의 원본/사건·사용 근거와 QMUL과 다른 정상2원본 필요',
        '원본 연결': '전체 원본은 이 뷰어에서 자동 탐색/업로드하지 않음. 여기서는 저장 미리보기만 표시'})]
    return out


def events(reader):
    saved = reader.json(EVENTS + '/events.json')
    timing = reader.json(EVENTS + '/run.json')
    outer = reader.json('artifacts/events/m3-synthetic-20260922-01/demo-final-execution.json')
    require(saved['purpose'] == 'synthetic_test' and saved['model_calls'] == 0, 'synthetic_scope_mismatch')
    out = view(PAGES['events'], '시험용 입력 — 실제 모델 결과 아님',
               '사람이 구성한 시험용 점수로 이벤트 코드를 확인한 결과입니다. 실제 사고/정상 성능으로 해석하지 않습니다.')
    out['tables'] = [('시험용 이벤트', [{k: e.get(k) for k in ('event_id', 'status', 'positive_count',
        'evidence_start_seconds', 'evidence_end_seconds', 'alert_decision_source_time_sec', 'last_transition_reason')}
        for e in records(saved['events'], 29)]), ('입력별 처리와 중복', records(saved['delivery_receipts'], 29)),
        ('미판정·중단 기록', records(saved['unclassified'], 29))]
    out['details'] = [('시험 설정 — 실제 적용 미승인', saved['policy']),
        ('시험 실행 기록', {'전체 예시 프로세스 초': outer['process_start_to_exit_seconds'],
        '이벤트 처리 초': timing['event_processing_seconds'], 'CPU 최대 RSS bytes': timing['peak_rss_bytes'],
        '실제 독립 지표': None, '사용자 확인': saved['user_review_status']})]
    return out


def validation(reader):
    saved = reader.json(LOSSES)['independent_validation']
    require(saved['status'] == 'not_run', 'new_validation_result_requires_catalogue_update')
    out = view(PAGES['validation'], '미실행', '현재 등록된 독립 검증 결과는 없습니다. 0점이 아니라 아직 측정하지 않은 상태입니다.')
    out['tables'] = [('독립 평가 지표', [{'항목': name, '값': None, '상태': '미실행'}
        for name in ('Accuracy', '사고 Precision', '사고 Recall', '사고 F1', '평균 손실')])]
    out['details'] = [('남은 조건', {'자료': '학습과 다른 정상 원본2개 및 사고 후보의 관계/사용 근거·추가 맥락 확인',
        '구간 사고 정답': '새 사고 두 구간의 사고 포함·224 식별 답변은 이미 기록됨; 검증 후보 검수 화면 참조',
        '주의': 'QMUL 다른 시각·008/009 다른 구간은 독립 원본으로 세지 않음',
        '실행': '자료가 준비된 뒤 별도 승인 실행. 이 화면에서는 학습·추론하지 않음'})]
    return out


def expansion(reader):
    plan = reader.json(EXPANSION + '/scale-500-20260923-01/expansion-plan.json')
    evidence = reader.json(EXPANSION + '/allocation-20260923-01/split_inventory.user-reported.json')
    saved = reader.json(EXPANSION + '/scale-200-20260923-01/epoch-results-existing.json')
    require(plan.get('purpose') == 'metadata_only_expansion_proposal'
            and plan.get('training_ready') is False, 'not_a_preparation_plan')
    candidates = records(plan['candidates'], limit=600)
    require(len({r['source_path'] for r in candidates}) == len(candidates), 'duplicate_candidate_path')
    require(all(r['split'] == 'unassigned' and r['training_authorized'] is False
                for r in candidates), 'unexpected_execution_authorization')
    counts = {k: sum(r['proposed_role'] == k for r in candidates)
              for k in ('training_candidate', 'development_review_reserved')}
    require(counts == plan['counts'], 'candidate_count_mismatch')
    for role in counts:
        require(plan['requested'][role] - counts[role] == plan['shortfalls'][role], 'candidate_shortfall_mismatch')
    require(not set(evidence['splits']['train']) & set(evidence['splits']['test']), 'official_split_overlap')
    out = view(PAGES['expansion'], '확대 준비 현황 + 이전 모델의 실제 학습 로그',
               '500개를 학습한 결과가 아닙니다. 정상 구성·촬영 그룹·실행 상한은 미확정입니다. 표시된 epoch 로그는 이전 33구간 모델입니다.')
    out['tables'] = [('계획 수량과 실제 선정 수량', [
        {'구분':role, '요청':plan['requested'][role], '선정':counts[role], '부족':plan['shortfalls'][role]}
        for role in counts]),
        ('DETRAC 공식 분할 · 사용자 Colab 결과', [
            {'공식 분할':k,'시퀀스 수':len(v)} for k,v in evidence['splits'].items()]),
        ('사고 후보 목록 · 실제 입력 준비/학습 완료 수가 아님', [
            {'파일':r['source_path'],'계획 역할':r['proposed_role'],'시작 초':r['start_seconds'],
             '끝 초':r['end_seconds'],'실험 분할':r['split'],'실행 승인':False} for r in candidates])]
    epochs = records(saved['rows'], limit=100)
    require(saved['experiment'] == 't9-expansion-20260922-01', 'unexpected_epoch_experiment')
    require(all(r.get(k) is None for r in epochs for k in
                ('train_f1','validation_f1','validation_recall','validation_pr_auc_ap')), 'historical_metrics_not_recorded')
    table = [{'Epoch':r['epoch'],'Train Loss (online)':number(r['train_loss_online']),
              '정상 Train Loss':number(r['normal_train_loss_online']),
              'Train F1':'미측정','Validation F1':'미측정','Validation Recall':'미측정',
              'Validation PR-AUC(AP)':'미측정','핵심 해석':r['interpretation']} for r in epochs]
    out['tables'].append(('이전 33구간 학습 · 요청한 성능표 형식', table))
    out['charts'] = [{'title':'이전 33구간 모델의 실제 학습 손실', 'rows':epochs,
                      'x':'epoch','y':['train_loss_online','normal_train_loss_online']}]
    out['details'] = [('실행 흐름과 남은 일', {
        '흐름':'Colab 학습·평가 → 결과 Drive 저장 → Mac 결과 폴더 → Streamlit 비교',
        '현재':'후보 계획과 기존 손실 표시만 연결. 새 학습·epoch 평가 없음.',
        '남은 일':'정상 수량/원본 그룹 분할, 실행 명세·시간 상한, epoch별 평가 구현, 새 Colab 결과 등록',
        '기록':'선택 이유·변경 설정·모델/데이터/분할 버전·실측 시간·오류 사례를 보존',
        '화면 역할':'읽기 전용. 이 화면을 열어도 학습하거나 원격 결과를 자동 수신하지 않음.'})]
    return out


def colab_predictions(reader):
    base='artifacts/colab/reserved-prediction-20260923-01'
    saved=reader.json(base+'/received/observed-summary.json')
    prepared=reader.json(base+'/manifest.user.json')
    require(saved.get('independent_validation') is False and saved.get('metrics') is None
            and saved.get('inference_success_count')==10, 'prediction_only_scope_changed')
    samples=records(prepared['records'],10)
    index(samples,'sample_id')
    table=[]
    for role,prefix in [('accident','accident'),('normal','normal')]:
        group=[r for r in samples if r['requested_class']==role]
        scores=saved[prefix+'_scores']
        predictions=saved['accident_candidate_predictions' if role=='accident' else 'normal_assumption_predictions']
        require(len(group)==len(scores)==len(predictions)==5,'prediction_count_mismatch')
        for r,score,prediction in zip(group,scores,predictions):
            score=number(score)
            require(0<=score<=1 and type(prediction) is int and prediction in (0,1)
                    and prediction==int(score>0.5),'saved_score_prediction_mismatch')
            require(r['target'] is None if role=='accident' else r['target']==0,'saved_label_status_changed')
            table.append({'구간 ID':r['sample_id'],'원본':r.get('source_path') or r.get('sequence_id'),
                          '시작 초':r['start_seconds'],'끝 초':r['end_seconds'],
                          '정답 상태':'미확정' if role=='accident' else '정상 — 사용자 가정',
                          '모델 예측':label(prediction),'사고 점수':score,
                          '정오 판정':'미확정' if role=='accident' else '사용자 가정과 일치' if prediction==0 else '사용자 가정과 불일치'})
    out=view(PAGES['colab_predictions'],'사용자 Colab 실행 결과 · 예측 전용 · 독립 검증 아님',
             '10구간 실제 예측 완료. 사고 정답·사건/카메라 독립성 미확정으로 정확도·F1은 미산출입니다. 화면은 추가 모델 실행을 하지 않습니다.')
    out['tables']=[('모든 구간의 예측과 정답 상태',table),('시간 측정 범위',[
        {'범위':title,'초':number(saved[key])} for title,key in [
            ('10구간 추론 루프','inference_loop_seconds'),('모델 로딩 포함 내부 작업','model_worker_seconds_including_load'),
            ('자식 프로세스 전체','worker_wall_seconds'),('입력 준비','staging_seconds'),
            ('Drive 저장 전 활성 전체','active_seconds_before_save')]])]
    out['details']=[('장치·보존 확인',{'GPU':saved['device_user_reported'],
        '가중치·BN 유지':saved['model_and_bn_unchanged'],
        'PyTorch 최대 할당 MiB':number(saved['cuda_peak_allocated_bytes'])/2**20,
        'PyTorch 최대 예약 MiB':number(saved['cuda_peak_reserved_bytes'])/2**20,
        '주의':'GPU 전체 사용량 아님. Drive 저장·마운트·업로드·사용자 대기는 시간표에 미포함.'}),
        ('직접 확인할 자료',{'Drive 결과 폴더':saved['drive_result'],
        '미리보기':'PNG는 아직 로컬에 전달되지 않았습니다. Drive 결과 폴더 전체를 내려받아 report.html을 여세요.',
        '다음':'사고5구간 장면 검수와 사건·카메라 분리 근거 확인. 점수로 정답 생성 금지.',
        '한계':'ACCIDENT 사고/DETRAC 정상이라는 출처 차이로 구분했을 가능성은 아직 배제되지 않음.'})]
    return out


def midterm(reader):
    base='artifacts/colab/epoch-continuation-20260923-01/received'
    result=reader.json(base+'/result.json'); execution=reader.json(base+'/execution.json')
    summary=reader.json(base+'/summary.json'); epochs=records(reader.json(base+'/epoch-results.json'))
    previous=reader.json('artifacts/colab/reference-benchmark-20260923-01/received/reference-metrics.json')
    require(result['status']=='completed_limited_loop' and result['training_run'] is True
            and result['validation_run'] is False and execution['returncode']==0
            and not execution['timed_out'] and result['progress']['step']==1910
            and result['progress']['completed_epochs']==5 and result['steps_this_run']==498,
            'epoch5_completion_record_mismatch')
    require(previous['independent_validation'] is False
            and previous['model_sha256']==result['parent_checkpoint_sha256']
            and previous['scored_segments']+previous['failed_or_missing']==previous['expected_segments'],
            'previous_reference_scope_mismatch')
    require(sum(r['recorded_updates'] for r in epochs)==498,'epoch_log_count_mismatch')
    loss=[]
    for r in epochs:
        count=r['recorded_updates']
        require(r['epoch_complete']==(count==382),'epoch_coverage_mismatch')
        loss.append({'집계 범위':f"epoch {r['epoch']} · {'전체' if r['epoch_complete'] else '이번 실행 부분'} {count}회",
                     '평균 학습 loss':number(r['online_mean_loss'])})
    out=view(PAGES['midterm'],'중간보고 · 5 epoch 학습 완료 / 새 모델 평가 미실행',
             '이전 모델의 23구간 참고 성능과 새 모델의 학습 완료를 구분합니다. 독립 검증 성능이 아니며 미실행은 0점이 아닙니다.')
    metrics=[]
    for title,k in [('Accuracy','accuracy'),('사고 Precision','accident_precision'),('사고 Recall','accident_recall'),('사고 F1','accident_f1')]:
        score=number(previous['metrics'][k]);require(0<=score<=1,'invalid_reference_metric')
        metrics.append({'지표':title,'이전 1412 step 모델':f'{score*100:.2f}%','새 1910 step 모델':'미측정 · 비교 평가 미실행'})
    timings=[{'범위':'입력 준비','초':number(summary['staging']['staging_seconds'])},
             {'범위':'이번 학습 worker (저장 포함)','초':number(result['worker_wall_seconds'])},
             {'범위':'전체 명령','초':number(execution['command_wall_seconds'])},
             {'범위':'부모 파일 준비 포함 · Drive 저장 전','초':number(execution['active_seconds_before_save'])}]
    parts=[{'항목':k,'초':number(result['timing'][key])} for k,key in
           [('입력 읽기','input_seconds'),('모델 계산','compute_seconds'),('체크포인트 저장','checkpoint_seconds')]]
    out['tables']=[('현재 완료와 미실행',[
        {'작업':'누적 5 epoch 학습','상태':'실행 확인','근거':'382구간 × 5 = 1,910 업데이트; 이번 추가 498'},
        {'작업':'저장 가중치 재로딩','상태':'실행 확인' if result['saved_state_reload_verified'] else '미확인','근거':'state_dict 일치; 재로딩 후 추론은 미실행'},
        {'작업':'새 모델 23구간 비교 평가','상태':'미실행','근거':'이전 점수 재사용, 새 모델 예측 필요'},
        {'작업':'독립 CCTV·긴 영상 사고 구간 평가','상태':'미실행','근거':'사건·카메라 독립성, 시간 구간 정답 확인 필요'},
        {'작업':'사용자 화면 확인','상태':'확인 대기','근거':'화면을 열어도 검수 기록은 변경하지 않음'}]),
        ('학습 구간 구성',[{'클래스':'사고','구간 수':182},{'클래스':'정상','구간 수':200}]),
        ('이번 실행에 기록된 loss',loss),('모델 비교 · 참고 지표',metrics),
        ('이전 모델 평가의 처리 범위',[{'전체 예약':previous['expected_segments'],'예측 성공':previous['scored_segments'],
          '입력 실패':previous['failed_or_missing'],**previous['confusion_matrix']}]),
        ('시간 측정 범위 · 아래 항목은 서로 포함되므로 합산하지 않음',timings)]
    out['charts']=[{'kind':'bar','title':'학습 loss · 부분/전체 평균을 추세로 연결하지 않음','rows':loss,'x':'집계 범위','y':'평균 학습 loss'},
                   {'kind':'bar','title':'이번 학습의 기록된 시간 구성 (초)','rows':parts,'x':'항목','y':'초'}]
    out['details']=[('해석과 한계',{'loss':'학습 중 batch 평균이며 정확도가 아님. 4번째 일부116개와 5번째 전체382개는 직접 비교 불가.',
        '참고 평가':'원본 주석 사고 / 사용자 가정 정상. 성공23에만 지표 계산, 실패7은 별도. 출처 편향·사건/카메라 독립성 미확인.',
        '다음 작업':'새 모델로 기존 성공23구간을 예측해 이전 모델과 비교. 현재 화면은 실행 버튼 없는 읽기 전용.',
        '시간':'Drive 최종 저장·마운트·업로드·사용자 대기 제외. 누적 training_seconds는 이전 실행도 포함한다.'}),
        ('모델 보존 확인',{'장치':result['actual_device'],'특징부·BN 유지':result['features_and_bn_unchanged'],
          '분류층 변경':result['head_changed'],'재로딩 추론':result['reload_inference_run']})]
    received='artifacts/colab/epoch5-comparison-20260923-01/received'
    if (reader.root/received/'comparison.json').exists():
        comp=reader.json(received+'/comparison.json');new=reader.json(received+'/reference-metrics.json')
        execution_new=reader.json(received+'/execution.json')
        require(execution_new['status']=='completed' and execution_new['worker']['returncode']==0
                and not execution_new['worker']['timed_out'] and comp['training_run'] is False
                and comp['independent_validation'] is False and new['independent_validation'] is False
                and new['previous_model_sha256']==previous['model_sha256']
                and comp['previous_metrics']==previous['metrics'] and comp['new_metrics']==new['metrics'],
                'comparison_receipt_mismatch')
        old_rows={r['sample_id']:r for r in previous['rows']};new_rows={r['sample_id']:r for r in new['rows']}
        require(len(new_rows)==len(new['rows'])==30 and set(old_rows)==set(new_rows)
                and len(comp['rows'])==30,'comparison_rows_changed')
        require({s for s,r in old_rows.items() if r['status']=='predicted'}==
                {s for s,r in new_rows.items() if r['status']=='predicted'} and comp['same_scored_samples'] is True,
                'comparison_denominators_differ')
        table=[]
        for r in comp['rows']:
            sid=r['sample_id'];old=old_rows[sid];fresh=new_rows[sid]
            require(r['previous_prediction']==old['prediction'] and r['new_prediction']==fresh['prediction']
                    and r['previous_score']==old['accident_score'] and r['new_score']==fresh['accident_score'],
                    'comparison_prediction_mismatch')
            table.append({'구간':sid,'참고 정답':fresh['reference_target'],'이전 예측':old['prediction'],
                          '새 예측':fresh['prediction'],'이전 사고 점수':old['accident_score'],
                          '새 사고 점수':fresh['accident_score'],'새 정오':fresh['correct'],'상태':fresh['status']})
        for row,k in zip(metrics,('accuracy','accident_precision','accident_recall','accident_f1')):
            value=number(new['metrics'][k]);require(0<=value<=1,'invalid_reference_metric')
            row['새 1910 step 모델']=f'{value*100:.2f}%'
        metrics.append({'지표':'고정 모델 평균 CE loss','이전 1412 step 모델':f"{previous['metrics']['mean_loss']:.5f}",
                        '새 1910 step 모델':f"{new['metrics']['mean_loss']:.5f}"})
        out['scope']='중간보고 · 5 epoch 학습 및 동일23구간 참고 비교 완료'
        out['notice']='두 모델의 23구간 판정은 동일합니다. 새 모델 평균 참고 loss는 상승했습니다. 독립 검증이 아니며 실패7구간은 별도 유지합니다.'
        status=out['tables'][0][1][2];status.update(상태='실행 확인',근거='기존23입력 동일 비교; 독립 검증 아님')
        out['tables'].append(('새·이전 모델 전체30후보 비교 (0 정상 / 1 사고)',table))
        out['tables'].append(('이번 비교 평가 시간',[
            {'범위':'입력·모델 준비','초':execution_new['staging']['seconds']},
            {'범위':'비교 worker 전체','초':execution_new['worker']['seconds']},
            {'범위':'저장 전 전체','초':execution_new['active_seconds_before_save']}]))
        out['details'][0][1]['다음 작업']='추가 epoch 효과가 확인되지 않음. 같은 설정을 유지하고 기존 공식train 중 미사용 자료 확대 목록을 검토(제안).'
        out['details'].append(('비교 모델 SHA',{'이전':previous['model_sha256'],'새 모델':new['model_sha256']}))
    return out


def load_view(root, key):
    require(key in PAGES, 'unknown_catalogue_entry')
    reader = ArtifactReader(root)
    try:
        result = {'midterm': midterm, 'latest': latest_training, 'training': training, 'continuous': continuous, 'review': reviews,
                  'events': events, 'validation': validation, 'expansion': expansion,
                  'colab_predictions':colab_predictions}[key](reader)
    except (KeyError, TypeError, IndexError, AttributeError):
        raise ViewerError('saved_schema_mismatch') from None
    except OSError:
        raise ViewerError('artifact_unavailable') from None
    result['source_sha256'] = dict(reader.reads)
    result['viewer_model_calls'] = 0
    result['viewer_training_updates'] = 0
    return result
