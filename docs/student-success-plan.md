# Student Success — Implementation Plan

Feature: combine **attendance** + **assignments/quizzes** into a per-student
profile, a staff dashboard, and a grounded staff chat that can answer questions
and suggest improvement plans.

Scope for this phase: **attendance + assignments only.** Extracurricular
activities are deferred (no new collection needed now). Both source collections
already exist and are seeded, so this feature is an *aggregation + API + UI*
layer with zero data migration.

---

## 1. Data sources (already present)

MongoDB `rean_face_poc`:

- `attendance` — one row per (StuID, date, session). Fields used: `StuID`,
  `status` (`P`/`L`/`A`), `date`, `dateAt`, `session`, `SubID`, `SubNa`,
  `CrID`, `InId`.
- `assignments` — one doc per assignment with a `Students[]` array. Fields used:
  `Catry` (`Homework`/`Quiz`/`Project`/`Seminar`), `SubID`, `SubNa`, `CrID`,
  `InId`, `assgnDueDt`, and per-student `{ StuID, status (assigned/submitted/
  graded), marks (int|null) }`.
- `students` — identity + `CurCrID`/`CurCrNm`, `InId` for scoping.

No schema changes required.

---

## 2. Aggregation layer — `student_profile`

Computed on demand (no materialized collection in v1), keyed on `StuID`.
New method in `face-service/app/db.py`, alongside `student_stats`.

```py
def student_profile(self, sid):
    rec = self.get(sid)
    if not rec: return None
    att = self.student_stats(sid)                     # reuse existing
    acad = self._academics(sid, rec["raw"].get("CurCrID"))
    signals = self._signals(att, acad)
    return { "sid": sid, "name": rec["name"], "cls": rec["cls"],
             "InId": rec["raw"].get("InId"), "CrID": rec["raw"].get("CurCrID"),
             "attendance": att, "academics": acad, "signals": signals }
```

`_academics(sid, crid)` — scan `assignments` where `Students.StuID == sid`,
pull that student's sub-entry, and reduce:

```
academics = {
  quizAvg, homeworkAvg, projectAvg,   # mean of graded marks by Catry
  submitted, graded, missing,         # missing = assigned & past due & not submitted
  byCategory: { Quiz: {avg, count, missing}, Homework: {...}, ... }
}
```

`_signals(att, acad)` — threshold rules over the two blocks (auditable, no ML):

- `attendance_low` — rate < 75%
- `attendance_declining` — recent window rate drops >15% vs. prior window
- `missing_assignments` — missing ≥ 2
- `quiz_avg_below_60` — quiz average < 60
- `at_risk` — attendance_declining **and** (quiz_avg_below_60 or missing_assignments)

The cross-signal (`at_risk`) is the payoff of joining the two datasets — neither
number shows it alone.

Cohort variant: `cohort_signals(cls)` runs the same reduction across a class and
returns a ranked list of flagged students for the dashboard.

---

## 3. Backend API (`face-service/app/main.py`)

New endpoints (add Pydantic models to `schemas.py`):

- `GET /api/students/{sid}/profile/full` → `student_profile(sid)`
- `GET /api/analytics/cohort?cls=<name>` → `cohort_signals(cls)`
- `POST /api/chat` → grounded chat (see §4)

### Auth hardening (prerequisite for chat)

Today the base64 token from `/api/auth/login` is issued but **not verified** on
read routes. Before shipping chat, add a FastAPI dependency that decodes the
token (`loginId:type`), loads the login doc, and yields `{InId, type,
courses}`. Apply it to the new endpoints. This is the guardrail the chat leans
on — do it first.

```py
def current_user(authorization: str = Header(None)) -> dict: ...
# raises 401 if missing/invalid; returns scope used to filter every query
```

---

## 4. Chat layer — text-to-query, constrained

Chosen approach: **text-to-query**, but generation is constrained to a
whitelist and the server owns access control. The LLM never emits raw Mongo.

Flow per message:

1. LLM receives the question + a compact schema description and returns a
   **structured query intent**, not a query string:
   ```json
   { "collection": "attendance" | "assignments" | "student_profile",
     "filters": { "field": "...", "op": "eq|lt|gt|in", "value": ... },
     "aggregation": "none" | "count" | "avg" }
   ```
2. Server **validates** intent against an allow-list of collections, fields,
   and operators. Reject anything off-list.
3. Server **injects mandatory scope** before running: merges
   `{ InId: user.InId, CrID: {$in: user.courses} }` into every filter and
   forbids the intent from overriding them. The LLM chooses *what* to look for;
   the server decides *what it is allowed to see*.
4. Run against a **read-only Mongo user** with a query timeout and a row cap.
5. LLM composes the answer **from the returned rows**, and the response includes
   the intent it ran + raw counts so staff can verify. Cite counts, never
   paraphrase numbers.

Aggregations that people act on (class average, at-risk counts) are **fixed,
tested functions** the model calls by name — not generated — to avoid subtly
wrong math. Generated intents are reserved for filtering/lookup.

### Improvement-plan suggestions

When staff ask "how do I help X," the chat calls `student_profile`, then asks
the LLM to draft suggestions **grounded in the returned signals**. Framed as
suggestions to the teacher, shown with the underlying data, **never
auto-assigned** to the student. Human in the loop by design.

LLM access: reuse an existing provider key via env; a new
`face-service/app/chat.py` module holds the schema prompt, intent validator,
scope injector, and composer. Keep the model provider swappable.

---

## 5. Frontend (`attendance-ui`)

- `api.js` — add `studentProfileFull(sid)`, `cohort(cls)`, `chat(messages)`.
- `pages/StudentDetail.jsx` — add a profile section: attendance rate + trend,
  academics by category, and signal badges (reuse the existing `Badge`).
- `pages/Insights.jsx` (new) — cohort dashboard: class picker → ranked at-risk
  list + attendance-vs-quiz scatter/timeline. Descriptive only (no causal
  claims, no student ranking exposed to students).
- `pages/Chat.jsx` (new) — message thread; render the cited counts/query the
  server returns beneath each answer.
- `main.jsx` — add `insights` and `chat` routes inside the `RequireAuth` block.

Charts: the UI already uses react-bootstrap; add a lightweight chart lib
(e.g. recharts) or reuse existing patterns.

---

## 6. Build order (phased)

1. **Aggregation** — `student_profile` + `_academics` + `_signals` in `db.py`;
   unit-test against seeded data. *(no UI yet)*
2. **Profile endpoint + student page** — `GET .../profile/full`; extend
   `StudentDetail.jsx`. First visible value.
3. **Cohort analytics** — `cohort_signals` + `/api/analytics/cohort` +
   `Insights.jsx` dashboard.
4. **Auth hardening** — `current_user` dependency on the new routes.
   *(Do before chat.)*
5. **Chat** — read-only Mongo user, `chat.py` (intent → validate → scope →
   run → compose), `/api/chat`, `Chat.jsx`.
6. **Improvement suggestions** — layer onto chat once profiles are trusted.

Each phase is independently shippable; value starts at phase 2.

---

## 7. Testing

- Unit-test `_academics` / `_signals` on seeded `assignments` + `attendance`
  (assert averages, missing counts, and each signal fires/clears correctly).
- Chat safety tests: intents referencing disallowed collections/fields are
  rejected; scope injection cannot be overridden; a teacher cannot read a
  student outside their `InId`/courses; row cap and timeout enforced.
- Verify no numbers in chat answers appear that aren't in the returned rows
  (anti-hallucination check).

---

## 8. Notes / guardrails

- Keep all analytics **descriptive** — show "low attendance + low quiz avg,"
  do not assert causation or rank/label students in student-facing views.
- Improvement plans are teacher-facing suggestions with data shown, never
  auto-applied.
- Read-only DB credentials for the chat path; app write credentials never
  reach the LLM path.
