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
import difflib
import json
import re
import urllib.request
import urllib.error
from datetime import date as _date, datetime, timedelta

from .config import settings

# --------------------------------------------------------------------------- #
# Allow-list: the only collections/fields/operators a generated query may touch
# --------------------------------------------------------------------------- #
OPS = {"eq", "in", "gte", "lte"}
ALLOWED = {
    "attendance": {"StuID", "status", "date", "session", "SubID", "CrID"},
    "assignments": {"Catry", "SubID", "CrID", "StaffID"},
}
ACTIONS = {"student_profile", "cohort", "query", "attendance_summary",
           "attendance_range", "answer"}

SYSTEM_PROMPT = """You are a warm, knowledgeable assistant for school staff. Be \
genuinely helpful and conversational — answer general questions, explain ideas, \
brainstorm, and offer teaching advice using your own knowledge and judgment, just \
like a capable colleague would.

ONE hard rule: any specific fact about a REAL student — their attendance, grades,
assignments, or risk status — must come from the tools, never from memory or
guessing. When a question is about a specific student or this school's data, call
the right tool and answer ONLY from what it returns. Never invent names, numbers,
dates, or records. If the tools don't have it, say you don't have that data.

Tools (call at most one, only when you need school data):
- get_attendance(period): present/absent counts or attendance rate for a day or
  period. Pass a phrase ("today", "last week", a date) — the server resolves the
  real dates, so you never compute a date yourself.
- get_student(student): one student's attendance, grades, and risk signals.
- get_cohort(class_name): at-risk students in a class.
- search_records(collection, filters, aggregation): count/list specific rows.

For anything NOT about specific student/school data — greetings, general
education questions, advice, definitions, brainstorming — just reply naturally,
no tool needed. Use earlier messages for context: resolve "he/she/they/that
student/what about .../his quizzes" to the student, class, or period discussed
just before, rather than asking again. Call at most one tool per reply."""

# OpenAI-style tool schemas. The model PICKS a tool; the server still resolves
# dates and injects the caller's scope when it runs the tool (see run_intent).
TOOLS = [
    {"type": "function", "function": {
        "name": "get_attendance",
        "description": "Attendance figures for a day or a period. Use for present/"
                       "absent counts and attendance rate.",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string",
                       "description": "Natural phrase like 'today', 'yesterday', "
                                      "'last week', 'this month', or a date 'YYYY-MM-DD'."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "get_student",
        "description": "One student's full record: attendance, grades, risk signals. "
                       "If several students share a name, the server will ask which one.",
        "parameters": {"type": "object", "properties": {
            "student": {"type": "string", "description": "Student name or ID."},
            "class_name": {"type": "string",
                           "description": "Optional class/course to disambiguate a shared name."}},
            "required": ["student"]}}},
    {"type": "function", "function": {
        "name": "get_cohort",
        "description": "At-risk students in a class (attendance + assignment signals).",
        "parameters": {"type": "object", "properties": {
            "class_name": {"type": "string", "description": "Class or course name."}},
            "required": ["class_name"]}}},
    {"type": "function", "function": {
        "name": "search_records",
        "description": "Count or list specific rows in attendance or assignments.",
        "parameters": {"type": "object", "properties": {
            "collection": {"type": "string", "enum": ["attendance", "assignments"]},
            "filters": {"type": "array", "items": {"type": "object", "properties": {
                "field": {"type": "string"}, "op": {"type": "string", "enum": list(OPS)},
                "value": {}}}},
            "aggregation": {"type": "string", "enum": ["count", "list"]}},
            "required": ["collection", "aggregation"]}}},
]


def map_tool_call(name, args):
    """Map a model tool call to an internal intent (pure). Returns None if the
    tool name is unknown. run_intent then resolves dates + injects scope."""
    args = args or {}
    if name == "get_attendance":
        return {"action": "attendance_summary", "date": args.get("period") or "today"}
    if name == "get_student":
        return {"action": "student_profile", "student": args.get("student", ""),
                "class_hint": args.get("class_name")}
    if name == "get_cohort":
        return {"action": "cohort", "class": args.get("class_name") or args.get("class", "")}
    if name == "search_records":
        return {"action": "query", "collection": args.get("collection"),
                "filters": args.get("filters", []),
                "aggregation": args.get("aggregation", "count")}
    return None


# --------------------------------------------------------------------------- #
# LLM call (OpenAI-compatible /chat/completions via stdlib — no extra deps).
# Returns the full assistant message so callers can read tool_calls or content.
# --------------------------------------------------------------------------- #
def _chat_completion(messages, tools=None, force_json=False):
    payload = {"model": settings.chat_model, "messages": messages,
               "temperature": settings.chat_temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
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
    return data["choices"][0]["message"]


def _llm(messages, force_json=False):
    """Plain-text completion (used by the deterministic composer)."""
    return _chat_completion(messages, force_json=force_json).get("content") or ""


def _first_tool_call(message):
    """Extract (name, args_dict) from an assistant message, or (None, None)."""
    calls = message.get("tool_calls") or []
    if not calls:
        return None, None
    fn = calls[0].get("function", {})
    name = fn.get("name")
    raw = fn.get("arguments")
    if isinstance(raw, str):
        try:
            args = json.loads(raw or "{}")
        except Exception:
            args = {}
    else:
        args = raw or {}
    return name, args


# --------------------------------------------------------------------------- #
# Deterministic date handling. The model does NOT know today's date and will
# hallucinate (e.g. "2023-06-10"), so we resolve every relative period in code
# against the server clock and never trust the LLM for dates.
# --------------------------------------------------------------------------- #
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
# attendance nouns/verbs that signal the topic
_ATTN_WORDS = ("absent", "absence", "attendance", "attended", "turnout",
               "showed up", "checked in", "check-in", "present today",
               "present in", "were present", "are present", "who is present",
               "how many present", "how many are present")
# quantity cues that make it a *count* question (avoids "present a summary" etc.)
_QUANT_WORDS = ("how many", "number of", "count", "rate", "how's attendance",
                "attendance for")
# phrases that, on their own, mean "apply the previous question to this period"
_PERIOD_WORDS = ("today", "yesterday", "this week", "last week", "past week",
                 "last 7 days", "previous week", "this month", "last month")


def resolve_period(text, today=None):
    """Resolve a date phrase to a concrete day or range. Returns
    {"type":"day","date":"YYYY-MM-DD","label":...} or
    {"type":"range","start":...,"end":...,"label":...} or None."""
    today = today or settings.now_local().date()
    q = (text or "").lower()

    m = _DATE_RE.search(text or "")
    if m:
        return {"type": "day", "date": m.group(1), "label": m.group(1)}
    if "yesterday" in q:
        d = today - timedelta(days=1)
        return {"type": "day", "date": d.isoformat(), "label": "yesterday"}
    if "today" in q or "so far" in q:
        return {"type": "day", "date": today.isoformat(), "label": "today"}
    this_mon = today - timedelta(days=today.weekday())
    if "this week" in q:
        return {"type": "range", "start": this_mon.isoformat(),
                "end": today.isoformat(), "label": "this week"}
    if "last week" in q or "previous week" in q:
        last_mon = this_mon - timedelta(days=7)
        last_sun = this_mon - timedelta(days=1)
        return {"type": "range", "start": last_mon.isoformat(),
                "end": last_sun.isoformat(), "label": "last week"}
    if "past week" in q or "last 7 days" in q or "past 7 days" in q:
        start = today - timedelta(days=6)
        return {"type": "range", "start": start.isoformat(),
                "end": today.isoformat(), "label": "the past 7 days"}
    if "this month" in q:
        return {"type": "range", "start": today.replace(day=1).isoformat(),
                "end": today.isoformat(), "label": "this month"}
    if "last month" in q:
        first_this = today.replace(day=1)
        last_end = first_this - timedelta(days=1)
        return {"type": "range", "start": last_end.replace(day=1).isoformat(),
                "end": last_end.isoformat(), "label": "last month"}
    return None


def _period_to_intent(period):
    if period["type"] == "day":
        return {"action": "attendance_summary", "date": period["date"], "_label": period["label"]}
    return {"action": "attendance_range", "start": period["start"],
            "end": period["end"], "_label": period["label"]}


def _prev_was_attendance(history):
    """Was the most recent user turn an attendance question? (for follow-ups)"""
    for m in reversed(history or []):
        if m.get("role") == "user":
            return any(w in (m.get("content") or "").lower() for w in _ATTN_WORDS)
    return False


def pre_route(question, history=None, today=None):
    """Deterministically route only *clear* attendance-count questions (so a 0
    can't be spun and dates can't be hallucinated). Everything else — including
    general chat — falls through to the smart model. Follow-ups like "what about
    last week" continue an attendance conversation."""
    q = question.lower()
    has_attn = any(w in q for w in _ATTN_WORDS)
    has_quant = any(c in q for c in _QUANT_WORDS)
    period = resolve_period(question, today=today)

    # A clear attendance-count question needs the attendance topic AND either a
    # counting cue or an explicit period — this avoids hijacking "present a
    # lesson summary" and similar general phrasing.
    if has_attn and (has_quant or period):
        return _period_to_intent(period) if period else \
            {"action": "attendance_summary", "date": "today", "_label": "today"}
    # bare period follow-up ("what about last week?") after an attendance turn
    if period and any(w in q for w in _PERIOD_WORDS) and _prev_was_attendance(history):
        return _period_to_intent(period)
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
    if action == "attendance_range":
        for k in ("start", "end"):
            if not _DATE_RE.fullmatch(str(intent.get(k, ""))):
                return False, f"{k} must be YYYY-MM-DD"
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


def match_students(roster, query, class_hint=None):
    """Find candidate students for a name/id query. Pure — testable without a DB.
    Returns (matches, suggestions):
      - matches: strong matches (exact id/name or clear substring); 1 => resolved,
        >1 => ambiguous (ask which one).
      - suggestions: fuzzy near-matches when there is no strong match (typos).
    """
    q = (query or "").strip().lower()
    rows = [(s, (s.get("name") or "").lower(), str(s.get("sid", "")).lower())
            for s in roster]
    strong = []
    for s, nm, sid in rows:
        if not q:
            continue
        if q == sid or q == nm or (len(q) >= 3 and q in nm):
            strong.append(s)
    if class_hint:
        ch = str(class_hint).strip().lower()
        filtered = [s for s in strong if ch in (s.get("cls") or "").lower()]
        if filtered:
            strong = filtered
    if strong:
        # de-dup by sid, preserve order
        seen, uniq = set(), []
        for s in strong:
            if s["sid"] not in seen:
                seen.add(s["sid"])
                uniq.append(s)
        return uniq, []
    # no strong match -> fuzzy suggestions on full names (handles typos)
    close = difflib.get_close_matches(q, [nm for _, nm, _ in rows], n=6, cutoff=0.6)
    sugg = [s for s, nm, _ in rows if nm in close]
    return [], sugg


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
        who = intent.get("student", "")
        roster = store.list_students(scope=scope)   # already scope-filtered
        matches, suggestions = match_students(roster, who, intent.get("class_hint"))
        if len(matches) == 1:
            return {"kind": "student_profile", "data": store.student_profile(matches[0]["sid"])}
        if len(matches) > 1:
            return {"kind": "clarify", "query": who, "exact": True,
                    "candidates": _candidate_list(matches)}
        if suggestions:
            return {"kind": "clarify", "query": who, "exact": False,
                    "candidates": _candidate_list(suggestions)}
        return {"kind": "not_found",
                "text": f"I couldn't find a student matching '{who}' in your classes."}

    if action == "cohort":
        return {"kind": "cohort", "data": store.cohort_signals(intent.get("class", ""), scope=scope)}

    if action == "attendance_summary":
        date = intent.get("date")
        if not date or str(date).lower() == "today":
            date = None   # store defaults to today
        elif not _DATE_RE.fullmatch(str(date)):
            # resolve any other phrase the LLM may have emitted ("yesterday"/…)
            p = resolve_period(str(date))
            if p and p["type"] == "day":
                date = p["date"]
            elif p and p["type"] == "range":
                return {"kind": "attendance_range", "label": p.get("label"),
                        "data": store.attendance_range(p["start"], p["end"], scope=scope)}
            else:
                date = None
        return {"kind": "attendance_summary", "label": intent.get("_label"),
                "data": store.attendance_summary(date=date, scope=scope)}

    if action == "attendance_range":
        return {"kind": "attendance_range", "label": intent.get("_label"),
                "data": store.attendance_range(intent["start"], intent["end"], scope=scope)}

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


def _candidate_list(rows, limit=6):
    return [{"sid": s["sid"], "name": s.get("name"), "cls": s.get("cls")}
            for s in rows[:limit]]


def _format_clarify(result):
    """A grounded clarifying question listing only real, in-scope students."""
    q = result.get("query")
    cands = result.get("candidates", [])
    listed = "; ".join(
        f"{c['name']} ({c.get('cls') or 'no class'}, ID {c['sid']})" for c in cands)
    if result.get("exact"):
        return (f"More than one student matches \"{q}\": {listed}. "
                "Which one do you mean?")
    return (f"I couldn't find an exact match for \"{q}\". Did you mean: {listed}? "
            "Let me know which one.")


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


def _format_range(data, label=None):
    """Deterministic answer for a date range (e.g. 'last week')."""
    start, end = data.get("start"), data.get("end")
    when = f"{label} ({start} to {end})" if label else f"{start} to {end}"
    records = data.get("records", 0)
    if records == 0:
        return f"No attendance was recorded during {when}."
    present = data.get("present_records", 0)
    distinct = data.get("distinct_present", 0)
    days = data.get("days", 0)
    total = data.get("total_students", 0)
    return (f"During {when}: {present} present-mark{'s' if present != 1 else ''} "
            f"across {distinct} student{'s' if distinct != 1 else ''}, over {days} "
            f"day{'s' if days != 1 else ''} with records (out of {total} students).")


def _compose(question, result):
    if result["kind"] in ("answer", "not_found"):
        return result["text"]
    # Ambiguous / misspelled student name -> deterministic clarifying question.
    if result["kind"] == "clarify":
        return _format_clarify(result)
    # Attendance figures are answered deterministically, never by the model.
    if result["kind"] == "attendance_summary":
        return _format_attendance(result["data"])
    if result["kind"] == "attendance_range":
        return _format_range(result["data"], result.get("label"))
    context = json.dumps(result, default=str)[:6000]
    msgs = [
        {"role": "system", "content": COMPOSE_SYSTEM},
        {"role": "user", "content": f"Question: {question}\n\nData:\n{context}"},
    ]
    return _llm(msgs)


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def _history_msgs(history, limit=6):
    """Recent turns as OpenAI-style messages, for multi-turn context."""
    out = []
    for m in (history or [])[-limit:]:
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            out.append({"role": role, "content": m["content"]})
    return out


# Pronouns / vague references that mean "the student we were just discussing".
_PRONOUNS = (" he ", " him", " his ", " she ", " her", " they ", " them",
             " their", "that student", "this student", "the student",
             "same student")


def _mentions_person_ref(text):
    t = f" {text.lower()} "
    return any(p in t for p in _PRONOUNS)


def _focus_student(store, history, scope):
    """Find the most recently discussed student in the conversation so pronoun
    follow-ups resolve even if the model doesn't. Returns {sid,name} or None."""
    if not history:
        return None
    try:
        roster = store.list_students(scope=scope)   # scope-limited
    except Exception:
        return None
    for m in reversed(history):
        text = (m.get("content") or "").lower()
        if not text:
            continue
        for s in roster:
            nm = (s.get("name") or "").lower()
            if (nm and nm in text) or str(s.get("sid", "")).lower() in text:
                return {"sid": s["sid"], "name": s["name"]}
    return None


def answer(store, question, scope, context_sid=None, history=None):
    """Returns {answer, intent, error?}. Uses recent history for follow-ups and
    resolves all dates against the server clock. Degrades gracefully."""
    if not settings.chat_enabled:
        return {"answer": "Chat is disabled on this server.", "intent": None}
    if context_sid:
        question = f"(current student on screen: {context_sid})\n{question}"

    # Resolve pronoun/vague references to the student discussed earlier, so
    # follow-ups like "how are his quizzes?" work regardless of model strength.
    if history and _mentions_person_ref(question):
        foc = _focus_student(store, history, scope)
        if foc:
            question = (f"(the student being discussed is {foc['name']}, "
                        f"id {foc['sid']})\n{question}")

    # Deterministic shortcut for attendance/date questions — skips the LLM so it
    # can neither mis-route to a raw status query nor invent a date. Uses history
    # so follow-ups like "what about last week" continue the attendance topic.
    routed = pre_route(question, history=history)
    if routed is not None:
        result = run_intent(store, routed, scope)
        return {"answer": _compose(question, result), "intent": routed}

    today = settings.now_local().date().isoformat()
    sys_prompt = SYSTEM_PROMPT + f"\n\nToday's date is {today}."
    try:
        message = _chat_completion(
            [{"role": "system", "content": sys_prompt}]
            + _history_msgs(history)
            + [{"role": "user", "content": question}], tools=TOOLS)
    except (urllib.error.URLError, TimeoutError) as e:
        return {"answer": f"Could not reach the language model ({e}). Check that "
                          f"Ollama is running at {settings.chat_base_url}.",
                "intent": None, "error": "llm_unreachable"}
    except Exception as e:
        return {"answer": "Something went wrong talking to the language model.",
                "intent": None, "error": f"llm: {e}"}

    # No tool call -> the model answered directly (small talk / out of scope).
    name, args = _first_tool_call(message)
    if not name:
        return {"answer": (message.get("content") or "").strip()
                or "I can help with student attendance and assignments — what would "
                   "you like to know?", "intent": None}

    intent = map_tool_call(name, args)
    if intent is None:
        return {"answer": "I can only answer questions about attendance and assignments.",
                "intent": {"tool": name}, "error": "unknown_tool"}

    ok, err = validate_intent(intent)
    if not ok:
        return {"answer": "I can only answer questions about attendance and "
                          "assignments for students you have access to.",
                "intent": intent, "error": err}

    result = run_intent(store, intent, scope)
    try:
        final = _compose(question, result)
    except Exception as e:
        final = "I retrieved the data but couldn't summarize it."
        result["compose_error"] = str(e)
    return {"answer": final, "intent": intent}
