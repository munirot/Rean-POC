"""Pre-rollout session audit and the course/section picker source (no Mongo).

The audit exists because turning window enforcement on against dirty legacy data
would start refusing marks. These tests pin the two things it must catch.

    cd face-service
    python -m pytest tests/test_admin_audit.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Store                 # noqa: E402
from app.config import settings          # noqa: E402


class _Col:
    def __init__(self, docs=None):
        self.docs = docs or []

    def find(self, q=None, projection=None):
        q = {k: v for k, v in (q or {}).items() if not isinstance(v, dict)}
        return [d for d in self.docs if all(d.get(k) == v for k, v in q.items())]


def _store(sessions, periods=()):
    """sessions: list of session labels, one per attendance row."""
    s = object.__new__(Store)
    s.attn = _Col([{"InId": "IN1", "session": v} for v in sessions])
    s.get_periods = lambda in_id: list(periods)
    return s


MORNING = {"code": "MORNING", "name": "Morning", "start": "08:00", "end": "08:20",
           "graceMinutes": 10}


# --------------------------------------------------------------------------- #
# audit: unmatched labels
# --------------------------------------------------------------------------- #
def test_counts_distinct_labels_and_records():
    a = _store(["Morning"] * 3 + ["Afternoon"] * 2).audit_sessions("IN1")
    assert a["distinct"] == 2 and a["records"] == 5
    assert a["rows"][0]["session"] == "Morning" and a["rows"][0]["count"] == 3


def test_flags_labels_that_match_no_period():
    a = _store(["Morning", "Evening", "Evening"], [MORNING]).audit_sessions("IN1")
    by = {r["session"]: r for r in a["rows"]}
    assert by["Morning"]["matched"] is True and by["Morning"]["period"] == "Morning"
    assert by["Evening"]["matched"] is False
    assert a["unmatchedRecords"] == 2          # the two Evening rows


def test_nothing_is_unmatched_before_periods_exist():
    # With no periods configured the server doesn't enforce, so don't cry wolf.
    a = _store(["Morning", "Evening"]).audit_sessions("IN1")
    assert a["periodsConfigured"] == 0 and a["unmatchedRecords"] == 0


# --------------------------------------------------------------------------- #
# audit: stored variants of the same label
# --------------------------------------------------------------------------- #
def test_detects_case_and_whitespace_variants():
    a = _store(["Morning", "morning", "Morning "], [MORNING]).audit_sessions("IN1")
    assert a["variantGroups"] == 1
    # all three still RESOLVE (match_period is case/space insensitive) ...
    assert all(r["matched"] for r in a["rows"])
    # ... but each row knows about its siblings, which is the duplicate-row risk
    by = {r["session"]: r for r in a["rows"]}
    assert by["Morning"]["variants"] == ["Morning ", "morning"]


def test_distinct_labels_are_not_reported_as_variants():
    a = _store(["Morning", "Afternoon"], [MORNING]).audit_sessions("IN1")
    assert a["variantGroups"] == 0
    assert all(r["variants"] == [] for r in a["rows"])


def test_audit_reads_nothing_but_sessions():
    # It must stay read-only: no writes, no mutation of the attendance rows.
    s = _store(["Morning", "morning"], [MORNING])
    before = [dict(d) for d in s.attn.docs]
    s.audit_sessions("IN1")
    assert s.attn.docs == before


# --------------------------------------------------------------------------- #
# courses / sections for the policy scope picker
# --------------------------------------------------------------------------- #
def _course_store(students):
    s = object.__new__(Store)
    s.students = _Col(students)
    return s


def _stu(cr, crnm, sec=None, secnm=None, **extra):
    return {"InId": "IN1", "StFl": "A", "CurCrID": cr, "CurCrNm": crnm,
            "CurSecID": sec, "CurSecNm": secnm, **extra}


def test_courses_group_sections_and_count_students():
    s = _course_store([
        _stu("CR1", "Law", "SC1", "Section A"),
        _stu("CR1", "Law", "SC1", "Section A"),
        _stu("CR1", "Law", "SC2", "Section B"),
        _stu("CR2", "Economics", "SC1", "Section A"),
    ])
    courses = {c["CrID"]: c for c in s.list_courses("IN1")}
    assert courses["CR1"]["students"] == 3
    assert [x["SecID"] for x in courses["CR1"]["sections"]] == ["SC1", "SC2"]
    assert courses["CR1"]["sections"][0]["students"] == 2
    assert courses["CR2"]["students"] == 1


def test_courses_sorted_by_name_and_skip_students_without_a_course():
    s = _course_store([_stu("CR2", "Zoology"), _stu("CR1", "Anthropology"),
                       _stu(None, None)])
    names = [c["name"] for c in s.list_courses("IN1")]
    assert names == ["Anthropology", "Zoology"]


def test_course_falls_back_to_code_then_id_for_a_name():
    s = _course_store([_stu("CR1", None, CurCrCd="BIR"), _stu("CR2", None)])
    by = {c["CrID"]: c for c in s.list_courses("IN1")}
    assert by["CR1"]["name"] == "BIR"      # code when the name is missing
    assert by["CR2"]["name"] == "CR2"      # id as the last resort


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
