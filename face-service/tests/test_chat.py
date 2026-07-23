"""Safety tests for the chat intent layer (no LLM, no Mongo).

    cd face-service
    python -m pytest tests/test_chat.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import chat            # noqa: E402


# --------------------------------------------------------------------------- #
# validate_intent — the allow-list gate
# --------------------------------------------------------------------------- #
def test_valid_query_passes():
    ok, err = chat.validate_intent({
        "action": "query", "collection": "attendance",
        "filters": [{"field": "status", "op": "eq", "value": "A"}],
        "aggregation": "count"})
    assert ok and err is None


def test_unknown_action_rejected():
    ok, err = chat.validate_intent({"action": "drop_table"})
    assert not ok


def test_disallowed_collection_rejected():
    ok, err = chat.validate_intent({
        "action": "query", "collection": "logins",
        "filters": [], "aggregation": "count"})
    assert not ok and "logins" in err


def test_disallowed_field_rejected():
    # 'pwd' is not in the attendance allow-list — must be blocked
    ok, err = chat.validate_intent({
        "action": "query", "collection": "attendance",
        "filters": [{"field": "pwd", "op": "eq", "value": "x"}],
        "aggregation": "list"})
    assert not ok


def test_disallowed_operator_rejected():
    ok, err = chat.validate_intent({
        "action": "query", "collection": "attendance",
        "filters": [{"field": "status", "op": "$where", "value": "1"}],
        "aggregation": "list"})
    assert not ok


def test_answer_action_ok():
    ok, err = chat.validate_intent({"action": "answer", "text": "hi"})
    assert ok


# --------------------------------------------------------------------------- #
# tool-calling: model picks a tool -> internal intent (server still guards)
# --------------------------------------------------------------------------- #
def test_map_get_attendance_to_intent():
    intent = chat.map_tool_call("get_attendance", {"period": "last week"})
    assert intent == {"action": "attendance_summary", "date": "last week"}


def test_map_get_attendance_defaults_today():
    intent = chat.map_tool_call("get_attendance", {})
    assert intent["action"] == "attendance_summary" and intent["date"] == "today"


def test_map_get_student_and_cohort():
    assert chat.map_tool_call("get_student", {"student": "Dara"}) == \
        {"action": "student_profile", "student": "Dara"}
    assert chat.map_tool_call("get_cohort", {"class_name": "Law"}) == \
        {"action": "cohort", "class": "Law"}


def test_map_search_records_builds_query_intent():
    intent = chat.map_tool_call("search_records", {
        "collection": "assignments", "aggregation": "count",
        "filters": [{"field": "Catry", "op": "eq", "value": "Quiz"}]})
    ok, err = chat.validate_intent(intent)
    assert ok, err


def test_map_unknown_tool_is_none():
    assert chat.map_tool_call("drop_everything", {}) is None


def test_first_tool_call_parses_string_arguments():
    message = {"tool_calls": [{"function": {"name": "get_attendance",
                                            "arguments": '{"period": "yesterday"}'}}]}
    name, args = chat._first_tool_call(message)
    assert name == "get_attendance" and args == {"period": "yesterday"}


def test_first_tool_call_none_when_no_calls():
    assert chat._first_tool_call({"content": "hello"}) == (None, None)


def test_attendance_summary_action_ok():
    ok, err = chat.validate_intent({"action": "attendance_summary", "date": "today"})
    assert ok and err is None


def test_attendance_summary_routes_to_fixed_function():
    """'how many absent' must call attendance_summary, not a raw status query."""
    calls = {}

    class _S:
        def attendance_summary(self, date=None, scope=None):
            calls["date"] = date
            calls["scope"] = scope
            return {"present": 0, "absent": 30, "marked": 0, "total_students": 30}

    scope = {"InId": "IN001", "type": "staff", "sid": None, "courses": {"CR001"}}
    out = chat.run_intent(_S(), {"action": "attendance_summary", "date": "today"}, scope)
    assert out["kind"] == "attendance_summary"
    assert out["data"]["marked"] == 0        # no attendance recorded
    assert calls["date"] is None             # 'today' -> store default
    assert calls["scope"] is scope           # scope forwarded


# --------------------------------------------------------------------------- #
# scope injection — a generated intent can never widen access
# --------------------------------------------------------------------------- #
def test_assignment_scope_student_limited_to_self():
    scope = {"InId": "IN001", "type": "student", "sid": "S1", "courses": None}
    f = chat._assignment_scope(scope)
    assert f == {"InId": "IN001", "Students.StuID": "S1"}


def test_assignment_scope_staff_limited_to_courses():
    scope = {"InId": "IN001", "type": "staff", "sid": None, "courses": {"CR001"}}
    f = chat._assignment_scope(scope)
    assert f["InId"] == "IN001" and f["CrID"] == {"$in": ["CR001"]}


class _FakeCol:
    """Records the query it was asked to run so we can assert scope was injected."""
    def __init__(self):
        self.last_query = None

    def count_documents(self, q):
        self.last_query = q
        return 0

    def find(self, q, *a, **k):
        self.last_query = q
        return _FakeCursor()


class _FakeCursor:
    def limit(self, n):
        return []


class _FakeStore:
    def __init__(self):
        self.attn = _FakeCol()
        self.assignments = _FakeCol()

    # mirror the real Store helper used by run_intent
    @staticmethod
    def _scope_attn_query(scope):
        return {"InId": scope["InId"], "StuID": scope["sid"]} \
            if scope["type"] == "student" else {"InId": scope["InId"]}


def test_run_intent_injects_scope_and_ignores_inid_override():
    store = _FakeStore()
    scope = {"InId": "IN001", "type": "student", "sid": "S1", "courses": None}
    # a malicious intent trying to read another institute must not win
    intent = {"action": "query", "collection": "attendance",
              "filters": [{"field": "status", "op": "eq", "value": "A"},
                          {"field": "InId", "op": "eq", "value": "IN999"}],
              "aggregation": "count"}
    chat.run_intent(store, intent, scope)
    q = store.attn.last_query
    assert q["InId"] == "IN001"      # scope wins
    assert q["StuID"] == "S1"        # student locked to self
    assert q["status"] == "A"        # allowed filter applied


import datetime as _dt


# --------------------------------------------------------------------------- #
# pre-router — attendance questions bypass the LLM entirely
# --------------------------------------------------------------------------- #
def test_pre_route_catches_absent_today():
    import datetime as _d
    intent = chat.pre_route("How many students were absent today?")
    assert intent["action"] == "attendance_summary"
    # "today" resolves to a concrete date (or the literal 'today' fallback)
    assert intent["date"] in ("today", _d.date.today().isoformat())


def test_pre_route_picks_up_explicit_date():
    intent = chat.pre_route("how many present on 2026-07-20?")
    assert intent["action"] == "attendance_summary"
    assert intent["date"] == "2026-07-20"


def test_pre_route_ignores_non_attendance():
    assert chat.pre_route("who is missing assignments in law?") is None


# --------------------------------------------------------------------------- #
# deterministic dates + multi-turn follow-ups (the reported bug)
# --------------------------------------------------------------------------- #
_TODAY = _dt.date(2026, 7, 22)   # a Wednesday


def test_resolve_yesterday_is_concrete_not_hallucinated():
    p = chat.resolve_period("what about yesterday?", today=_TODAY)
    assert p == {"type": "day", "date": "2026-07-21", "label": "yesterday"}


def test_resolve_last_week_is_a_range():
    p = chat.resolve_period("last week", today=_TODAY)
    # previous calendar week Mon–Sun
    assert p["type"] == "range"
    assert p["start"] == "2026-07-13" and p["end"] == "2026-07-19"


def test_followup_last_week_continues_attendance_topic():
    # "what about last week" alone isn't attendance — but after an attendance
    # question it must continue that topic, as a range, not a hallucinated date.
    history = [{"role": "user", "content": "how many students present today?"},
               {"role": "assistant", "content": "0 present today."}]
    intent = chat.pre_route("what about last week?", history=history, today=_TODAY)
    assert intent["action"] == "attendance_range"
    assert intent["start"] == "2026-07-13" and intent["end"] == "2026-07-19"


def test_followup_without_attendance_history_is_not_routed():
    history = [{"role": "user", "content": "who teaches constitutional law?"}]
    assert chat.pre_route("what about last week?", history=history, today=_TODAY) is None


def test_format_range_no_records():
    msg = chat._format_range({"start": "2026-07-13", "end": "2026-07-19", "records": 0},
                             label="last week")
    assert "no attendance was recorded" in msg.lower() and "last week" in msg.lower()


# --------------------------------------------------------------------------- #
# deterministic attendance formatter — 0 is never spun into a positive
# --------------------------------------------------------------------------- #
def test_format_attendance_no_records_is_truthful():
    msg = chat._format_attendance({"date": "2026-07-22", "total_students": 30,
                                   "present": 0, "absent": 30, "marked": 0})
    assert "no attendance has been recorded" in msg.lower()
    assert "30" in msg
    assert "no one absent" not in msg.lower()


def test_format_attendance_empty_scope():
    msg = chat._format_attendance({"date": "2026-07-22", "total_students": 0,
                                   "present": 0, "absent": 0, "marked": 0})
    assert "no students found" in msg.lower()


def test_format_attendance_normal_day():
    msg = chat._format_attendance({"date": "2026-07-22", "total_students": 30,
                                   "present": 25, "absent": 5, "marked": 25})
    assert "25 of 30" in msg and "5 absent" in msg


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
