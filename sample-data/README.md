# Sample data — mycamu/Rean replica

Generates a small, internally-consistent dataset that mirrors the real Rean schema and loads
it into the POC MongoDB (`rean_face_poc`, alongside `face_students`), plus a JSON export per
collection you can re-import or inspect.

## Run

From `sample-data/` (uses the same Mongo as the face-service):

```bash
python seed_sample_data.py                    # generate + insert into Mongo + export JSON
python seed_sample_data.py --no-db            # only write JSON (no database)
MONGO_URI="mongodb://USER:PASS@localhost:27017/?authSource=admin" python seed_sample_data.py
```

Insert **replaces the sample collections only** — it does not touch `face_students`. Re-running
is safe (idempotent). Reproducible (fixed random seed).

Demo login password for every generated login: **`Rean@123`** (stored as a sha256 placeholder —
this is demo/UI data, not real authentication).

## What it creates (small demo)

| Collection | Count | Mirrors model | Key fields |
|---|---|---|---|
| `institutes` | 1 | `minstitute` | InId, InNa, InCd |
| `academic_years` | 1 | `macyr` | AcYr, AcYrNm |
| `programs` | 1 | `mprogram` | PrID, PrNa |
| `departments` | 4 | `mdepartment` | DeptID, DeptNa (faculties) |
| `courses` | 4 | `mcourse` | CrID, CrNa, DeptID |
| `semesters` | 1 | `msemester` | SemID, SemNa |
| `sections` | 1 | `msection` | SecID, SecNa |
| `subjects` | 8 | `msubject` | SubID, SubNa, CrID |
| `roles` | 4 | (login `Type` + `mmycamu_access_control`) | code, name, menus[] |
| `staffs` | 6 | `mstaff` | StaffID, SuNa, FoNa, Dept, Desi, SubE[] |
| `students` | 30 | `mstudent` | CmStudID/StuID, FNa, LNa, CurCrID, CurDeptID, RollNo |
| `logins` | 37 | `mlogin` | LoginID, Email, Type (admin/staff/student), pwd |
| `access_control` | 30 | `mmycamu_access_control` | StuID, stuSts, menus[] |
| `attendance` | 300 | `mattendance` (simplified daily log) | InId…SecID, StuID, SubID, date, session, status P/L/A, source |
| `assignments` | 11 | `massignment` | CmAssID, SubID, StaffID, assgnDueDt, Students[] |

Everything links up: students reference real courses/departments; attendance and assignments
reference real student IDs, subjects, and staff. The institute and names match your screenshots
(Royal University of Law and Economics; faculties of Public Administration, Law, Economics,
International Relations; Cambodian names).

## Roles

Modeled the way Rean actually does access — a login `Type` (`admin` / `staff` / `student`) plus a
menu-code list per role in `roles` and per student in `access_control` (`menus[].code`). Codes
match the sidebar items (ATTND, ASSIGN, GRADE, TIMETBL, …).

## Notes

- `attendance` is a **flat daily log** (one row per student/day/session) rather than the real
  `mattendance` aggregate document — much easier to query and drive a UI. Each row carries the
  full `InId/PrID/CrID/DeptID/SemID/SecID/AcYr` linkage plus `source: face|manual`, so it lines
  up with the face-attendance flow.
- Photos aren't included (`PhotoImgID: null`); enroll faces via the face-service/attendance-ui.
- To grow the set, bump `gen_students(30)` and the `working_days(..., 10)` count in the script.
```
