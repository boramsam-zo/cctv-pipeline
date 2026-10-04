"""M3 synthetic-only local event example: <=29 deliveries, no model/video reads."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import platform
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from boram3_cctv.events import EventError, EventProcessor, MAX_RECORDS, require


def write(path, value):
    with path.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def table(headers, rows):
    esc = lambda v: html.escape(str(v)) if v is not None else "—"
    return "<table><thead><tr>" + "".join(f"<th>{esc(x)}</th>" for x in headers) + \
        "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in row) + \
        "</tr>" for row in rows) + "</tbody></table>"


def write_report(path, result, rows):
    events = result["events"]
    receipts = result["delivery_receipts"]
    display_rows = []
    for index, raw in enumerate(rows):
        row = raw if isinstance(raw, dict) else {}
        if index < len(receipts):
            action, event_id = receipts[index]['action'], receipts[index]['event_id']
        else:
            action = 'failed_input' if index == len(receipts) else 'not_processed_after_failure'
            event_id = None
        display_rows.append((index + 1, row.get('prediction_id', row.get('window_id')),
            f"{row.get('start_seconds', '?')}–{row.get('end_seconds', '?')}",
            row.get('decision_source_time_sec'), row.get('prediction', row.get('status', 'invalid_object')),
            action, event_id))
    body = """<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>M3 이벤트 시험</title>
<style>body{font:16px/1.6 system-ui;max-width:1150px;margin:30px auto;padding:0 20px;color:#172536;background:#f5f7fa}
table{border-collapse:collapse;background:white;width:100%;margin:20px 0}th,td{padding:10px;border-bottom:1px solid #d6e0e9;text-align:left;overflow-wrap:anywhere}
aside{padding:20px;background:#fff0cb}code{overflow-wrap:anywhere}a{color:#075ba5}</style>
<h1>연속 의심 결과 묶기 · 시험용 입력</h1>
<aside>모든 입력은 사람이 구성한 <strong>synthetic_test</strong>입니다. 실제 영상·모델 예측·사고 정답이 아닙니다.
실제 사고 탐지 성능과 독립 검증은 미실행입니다. 영상 미리보기도 새로 생성하지 않았습니다.</aside>
<p>이벤트는 여러 의심 예측을 사람이 검토할 하나의 항목으로 묶은 기록입니다. ‘열림’은 로컬 기록 생성이며 외부 알림 전송이 아닙니다.</p>
<p>이번 시험 설정은 새 사고 예측CONFIRM_COUNT회로 열고, 정상 예측RELEASE_COUNT회로 닫습니다. 중복 예측은 횟수에 더하지 않습니다.
열린 동안만 묶고, 종료 후에는 다음 의심 결과에서 새 후보를 만듭니다. 대기시간(cooldown)은 시험에서0초입니다.
이 값들은 실제 적용 승인이나 최적값이 아닙니다. 저장 판정(argmax, 동점 정상)을 사용하며 점수 임계값을 바꾸지 않습니다.</p>
<p>확인 순서: 입력/중복 표 → 이벤트 표 → 상태 이력 → <a href="events.json">events.json</a>과 <a href="run.json">시간·범위 기록</a>.</p>
<h2>입력 전달과 처리 결과</h2>"""
    body = body.replace('CONFIRM_COUNT', str(result['policy']['confirm_count'])).replace(
        'RELEASE_COUNT', str(result['policy']['release_count']))
    body += '<p>실행 상태: <strong>' + html.escape(result['status']) + '</strong></p>'
    if result['error']:
        body += '<aside>실패 사유: ' + html.escape(json.dumps(result['error'], ensure_ascii=False)) + \
            ' · 실패와 이후 미처리 행도 아래에 남겼습니다.</aside>'
    body += table(["전달", "입력 ID", "시험 원본 구간(초)", "판단 시각", "입력 판정/상태", "처리", "이벤트 ID"], display_rows)
    body += "<h2>이벤트 기록</h2><p>candidate=후보, open=열림, closed=시험 해제 규칙 충족, " \
        "cancelled=확인 중단, incomplete=종료 판단 전에 입력 중단. 어느 상태도 실제 사고 종료 정답을 뜻하지 않습니다.</p>"
    body += table(["이벤트 ID", "상태", "의심 입력 범위", "최초 의심 판단", "열림 판단", "새 사고 예측 수", "마지막 사유"],
        [(e['event_id'], e['status'], f"{e['evidence_start_seconds']}–{e['evidence_end_seconds']}",
          e['first_suspicion_source_time_sec'], e['alert_decision_source_time_sec'], e['positive_count'], e['last_transition_reason']) for e in events])
    body += "<p>입력 범위는 모델이 본 범위입니다. 최초 의심/열림 판단 시각과 구분하며 정확한 충돌 시작·종료라고 하지 않습니다. 겹친 두 입력도 독립된 두 사건의 증거가 아닙니다.</p><h2>상태 이력</h2>"
    body += table(["이벤트 ID", "상태", "시험 원본 판단 시각", "사유"],
        [(t['event_id'], t['status'], t['decision_source_time_sec'], t['reason']) for t in result['transitions']])
    body += f"<p>요청 {len(rows)}건 · 처리 {len(receipts)}건 · 실패/미처리 {len(rows)-len(receipts)}건 · 중복 제외 고유 구간 {result['unique_windows']}개 · 미판정 기록 {len(result['unclassified'])}개. 오류/미판정은 정상으로 바꾸지 않습니다.</p>"
    body += "<p>UTC는 이번 기록 시각, elapsed_wall_sec는 프로그램 경과시간입니다. 실제 과거 영상의 알림 시각을 복원한 결과가 아닙니다. 동일 run/source 전체 입력을 재생하면 논리 이벤트를 재구성하며, 증분 체크포인트 재개는 아직 없습니다.</p><p>사용자 직접 확인: 대기. 다음 실제 적용에는 이벤트 규칙 선택이 필요합니다.</p></html>"
    with path.open("x") as f:
        f.write(body)


def run(input_path, output):
    started = time.perf_counter()
    require(input_path.is_file() and input_path.stat().st_size <= 128 * 1024, "missing_or_oversized_input")
    raw = input_path.read_bytes()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise EventError("invalid_input_json") from None
    require(isinstance(data, dict) and data.get("schema_version") == 1 and
            data.get("purpose") == "synthetic_test", "synthetic_input_required")
    rows = data.get("rows")
    require(isinstance(rows, list) and 1 <= len(rows) <= MAX_RECORDS, "select_1_to_29_deliveries")
    processor = EventProcessor(data.get("context"), data.get("policy"))
    artifacts = ROOT / "artifacts"
    require(output.resolve().is_relative_to(artifacts.resolve()) and output.resolve() != artifacts.resolve(),
            "output_must_be_under_project_artifacts")
    require(not output.exists(), "existing_output_protected")
    require(not input_path.resolve().is_relative_to(output.resolve()), "output_overlaps_input")
    output.mkdir(parents=True, exist_ok=False)
    deliveries = []
    error = None
    analysis_started = time.perf_counter()
    for index, row in enumerate(rows):
        try:
            require(time.perf_counter() - started < 30, "event_loop_time_limit")
            deliveries.append(processor.consume(row, emitted_at_utc=utc_now(),
                                               elapsed_wall_sec=time.perf_counter() - started))
        except EventError as exc:
            error = {"delivery_index": index, "reason": str(exc), "remaining_deliveries": len(rows) - index}
            break
    result = processor.finish(reason="analysis_failed" if error else "source_ended",
                              emitted_at_utc=utc_now(), elapsed_wall_sec=time.perf_counter() - started)
    analysis_seconds = time.perf_counter() - analysis_started
    unchanged = hashlib.sha256(input_path.read_bytes()).digest() == hashlib.sha256(raw).digest()
    if not unchanged:
        error = {"reason": "input_changed_during_run"}
    result.update(status="failed" if error else "completed_synthetic_test", error=error,
                  delivery_receipts=deliveries, total_deliveries=len(rows), inputs_unchanged=unchanged)
    write(output / "events.json", result)
    write_report(output / "report.html", result, rows)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    info = {"status": result["status"], "purpose": "synthetic_test", "error": error,
            "command": ["scripts/build_events.py", "--input", input_path.name, "--output", output.name],
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                            (Path(__file__), ROOT / "src/boram3_cctv/events.py")},
            "policy_sha256": processor.policy_sha256, "inputs_unchanged": unchanged,
            "actual_device": "cpu", "system": platform.system(), "machine": platform.machine(),
            "python": platform.python_version(), "peak_rss_bytes": int(rss if sys.platform == 'darwin' else rss * 1024),
            "event_processing_seconds": analysis_seconds, "prepare_process_report_seconds": time.perf_counter() - started,
            "timing_boundary": "run entry to events/HTML saved; excludes process startup and final run.json write",
            "loop_limit_seconds": 30, "max_deliveries": MAX_RECORDS,
            "model_calls": 0, "training_updates": 0, "video_frames_decoded": 0,
            "user_review_status": "pending"}
    write(output / "run.json", info)
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.input, args.output)
    except (EventError, OSError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc) if isinstance(exc, EventError) else type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "completed_synthetic_test" else 2


if __name__ == "__main__":
    raise SystemExit(main())
