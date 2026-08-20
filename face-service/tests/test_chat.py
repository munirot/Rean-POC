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
    s = chat.map_tool_call("get_student", {"student": "Dara"})
    assert s["action"] == "student_profile" and s["student"] == "Dara"
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


# --------------------------------------------------------------------------- #
# conversation context: pronoun follow-ups resolve to the prior student
# --------------------------------------------------------------------------- #
def test_mentions_person_ref():
    assert chat._mentions_person_ref("how are his quizzes?")
    assert chat._mentions_person_ref("what about that student")
    assert not chat._mentions_person_ref("how many present today")


class _RosterStore:
    def __init__(self, roster):
        self._roster = roster

    def list_students(self, scope=None):
        return self._roster


def test_focus_student_resolves_last_mentioned():
    store = _RosterStore([{"sid": "S1", "name": "Dara Sok"},
                          {"sid": "S2", "name": "Sophea Chan"}])
    history = [
        {"role": "user", "content": "how is Dara Sok doing?"},
        {"role": "assistant", "content": "Dara Sok: attendance 80%..."},
    ]
    foc = chat._focus_student(store, history, scope=None)
    assert foc == {"sid": "S1", "name": "Dara Sok"}


def test_focus_student_none_without_match():
    store = _RosterStore([{"sid": "S1", "name": "Dara Sok"}])
    history = [{"role": "user", "content": "how many present today?"}]
    assert chat._focus_student(store, history, scope=None) is None


# --------------------------------------------------------------------------- #
# ambiguous / misspelled student names -> clarifying question
# --------------------------------------------------------------------------- #
_ROSTER = [
    {"sid": "S1", "name": "Pisey Yem", "cls": "Class A"},
    {"sid": "S2", "name": "Pesey Yen", "cls": "Class B"},
    {"sid": "S3", "name": "Dara Sok", "cls": "Class A"},
    {"sid": "S4", "name": "Dara Sok", "cls": "Class C"},
]


def test_match_single_exact():
    matches, sugg = chat.match_students(_ROSTER, "Pisey Yem")
    assert [m["sid"] for m in matches] == ["S1"] and sugg == []


def test_match_ambiguous_same_name():
    matches, sugg = chat.match_students(_ROSTER, "Dara Sok")
    assert {m["sid"] for m in matches} == {"S3", "S4"}   # two students, one name


def test_match_class_hint_disambiguates():
    matches, _ = chat.match_students(_ROSTER, "Dara Sok", class_hint="Class C")
    assert [m["sid"] for m in matches] == ["S4"]


def test_match_typo_returns_suggestions():
    matches, sugg = chat.match_students(_ROSTER, "Pesey Yem")   # typo
    assert matches == []
    assert {s["sid"] for s in sugg} >= {"S1", "S2"}            # both close names


class _ClarifyStore:
    def __init__(self, roster):
        self._roster = roster

    def list_students(self, scope=None):
        return self._roster

    def student_profile(self, sid):
        return {"sid": sid, "name": "resolved"}


def test_run_intent_asks_which_one_when_ambiguous():
    store = _ClarifyStore(_ROSTER)
    out = chat.run_intent(store, {"action": "student_profile", "student": "Dara Sok"},
                          scope=None)
    assert out["kind"] == "clarify" and out["exact"] is True
    msg = chat._format_clarify(out)
    assert "which one" in msg.lower() and "Class A" in msg and "Class C" in msg


def test_run_intent_did_you_mean_on_typo():
    store = _ClarifyStore(_ROSTER)
    out = chat.run_intent(store, {"action": "student_profile", "student": "Pesey Yem"},
                          scope=None)
    assert out["kind"] == "clarify" and out["exact"] is False
    assert "did you mean" in chat._format_clarify(out).lower()


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


def test_run_intent_student_cannot_read_a_classmate():
    # The security guarantee for self-scoped chat: a student who supplies a filter
    # on their OWN scope key (StuID) can never swap in a classmate's id — the scope
    # key is locked, so the extra filter is dropped and the query stays on self.
    store = _FakeStore()
    scope = {"InId": "IN001", "type": "student", "sid": "S1", "courses": None}
    intent = {"action": "query", "collection": "attendance",
              "filters": [{"field": "StuID", "op": "eq", "value": "S2"},
                          {"field": "status", "op": "eq", "value": "P"}],
              "aggregation": "count"}
    chat.run_intent(store, intent, scope)
    q = store.attn.last_query
    assert q["StuID"] == "S1"        # locked to the caller — classmate ignored
    assert q["status"] == "P"


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


def test_pre_route_does_not_hijack_general_chat():
    # "present" as a verb / general questions must fall through to the model
    assert chat.pre_route("present a summary of this lesson") is None
    assert chat.pre_route("what are good ways to boost engagement?") is None
    assert chat.pre_route("can you help me plan a quiz?") is None


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


# --------------------------------------------------------------------------- #
# get_plan — improvement suggestions come from plan.py, never from the model
# --------------------------------------------------------------------------- #
class _PlanStore:
    """Roster + a profile with real at-risk signals, so build_plan has material."""

    def list_students(self, scope=None):
        return [{"sid": "S3", "name": "Dara Sok", "cls": "Class A"}]

    def student_profile(self, sid):
        return {
            "sid": sid, "name": "Dara Sok", "cls": "Class A",
            "attendance": {"records": 10, "rate": 60.0, "priorRate": 90.0,
                           "recentRate": 60.0, "present": 6, "late": 1, "absent": 3},
            "academics": {"quizAvg": 55.0, "missing": 3, "submitted": 4},
            "signals": ["attendance_low", "attendance_declining",
                        "missing_assignments", "quiz_avg_below_60", "at_risk"],
        }


def test_map_get_plan_to_intent():
    assert chat.map_tool_call("get_plan", {"student": "Dara Sok"}) == \
        {"action": "student_plan", "student": "Dara Sok", "class_hint": None}


def test_student_plan_action_allowed():
    ok, err = chat.validate_intent({"action": "student_plan", "student": "Dara Sok"})
    assert ok and err is None


def test_run_intent_plan_uses_deterministic_builder():
    out = chat.run_intent(_PlanStore(), {"action": "student_plan", "student": "Dara Sok"},
                          scope=None)
    assert out["kind"] == "student_plan"
    plan = out["data"]
    assert plan["atRisk"] is True and plan["onTrack"] is False
    # every suggestion maps to a real signal, and the disclaimer always rides along
    assert {s["signal"] for s in plan["suggestions"]} == {
        "attendance_low", "attendance_declining", "missing_assignments",
        "quiz_avg_below_60"}
    assert plan["disclaimer"]


def test_plan_is_composed_in_code_not_by_the_llm():
    """_compose must not call the model for a plan — the wording is pre-reviewed."""
    out = chat.run_intent(_PlanStore(), {"action": "student_plan", "student": "Dara Sok"},
                          scope=None)

    def _boom(*a, **k):
        raise AssertionError("_compose sent the plan to the LLM")

    original = chat._llm
    chat._llm = _boom
    try:
        text = chat._compose("How can I help Dara Sok?", out)
    finally:
        chat._llm = original
    # real numbers from the profile, verbatim from plan.py
    assert "60.0%" in text and "3 assignment(s)" in text and "55.0%" in text
    assert "not applied to the student automatically" in text


def test_run_intent_plan_clarifies_ambiguous_name():
    """Shares resolution with student_profile: two Dara Soks must still ask."""
    store = _ClarifyStore(_ROSTER)
    out = chat.run_intent(store, {"action": "student_plan", "student": "Dara Sok"},
                          scope=None)
    assert out["kind"] == "clarify" and out["exact"] is True


def test_run_intent_plan_not_found_stays_grounded():
    store = _ClarifyStore([])
    out = chat.run_intent(store, {"action": "student_plan", "student": "Nobody"},
                          scope=None)
    assert out["kind"] == "not_found" and "Nobody" in out["text"]


def test_composer_prompt_forbids_improvised_advice():
    """The old prompt allowed the model to coach when asked; it must not now."""
    assert "get_plan" in chat.COMPOSE_SYSTEM
    assert "UNLESS the user explicitly asks how to help" not in chat.COMPOSE_SYSTEM


def test_get_plan_is_advertised_to_the_model():
    names = {t["function"]["name"] for t in chat.TOOLS}
    assert "get_plan" in names
    assert "get_plan" in chat.SYSTEM_PROMPT


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
