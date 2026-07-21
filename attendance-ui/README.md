# Rean · Attendance UI

A trimmed platform front-end with just the two modules that matter for face attendance:
**Student Profiles** and **Attendance**. Built to match the existing **mycamu-react** look —
Bootstrap 5 + react-bootstrap, the same `Theme1` SCSS variables (navy `#091e42` / blue
`#0d9be1`), `material-symbols-rounded` icons, NotoSansKhmer font, and cloned `PageHeader` /
`Button` / `MatIcon` / sidenav (`left-nav_container`) components. Talks to the Python
`face-service`.

Stack note: styling is compiled from `src/styles/theme.scss`, which overrides Bootstrap's
`$primary`/`$info`/etc. with the mycamu palette and then imports `bootstrap/scss/bootstrap`,
mirroring how mycamu-react builds its theme.

## Pages

- **Dashboard** — today's present/absent counts, per-class rate, recent check-ins.
- **Students** — searchable roster; click a student for their profile + face-enrollment
  (upload photo or capture from camera, remove profile).
- **Take Attendance** — live camera (or class photo) recognizes enrolled students and marks
  them present for a session; adjustable strictness; auto-mark toggle; running present list.
- **Records** — attendance history filtered by date / class / session, with CSV export and undo.

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

1. **Students → open a few "Profile expected" students → enroll** a photo each (or camera).
2. **Take Attendance → set a session (e.g. Morning) → Start camera** → enrolled faces get a
   green box + name and are marked present (auto-mark on). Un-enrolled faces read *Unknown*.
3. **Dashboard / Records** to review and export.

## Notes

- Everything runs against the POC sample data seeded in the face-service Mongo (8 students).
- Recognition strictness maps to the backend cosine threshold; tune it with the benchmark
  (`../face-service/eval`).
- No liveness yet — a photo can pass. That's the next production step, per
  `../docs/face-attendance-plan.md`.
- This is a standalone demo surface; it mirrors the Student + Attendance modules that would
  live inside mycamu in production.
