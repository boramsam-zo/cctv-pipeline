"""Bounded local review events from saved predictions; no model or video I/O.

M3 prototype: only explicitly synthetic policies are enabled. Counts are fresh
predictions, not independent evidence or ground-truth accidents. Reconstruct a
processor by replaying the complete bounded input log; no incremental resume.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math

MAX_RECORDS = 29
UNCLASSIFIED = {"insufficient_frames_or_time_gap", "analysis_failed",
                "buffer_restarted", "incomplete_window"}


class EventError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise EventError(reason)


def fingerprint(value):
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise EventError("non_json_or_nonfinite_input") from None
    return hashlib.sha256(data.encode()).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 160 and all(
        ch.isalnum() or ch in "_-.:" for ch in value)


def validate_policy(policy):
    require(isinstance(policy, dict), "policy_required")
    require(set(policy) == {"purpose", "version", "decision_rule", "confirm_count",
            "release_count", "max_gap_seconds", "merge_rule", "rearm_rule",
            "cooldown_seconds"}, "explicit_policy_fields_required")
    require(policy["purpose"] == "synthetic_test", "real_event_policy_not_approved")
    require(identifier(policy["version"]), "invalid_policy_version")
    # Preserve E-1's already computed decision. Do not retune a score threshold.
    require(policy["decision_rule"] == "saved_argmax_normal_on_tie", "unsupported_decision_rule")
    for key in ("confirm_count", "release_count"):
        require(type(policy[key]) is int and 1 <= policy[key] <= MAX_RECORDS,
                "invalid_confirmation_or_release_count")
    require(finite(policy["max_gap_seconds"]) and 0 < policy["max_gap_seconds"] <= 60,
            "invalid_gap_seconds")
    require(policy["merge_rule"] == "while_open_only" and
            policy["rearm_rule"] == "next_positive_after_close" and
            type(policy["cooldown_seconds"]) is int and policy["cooldown_seconds"] == 0,
            "unsupported_merge_or_rearm_policy")
    return deepcopy(policy)


class EventProcessor:
    """One run/source, at most 29 unique windows; duplicate deliveries are no-ops."""

    def __init__(self, context, policy):
        self.policy = validate_policy(policy)
        require(isinstance(context, dict) and set(context) == {
            "run_id", "source_video_id", "model_version", "preprocessing_version"},
            "explicit_context_required")
        require(all(identifier(v) for v in context.values()), "invalid_context_identity")
        require(context["run_id"].startswith("synthetic_") and
                context["source_video_id"].startswith("synthetic_") and
                context["model_version"] == "synthetic_no_model", "synthetic_input_required")
        self.context = deepcopy(context)
        self.policy_sha256 = fingerprint(policy)
        self.events = []
        self.transitions = []
        self.unclassified = []
        self.receipts = []
        self.seen_predictions = {}
        self.seen_windows = {}
        self.active = None
        self.last_time = None
        self.last_elapsed = None
        self.finished = False
        self.finish_reason = None

    def _validate_row(self, row):
        require(isinstance(row, dict), "prediction_object_required")
        require(row.get("source_video_id") == self.context["source_video_id"], "source_mismatch")
        require(row.get("model_sha256") is None, "real_model_predictions_not_enabled")
        require(identifier(row.get("window_id")), "window_id_required")
        start, end, decision = (row.get(k) for k in
                               ("start_seconds", "end_seconds", "decision_source_time_sec"))
        require(all(finite(t) for t in (start, end, decision)) and
                0 <= start < end and end == decision, "invalid_source_time")
        status = row.get("status")
        require(status == "predicted" or status in UNCLASSIFIED, "unsupported_prediction_status")
        if status == "predicted":
            require(identifier(row.get("prediction_id")), "prediction_id_required")
            require(type(row.get("frame_count")) is int and row["frame_count"] == 16,
                    "incomplete_predicted_window")
            require(type(row.get("prediction")) is int and row["prediction"] in (0, 1),
                    "invalid_class")
            logits = row.get("logits")
            require(isinstance(logits, list) and len(logits) == 2 and all(finite(v) for v in logits),
                    "invalid_saved_logits")
            require(row["prediction"] == int(logits[1] > logits[0]), "saved_class_disagrees_with_logits")
            score = row.get("accident_score")
            require(finite(score) and 0 <= score <= 1, "invalid_saved_score")
            high = max(logits)
            values = [math.exp(x - high) for x in logits]
            require(abs(score - values[1] / sum(values)) <= 1e-6, "saved_score_disagrees_with_logits")
        else:
            require(all(row.get(k) is None for k in
                        ("prediction", "prediction_id", "accident_score", "logits")),
                    "unclassified_cannot_have_prediction")
        return fingerprint(row)

    def _transition(self, event, status, reason, at, utc, elapsed):
        event["status"] = status
        event["last_transition_reason"] = reason
        self.transitions.append({"event_id": event["event_id"], "status": status,
            "reason": reason, "decision_source_time_sec": at,
            "recorded_at_utc": utc, "elapsed_wall_sec": elapsed})
        if status in ("closed", "cancelled", "incomplete"):
            event["closed_decision_source_time_sec"] = at
            self.active = None

    def _interrupt(self, reason, at, utc, elapsed):
        if self.active is not None:
            self._transition(self.active, "incomplete", reason, at, utc, elapsed)

    def consume(self, row, *, emitted_at_utc, elapsed_wall_sec):
        """Validate before mutation; source order is checked only for fresh rows."""
        digest = self._validate_row(row)
        wid, pid = row["window_id"], row.get("prediction_id")
        previous = self.seen_predictions.get(pid) if pid is not None else self.seen_windows.get(wid)
        if previous is not None:
            require(previous["sha256"] == digest, "duplicate_identity_content_conflict")
            return {"window_id": wid, "prediction_id": pid, "action": "duplicate_ignored",
                    "event_id": previous["event_id"]}
        require(wid not in self.seen_windows, "window_identity_content_conflict")
        require(not self.finished, "processor_finished")
        require(len(self.seen_windows) < MAX_RECORDS, "unique_window_limit")
        at = row["decision_source_time_sec"]
        require(self.last_time is None or at > self.last_time, "source_time_not_increasing")
        require(finite(elapsed_wall_sec) and elapsed_wall_sec >= 0 and
                (self.last_elapsed is None or elapsed_wall_sec >= self.last_elapsed), "invalid_elapsed_time")
        try:
            stamp = datetime.fromisoformat(emitted_at_utc)
            valid_utc = stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0
        except (TypeError, ValueError):
            valid_utc = False
        require(valid_utc, "explicit_utc_required")
        if self.last_time is not None and at - self.last_time > self.policy["max_gap_seconds"]:
            self.unclassified.append({"reason": "decision_time_gap",
                "start_seconds": self.last_time, "end_seconds": at,
                "window_id": wid, "truth": None})
            self._interrupt("decision_time_gap", at, emitted_at_utc, elapsed_wall_sec)
        affected = self.active
        if row["status"] in UNCLASSIFIED:
            self.unclassified.append({"reason": row["status"], "window_id": wid,
                "start_seconds": row["start_seconds"], "end_seconds": row["end_seconds"], "truth": None})
            self._interrupt(row["status"], at, emitted_at_utc, elapsed_wall_sec)
            action = "unclassified"
        elif row["prediction"] == 1:
            if self.active is None:
                event = {**self.context, "event_id": "event_" + fingerprint([
                    self.context, self.policy_sha256, wid])[:24],
                    "event_policy_version": self.policy["version"],
                    "event_policy_sha256": self.policy_sha256,
                    "first_suspicion_source_time_sec": at,
                    "evidence_start_seconds": row["start_seconds"], "evidence_end_seconds": row["end_seconds"],
                    "positive_prediction_ids": [], "release_prediction_ids": [],
                    "positive_count": 0, "release_streak": 0,
                    "alert_decision_source_time_sec": None, "alert_emitted_at_utc": None,
                    "alert_elapsed_wall_sec": None, "closed_decision_source_time_sec": None,
                    "ground_truth_event_id": None, "external_notification_sent": False}
                self.events.append(event)
                self.active = event
                self._transition(event, "candidate", "first_positive", at, emitted_at_utc, elapsed_wall_sec)
            event = affected = self.active
            event["positive_prediction_ids"].append(pid)
            event["positive_count"] += 1
            event["release_streak"] = 0
            event["evidence_end_seconds"] = row["end_seconds"]
            if event["status"] == "candidate" and event["positive_count"] >= self.policy["confirm_count"]:
                event.update(alert_decision_source_time_sec=at, alert_emitted_at_utc=emitted_at_utc,
                             alert_elapsed_wall_sec=elapsed_wall_sec)
                self._transition(event, "open", "confirmation_rule_met", at, emitted_at_utc, elapsed_wall_sec)
            action = event["status"]
        else:
            action = "no_active_event"
            if self.active is not None:
                event = self.active
                event["release_prediction_ids"].append(pid)
                event["release_streak"] += 1
                if event["status"] == "candidate":
                    self._transition(event, "cancelled", "confirmation_interrupted", at, emitted_at_utc, elapsed_wall_sec)
                elif event["release_streak"] >= self.policy["release_count"]:
                    self._transition(event, "closed", "release_rule_met_not_ground_truth_end", at, emitted_at_utc, elapsed_wall_sec)
                action = event["status"]
        receipt = {"window_id": wid, "prediction_id": pid, "action": action,
                   "event_id": affected["event_id"] if affected else None}
        stored = {"sha256": digest, "event_id": receipt["event_id"]}
        self.seen_windows[wid] = stored
        if pid is not None:
            self.seen_predictions[pid] = stored
        self.receipts.append(receipt)
        self.last_time, self.last_elapsed = at, elapsed_wall_sec
        return deepcopy(receipt)

    def finish(self, *, reason, emitted_at_utc, elapsed_wall_sec):
        require(reason in ("source_ended", "analysis_failed", "timeout"), "invalid_finish_reason")
        if not self.finished:
            # Reuse timestamp validation without inventing another prediction.
            require(finite(elapsed_wall_sec) and elapsed_wall_sec >= (self.last_elapsed or 0), "invalid_elapsed_time")
            try:
                stamp = datetime.fromisoformat(emitted_at_utc)
                valid = stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0
            except (TypeError, ValueError):
                valid = False
            require(valid, "explicit_utc_required")
            self._interrupt(reason + "_before_release", self.last_time, emitted_at_utc, elapsed_wall_sec)
            self.finished = True
            self.finish_reason = reason
        return self.snapshot()

    def snapshot(self):
        return deepcopy({"schema_version": 1, "purpose": "synthetic_test",
            "context": self.context, "policy": self.policy, "policy_sha256": self.policy_sha256,
            "finished": self.finished, "finish_reason": self.finish_reason,
            "events": self.events, "transitions": self.transitions,
            "unclassified": self.unclassified, "receipts": self.receipts,
            "unique_windows": len(self.seen_windows), "unique_predictions": len(self.seen_predictions),
            "deduplication_window_ledger": self.seen_windows,
            "deduplication_prediction_ledger": self.seen_predictions,
            "deduplication_scope": "one run/source; <=29 unique windows; replay full log to reconstruct",
            "independent_validation": False, "model_calls": 0, "training_updates": 0,
            "event_metrics": None, "metrics_reason": "synthetic_function_test_no_real_ground_truth",
            "user_review_status": "pending"})
