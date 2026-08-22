# Rean · Attendance UI

A platform front-end for face attendance, covering enrollment, capture, the
student-facing portal, and the staff/admin workflows around them. Built to match the
existing **mycamu-react** look —
Bootstrap 5 + react-bootstrap, the same `Theme1` SCSS variables (navy `#091e42` / blue
`#0d9be1`), `material-symbols-rounded` icons, NotoSansKhmer font, and cloned `PageHeader` /
`Button` / `MatIcon` / sidenav (`left-nav_container`) components. Talks to the Python
`face-service`.

Stack note: styling is compiled from `src/styles/theme.scss`, which overrides Bootstrap's
`$primary`/`$info`/etc. with the mycamu palette and then imports `bootstrap/scss/bootstrap`,
mirroring how mycamu-react builds its theme.

## Pages

The sidebar is role-aware and routes are role-guarded server-side *and* client-side —
a student who deep-links to a staff page is redirected to their own landing.

**Everyone**
- **Dashboard** — staff see today's counts and recent check-ins; a student sees their
  own dashboard instead (attendance rate, "My progress" at-risk view in supportive
  first-person wording, recent records, leave requests).
- **Take Attendance / Self Check-in** — live camera recognizes enrolled students and
  marks them present. A student may only mark themselves, and must pass a live
  head-turn challenge first. Marking is confined to the configured capture period.
- **Attendance Records** — history by date / class / session, CSV export, staff edit.
- **Ask AI** — grounded chat over attendance and assignments, scoped to the caller: a
  student can only ever ask about their own record.

**Staff & admin**
- **Students** — searchable roster; open a student to enroll their face (guided
  multi-angle capture with liveness) or view their profile and improvement plan.
- **Class Scan** — whole-class camera sitting: confirmed / ambiguous / not-detected
  buckets with one-tap resolution. Off unless the server enables it.
- **Disputes** — students challenging a mark; approving corrects the attendance row.
- **Leave requests** — approving reclassifies that student's absences to Excused,
  which leaves them out of the attendance-rate denominator.
- **Insights** — cohort at-risk signals.

**Admin only** (via the gear in the right-hand rail)
- **Settings** — capture periods, capture mode per institute/course/section, room
  camera calibration and tokens, and a pre-rollout check of existing session labels.

## Run

1. Start the backend (from `../face-service`, with MongoDB running):
   ```bash
   ./run.sh                     # http://localhost:8000
   ```
2. Start this UI:
   ```bash
   cd attendance-ui
   npm install
   npm run dev                  # http://localhost:5173
   ```
   Vite proxies `/api` → `http://localhost:8000`, so the browser stays same-origin and the
   live camera works (localhost is a secure context).

Point the backend elsewhere with `VITE_API_TARGET=http://host:port npm run dev`, or hit a
fully separate origin at build time with `VITE_API_BASE=https://api.example.com`.

## Typical flow

1. **Students → open a student → enroll** with the guided capture (centre, left, right;
   every frame is re-validated server-side for pose, quality and liveness).
2. **Take Attendance → pick the period → Start camera** → enrolled faces get a green box
   and are marked present once confirmed across a few frames. Un-enrolled faces read
   *Unknown*; a look-alike too close to call reads *Unknown* rather than guessing.
3. **Dashboard / Records** to review and export.

## Notes

- Everything runs against the sample data seeded in the face-service Mongo (30 students).
- The strictness slider maps to the backend cosine threshold — **higher is stricter**.
  It only overrides the server's own value once dragged; tune the real default with
  `../face-service/eval` and `../docs/face-matching-tuning.md`.
- Liveness is in place: passive anti-spoofing on every recognition, a motion-based
  guided enrollment, and an active head-turn challenge for unsupervised student
  self check-in. The passive scorer still ships as a classical-CV baseline — install
  a MiniFASNet ONNX model and calibrate before relying on it in production.
- This is a standalone demo surface; it mirrors the Student + Attendance modules that would
  live inside mycamu in production.
