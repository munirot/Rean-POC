"""Capture windows: period matching, window->status, and enforcement policy.

Phase 1 of docs/attendance-policy-plan.md. The clock logic is pure (minutes since
local midnight), so these run without Mongo or a frozen system clock.

    cd face-service
    python -m pytest tests/test_attendance_window.py -v
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Store                 # noqa: E402
from app.config import settings          # noqa: E402


MORNING = {"code": "MORNING", "name": "Morning", "start": "08:00", "end": "08:20",
           "graceMinutes": 10}

_ORIGINAL_ENFORCE = settings.attendance_enforce_window


def _at(hh, mm):
    """A local-timezone datetime at hh:mm, as capture_status expects."""
    return datetime(2026, 8, 21, hh, mm, tzinfo=settings.tzinfo)


def _store(periods, enforce=True):
    s = object.__new__(Store)                    # no Mongo
    s.get_periods = lambda in_id: periods
    settings.attendance_enforce_window = enforce
    return s


# --------------------------------------------------------------------------- #
# clock parsing
# --------------------------------------------------------------------------- #
def test_hhmm_parsing():
    assert Store._hhmm_to_minutes("08:05") == 485
    assert Store._hhmm_to_minutes("00:00") == 0
    assert Store._hhmm_to_minutes("23:59") == 1439


def test_hhmm_rejects_malformed():
    for bad in ("8am", "25:00", "08:99", "", None, "08", 815):
        assert Store._hhmm_to_minutes(bad) is None


# --------------------------------------------------------------------------- #
# window -> status, including the boundaries
# --------------------------------------------------------------------------- #
def test_inside_window_is_present():
    for t in (8 * 60, 8 * 60 + 10, 8 * 60 + 20):      # start, middle, end inclusive
        assert Store.window_status(MORNING, t) == "P"


def test_before_the_window_is_rejected():
    assert Store.window_status(MORNING, 8 * 60 - 1) is None


def test_grace_period_is_late():
    assert Store.window_status(MORNING, 8 * 60 + 21) == "L"   # first minute after end
    assert Store.window_status(MORNING, 8 * 60 + 30) == "L"   # last grace minute


def test_after_grace_is_rejected():
    assert Store.window_status(MORNING, 8 * 60 + 31) is None


def test_zero_grace_rejects_immediately_after_end():
    p = {**MORNING, "graceMinutes": 0}
    assert Store.window_status(p, 8 * 60 + 20) == "P"
    assert Store.window_status(p, 8 * 60 + 21) is None


def test_malformed_period_never_grants_a_status():
    for bad in ({"start": "oops", "end": "08:20"},
                {"start": "08:00", "end": "nope"},
                {"start": "09:00", "end": "08:00"},          # end before start
                {}):
        assert Store.window_status(bad, 8 * 60 + 5) is None


# --------------------------------------------------------------------------- #
# session label -> period
# --------------------------------------------------------------------------- #
def test_match_period_by_name_or_code_case_insensitively():
    for label in ("Morning", "morning", "  MORNING  ", "MoRnInG"):
        assert Store.match_period([MORNING], label) is MORNING


def test_match_period_returns_none_for_unknown_label():
    assert Store.match_period([MORNING], "Evening") is None
    assert Store.match_period([MORNING], "") is None


# --------------------------------------------------------------------------- #
# capture_status — the policy actually enforced on POST /api/attendance
# --------------------------------------------------------------------------- #
def test_enforcement_off_always_allows_present():
    s = _store([MORNING], enforce=False)
    assert s.capture_status("IN1", "Morning", now=_at(23, 0)) == ("P", None)


def test_no_periods_configured_is_unchanged_behaviour():
    # Back-compat: an institute that never set up windows keeps working.
    s = _store([], enforce=True)
    assert s.capture_status("IN1", "anything", now=_at(3, 0)) == ("P", None)


def test_inside_window_marks_present():
    s = _store([MORNING])
    assert s.capture_status("IN1", "Morning", now=_at(8, 10)) == ("P", None)


def test_grace_marks_late():
    s = _store([MORNING])
    assert s.capture_status("IN1", "Morning", now=_at(8, 25)) == ("L", None)


def test_outside_window_is_closed():
    s = _store([MORNING])
    status, err = s.capture_status("IN1", "Morning", now=_at(3, 0))
    assert status is None and err == "closed"


def test_unknown_session_cannot_bypass_the_window():
    # The bypass this closes: a student posting session="whatever" to dodge the
    # window. With periods configured, an unrecognised label is refused.
    s = _store([MORNING])
    status, err = s.capture_status("IN1", "Whenever", now=_at(3, 0))
    assert status is None and err == "unknown_session"


# --------------------------------------------------------------------------- #
# staff corrections are never window-gated
# --------------------------------------------------------------------------- #
def test_staff_edit_path_has_no_window_logic():
    # set_attendance is the teacher's correction path; gating it would make it
    # impossible to fix attendance after class. Assert it never consults the window.
    import inspect
    src = inspect.getsource(Store.set_attendance)
    assert "capture_status" not in src and "window_status" not in src


# --------------------------------------------------------------------------- #
# the status actually reaches the attendance row
# --------------------------------------------------------------------------- #
class _Col:
    def __init__(self, docs=None):
        self.docs = docs or []

    def find_one(self, q, projection=None):
        return next((d for d in self.docs
                     if all(d.get(k) == v for k, v in q.items())), None)

    def insert_one(self, doc):
        doc.setdefault("_id", "oid")
        self.docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()


def _mark_store():
    s = object.__new__(Store)
    s._indexed = True
    s.attn = _Col()
    s.subjects = _Col([{"CrID": "C1", "SubID": "S1", "SubNa": "Torts"}])
    s.students = _Col([{"StuID": "S9", "CurCrNm": "Law"}])
    s.get = lambda sid: {"sid": "S9", "name": "Dara",
                         "raw": {"InId": "IN1", "CurCrID": "C1"}}
    return s


def test_grace_status_is_written_to_the_row():
    s = _mark_store()
    rec, created = s.mark_attendance("S9", "Morning", source="face",
                                     date="2026-08-21", status="L")
    assert created and rec["status"] == "L"
    assert s.attn.docs[0]["status"] == "L"


def test_mark_defaults_to_present_and_rejects_junk_status():
    s = _mark_store()
    rec, _ = s.mark_attendance("S9", "Morning", date="2026-08-21")
    assert rec["status"] == "P"
    s2 = _mark_store()
    rec2, _ = s2.mark_attendance("S9", "Morning", date="2026-08-21", status="XX")
    assert rec2["status"] == "P"          # unknown status falls back, never stored raw


def test_zz_restore_enforcement_default():
    """Defined last so it runs last: these tests flip a process-wide setting, and
    leaving it on would silently change behaviour for anything running after."""
    settings.attendance_enforce_window = _ORIGINAL_ENFORCE
    assert settings.attendance_enforce_window == _ORIGINAL_ENFORCE


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
