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


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
