"""Tests for the deterministic improvement-plan builder (no LLM, no Mongo).

    cd face-service
    python -m pytest tests/test_plan.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.plan import build_plan            # noqa: E402


def _profile(signals, **over):
    a = {"rate": 60.0, "priorRate": 90.0, "recentRate": 60.0,
         "present": 6, "late": 1, "absent": 3}
    ac = {"quizAvg": 55.0, "missing": 3, "submitted": 4}
    a.update(over.get("attendance", {}))
    ac.update(over.get("academics", {}))
    return {"sid": "S1", "name": "Test Student", "cls": "Law",
            "attendance": a, "academics": ac, "signals": signals}


def test_on_track_has_no_suggestions():
    plan = build_plan(_profile([]))
    assert plan["onTrack"] is True
    assert plan["suggestions"] == []
    assert "on track" in plan["summary"].lower()


def test_each_signal_makes_one_grounded_suggestion():
    plan = build_plan(_profile(
        ["attendance_low", "missing_assignments", "quiz_avg_below_60"]))
    signals = [s["signal"] for s in plan["suggestions"]]
    assert set(signals) == {"attendance_low", "missing_assignments", "quiz_avg_below_60"}
    # observations quote real numbers from the profile
    text = " ".join(s["observation"] for s in plan["suggestions"])
    assert "60" in text and "3" in text and "55" in text


def test_at_risk_flag_and_focus_areas():
    plan = build_plan(_profile(
        ["attendance_declining", "quiz_avg_below_60", "at_risk"]))
    assert plan["atRisk"] is True
    assert "Attendance" in plan["focus"] and "Quizzes" in plan["focus"]


def test_at_risk_is_not_its_own_suggestion():
    plan = build_plan(_profile(["at_risk"]))
    # 'at_risk' alone is a summary flag; it produces no standalone suggestion block
    assert all(s["signal"] != "at_risk" for s in plan["suggestions"])


def test_disclaimer_present():
    plan = build_plan(_profile(["attendance_low"]))
    assert "not applied" in plan["disclaimer"].lower()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
