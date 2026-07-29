"""Fill a full month of mock ATTENDANCE and ASSIGNMENTS/EXAMS for every student.

Reads the existing students / subjects / staff from MongoDB (so StuIDs stay linked
to any face profiles you've enrolled), then regenerates and OVERWRITES just the
`attendance` and `assignments` collections for the whole target month.

  - Attendance: one record per student, per subject in their course, per working
    day (Mon-Fri) of the month. Status P/L/A with a per-student reliability so some
    students trend low (useful for the Insights signals). Check-in times are stored
    in UTC but land at a realistic Cambodia morning (~07:45-08:40 local).
  - Assignments/exams: several per subject spread across the month, categories
    Homework / Quiz / Exam / Project. Each carries a Students[] array with per-
    student status (assigned/submitted/graded) and marks.

Nothing else is touched — students, subjects, staff, logins, face_embeddings all
stay as they are.

Usage (from sample-data/):
    python seed_month_data.py                      # current month, write to Mongo
    python seed_month_data.py --month 2026-07      # a specific month
    python seed_month_data.py --to-date            # skip days after today
    python seed_month_data.py --no-db              # only export JSON, no Mongo write
    MONGO_URI=... python seed_month_data.py        # override connection

The default month is the current calendar month in Cambodia time (UTC+7).
"""
import argparse
import calendar
import json
import os
import random
from datetime import datetime, timedelta, timezone

# Cambodia is UTC+7 with no daylight saving — a fixed offset is exact.
KH_TZ = timezone(timedelta(hours=7))
random.seed(42)

# Assignment/exam categories and how often each is used per subject.
CATEGORIES = ["Homework", "Quiz", "Exam", "Project"]


# --------------------------------------------------------------------------- #
# Load the roster we build on top of
# --------------------------------------------------------------------------- #
def load_reference(db):
    """Pull the live students / subjects / staff so generated IDs line up."""
    students = list(db["students"].find({"StFl": {"$ne": "I"}}, {"_id": 0}))
    subjects = list(db["subjects"].find({}, {"_id": 0}))
    staff = list(db["staffs"].find({}, {"_id": 0}))
    if not students:
        raise SystemExit("No students found — run seed_sample_data.py first.")
    if not subjects:
        raise SystemExit("No subjects found — run seed_sample_data.py first.")
    return students, subjects, staff


def subjects_for_course(subjects):
    by_course = {}
    for s in subjects:
        by_course.setdefault(s.get("CrID"), []).append(s)
    return by_course


def teacher_lookup(staff):
    """Map SubID -> staff (via SubE), plus a dept fallback."""
    by_sub, by_dept = {}, {}
    for s in staff:
        for e in s.get("SubE", []):
            if e.get("SubID"):
                by_sub[e["SubID"]] = s
        if s.get("Dept"):
            by_dept.setdefault(s["Dept"], s)
    return by_sub, by_dept


# --------------------------------------------------------------------------- #
# Calendar helpers
# --------------------------------------------------------------------------- #
def working_days(year, month, upto=None):
    """All Mon-Fri dates in the month, optionally capped at `upto` (date str)."""
    days = []
    last = calendar.monthrange(year, month)[1]
    for dom in range(1, last + 1):
        d = datetime(year, month, dom)
        if d.weekday() < 5:  # Mon-Fri
            ds = d.strftime("%Y-%m-%d")
            if upto and ds > upto:
                break
            days.append(ds)
    return days


def checkin_ts(day, status):
    """A realistic Cambodia morning check-in for a record, returned as aware UTC.

    Present ~07:45-08:15, Late ~08:20-08:55. Stored in UTC so the UI (which renders
    in Cambodia time) shows the right local clock time."""
    base = datetime.strptime(day, "%Y-%m-%d")
    if status == "L":
        minutes = random.randint(20, 55)  # after an 08:00 start
    else:
        minutes = random.randint(-15, 15)
    local = base.replace(hour=8, tzinfo=KH_TZ) + timedelta(minutes=minutes)
    return local.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #
def gen_attendance(students, subjects, staff, days):
    by_course = subjects_for_course(subjects)
    t_by_sub, t_by_dept = teacher_lookup(staff)
    records = []
    for stu in students:
        crs_subs = by_course.get(stu.get("CurCrID"), [])
        if not crs_subs:
            continue
        # Per-student baseline reliability so attendance isn't uniform.
        present_rate = random.uniform(0.68, 0.97)
        name = f"{stu.get('FNa', '')} {stu.get('LNa', '')}".strip()
        for sub in crs_subs:
            teacher = t_by_sub.get(sub["SubID"]) or t_by_dept.get(stu.get("CurDeptID")) \
                or (staff[0] if staff else None)
            for day in days:
                r = random.random()
                if r < present_rate:
                    status = "P"
                elif r < present_rate + 0.08:
                    status = "L"
                else:
                    status = "A"
                ts = checkin_ts(day, status)
                records.append({
                    "InId": stu.get("InId", "IN001"), "PrID": stu.get("CurPrID", "PR001"),
                    "CrID": stu.get("CurCrID"), "DeptID": stu.get("CurDeptID"),
                    "SemID": stu.get("CurSemID", "SM001"), "SecID": stu.get("CurSecID", "SC001"),
                    "AcYr": stu.get("CurAcYr", "AY2526"),
                    "StuID": stu["StuID"], "StuNa": name,
                    "SubID": sub["SubID"], "SubNa": sub.get("SubNa"),
                    "date": day,
                    "dateAt": datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KH_TZ),
                    "session": "Morning", "status": status,
                    "markedBy": teacher["StaffID"] if teacher else None,
                    # Absent students obviously weren't captured by the camera.
                    "source": ("face" if status != "A" and random.random() < 0.6 else "manual"),
                    "similarity": (round(random.uniform(0.42, 0.72), 4)
                                   if status != "A" and random.random() < 0.6 else None),
                    "CrAt": ts if status != "A" else None,
                })
    return records


def _spread_dates(days, n):
    """Pick n roughly-even dates across the month's working days."""
    if not days or n <= 0:
        return []
    if n == 1:
        return [days[len(days) // 2]]
    step = (len(days) - 1) / (n - 1)
    return [days[round(i * step)] for i in range(n)]


def gen_assignments(students, subjects, staff, days):
    t_by_sub, t_by_dept = teacher_lookup(staff)
    stu_by_course = {}
    for stu in students:
        stu_by_course.setdefault(stu.get("CurCrID"), []).append(stu)

    assignments = []
    idx = 1
    for sub in subjects:
        crs_students = stu_by_course.get(sub.get("CrID"), [])
        if not crs_students:
            continue
        teacher = t_by_sub.get(sub["SubID"]) or t_by_dept.get(sub.get("DeptID")) \
            or (staff[0] if staff else None)
        # A month's worth per subject: 2 homeworks, 1 quiz, 1 exam, 1 project.
        plan = ["Homework", "Homework", "Quiz", "Exam", "Project"]
        due_dates = _spread_dates(days, len(plan))
        for cat, due in zip(plan, due_dates):
            assigned = (datetime.strptime(due, "%Y-%m-%d") - timedelta(days=7))
            due_dt = datetime.strptime(due, "%Y-%m-%d")
            past_due = due < days[-1] if days else False
            is_exam = cat == "Exam"
            students_arr = []
            for st in crs_students:
                # Past-due work is mostly graded; upcoming work is assigned/submitted.
                if past_due:
                    status = random.choices(
                        ["graded", "submitted", "assigned"], weights=[70, 20, 10])[0]
                else:
                    status = random.choices(
                        ["assigned", "submitted"], weights=[60, 40])[0]
                if status == "graded":
                    # Exams skew a bit lower/wider than coursework.
                    lo, hi = (45, 98) if is_exam else (60, 100)
                    marks = random.randint(lo, hi)
                else:
                    marks = None
                students_arr.append({"StuID": st["StuID"], "status": status, "marks": marks})
            assignments.append({
                "CmAssID": f"AS{idx:03d}", "InId": sub.get("InId", "IN001"), "PrID": "PR001",
                "CrID": sub.get("CrID"), "DeptID": sub.get("DeptID"),
                "SemID": "SM001", "SecID": "SC001", "AcYr": "AY2526",
                "Title": f"{sub.get('SubNa')} — {cat}",
                "Catry": cat, "SubID": sub["SubID"], "SubNa": sub.get("SubNa"),
                "StaffID": teacher["StaffID"] if teacher else None,
                "StaffNa": (f"{teacher.get('FoNa','')} {teacher.get('SuNa','')}".strip()
                            if teacher else None),
                "assgndDt": assigned.strftime("%Y-%m-%d"),
                "assgnDueDt": due_dt.strftime("%Y-%m-%d"), "endTme": "23:59",
                "maxMarks": 100,
                "Students": students_arr,
                "StFl": "A", "CrAt": datetime.now(timezone.utc),
            })
            idx += 1
    return assignments


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def export_json(data, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, docs in data.items():
        path = os.path.join(out_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(docs, f, ensure_ascii=False, indent=2, default=str)


def overwrite_mongo(data, uri, db_name):
    from pymongo import MongoClient
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[db_name]
    for name, docs in data.items():
        db[name].delete_many({})           # overwrite this collection
        if docs:
            db[name].insert_many([dict(d) for d in docs])
    return db


def main():
    now_kh = datetime.now(KH_TZ)
    ap = argparse.ArgumentParser(description="Seed a full month of attendance + assignments")
    ap.add_argument("--month", default=now_kh.strftime("%Y-%m"),
                    help="Target month YYYY-MM (default: current month, Cambodia time)")
    ap.add_argument("--to-date", action="store_true",
                    help="Skip working days after today (no future-dated attendance)")
    ap.add_argument("--no-db", action="store_true", help="Only export JSON, no Mongo write")
    ap.add_argument("--uri", default=os.getenv("MONGO_URI",
                    "mongodb://admin:mysecurepassword@localhost:27017"))
    ap.add_argument("--db", default=os.getenv("FACE_DB_NAME", "rean_face_poc"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "exports"))
    args = ap.parse_args()

    year, month = (int(x) for x in args.month.split("-"))
    upto = now_kh.strftime("%Y-%m-%d") if args.to_date else None
    days = working_days(year, month, upto=upto)
    if not days:
        raise SystemExit(f"No working days found for {args.month}"
                         + (" up to today" if args.to_date else ""))

    # Reference roster: read from Mongo unless we're purely exporting offline.
    if args.no_db:
        # Offline export still needs a roster; try Mongo, fall back to seed constants.
        try:
            from pymongo import MongoClient
            db = MongoClient(args.uri, serverSelectionTimeoutMS=3000)[args.db]
            db.command("ping")
            students, subjects, staff = load_reference(db)
        except Exception as e:
            raise SystemExit(f"--no-db still needs the roster from Mongo and it was "
                             f"unreachable ({e}). Start Mongo or drop --no-db.")
    else:
        from pymongo import MongoClient
        client = MongoClient(args.uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        db = client[args.db]
        students, subjects, staff = load_reference(db)

    attendance = gen_attendance(students, subjects, staff, days)
    assignments = gen_assignments(students, subjects, staff, days)
    data = {"attendance": attendance, "assignments": assignments}

    export_json(data, args.out)

    present = sum(1 for r in attendance if r["status"] == "P")
    late = sum(1 for r in attendance if r["status"] == "L")
    absent = sum(1 for r in attendance if r["status"] == "A")
    print(f"Month {args.month}: {len(days)} working days ({days[0]} .. {days[-1]})")
    print(f"  students        {len(students):>6d}")
    print(f"  attendance rows {len(attendance):>6d}  (P {present} / L {late} / A {absent})")
    print(f"  assignments     {len(assignments):>6d}  (incl. Exam category)")
    print(f"JSON exports -> {args.out}")

    if not args.no_db:
        overwrite_mongo(data, args.uri, args.db)
        print(f"Overwrote 'attendance' and 'assignments' in MongoDB '{args.db}' "
              f"(students/subjects/staff/faces untouched).")


if __name__ == "__main__":
    main()
