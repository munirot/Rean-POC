"""Deterministic improvement-plan suggestions from a student_profile.

Design choices (learned from the chat work):
- Fully deterministic — no LLM. Every observation quotes real numbers from the
  profile, so it can't hallucinate and works even with no model available.
- Audience-aware wording, pre-reviewed in code (never composed by a model):
  * "teacher"  — SUGGESTIONS a teacher reviews and decides to act on. This is
    the default and preserves the original behaviour.
  * "student"  — the same grounded signals spoken TO the student in supportive,
    first-person language: what's happening and what they can do about it.
- Supportive, non-punitive language focused on understanding and support.

Each signal maps to one grounded suggestion block: an observation built from the
profile, plus a few concrete things to consider — phrased for the audience.
"""

DISCLAIMER = ("These are suggestions for you to review and adapt — they are not "
              "applied to the student automatically.")

# Shown to a student. Reassures that this is private, supportive guidance rather
# than a penalty or a permanent mark against them.
STUDENT_DISCLAIMER = ("This is private guidance to help you — it's supportive, "
                      "not a penalty. Talk to your teacher any time you'd like help.")


def _attendance_low(a, ac, audience):
    rate = a.get("rate")
    if audience == "student":
        return {
            "area": "Attendance",
            "observation": f"Your attendance is {rate}% — below the 75% you need.",
            "actions": [
                "Aim to make every session this week — short streaks add up fast.",
                "If something's making class hard to reach, tell your teacher or advisor early.",
                "Set a simple weekly goal and watch your rate here to stay on track.",
            ],
        }
    return {
        "area": "Attendance",
        "observation": f"Attendance is {rate}% (below the 75% threshold).",
        "actions": [
            "Have a supportive one-on-one to understand what's getting in the way.",
            "Agree on a simple attendance goal and check in weekly.",
            "If the pattern continues, loop in the student's advisor or family.",
        ],
    }


def _attendance_declining(a, ac, audience):
    prior, recent = a.get("priorRate"), a.get("recentRate")
    if audience == "student":
        return {
            "area": "Attendance",
            "observation": f"Your attendance recently slipped from {prior}% to {recent}%.",
            "actions": [
                "A recent dip is easy to turn around — start with the next session.",
                "If your schedule, workload, or wellbeing changed, reach out for support.",
            ],
        }
    return {
        "area": "Attendance",
        "observation": (f"Attendance has dropped from {prior}% to "
                        f"{recent}% recently."),
        "actions": [
            "Check in early — a recent drop is a leading indicator.",
            "Ask whether workload, schedule, or wellbeing changed lately.",
        ],
    }


def _missing_assignments(a, ac, audience):
    missing = ac.get("missing")
    if audience == "student":
        return {
            "area": "Assignments",
            "observation": f"You have {missing} assignment(s) missing or overdue.",
            "actions": [
                "Pick the most overdue one and make a start — small steps count.",
                "Ask your teacher for a new date or a catch-up slot if you need it.",
                "Flag anything you didn't understand so it doesn't hold you back.",
            ],
        }
    return {
        "area": "Assignments",
        "observation": f"{missing} assignment(s) are missing or overdue.",
        "actions": [
            "Prioritise the overdue items together and set realistic new dates.",
            "Offer a short catch-up or office-hours slot.",
            "Check for any understanding gaps behind the missed work.",
        ],
    }


def _quiz_low(a, ac, audience):
    avg = ac.get("quizAvg")
    if audience == "student":
        return {
            "area": "Quizzes",
            "observation": f"Your quiz average is {avg}% — a bit below 60%.",
            "actions": [
                "Look back at recent quiz topics to spot where it slipped.",
                "Practise the one or two weakest topics — that's where scores move most.",
                "Ask for a quick re-check quiz to rebuild your confidence.",
            ],
        }
    return {
        "area": "Quizzes",
        "observation": f"Quiz average is {avg}% (below 60%).",
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


def _summary(audience, on_track, at_risk, focus):
    areas = ", ".join(focus)
    if audience == "student":
        if on_track:
            return "You're on track — no attendance or assignment concerns right now. Keep it up!"
        if at_risk:
            return (f"A few areas need attention: {areas}. Acting now makes a real "
                    "difference — and you don't have to do it alone.")
        return f"A couple of things to keep an eye on: {areas}."
    if on_track:
        return "On track — no attendance or assignment concerns flagged."
    if at_risk:
        return (f"At risk: {areas} need attention. "
                "Attendance is declining alongside academic signals.")
    return f"Some areas to watch: {areas}."


def build_plan(profile, audience="teacher"):
    """Return an improvement plan derived from the profile's signals.

    audience="teacher" (default) yields suggestions a teacher reviews and acts on.
    audience="student" speaks the same grounded signals TO the student in
    supportive, first-person language. Wording is pre-reviewed here in code.
    """
    a = profile.get("attendance", {})
    ac = profile.get("academics", {})
    signals = list(profile.get("signals", []))
    at_risk = "at_risk" in signals

    suggestions, focus = [], []
    for sig in signals:
        builder = _BUILDERS.get(sig)
        if builder:
            block = builder(a, ac, audience)
            suggestions.append({"signal": sig, **block})
            if block["area"] not in focus:
                focus.append(block["area"])

    on_track = len(suggestions) == 0

    return {
        "sid": profile.get("sid"),
        "name": profile.get("name"),
        "cls": profile.get("cls"),
        "audience": audience,
        "onTrack": on_track,
        "atRisk": at_risk,
        "summary": _summary(audience, on_track, at_risk, focus),
        "focus": focus,
        "suggestions": suggestions,
        "disclaimer": STUDENT_DISCLAIMER if audience == "student" else DISCLAIMER,
    }
