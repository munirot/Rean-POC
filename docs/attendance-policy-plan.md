# Attendance Policy — Admin-Configurable Mode & Capture Periods

**Project:** Rean / mycamu — `face-service` (FastAPI) + `attendance-ui`
**Problem:** How attendance is captured is hard-coded. There is no way for a university
admin to say *"section A uses the room camera, section B uses individual scan"*, and no
way to say *"attendance may only be taken between 08:00 and 08:20"*. Today a student
can self-check-in at any hour of the day.
**Goal:** Admin-managed policy that selects the capture **mode** per class and confines
capture to a defined **period**, enforced server-side.
**Status:** Draft for review · Date: 2026-08-21
**Companion to:** `docs/class-camera-attendance-plan.md` (mode `class_camera` is defined there)

---

## 1. Goal & scope

This governs **both** capture modes, including the individual scan that ships today —
it is not class-camera-specific.

**In scope:** period definitions, mode policy with scope inheritance, server-side window
enforcement, late/grace handling, and the first admin configuration UI.

**Out of scope:** the timetable (`TIMETBL`) itself, per-student exemptions, room/device
management, holiday calendars.

---

## 2. Key decisions (locked)

1. **Mode is set per section, inheriting upward.** Admin sets an institute default; a
   course or section may override. **Most specific wins** — the same precedence idea
   `user_scope` already uses for `InId`/`CrID`. This is what allows piloting the camera
   in one room while everything else stays on individual scan.

2. **Capture periods are named, with clock times.** The admin defines periods per
   institute (e.g. `Morning 08:00–08:20`). Self-contained — **no dependency on the
   unbuilt timetable**. This replaces the free-text session box.

3. **Late arrivals get a grace window and are marked `L` (Late).** Inside the period →
   `P`; inside grace → `L`; after that → rejected. Uses the existing `P`/`L`/`A`
   statuses and the `late` column the UI already renders.

4. **Enforcement is server-side.** See §5 — this is the security-critical decision.

5. **Staff manual edits are never window-gated.** See §5.2.

---

## 3. Why this cannot be env configuration

Every knob so far (`MATCH_THRESHOLD`, `SELF_CHECKIN_CHALLENGE`, …) lives in
`app/config.py` as a process-wide env var. That model breaks here on three counts:

| Requirement | Env config |
|---|---|
| Different mode per section | Impossible — `settings` is one global value |
| Admin edits it | Requires shell access + a service restart (`uvicorn` runs without `--reload`) |
| Auditable change history | None |

So policy moves to **MongoDB**, read at request time. Env keeps only the *fallback
default* used when no policy row exists, so behaviour is unchanged for an un-configured
institute.

### 3.1 The hidden dependency: sessions are currently free text

`TakeAttendance.jsx:310` renders the session as a text input — a teacher literally types
`Morning`, and `mark_attendance` stores that string. **A time window cannot be attached
to free text.** Structuring sessions into defined periods is therefore a prerequisite,
not a nice-to-have, and it is the single biggest change to the existing flow (§8).

---

## 4. Data model

### 4.1 `attendance_periods` — one doc per institute

```
{ InId: "IN001",
  periods: [
    { code: "MORNING",   name: "Morning",   start: "08:00", end: "08:20", graceMinutes: 10 },
    { code: "AFTERNOON", name: "Afternoon", start: "13:00", end: "13:20", graceMinutes: 10 }
  ],
  updatedBy, updatedAt }
```

`start`/`end` are **local wall-clock** strings in the institute's timezone (§5.1).
`name` must match the legacy free-text session values so existing history stays
queryable (§9).

### 4.2 `attendance_policies` — one doc per scope level

```
{ InId: "IN001",
  scope: "institute" | "course" | "section",
  CrID: null | "CR01",              // required for course/section scope
  SecID: null | "SEC-A",            // required for section scope
  mode: "individual" | "class_camera" | "both",
  allowIndividualFallback: true,    // class_camera only — see §6.2
  enforceWindow: true,
  updatedBy, updatedAt }
```

Indexes: `{InId, scope, CrID, SecID}` unique; `{InId}` for the admin list view.

Both collections are small and read-mostly — a natural fit for the same lazy-cache +
stamp-invalidation pattern `Store` already uses for the face gallery, if profiling
shows the per-mark lookup matters.

---

## 5. Resolution & enforcement

### 5.1 Resolving the effective policy

```
resolve_policy(InId, CrID, SecID) ->
    section policy  (InId + CrID + SecID)
      else course policy   (InId + CrID)
      else institute policy (InId)
      else built-in default from settings
```

The student's `CrID`/`SecID` come from their own document (`CurCrID`, `CurSecID`) —
already read by `mark_attendance` when it builds the attendance row, so no extra query.

### 5.2 Where the window is enforced

**Timezone correctness is a real trap here.** The codebase is deliberately careful about
local time — `settings.now_local()`, `today_str()`, and `dateAt` anchored to *Cambodia*
midnight (UTC+7), not UTC. A window check that compares `"08:00"` against a naive
`datetime.now()` on a UTC server is wrong by seven hours and would silently reject every
real check-in. **All window arithmetic must go through `settings.now_local()`.**

Enforcement point matters as much as correctness:

| Path | Window enforced? | Why |
|---|---|---|
| `POST /api/attendance` (face / self check-in) | **Yes** | Students self-mark; this is the path a student could call directly with `curl`. Client-side hiding is UX, not security. |
| Class-camera frame ingest / session close | **Yes** | Same rule, applied when the session is finalized. |
| `PUT /api/attendance` (`set_attendance`, staff) | **No** | This is the teacher's *correction* path. Gating it would make it impossible to fix a mistake after class — the exact scenario corrections exist for. |
| Dispute resolution (`approve`) | **No** | Already staff-only and deliberate; it corrects history. |

That split is the whole design: **the window constrains automated capture, never human
correction.**

### 5.3 Status from the window

```
now = settings.now_local()
in [start, end]                    -> "P"
in (end, end + graceMinutes]       -> "L"
outside                            -> reject (403, "outside the capture period")
```

`Store.mark_attendance` currently hard-codes `"status": "P"`. It needs to accept the
resolved status instead — a small but real change to a well-tested function.

---

## 6. Mode behaviour

### 6.1 Which capture paths each mode permits

| Mode | Individual face / self check-in | Class-camera session | Staff manual |
|---|---|---|---|
| `individual` | ✅ | ❌ rejected | ✅ |
| `class_camera` | ⚙️ `allowIndividualFallback` | ✅ | ✅ |
| `both` | ✅ | ✅ | ✅ |

### 6.2 Why `allowIndividualFallback` exists
In `class_camera` mode a student the camera never confirms lands on the teacher's
exception list. That is the agreed design — but on a bad day (back row, poor light) the
list could be long. Leaving individual scan available as a fallback lets a student
resolve their own case at the kiosk instead of queuing for teacher confirmation.
Defaults to `true`; an institute wanting strict camera-only can turn it off.

---

## 7. API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/attendance/policy` | any authenticated | Resolved policy + today's period state for the caller's context. Drives the UI. |
| `GET` | `/api/admin/attendance-periods` | **admin** | Read period definitions |
| `PUT` | `/api/admin/attendance-periods` | **admin** | Replace period definitions |
| `GET` | `/api/admin/attendance-policies` | **admin** | List policies for the institute |
| `PUT` | `/api/admin/attendance-policies` | **admin** | Upsert one scope's policy |
| `DELETE` | `/api/admin/attendance-policies/{id}` | **admin** | Remove an override (falls back to parent) |

`GET /api/attendance/policy` returns enough for the client to render honestly:

```json
{ "mode": "individual", "enforceWindow": true,
  "period": { "code": "MORNING", "name": "Morning", "start": "08:00", "end": "08:20" },
  "state": "open" | "grace" | "closed" | "before",
  "opensAt": "...", "closesAt": "...", "markStatus": "P" }
```

**Small cleanup this justifies:** the admin guard is currently duplicated inline twice
(`main.py:264`, `main.py:272`). Extract `require_admin(user)` alongside the existing
`require_staff(user)`.

---

## 8. Frontend work

1. **New admin Settings page** (`Settings.jsx`) — the first admin configuration surface
   in the product. Period editor + a policy table (institute default, plus per-course /
   per-section overrides with an "inherited" indicator). Route guarded with
   `RequireRole roles={['admin']}`, nav item gated the same way.

2. **`TakeAttendance.jsx` — replace the free-text session box with a period picker**
   driven by `/api/attendance/policy`, and surface window state plainly:
   - *before*: "Attendance opens at 08:00"
   - *open*: "Open until 08:20" (+ marks land as Present)
   - *grace*: "Late window — marks count as Late until 08:30"
   - *closed*: capture disabled, with "ask your teacher to mark you" for students
   - mode `class_camera` without fallback: hide self-scan entirely, explain why

3. **`Records.jsx`** — unchanged, but `L` already renders as Late, so grace-marked rows
   need no work.

---

## 9. Migration & back-compat

Existing attendance rows carry free-text sessions (`"Morning"` from the seeder and any
typed variants). Two rules keep history intact:

- Seed each institute's default periods with `name` values **matching the existing
  session strings**, so old rows join cleanly to new periods by name.
- Ship with `enforceWindow: false` as the built-in default. Nothing changes until an
  admin opts in — no institute wakes up unable to take attendance because a period was
  never configured. This mirrors `CLASS_CAM_ENABLED=false` in the companion plan.

A short audit of distinct `session` values in `attendance` should run before rollout to
catch typo variants ("morning", "Morning ") that would not map.

---

## 10. Phased roadmap

| Phase | Deliverable |
|---|---|
| **1. Periods + window enforcement** | `attendance_periods`, resolution, `mark_attendance` status from window, enforcement on `POST /api/attendance`. Applies to the **current individual flow** — value even before any camera exists. Tests: boundary times, grace, timezone, staff-edit bypass. |
| **2. Admin UI** | `require_admin`, the six endpoints, `Settings.jsx` period editor. |
| **3. Mode policy** | `attendance_policies` + inheritance, mode gating of capture paths, policy table in the admin UI. |
| **4. Client integration** | Period picker replaces free text; window state in `TakeAttendance`; mode-driven UI. |

Phase 1 alone closes a live gap — today a student can self-check-in at 3 a.m.

---

## 11. New/changed files (projected)

```
face-service/app/config.py      default mode/window fallbacks
face-service/app/db.py          attendance_periods + attendance_policies, resolve_policy,
                                window->status, mark_attendance status param
face-service/app/main.py        require_admin, 6 endpoints, window enforcement on POST
face-service/app/schemas.py     Period/Policy/PolicyState models
face-service/tests/test_attendance_policy.py   resolution precedence, window boundaries,
                                timezone, grace, staff-edit bypass
attendance-ui/src/pages/Settings.jsx           admin config UI (new)
attendance-ui/src/pages/TakeAttendance.jsx     period picker + window state
attendance-ui/src/main.jsx, components/Layout.jsx   admin-guarded route + nav
attendance-ui/src/api.js                       policy + admin helpers
```

---

## 12. Open questions

1. **Do periods vary by day of week?** A section may meet Mon/Wed only. Current model is
   one period set per institute; per-day schedules likely need the timetable.
2. **Multiple classes in one period** — if two sections both meet 08:00–08:20 in
   different rooms, does the period need a room/section binding, or is the existing
   `(StuID, date, session)` uniqueness sufficient?
3. **Who may edit policy** — institute admin only, or a delegated academic-office role?
   Today `type == "admin"` is the only admin-ish check that exists.
4. **Timezone per institute** — `APP_TZ_OFFSET_HOURS` is process-wide. A multi-institute
   deployment across timezones would need it per institute.
5. **Grace default** — 10 minutes assumed; confirm against actual academic policy.
