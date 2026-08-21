"""Capture-mode policy: scope inheritance, validation, and mode gating.

Phase 3 of docs/attendance-policy-plan.md. No Mongo — the policy collection is
faked, and the precedence/gating logic is what these pin down.

    cd face-service
    python -m pytest tests/test_attendance_policy.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Store                 # noqa: E402
from app.config import settings          # noqa: E402

_ORIG_CAM = settings.class_cam_enabled


class _Col:
    """Just enough of a Mongo collection for policy lookups."""
    def __init__(self, docs=None):
        self.docs = docs or []

    def find_one(self, q, projection=None):
        return next((d for d in self.docs
                     if all(d.get(k) == v for k, v in q.items())), None)

    def find(self, q=None):
        q = q or {}
        return [d for d in self.docs if all(d.get(k) == v for k, v in q.items())]

    def update_one(self, q, upd, upsert=False):
        d = self.find_one(q)
        if d:
            d.update(upd.get("$set", {}))
        elif upsert:
            self.docs.append({**q, **upd.get("$set", {})})

    def create_index(self, *a, **k):
        pass


def _p(scope, mode, cr=None, sec=None, **extra):
    return {"_id": f"{scope}-{cr}-{sec}", "InId": "IN1", "scope": scope,
            "CrID": cr, "SecID": sec, "mode": mode,
            "allowIndividualFallback": True, **extra}


def _store(policies):
    s = object.__new__(Store)
    s._indexed = True
    s.policies = _Col(list(policies))
    return s


# --------------------------------------------------------------------------- #
# precedence: section > course > institute > default
# --------------------------------------------------------------------------- #
def test_falls_back_to_default_when_nothing_configured():
    p = _store([]).resolve_policy("IN1", "C1", "S1")
    assert p["mode"] == settings.attendance_default_mode
    assert p["scope"] == "default" and p["inherited"] is True


def test_institute_policy_applies_to_every_class():
    s = _store([_p("institute", "both")])
    assert s.resolve_policy("IN1", "C1", "S1")["scope"] == "institute"
    assert s.resolve_policy("IN1", "C9", "S9")["mode"] == "both"


def test_course_overrides_institute():
    s = _store([_p("institute", "individual"), _p("course", "both", cr="C1")])
    assert s.resolve_policy("IN1", "C1", "S1")["scope"] == "course"
    # a different course still inherits the institute default
    assert s.resolve_policy("IN1", "C2", "S1")["scope"] == "institute"


def test_section_wins_over_course_and_institute():
    s = _store([_p("institute", "individual"),
                _p("course", "individual", cr="C1"),
                _p("section", "both", cr="C1", sec="S1")])
    p = s.resolve_policy("IN1", "C1", "S1")
    assert p["scope"] == "section" and p["mode"] == "both"
    # a sibling section of the same course falls back to the course policy
    assert s.resolve_policy("IN1", "C1", "S2")["scope"] == "course"


def test_resolved_policy_marks_whether_it_was_inherited():
    s = _store([_p("institute", "individual")])
    assert s.resolve_policy("IN1", "C1", "S1")["inherited"] is False
    assert _store([]).resolve_policy("IN1", "C1", "S1")["inherited"] is True


# --------------------------------------------------------------------------- #
# validation, incl. the CLASS_CAM_ENABLED gate
# --------------------------------------------------------------------------- #
def test_class_camera_is_refused_while_the_pipeline_is_unbuilt():
    settings.class_cam_enabled = False
    for mode in ("class_camera", "both"):
        fields, err = Store.validate_policy("institute", None, None, mode)
        assert fields is None and "CLASS_CAM_ENABLED" in err


def test_class_camera_is_accepted_once_enabled():
    settings.class_cam_enabled = True
    try:
        fields, err = Store.validate_policy("institute", None, None, "class_camera")
        assert err is None and fields["mode"] == "class_camera"
    finally:
        settings.class_cam_enabled = False


def test_scope_requires_its_identifiers():
    assert Store.validate_policy("course", None, None, "individual")[1]
    assert Store.validate_policy("section", "C1", None, "individual")[1]
    assert Store.validate_policy("section", "C1", "S1", "individual")[1] is None


def test_scope_identifiers_are_normalised():
    f, err = Store.validate_policy("institute", "C1", "S1", "individual")
    assert err is None and f["CrID"] is None and f["SecID"] is None   # dropped
    f, err = Store.validate_policy("course", "C1", "S1", "individual")
    assert err is None and f["CrID"] == "C1" and f["SecID"] is None   # section dropped


def test_bad_scope_or_mode_rejected():
    assert Store.validate_policy("campus", None, None, "individual")[1]
    assert Store.validate_policy("institute", None, None, "telepathy")[1]


def test_set_policy_refuses_to_store_an_invalid_mode():
    s = _store([])
    settings.class_cam_enabled = False
    policy, err = s.set_policy("IN1", "institute", None, None, "class_camera")
    assert policy is None and err
    assert s.policies.docs == []          # nothing written


def test_set_policy_upserts_by_scope():
    s = _store([])
    s.set_policy("IN1", "institute", None, None, "individual", login_id="admin1")
    s.set_policy("IN1", "institute", None, None, "individual", login_id="admin2")
    assert len(s.policies.docs) == 1      # same scope updates, never duplicates
    assert s.policies.docs[0]["updatedBy"] == "admin2"


# --------------------------------------------------------------------------- #
# mode gating of individual capture
# --------------------------------------------------------------------------- #
def test_individual_and_both_allow_self_checkin():
    for mode in ("individual", "both"):
        assert Store.individual_capture_allowed({"mode": mode}) is True


def test_class_camera_blocks_self_checkin_unless_fallback_allowed():
    assert Store.individual_capture_allowed(
        {"mode": "class_camera", "allowIndividualFallback": False}) is False
    assert Store.individual_capture_allowed(
        {"mode": "class_camera", "allowIndividualFallback": True}) is True


def test_missing_policy_defaults_to_allowing_capture():
    # Never strand a class because a policy field is absent.
    assert Store.individual_capture_allowed({}) is True
    assert Store.individual_capture_allowed(None) is True


# --------------------------------------------------------------------------- #
# staff hand-marking is not automated capture
# --------------------------------------------------------------------------- #
def test_staff_manual_marking_bypasses_mode_and_window():
    """A teacher marking from the roster is correction, not capture. Assert the
    endpoint exempts it — otherwise the Manual tab breaks outside the window.

    Reads main.py as text rather than importing it, so this test needs no FastAPI."""
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, "..", "app", "main.py")).read()
    body = src.split("def mark_attendance(", 1)[1].split("\n@app.", 1)[0]
    assert "staff_manual = " in body and "if not staff_manual:" in body
    # and that the exemption checks the ROLE, so a student can't claim source=manual
    assert '"admin", "staff"' in body


def test_zz_restore_class_cam_default():
    settings.class_cam_enabled = _ORIG_CAM
    assert settings.class_cam_enabled == _ORIG_CAM


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
