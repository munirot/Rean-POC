"""Deterministic improvement-plan suggestions from a student_profile.

Design choices (learned from the chat work):
- Fully deterministic — no LLM. Every observation quotes real numbers from the
  profile, so it can't hallucinate and works even with no model available.
- Teacher-facing SUGGESTIONS, never actions applied to the student. The caller
  (a teacher) reviews and decides. This is enforced by framing + a disclaimer.
- Supportive, non-punitive language focused on understanding and support.

Each signal maps to one grounded suggestion block: an observation built from the
profile, plus a few concrete things the teacher could consider.
"""

DISCLAIMER = ("These are suggestions for you to review and adapt — they are not "
              "applied to the student automatically.")


def _attendance_low(a, ac):
    return {
        "area": "Attendance",
        "observation": f"Attendance is {a.get('rate')}% (below the 75% threshold).",
        "actions": [
            "Have a supportive one-on-one to understand what's getting in the way.",
            "Agree on a simple attendance goal and check in weekly.",
            "If the pattern continues, loop in the student's advisor or family.",
        ],
    }


def _attendance_declining(a, ac):
    return {
        "area": "Attendance",
        "observation": (f"Attendance has dropped from {a.get('priorRate')}% to "
                        f"{a.get('recentRate')}% recently."),
        "actions": [
            "Check in early — a recent drop is a leading indicator.",
            "Ask whether workload, schedule, or wellbeing changed lately.",
        ],
    }


def _missing_assignments(a, ac):
    return {
        "area": "Assignments",
        "observation": f"{ac.get('missing')} assignment(s) are missing or overdue.",
        "actions": [
            "Prioritise the overdue items together and set realistic new dates.",
            "Offer a short catch-up or office-hours slot.",
            "Check for any understanding gaps behind the missed work.",
        ],
    }


def _quiz_low(a, ac):
    return {
        "area": "Quizzes",
        "observation": f"Quiz average is {ac.get('quizAvg')}% (below 60%).",
        "actions": [
            "Review recent quiz topics to pinpoint specific gaps.",
            "Suggest targeted practice on the weakest topics.",
            "Consider a short formative re-assessment to rebuild confidence.",
        ],
    }


# signal code -> builder. 'at_risk' is a summary flag, not its own suggestion.
_BUILDERS = {
    "attendance_low": _attendance_low,
    "attendance_declining": _attendance_declining,
    "missing_assignments": _missing_assignments,
    "quiz_avg_below_60": _quiz_low,
}


def build_plan(profile):
    """Return a teacher-facing improvement plan derived from the profile's signals."""
    a = profile.get("attendance", {})
    ac = profile.get("academics", {})
    signals = list(profile.get("signals", []))
    at_risk = "at_risk" in signals

    suggestions, focus = [], []
    for sig in signals:
        builder = _BUILDERS.get(sig)
        if builder:
            block = builder(a, ac)
            suggestions.append({"signal": sig, **block})
            if block["area"] not in focus:
                focus.append(block["area"])

    on_track = len(suggestions) == 0
    if on_track:
        summary = "On track — no attendance or assignment concerns flagged."
    elif at_risk:
        summary = (f"At risk: {', '.join(focus)} need attention. "
                   "Attendance is declining alongside academic signals.")
    else:
        summary = f"Some areas to watch: {', '.join(focus)}."

    return {
        "sid": profile.get("sid"),
        "name": profile.get("name"),
        "cls": profile.get("cls"),
        "onTrack": on_track,
        "atRisk": at_risk,
        "summary": summary,
        "focus": focus,
        "suggestions": suggestions,
        "disclaimer": DISCLAIMER,
    }
