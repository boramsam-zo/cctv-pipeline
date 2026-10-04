"""VLM facts -> neural embedding + SQLite -> operator agency report."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = "intfloat/multilingual-e5-small"
STATES = ["observed", "not_observed", "unknown"]


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def schema():
    signal = {"type": "object", "additionalProperties": False,
              "required": ["state", "evidence_ids"], "properties": {
                  "state": {"enum": STATES}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}}
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "event_id", "candidate_time_s", "description", "location", "scene", "participants", "accident", "hazards", "people", "evidence", "uncertainties"],
            "properties": {
                "schema_version": {"const": "2.0"}, "event_id": {"type": "string", "minLength": 1, "pattern": "^[A-Za-z0-9_-]+$"},
                "candidate_time_s": {"type": ["number", "null"], "minimum": 0}, "description": {"type": "string", "minLength": 1},
                "location": {"type": "object", "additionalProperties": False, "required": ["camera_id", "registered_location", "road_type", "tunnel", "mountain_road", "near_forest"], "properties": {
                    "camera_id": {"type": ["string", "null"]}, "registered_location": {"type": ["string", "null"]},
                    "road_type": {"enum": ["highway", "urban", "rural", "unknown"]},
                    **{k: signal for k in ["tunnel", "mountain_road", "near_forest"]}}},
                "scene": {"type": "object", "additionalProperties": False, "required": ["day_time", "weather"], "properties": {
                    "day_time": {"enum": ["day", "night", "unknown"]}, "weather": {"enum": ["clear", "cloudy", "rain", "snow", "fog", "unknown"]}}},
                "participants": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                    "required": ["id", "type", "count", "evidence_ids"], "properties": {
                        "id": {"type": "string"}, "type": {"enum": ["Car", "Truck", "Bus", "Motorcycle", "Bicycle", "Pedestrian", "Unknown"]},
                        "count": {"type": "integer", "minimum": 1}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}}},
                "accident": {"type": "object", "additionalProperties": False, "required": ["forms", "vehicle_count", "collision_count"], "properties": {
                    "forms": {"type": "array", "uniqueItems": True, "items": {"enum": ["rear_end", "head_on", "side_collision", "single_vehicle", "multi_vehicle", "rollover", "structure_collision", "fall", "unknown"]}},
                    "vehicle_count": {"type": ["integer", "null"], "minimum": 1}, "collision_count": {"type": ["integer", "null"], "minimum": 0}}},
                "hazards": {"type": "object", "additionalProperties": False, "required": ["fire", "smoke", "debris", "cargo_spill", "lane_blocked", "forest_fire_spread", "secondary_collision", "dangerous_goods_mark"],
                    "properties": {k: signal for k in ["fire", "smoke", "debris", "cargo_spill", "lane_blocked", "forest_fire_spread", "secondary_collision", "dangerous_goods_mark"]}},
                "people": {"type": "object", "additionalProperties": False, "required": ["person_on_road", "rider_separated", "visible_entrapment"],
                    "properties": {k: signal for k in ["person_on_road", "rider_separated", "visible_entrapment"]}},
                "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "observation"], "properties": {
                    "id": {"type": "string"}, "observation": {"type": "string", "minLength": 1}}}},
                "uncertainties": {"type": "array", "items": {"type": "string"}}
            }}


def validate(event):
    from jsonschema import Draft202012Validator
    Draft202012Validator(schema()).validate(event)
    ids = [e["id"] for e in event["evidence"]]
    if len(ids) != len(set(ids)):
        raise ValueError("근거 ID 중복")
    for group in [event["hazards"], event["people"], {k: event["location"][k] for k in ["tunnel", "mountain_road", "near_forest"]}]:
        for key, value in group.items():
            if value["state"] == "observed" and not value["evidence_ids"]:
                raise ValueError(f"{key}: 관찰됨에는 근거 ID가 필요함")
            if set(value["evidence_ids"]) - set(ids):
                raise ValueError(f"{key}: 존재하지 않는 근거 ID")
    for p in event["participants"]:
        if set(p["evidence_ids"]) - set(ids):
            raise ValueError("객체 근거 ID가 존재하지 않음")


@lru_cache(maxsize=2)
def encoder(model=MODEL, revision=None, local_path=None):
    from sentence_transformers import SentenceTransformer
    local = local_path or os.environ.get("RAG_MODEL_PATH")
    if local:
        return SentenceTransformer(local, local_files_only=True, device="cpu")
    return SentenceTransformer(model, revision=revision, cache_folder=str(ROOT / "model_cache"), device="cpu")


def split_passages(model, text):
    # Preserve full original content in DB; embed token windows to avoid truncation.
    ids = model.tokenizer.encode(text, add_special_tokens=False)
    if not ids:
        return [text]
    return [model.tokenizer.decode(ids[i:i+440], skip_special_tokens=True) for i in range(0, len(ids), 380)]


def build_index(chunks, database, revision=None):
    import numpy as np
    model = encoder(revision=revision)
    rows = [json.loads(line) for line in Path(chunks).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    legal = [r for r in rows if r.get("corpus_kind") != "statistics" and r.get("result_group") != "statistics"]
    chunks_by_id = {}
    for row in legal:
        cid = row.get("chunk_id")
        if not cid or not row.get("content"):
            raise ValueError("chunk_id 또는 content 누락")
        if cid in chunks_by_id and chunks_by_id[cid] != row:
            raise ValueError(f"서로 다른 내용의 chunk_id 중복: {cid}")
        chunks_by_id[cid] = row
    texts, passage_rows = [], []
    for cid, row in chunks_by_id.items():
        text = row.get("embedding_input") or (row.get("title", "") + " " + row["content"])
        for number, passage in enumerate(split_passages(model, text)):
            passage_rows.append((cid, number, passage))
            texts.append("passage: " + passage)
    vectors = model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=True).astype(np.float32)
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    staging = Path(str(database) + ".building")
    if staging.exists():
        raise ValueError(f"이전 미완료 DB를 확인하세요: {staging}")
    con = sqlite3.connect(staging)
    with con:
        con.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        con.execute("CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,payload TEXT)")
        con.execute("CREATE TABLE vectors(id INTEGER PRIMARY KEY,chunk_id TEXT,part INTEGER,text TEXT,vector BLOB)")
        for cid, row in chunks_by_id.items():
            con.execute("INSERT INTO chunks VALUES(?,?)", (cid, json.dumps(row, ensure_ascii=False)))
        for (cid, part, text), v in zip(passage_rows, vectors):
            con.execute("INSERT INTO vectors(chunk_id,part,text,vector) VALUES(?,?,?,?)", (cid, part, text, v.tobytes()))
        actual_revision = getattr(model[0].auto_model.config, "_commit_hash", None)
        metadata = {"model": MODEL, "revision": actual_revision or revision, "dimensions": int(vectors.shape[1]),
                    "normalized": True, "source_sha256": hashlib.sha256(Path(chunks).read_bytes()).hexdigest(),
                    "all_chunks": len(rows), "legal_chunks": len(chunks_by_id), "passages": len(texts),
                    "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        for k, v in metadata.items():
            con.execute("INSERT INTO metadata VALUES(?,?)", (k, json.dumps(v)))
    con.close()
    staging.replace(database)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


def tokenize(text):
    # word BM25; common Korean compounds receive explicit query synonyms.
    return re.findall(r"[가-힣a-zA-Z0-9]+", text.lower())


def bm25(query, docs):
    corpus = [tokenize(d.get("embedding_input") or d["content"]) for d in docs]
    df = Counter(t for ts in corpus for t in set(ts))
    n, average = len(corpus), sum(map(len, corpus)) / max(1, len(corpus))
    scores = []
    for d, ts in zip(docs, corpus):
        tf, score = Counter(ts), 0.0
        for t in set(tokenize(query)):
            if tf[t]:
                idf = math.log(1 + (n - df[t] + .5) / (df[t] + .5))
                score += idf * tf[t] * 2.5 / (tf[t] + 1.5 * (.25 + .75 * len(ts) / average))
        if score > 0:
            scores.append((d["chunk_id"], score))
    return sorted(scores, key=lambda x: x[1], reverse=True)


def observed(event, group, field):
    return event[group][field]["state"] == "observed"


def topics(event):
    t = {"traffic"}
    if event["location"]["road_type"] == "highway": t.add("highway")
    if observed(event, "location", "tunnel"): t.add("tunnel")
    if observed(event, "location", "mountain_road"): t.add("mountain")
    if observed(event, "hazards", "fire"): t.add("fire")
    if observed(event, "hazards", "smoke"): t.add("smoke")
    if observed(event, "hazards", "forest_fire_spread"): t.add("forest")
    if observed(event, "people", "visible_entrapment") or "rollover" in event["accident"]["forms"]: t.add("rescue")
    if observed(event, "hazards", "lane_blocked"): t.add("blocked")
    if observed(event, "hazards", "debris") or observed(event, "hazards", "cargo_spill"): t.add("debris")
    if "multi_vehicle" in event["accident"]["forms"]: t.add("multi")
    if any(v["state"] == "observed" for v in event["people"].values()): t.add("people")
    return t


QUERY_TERMS = {"traffic": "교통사고 초동조치 관계기관 공동대응 통보 사고유형 사진촬영", "highway": "고속도로순찰대 초동조치",
               "tunnel": "터널 사고 터널관리자 진입통제 대피", "mountain": "산지 도로 사고 접근로", "fire": "차량 화재 소방 진화 구조 출동",
               "smoke": "연기 대피 소방 확인", "forest": "산불 산림재난 상황통보 산림청 지자체 진화",
               "blocked": "교통통제 도로관리청 도로관리자", "debris": "파편 도로 장애물 제거 현장보존",
               "multi": "다중 충돌 다수 차량 사고 공동대응", "people": "구조 구급 사상자 확인 응급구호"}


def applicable(doc, ts):
    meta = doc.get("metadata") or {}
    tags = " ".join(meta.get("scenario_tags") or [])
    title = doc.get("title", "")
    specific = title + " " + " ".join(meta.get("scenario_tags") or [])
    if "화재" in specific and "fire" not in ts:
        return False
    # Broad articles are not excluded merely because one paragraph mentions a special scenario.
    specific = tags + " " + title
    conditions = " ".join(meta.get("application_conditions") or [])
    title = doc.get("title", "")
    if any(term in conditions + title for term in ["사체", "로드킬", "동물이 죽", "시설물 손상", "도로시설물 복구", "원인자를 확인할 수 없"]):
        return False
    if "고속국도" in conditions and "highway" not in ts:
        return False
    if any(term in conditions + title for term in ["의료소", "긴급구조 현장지휘", "사상자 수", "주정차", "강제처분"]):
        return False
    if any(term in doc.get("content", "") + title for term in ["소방활동구역", "행정대집행"]):
        return False  # field establishment / coercive-law prerequisites are not in the VLM input
    if "지방자치단체 협력이 필요한" in conditions:
        return False
    if "교량" in tags or "교량" in conditions: return False
    if "다수 사상자" in conditions: return False  # must come from verified field report, not vehicle count
    if "전복·차체 끼임" in tags and "차량 충돌" not in tags and "rescue" not in ts: return False
    if "위험물" in conditions or "화학물질" in conditions: return False
    if "고속도로순찰대" in title and "highway" not in ts: return False
    for term, topic in [("터널", "tunnel"), ("산불", "forest"), ("산림재난", "forest")]:
        if term in specific and topic not in ts: return False
    if any(s in specific for s in ["위험물", "화학물질", "전기차", "배터리", "침수", "붕괴", "뺑소니", "주취"]):
        return False  # no supported verified fields for these legal triggers in v2
    if "화재" in specific and not ({"fire", "smoke"} & ts): return False
    return True


def observed_features(event):
    return [f"{group}.{key}" for group in ["location", "hazards", "people"]
            for key, value in event[group].items()
            if isinstance(value, dict) and value.get("state") == "observed"]


def check_source(doc, plan, ts):
    meta = doc.get("metadata") or {}
    actors = meta.get("actors") or []
    aliases = {"police": ["경찰"], "fire_rescue": ["소방", "구조", "구급"],
               "road_manager": ["도로", "터널", "관리자"], "forest": ["산림"],
               "local_government": ["지자체", "지방자치", "시장", "군수"]}
    if not actors or not any(a in actor for actor in actors for a in aliases[plan["agency_key"]]):
        return False, "조치 주체 불일치 또는 미확인"
    conditions = " ".join(meta.get("application_conditions") or [])
    if not applicable(doc, ts):
        return False, "상황 조건 불일치"
    if "화재" in conditions and "확인" in conditions and "fire" not in ts:
        return False, "화재 확인 조건 미충족"
    if any(t in conditions for t in ["실제 끼임", "요구조자 확인", "사상자가 확인"]):
        if not ({"people", "rescue"} & ts):
            return False, "현장 확인 조건 미충족"
    return True, "주체·구조화 조건 점검 완료; 본문 요건·예외는 담당자 확인 필요"


def search_topics(event):
    ts = topics(event)
    result = [("common_initial_response", QUERY_TERMS["traffic"], {"traffic"})]
    base = re.split(r"검색\s*단서\s*:", event["description"])[0].strip()
    for topic in sorted(ts - {"traffic"}):
        result.append((topic, base + " " + QUERY_TERMS.get(topic, "구조 대응"), ts))
    return result


def retrieve(database, event, contacts, *, model_path=None, budget_check=None):
    import numpy as np
    check_budget = budget_check or (lambda: None)
    check_budget()
    validate(event)
    database = Path(database).resolve()
    if not database.is_file():
        raise FileNotFoundError("먼저 index 명령으로 DB를 구축하세요: " + str(database))
    con = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    meta = {k: json.loads(v) for k, v in con.execute("SELECT key,value FROM metadata")}
    docs = [json.loads(r[0]) for r in con.execute("SELECT payload FROM chunks ORDER BY chunk_id")]
    vector_rows = list(con.execute("SELECT chunk_id, vector FROM vectors"))
    con.close()
    check_budget()
    model = encoder(meta["model"], meta["revision"], local_path=model_path)
    check_budget()
    actual_revision = getattr(model[0].auto_model.config, "_commit_hash", None)
    if meta["revision"] and actual_revision and meta["revision"] != actual_revision:
        raise ValueError("인덱스 모델 revision과 검색 모델이 다릅니다")
    ts = topics(event)
    by_id = {d["chunk_id"]: d for d in docs}
    matrix = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in vector_rows])
    if matrix.shape[1] != meta["dimensions"]: raise ValueError("벡터 차원 불일치")
    base = re.split(r"검색\s*단서\s*:", event["description"])[0].strip()
    plans = agency_plans(event)
    search_trace, sources = [], {}
    for plan in plans:
        fused, ranks, dense, topic_traces, checks = {}, {}, {}, [], {}
        for topic, topic_query, scope_ts in search_topics(event):
            agency_query = topic_query + " " + plan["agency"] + " " + plan["task_terms"]
            candidates = []
            for doc in docs:
                if not any(plan["agency_match"] in a for a in (doc.get("metadata") or {}).get("agencies", [])):
                    continue
                valid, reason = check_source(doc, plan, scope_ts)
                checks[doc["chunk_id"]] = {"accepted": valid, "reason": reason}
                if valid:
                    candidates.append(doc)
            candidate_ids = {d["chunk_id"] for d in candidates}
            bm = bm25(agency_query, candidates)[:20]
            check_budget()
            qv = model.encode(["query: " + agency_query], normalize_embeddings=True)[0]
            check_budget()
            local_dense = {}
            for (cid, _), score in zip(vector_rows, matrix @ qv):
                if cid in candidate_ids:
                    local_dense[cid] = max(local_dense.get(cid, -1), float(score))
            sem = sorted(local_dense.items(), key=lambda x: x[1], reverse=True)[:20]
            local_fused, local_ranks = {}, {}
            for method, weight, hits in [("bm25", .6, bm), ("embedding", .4, sem)]:
                for rank, (cid, score) in enumerate(hits, 1):
                    local_fused[cid] = local_fused.get(cid, 0) + weight / (60 + rank)
                    local_ranks.setdefault(cid, {})[method] = {"rank": rank, "score": score}
            eligible = [cid for cid in sorted(local_fused, key=local_fused.get, reverse=True)
                        if local_dense.get(cid, -1) >= .65][:3]
            for cid in eligible:
                if local_fused[cid] > fused.get(cid, -1):
                    fused[cid], ranks[cid] = local_fused[cid], local_ranks[cid]
                dense[cid] = max(dense.get(cid, -1), local_dense[cid])
            topic_traces.append({"topic": topic, "query": agency_query, "bm25_top20": bm,
                                 "embedding_top20": sem, "selected_chunk_ids": eligible})
        # Preserve common and scenario-topic hits independently, then deduplicate.
        common = topic_traces[0]["selected_chunk_ids"][:1]
        scenario = list(dict.fromkeys(cid for trace in topic_traces[1:] for cid in trace["selected_chunk_ids"] if cid not in common))
        scenario.sort(key=lambda cid: fused[cid], reverse=True)
        chosen = common + scenario[:3 - len(common)]
        if not scenario:
            chosen = topic_traces[0]["selected_chunk_ids"][:3]
        actual_features = observed_features(event)
        plan["observed_features"] = actual_features
        # Keep the agency-specific rationale, especially conditional rescue.
        # Observed features remain available separately without replacing it.
        plan["legal_basis"] = chosen
        plan["evidence_status"] = "retrieved_candidates" if chosen else "insufficient_evidence"
        plan["contact"] = contacts.get(plan["agency_key"], {"channel": "등록 연락처 없음", "address": None})
        plan["handoff_message"] = {
            "event_id": event["event_id"], "location": event["location"]["registered_location"],
            "camera_id": event["location"]["camera_id"], "summary": base,
            "confirmed_visual_conditions": sorted(ts), "human_status": "현장 확인 필요",
            "notification_condition": plan["notification"]}
        plan["retrieved_action_excerpt"] = [
            {"chunk_id": cid, "text": summarize_source(by_id[cid]["content"])} for cid in chosen]
        if not chosen:
            plan["actions"] = []
            plan["note"] = "해당 기관의 조치 근거를 찾지 못함. 담당자가 확인해야 함."
        for cid in chosen:
            d = by_id[cid]
            sources[cid] = {"chunk_id": cid, "title": d.get("title"), "section": d.get("section"), "content": d["content"],
                            "source_url": d.get("source_url"), "corpus_kind": d.get("corpus_kind"),
                            "content_role": (d.get("metadata") or {}).get("content_role", "unspecified"),
                            "actors": (d.get("metadata") or {}).get("actors", []),
                            "application_conditions": (d.get("metadata") or {}).get("application_conditions", []),
                            "verification_status": (d.get("metadata") or {}).get("verification_status"),
                            "source_version": d.get("version"), "metadata_is_editorial": True}
        search_trace.append({"agency": plan["agency"], "topics": topic_traces,
                             "condition_actor_checks": checks,
                             "selected": [{"chunk_id": c, "rrf": fused[c], "search": ranks[c]} for c in chosen]})
    result = {"event_id": event["event_id"], "status": "draft_requires_operator_review", "schema_version": "2.0",
            "input": event, "accident_summary": base, "observed_topics": sorted(ts), "agency_notifications": plans,
            "sources": list(sources.values()), "uncertainties": event["uncertainties"],
            "generation": {"mode": "rule_based_templates", "llm_connected": False,
                           "actions_are": "운영 보조 제안; 개별 문장의 법적 의무를 자동 확정하지 않음"},
            "retrieval": {"model": meta, "db": str(Path(database).resolve()), "vector_search": "SQLite BLOB 저장 후 정규화 벡터 내적 전수검색",
                          "fusion": "weighted_rrf", "weights": {"bm25": .6, "embedding": .4}, "rrf_k": 60,
                          "experimental_cosine_floor": .65, "trace": search_trace,
                          "date_filter_supported": False, "reference_date": None}}
    result.update(report_view(result))
    return result


def verification_needed(event):
    """List absent or unknown input values without changing the VLM facts."""
    needed = list(event["uncertainties"])
    optional_fields = [
        (event["candidate_time_s"], "영상 후보 시각 미제공: 원본 영상 시각 확인 필요"),
        (event["location"]["camera_id"], "카메라 ID 미등록: 카메라 식별 정보 확인 필요"),
        (event["location"]["registered_location"], "등록 위치 미제공: 사건 위치 확인 필요"),
        (event["accident"]["vehicle_count"], "관련 차량 수 미확인: 현장 확인 필요"),
        (event["accident"]["collision_count"], "충돌 횟수 미확인: 영상·현장 확인 필요"),
    ]
    for value, message in optional_fields:
        if value is None or value == "":
            needed.append(message)
    labels = {
        "location": {"tunnel": "터널 여부", "mountain_road": "산지 도로 여부", "near_forest": "산림 인접 여부"},
        "hazards": {"fire": "불꽃", "smoke": "연기", "debris": "파편", "cargo_spill": "적재물 유출",
                    "lane_blocked": "차로 점유", "forest_fire_spread": "산림 화재 확산",
                    "secondary_collision": "추가 충돌", "dangerous_goods_mark": "위험물 표지"},
        "people": {"person_on_road": "도로 위 사람", "rider_separated": "운전자·탑승자 이탈",
                   "visible_entrapment": "차체 끼임"},
    }
    for group, fields in labels.items():
        for key, label in fields.items():
            if event[group][key]["state"] == "unknown":
                needed.append(label + " 판단 불가: 추가 영상·현장 확인 필요")
    for value, label in [(event["location"]["road_type"], "도로 종류"),
                         (event["scene"]["day_time"], "주야간"),
                         (event["scene"]["weather"], "날씨")]:
        if value == "unknown":
            needed.append(label + " 판단 불가: 등록 정보·현장 확인 필요")
    if "unknown" in event["accident"]["forms"]:
        needed.append("사고 형태 판단 불가: 영상·현장 확인 필요")
    if any(p["type"] == "Unknown" for p in event["participants"]):
        needed.append("참여 객체 종류 판단 불가: 영상·현장 확인 필요")
    return list(dict.fromkeys(needed))


def source_report_use(source):
    """Keep the supplied accident-type definition as a reference, not an action."""
    title, body = source.get("title", ""), source["content"]
    if "사고유형의 결정" in title and "교통사고의 유형은 다음" in body:
        return "accident_type_definition"
    return "field_response_reference"


def report_view(report):
    """Link displayed responses to retrieved text, retaining raw retrieval separately."""
    sources = {s["chunk_id"]: s for s in report["sources"]}
    responses, references = [], {}
    for plan in report["agency_notifications"]:
        actions = []
        for cid in plan["legal_basis"]:
            source = sources[cid]
            if source_report_use(source) == "accident_type_definition":
                references.setdefault(cid, {
                    **source, "reference_id": f"R{len(references) + 1}",
                    "report_use": "accident_type_definition"
                })
                continue
            # Preparedness/training provisions are not immediate incident actions.
            if any(word in source["content"] for word in ["종합훈련", "정기적인", "계획을 수립하여야"]):
                continue
            text = agency_excerpt(source, plan["agency_key"])
            if not text:
                continue
            ref_id = references.setdefault(cid, {
                **source, "reference_id": f"R{len(references) + 1}",
                "report_use": "field_response_reference"
            })["reference_id"]
            actions.append({"text": text, "reference_ids": [ref_id],
                            "text_kind": "source_based_response",
                            "actors": source.get("actors", [])})
        responses.append({"agency": plan["agency"], "notification": plan["notification"],
                          "reason": plan["reason"], "contact": plan["contact"],
                          "observed_features": observed_features(report["input"]),
                          "control_handoff": {"summary": report["accident_summary"],
                              "location": report["input"]["location"]["registered_location"],
                              "observed_features": observed_features(report["input"]),
                              "request": "관찰 상황을 전달하고, 아래 근거에 따른 해당 기관의 대응 가능 여부와 통제·지원 필요를 확인 요청한다." if actions else "관찰 상황 전달 및 현장 확인 요청. 검색 근거가 없어 구체 조치를 지정하지 않는다."},
                          "field_response_reference": actions,
                          "actions": actions,
                          "evidence_status": "retrieved_text" if actions else "insufficient_evidence"})
    return {"report_format_version": "2.1", "report_template_revision": "team-rag-integration-20261003-v1",
            "agency_responses": responses,
            "verification_needed": verification_needed(report["input"]),
            "references": list(references.values())}


def agency_excerpt(source, agency_key):
    """Scope known shared SOP text to the responding actor; unknown sources stay extractive."""
    cid, body = source["chunk_id"], re.sub(r"\s+", "", source["content"])
    if cid.startswith("scenario-SOP-R10-"):
        if not all(term in body for term in ["진입을차단", "제연", "연결통로"]):
            return ""
        if agency_key == "fire_rescue":
            return "경찰·도로관리자와 진입 차단 및 통제 상황을 확인한다. 터널 관계자와 제연을 협의하고 연결통로를 피난·구조에 활용한다."
        return "소방 및 관계기관과 터널 진입 차단·통제 상황을 확인한다."
    if cid.startswith("scenario-FH-R05-"):
        if agency_key != "fire_rescue":
            return ""
        return summarize_source(source["content"])
    if cid.endswith("article-4-paragraph-3"):
        if agency_key != "police" or not all(term in body for term in ["2차사고예방", "최종정지지점", "응급구호"]):
            return ""
        return "수신호·표지 등으로 2차 사고를 예방하고 사고 차량 최종 정지지점 및 현장 증거를 기록한다. 당사자·목격자 연락처를 확보하고, 사상자가 확인되면 응급 구호와 피해 정도 파악을 시행한다."
    if cid.startswith("scenario-preserved-02-"):
        if agency_key != "road_manager" or not all(term in body for term in ["진입을차단", "이상상황을통보", "전원이정상적"]):
            return ""
        return "진입차단설비 또는 입구정보 표지판으로 차량 진입을 차단한다. 방송·차로이용규제신호등 등으로 비상상황을 알리고, 전원이 정상 공급되는 경우 터널 조명을 모두 점등한다."
    return summarize_source(source["content"])


def summarize_source(text):
    """Extractive summary: preserve exact source sentences, never invent obligations."""
    text = re.sub(r"\[[^\]]*\]", "", text)
    sentences = re.split(r"(?<=[.!?])\s+(?=[가-힣①②③1-9])|\n\s*\n", text)
    keywords = ["통보", "요청", "출동", "통제", "대피", "피난", "진화", "구호", "구조", "안전", "제거", "협조"]
    picked = [s.strip() for s in sentences if any(k in s for k in keywords)]
    return "\n".join((picked or [text.strip()])[:2])


def agency_plans(event):
    ts = topics(event)
    plans = []
    def add(key, name, match, when, condition, actions, terms):
        plans.append({"agency_key": key, "agency": name, "agency_match": match, "notification": when,
                      "reason": condition, "actions": actions, "task_terms": terms})
    add("police", "경찰", "경찰", "연락 대상", "교통사고 발생 및 후속 차량 안전 확인 필요",
        ["현장 안전을 확보하고 사고 차량·사람 위치를 확인한다.", "필요한 범위의 교통통제와 사고 흔적 보존을 시행한다."], "초동조치 교통통제 현장 보존")
    if {"fire", "smoke", "people"} & ts:
        add("fire_rescue", "소방·구급", "소방", "연락 대상", "불꽃·연기 또는 도로 위 사람·고립 정황이 관찰됨",
            ["화재·연기와 구조 대상 여부를 확인하고 필요한 진화·구조·구급 대응을 시행한다.", "경찰 및 시설관리자와 접근·대피 경로를 공유한다."], "소방 출동 구조 구급 화재 진압")
    else:
        add("fire_rescue", "소방·구급", "소방", "현장 확인 후 조건부", "탑승자 상태가 영상으로 확인되지 않음",
            ["현장에서 구조·구급 필요가 확인되면 공동 대응한다."], "관계기관 공동대응 구조 구급")
    if {"tunnel", "blocked", "debris", "highway"} & ts:
        add("road_manager", "도로·터널 관리기관", "도로관리", "연락 대상", "도로 통행 또는 시설 관리 협조 필요",
            (["터널 진입·대피 및 설비 상태를 소방·경찰과 공유한다."] if "tunnel" in ts else []) +
            ["통제 범위와 도로 장애물·시설 손상을 확인하고 통행 회복을 지원한다."], "도로관리청 도로관리자 터널관리자 통제 대피")
    if "forest" in ts:
        add("forest", "산림기관", "산림", "연락 대상", "주변 산림으로의 화재 확산이 관찰됨",
            ["산불 상황과 접근로를 확인하고 관할 산림 대응기관에 통보한다.", "산불 진화와 현장 지휘·지원 협조를 수행한다."], "산불 신고 산림청 산림재난관리기관 진화")
        add("local_government", "지자체", "지자체", "연락 대상", "산림 화재 확산으로 주변 주민·도로 이용자 안전 확인 필요",
            ["관할 산림기관·소방과 상황을 공유한다.", "필요시 주민 안내·대피 및 현장 지원을 협조한다."], "주민 대피 지자체 산불 지원")
    elif "mountain" in ts and {"fire", "smoke"} & ts:
        add("forest", "산림기관", "산림", "확산 확인 후 조건부", "산지 차량 화재만으로 산불 발생을 확정하지 않음",
            ["주변 산림으로 번지는 정황이 확인되면 산불 신고와 대응 협조를 진행한다."], "산림 인접 차량 화재 산불 우려 신고")
    return plans


def markdown(report):
    e = report["input"]
    lines = [f"# 관제 사고 대응 보고 초안 — {report['event_id']}", "", "## 사고 개요", "", report["accident_summary"], "",
             f"- 카메라: {e['location']['camera_id'] or '미등록'} / 위치: {e['location']['registered_location'] or '등록 위치 확인 필요'}",
             f"- 영상 후보 시각: {str(e['candidate_time_s']) + '초' if e['candidate_time_s'] is not None else '미제공'}", "- 차량 내부 탑승자 및 부상 상태: 현장 확인 필요",
             f"- 사고 형태: {', '.join(e['accident']['forms']).replace('unknown', '판단 불가')} / 관련 차량 수: {e['accident']['vehicle_count'] if e['accident']['vehicle_count'] is not None else '미확인'}",
             "", "## 영상 관찰 및 판단 불가 항목", ""]
    labels = {"fire": "불꽃", "smoke": "연기", "debris": "파편", "lane_blocked": "차로 점유", "forest_fire_spread": "산림 화재 확산"}
    state_labels = {"observed": "관찰됨", "not_observed": "가시 구간에서 관찰되지 않음", "unknown": "판단 불가"}
    for k, label in labels.items():
        v = e["hazards"][k]
        lines.append(f"- {label}: {state_labels[v['state']]} / 영상 근거: {', '.join(v['evidence_ids']) or '없음'}")
    view = report if "agency_responses" in report else report_view(report)
    lines += ["", "## 관제의 전달·요청 사항", ""]
    for p in view["agency_responses"]:
        lines += [f"### {p['agency']} — {p['notification']}", "", f"- 이유: {p['reason']}",
                  f"- 연락: {p['contact'].get('channel')} ({p['contact'].get('address') or '관할 연락처 등록 필요'})"]
        handoff = p["control_handoff"]
        lines += ["- 전달: " + handoff["summary"], "- 요청: " + handoff["request"]]
        refs = list(dict.fromkeys(r for a in p["actions"] for r in a["reference_ids"]))
        if refs:
            lines.append("- 대응 참고 근거: " + " ".join(f"[{r}]" for r in refs))
        lines.append("")
    lines += ["## 현장 대응 참고", ""]
    for p in view["agency_responses"]:
        lines += [f"### {p['agency']} — {p['notification']}", ""]
        for a in p["field_response_reference"]:
            citations = " ".join(f"[{r}]" for r in a["reference_ids"])
            lines += [f"- {a['text']} {citations}"]
        if not p["actions"]:
            lines.append("- 현장 대응 근거 미확인. 구체 조치 생성 없음.")
        lines.append("")
    lines += ["## 확인 필요 사항", ""] + ["- " + u for u in view["verification_needed"]]
    lines += ["", "기관별 대응은 검색된 청크의 발췌·요약이다. 공동 대응 내용은 하단의 조치 주체를 확인한다. 입력 영상 자체를 이 RAG가 다시 판독한 것은 아니다.",
              "", "## 관련 근거", ""]
    for s in view["references"]:
        lines += [f"### [{s['reference_id']}] {s['title']}", "", f"청크: {s['chunk_id']}", "", s["content"], "",
                  f"- 종류: {s['corpus_kind']} / 적용 조건: {'; '.join(s['application_conditions'])}",
                  f"- 본문 성격: {s['content_role']} (source_based_summary는 제공자가 작성한 비원문 요약)",
                  f"- 원문 조치 주체: {'; '.join(s['actors']) or '본문에서 확인 필요'}",
                  f"- 출처: {s['source_url']}", ""]
        if s.get("report_use") == "accident_type_definition":
            lines += ["- 보고서 용도: 사고유형 정의 참고. 현장 대응 지침으로 사용하지 않음.", ""]
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("--chunks", type=Path, required=True)
    index.add_argument("--db", type=Path, default=ROOT / "data/index.sqlite")
    index.add_argument("--revision")
    run = sub.add_parser("report")
    run.add_argument("event", type=Path)
    run.add_argument("--db", type=Path, default=ROOT / "data/index.sqlite")
    run.add_argument("--contacts", type=Path, default=ROOT / "contacts.json")
    run.add_argument("--output", type=Path, default=ROOT / "outputs")
    sub.add_parser("schema")
    args = p.parse_args()
    if args.command == "schema": dump(ROOT / "event.schema.json", schema())
    elif args.command == "index": build_index(args.chunks, args.db, args.revision)
    else:
        result = retrieve(args.db, read(args.event), read(args.contacts))
        dump(args.output / (result["event_id"] + ".json"), result)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / (result["event_id"] + ".md")).write_text(markdown(result), encoding="utf-8")
        print(f"report saved: {args.output / result['event_id']}")


if __name__ == "__main__": main()
