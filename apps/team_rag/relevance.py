"""Conservative source eligibility from structured facts, without reading video.

This is a retrieval/display guard, not a determination that a law applies.
Document text and editorial metadata remain immutable evidence.
"""
from __future__ import annotations

import re

REVISION = "structured-source-relevance-20261005-v1"


def source_relevance(source, event, agency_key):
    meta = source.get("metadata") or source
    title = source.get("title") or ""
    conditions = " ".join(meta.get("application_conditions") or [])
    focus = " ".join([title, conditions,
                      * (meta.get("scenario_tags") or []),
                      * (meta.get("retrieval_conditions") or [])])
    # Article headings reveal scope even when the supplied title only has a
    # number. Do not infer a document's scope from incidental body mentions.
    focus += " " + " ".join(re.findall(r"제\d+조(?:의\d+)?\([^)]*\)", source.get("content", "")))
    states, reasons = {}, []

    def require(group, field, label):
        state = event[group][field]["state"]
        states[f"{group}.{field}"] = state
        if state != "observed":
            meaning = "가시 구간에서 미관찰" if state == "not_observed" else "판단 불가"
            reasons.append(f"{label}: {meaning}; 해당 조건 근거로 사용하지 않음")

    if "사고유형의 결정" in title and "교통사고의 유형은 다음" in source.get("content", ""):
        # A definition listing alternatives is not a claim that all occurred.
        return {"revision": REVISION, "accepted": True, "status": "definition_only",
                "reasons": [], "observed_states": {}}

    # Some supplied provisions explicitly do not require a pedestrian to be
    # present. Their unsupported location/legal designation is still checked.
    presence_independent = any(word in conditions + " " + source.get("content", "") for word in
                               ("보행자가 없더라도", "보행자의 횡단 여부와 관계없이"))
    if any(word in focus for word in ("보행자", "횡단보도")) and not presence_independent:
        require("people", "person_on_road", "도로 위 사람")
        confirmed_pedestrian = any(p["type"] == "Pedestrian" and p["evidence_ids"]
                                   for p in event["participants"])
        states["participants.pedestrian"] = "observed" if confirmed_pedestrian else "unconfirmed"
        if not confirmed_pedestrian:
            reasons.append("보행자 참여 근거 미확인; 탑승자·구조 대상과 보행자를 동일시하지 않음")

    # These preconditions have no verified input fields in event-v2. Do not
    # guess them from free text, a model score, or the presence of a person.
    missing = []
    for words, label in [
        (("횡단보도", "정지선", "통행 의도"), "횡단보도·신호·실제 횡단/통행 의도"),
        (("보호구역",), "법정 보호구역 지정"),
    ]:
        if any(word in focus for word in words):
            missing.append(label)
    if missing:
        reasons.append("현재 입력에 확인 필드가 없는 조건: " + "; ".join(missing))

    actors = meta.get("actors") or []
    if any("운전자" in actor for actor in actors):
        reasons.append("운전자 의무·조사 참고 자료를 기관의 현장 대응 지침으로 사용하지 않음")

    return {"revision": REVISION, "accepted": not reasons,
            "status": "conditions_not_established" if reasons else "no_additional_unmet_condition",
            "reasons": reasons, "observed_states": states,
            "agency_key": agency_key}
