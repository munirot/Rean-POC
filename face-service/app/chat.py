"""Grounded staff chat over attendance + assignments.

Design (safety first):
  1. The LLM never writes raw Mongo. It returns a small, structured *intent*.
  2. We validate that intent against an allow-list of actions/collections/fields.
  3. We inject the caller's authorization scope (InId + courses/sid) into every
     query — the model chooses WHAT to look for; the server decides what it may
     SEE. Generated intents can never widen scope.
  4. Reads are capped (chat_row_cap) and only find/count are ever issued.
  5. The final answer is composed from the returned rows, and the raw data +
     intent are returned so staff can verify (cite counts, don't trust prose).

Aggregations people act on (a student's full profile, a class's at-risk list)
use the fixed, tested functions from db.py — NOT generated queries.

The LLM is any OpenAI-compatible endpoint; default is local Ollama so student
data stays on-prem (see config.chat_*).
"""
import json
import re
import urllib.request
import urllib.error

from .config import settings

# --------------------------------------------------------------------------- #
# Allow-list: the only collections/fields/operators a generated query may touch
# --------------------------------------------------------------------------- #
OPS = {"eq", "in", "gte", "lte"}
ALLOWED = {
    "attendance": {"StuID", "status", "date", "session", "SubID", "CrID"},
    "assignments": {"Catry", "SubID", "CrID", "StaffID"},
}
ACTIONS = {"student_profile", "cohort", "query", "attendance_summary", "answer"}

SYSTEM_PROMPT = """You are a data assistant for school staff. You do NOT answer \
from memory — you translate the question into ONE JSON intent that the server \
runs against the database. Output ONLY valid JSON, no prose.

Intent shapes:
- Attendance counts for a day (how many present / absent / attendance rate,
  today or a given date): {"action":"attendance_summary","date":"today"}
  or {"action":"attendance_summary","date":"2026-07-22"}
- Look up one student's full record (attendance + grades + risk signals):
  {"action":"student_profile","student":"<name or student id>"}
- List at-risk students in a class:
  {"action":"cohort","class":"<class/course name>"}
- Filter/count specific rows (e.g. how many quizzes, records for one student):
  {"action":"query","collection":"attendance"|"assignments",
   "filters":[{"field":"<field>","op":"eq|in|gte|lte","value":<v>}],
   "aggregation":"count"|"list"}
- If the question is not about attendance or assignments, or you need to reply
  directly: {"action":"answer","text":"<reply>"}

IMPORTANT: For "how many students are present/absent" use attendance_summary —
NOT a query on status. Absence is implicit (a student with no present record),
so counting status='A' rows is wrong. Use query only for specific row lookups.

Allowed query fields:
- attendance: StuID, status (P=present,L=late,A=absent), date (YYYY-MM-DD), session, SubID, CrID
- assignments: Catry (Quiz/Homework/Project/Seminar), SubID, CrID, StaffID
Never invent fields. Prefer student_profile / cohort for "how is X doing" or
"who is struggling" questions."""


# --------------------------------------------------------------------------- #
# LLM call (OpenAI-compatible /chat/completions via stdlib — no extra deps)
# --------------------------------------------------------------------------- #
def _llm(messages, force_json=False):
    payload = {"model": settings.chat_model, "messages": messages, "temperature": 0.2}
    if force_json:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        settings.chat_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {settings.chat_api_key}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=settings.chat_timeout) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"]


# --------------------------------------------------------------------------- #
# Deterministic pre-router: catch common, error-prone questions BEFORE the LLM.
# Attendance-count questions must never be answered by a raw status query, so we
# force them to the summary function here rather than trusting the model to pick.
# --------------------------------------------------------------------------- #
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_ATTN_WORDS = ("absent", "present", "attendance", "attended", "turnout",
               "how many showed", "who showed up", "checked in", "check-in")


def pre_route(question):
    """Return an intent dict for questions we can route deterministically, else None."""
    q = question.lower()
    if any(w in q for w in _ATTN_WORDS) and any(
            k in q for k in ("how many", "count", "number of", "today", "attendance",
                             "were", "are", "rate", "on ")):
        m = _DATE_RE.search(question)
        return {"action": "attendance_summary", "date": m.group(1) if m else "today"}
    return None


def _extract_json(text):
    """Parse a JSON object, tolerating stray prose around it."""
    try:
        return json.loads(text)
    except Exception:
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j != -1 and j > i:
            return json.loads(text[i:j + 1])
        raise


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_intent(intent):
    """Return (ok, error). Pure — no DB, no network."""
    if not isinstance(intent, dict):
        return False, "intent must be an object"
    action = intent.get("action")
    if action not in ACTIONS:
        return False, f"unknown action: {action}"
    if action == "query":
        coll = intent.get("collection")
        if coll not in ALLOWED:
            return False, f"collection not allowed: {coll}"
        for f in intent.get("filters", []):
            if not isinstance(f, dict):
                return False, "each filter must be an object"
            if f.get("field") not in ALLOWED[coll]:
                return False, f"field not allowed on {coll}: {f.get('field')}"
            if f.get("op") not in OPS:
                return False, f"operator not allowed: {f.get('op')}"
        if intent.get("aggregation") not in ("count", "list"):
            return False, "aggregation must be 'count' or 'list'"
    return True, None


def _apply_op(op, value):
    return value if op == "eq" else {"$in": value} if op == "in" \
        else {"$gte": value} if op == "gte" else {"$lte": value}


def _assignment_scope(scope):
    """Scope fragment for the assignments collection."""
    if not scope:
        return {}
    f = {"InId": scope["InId"]}
    if scope["type"] == "student":
        f["Students.StuID"] = scope["sid"]
    elif scope["type"] == "staff" and scope.get("courses") is not None:
        f["CrID"] = {"$in": sorted(scope["courses"])}
    return f


# --------------------------------------------------------------------------- #
# Execution (scope always injected; reads only)
# --------------------------------------------------------------------------- #
def run_intent(store, intent, scope):
    action = intent["action"]
    if action == "answer":
        return {"kind": "answer", "text": intent.get("text", "")}

    if action == "student_profile":
        who = str(intent.get("student", "")).strip().lower()
        match = None
        for s in store.list_students(scope=scope):   # already scope-filtered
            if who and (who == str(s["sid"]).lower() or who in (s["name"] or "").lower()):
                match = s
                break
        if not match:
            return {"kind": "not_found", "text": f"No student matching '{intent.get('student')}' in your access."}
        return {"kind": "student_profile", "data": store.student_profile(match["sid"])}

    if action == "cohort":
        return {"kind": "cohort", "data": store.cohort_signals(intent.get("class", ""), scope=scope)}

    if action == "attendance_summary":
        date = intent.get("date")
        if not date or str(date).lower() == "today":
            date = None   # store defaults to today
        return {"kind": "attendance_summary",
                "data": store.attendance_summary(date=date, scope=scope)}

    # action == "query"
    coll = intent["collection"]
    base = store._scope_attn_query(scope) if coll == "attendance" else _assignment_scope(scope)
    q = dict(base)
    for f in intent.get("filters", []):
        # scope-owned keys can be narrowed but never replaced wholesale
        if f["field"] in ("InId",):
            continue
        q[f["field"]] = _apply_op(f["op"], f["value"])
    col = store.attn if coll == "attendance" else store.assignments
    if intent.get("aggregation") == "count":
        return {"kind": "count", "collection": coll, "query": _safe(q),
                "count": col.count_documents(q)}
    rows = list(col.find(q, {"_id": 0}).limit(settings.chat_row_cap))
    return {"kind": "list", "collection": coll, "query": _safe(q),
            "count": len(rows), "rows": _safe(rows)}


def _safe(obj):
    """JSON-safe copy (drop ObjectId/embeddings/etc.)."""
    return json.loads(json.dumps(obj, default=str))


# --------------------------------------------------------------------------- #
# Composition — answer strictly from the returned data
# --------------------------------------------------------------------------- #
COMPOSE_SYSTEM = """Answer the staff member's question using ONLY the JSON data \
provided. Rules:
- Use exact numbers from the data. Never invent, infer, or extrapolate figures.
- Answer ONLY what was asked. Do NOT add recommendations, next steps, coaching,
  or commentary UNLESS the user explicitly asks how to help or improve a student.
- If the data is empty or a count is 0, say so plainly. Do not spin 0 into a
  positive ("no one absent") — describe what the data actually shows.
- For attendance summaries: 'present' is students marked present; 'absent' is
  everyone else in the roster; 'marked'/'records' is how many attendance rows
  exist. If records is 0, state clearly that no attendance has been recorded for
  that day yet — do not imply everyone attended or no one was absent.
- If results are limited to the staff member's own classes, you may note that.
- Be concise: 1–3 sentences for simple questions."""


def _format_attendance(data):
    """Compose the attendance answer in code — never via the LLM — so a 0 can
    never be spun into a positive. Truthful about empty scope / no records."""
    date = data.get("date")
    total = data.get("total_students", 0)
    present = data.get("present", 0)
    absent = data.get("absent", 0)
    marked = data.get("marked", 0)
    if total == 0:
        return f"No students found in your classes for {date}."
    if marked == 0:
        return (f"On {date}, no attendance has been recorded yet: 0 of {total} "
                f"students are marked present, so all {total} are currently "
                f"unmarked (counted as absent).")
    return (f"On {date}: {present} of {total} students present, {absent} absent "
            f"({marked} attendance record{'s' if marked != 1 else ''} logged).")


def _compose(question, result):
    if result["kind"] in ("answer", "not_found"):
        return result["text"]
    # Attendance counts are answered deterministically, not by the model.
    if result["kind"] == "attendance_summary":
        return _format_attendance(result["data"])
    context = json.dumps(result, default=str)[:6000]
    msgs = [
        {"role": "system", "content": COMPOSE_SYSTEM},
        {"role": "user", "content": f"Question: {question}\n\nData:\n{context}"},
    ]
    return _llm(msgs)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def answer(store, question, scope, context_sid=None):
    """Returns {answer, intent, data, error?}. Degrades gracefully if the LLM
    is unreachable or the intent is invalid."""
    if not settings.chat_enabled:
        return {"answer": "Chat is disabled on this server.", "intent": None, "data": None}
    if context_sid:
        question = f"(current student on screen: {context_sid})\n{question}"

    # Deterministic shortcut for attendance-count questions — skip the LLM's
    # intent step entirely so it can't mis-route to a raw status query.
    routed = pre_route(question)
    if routed is not None:
        result = run_intent(store, routed, scope)
        return {"answer": _compose(question, result), "intent": routed, "data": result}

    try:
        raw = _llm([{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question}], force_json=True)
        intent = _extract_json(raw)
    except (urllib.error.URLError, TimeoutError) as e:
        return {"answer": f"Could not reach the language model ({e}). Check that "
                          f"Ollama is running at {settings.chat_base_url}.",
                "intent": None, "data": None, "error": "llm_unreachable"}
    except Exception as e:
        return {"answer": "I couldn't understand that as a data question. Try asking "
                          "about a student's attendance or assignments.",
                "intent": None, "data": None, "error": f"parse: {e}"}

    ok, err = validate_intent(intent)
    if not ok:
        return {"answer": "I can only answer questions about attendance and "
                          "assignments for students you have access to.",
                "intent": intent, "data": None, "error": err}

    result = run_intent(store, intent, scope)
    try:
        final = _compose(question, result)
    except Exception as e:
        final = "I retrieved the data but couldn't summarize it — see the details below."
        result["compose_error"] = str(e)
    return {"answer": final, "intent": intent, "data": result}
