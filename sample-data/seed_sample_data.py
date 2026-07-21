"""Generate a small, internally-consistent sample dataset that mirrors the real
mycamu/Rean schema, insert it into MongoDB (the POC `rean_face_poc` DB, alongside
`face_students`), and export one JSON file per collection.

Field names follow the real models (InId, PrID, CrID, DeptID, SemID, SecID, AcYr,
CmStudID/StuID, StaffID, LoginID, Type, StFl, CrAt ...). See README for the mapping.

Usage (from sample-data/):
    python seed_sample_data.py                 # generate + insert into Mongo + export JSON
    python seed_sample_data.py --no-db         # only write JSON exports (no Mongo)
    MONGO_URI=... python seed_sample_data.py    # override connection

Demo login password for every generated login: "Rean@123"
(pwd is stored as a sha256 hash placeholder — this data is for demo/UI only, not real auth).
"""
import argparse
import hashlib
import json
import os
import random
from datetime import datetime, timedelta, timezone

random.seed(42)
NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)
DEMO_PWD = "Rean@123"


def h(pw):
    return "sha256$" + hashlib.sha256(pw.encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
INSTITUTE = {"InId": "IN001", "InNa": "Royal University of Law and Economics",
             "InCd": "RULE", "City": "Phnom Penh", "Country": "Cambodia", "StFl": "A"}

ACADEMIC_YEAR = {"AcYr": "AY2526", "AcYrNm": "2025/26",
                 "startDt": "2025-10-01", "endDt": "2026-07-31", "StFl": "A"}

PROGRAM = {"PrID": "PR001", "PrNa": "Bachelor Degree", "PrCd": "BACH",
           "InId": "IN001", "StFl": "A"}

DEPARTMENTS = [
    {"DeptID": "DP001", "DeptNa": "Faculty of Public Administration", "DeptCd": "FPA"},
    {"DeptID": "DP002", "DeptNa": "Faculty of Law", "DeptCd": "FLAW"},
    {"DeptID": "DP003", "DeptNa": "Faculty of Economics", "DeptCd": "FECO"},
    {"DeptID": "DP004", "DeptNa": "Faculty of International Relations", "DeptCd": "FIR"},
]
for d in DEPARTMENTS:
    d.update(InId="IN001", PrID="PR001", StFl="A")

COURSES = [
    {"CrID": "CR001", "CrNa": "Bachelor of International Relations", "CrCd": "BIR", "DeptID": "DP004"},
    {"CrID": "CR002", "CrNa": "Bachelor of Public Administration", "CrCd": "BPA", "DeptID": "DP001"},
    {"CrID": "CR003", "CrNa": "Bachelor of Law", "CrCd": "LLB", "DeptID": "DP002"},
    {"CrID": "CR004", "CrNa": "Bachelor of Economics", "CrCd": "BEC", "DeptID": "DP003"},
]
for c in COURSES:
    c.update(InId="IN001", PrID="PR001", StFl="A")

SEMESTER = {"SemID": "SM001", "SemNa": "Semester 1", "PrID": "PR001", "InId": "IN001", "StFl": "A"}
SECTION = {"SecID": "SC001", "SecNa": "Section A", "InId": "IN001", "StFl": "A"}

SUBJECTS = [
    {"SubID": "SB001", "SubNa": "International Law", "SubCd": "INT101", "CrID": "CR001", "DeptID": "DP004"},
    {"SubID": "SB002", "SubNa": "Diplomacy & Negotiation", "SubCd": "INT102", "CrID": "CR001", "DeptID": "DP004"},
    {"SubID": "SB003", "SubNa": "Public Policy", "SubCd": "PUB101", "CrID": "CR002", "DeptID": "DP001"},
    {"SubID": "SB004", "SubNa": "Governance & Ethics", "SubCd": "PUB102", "CrID": "CR002", "DeptID": "DP001"},
    {"SubID": "SB005", "SubNa": "Constitutional Law", "SubCd": "LAW101", "CrID": "CR003", "DeptID": "DP002"},
    {"SubID": "SB006", "SubNa": "Criminal Law", "SubCd": "LAW102", "CrID": "CR003", "DeptID": "DP002"},
    {"SubID": "SB007", "SubNa": "Microeconomics", "SubCd": "ECO101", "CrID": "CR004", "DeptID": "DP003"},
    {"SubID": "SB008", "SubNa": "Statistics", "SubCd": "ECO102", "CrID": "CR004", "DeptID": "DP003"},
]
for s in SUBJECTS:
    s.update(InId="IN001", credits=3, StFl="A")

MENU_STUDENT = ["MYINST", "ATTND", "EXAMSCH", "GRADE", "REPORTS", "PROGREP", "ASSESS",
                "HOLIDAY", "TIMETBL", "LEAVE", "SERVICES", "BILLING", "COUNSEL", "ENROLL",
                "TRANSCRIPT", "ACTIVITY", "CLEARANCE", "ANNOUNCE", "FEEDBACK", "PROJECT",
                "SCHOLAR", "EPORT", "FINALRES", "IDCARD", "DOCREC"]
MENU_STAFF = ["DASH", "REPORTS", "STUDENTS", "ATTND", "ASSIGN"]
MENU_ADMIN = MENU_STAFF + MENU_STUDENT

ROLES = [
    {"code": "admin", "name": "Administrator", "menus": MENU_ADMIN, "StFl": "A"},
    {"code": "staff", "name": "Teacher / Staff", "menus": MENU_STAFF, "StFl": "A"},
    {"code": "student", "name": "Student", "menus": MENU_STUDENT, "StFl": "A"},
    {"code": "parent", "name": "Parent", "menus": ["MYINST", "ATTND", "GRADE", "FINALRES"], "StFl": "A"},
]

# name pools (Khmer given/family names; family name first is common but we keep First/Last fields)
FAMILY = ["Nern", "Mao", "Kakada", "Ravy", "Piseth", "Chhoun", "Perm", "Chomnan", "Rayuth",
          "Hong", "Champay", "Nimith", "Vuthy", "Sok", "Chan", "Meas", "Ngo", "Voun", "Pich",
          "Kim", "Sao", "Chea", "Ly", "Heng", "Ouk", "Sam", "Tep", "Yem", "Rith", "Bora"]
GIVEN_M = ["Channa", "Kimlang", "Hean", "Surn", "Sokun", "Vann", "Seav", "Hak", "Vutha",
           "Pheab", "Chakra", "Dara", "Sophal", "Vichea", "Rithy", "Bunly", "Sovann", "Rithi"]
GIVEN_F = ["Pisey", "Noch", "Sreyleak", "Chenda", "Sreymom", "Bopha", "Kanha", "Devi",
           "Sophea", "Maly", "Nary", "Chantha", "Leakhena", "Thida"]


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #
def gen_staff():
    staff, logins = [], []
    designations = ["Lecturer", "Assistant Professor", "Senior Lecturer"]
    # one teacher roughly per department, plus extras -> 6
    picks = [("DP004", "SB001"), ("DP004", "SB002"), ("DP001", "SB003"),
             ("DP002", "SB005"), ("DP003", "SB007"), ("DP003", "SB008")]
    for i, (dept, sub) in enumerate(picks, 1):
        sid = f"ST{i:03d}"
        fam = FAMILY[i + 12]; giv = GIVEN_M[i + 10]
        gender = "M"
        email = f"{giv.lower()}.{fam.lower()}@rule.edu.kh"
        staff.append({
            "StaffID": sid, "InId": "IN001", "LoginID": f"LG-S{i:03d}",
            "Intls": f"{fam[0]}{giv[0]}", "SuNa": fam, "FoNa": giv, "Titl": "Mr.",
            "Email": email, "Phone": f"0{random.randint(10,99)}{random.randint(100000,999999)}",
            "Gend": gender, "Dept": dept, "Desi": random.choice(designations),
            "EmpCat": "Teaching", "SubE": [{"SubID": sub}], "DOJ": "2021-09-01",
            "StFl": "A", "CrAt": NOW,
        })
        logins.append(_login(f"LG-S{i:03d}", f"{giv} {fam}", email, "staff", staff_id=sid))
    return staff, logins


def _login(login_id, name, email, ltype, stu_id=None, staff_id=None):
    doc = {"LoginID": login_id, "InId": "IN001", "Name": name, "Email": email,
           "Type": ltype, "CamuPin": f"PIN{random.randint(100000,999999)}",
           "pwd": h(DEMO_PWD), "isvrfd": "Y", "StFl": "A", "CrAt": NOW}
    if stu_id:
        doc["Student"] = [{"ProfileId": stu_id}]
    if staff_id:
        doc["StaffID"] = staff_id
    return doc


def gen_students(n=30):
    students, logins, access = [], [], []
    course_cycle = [c for c in COURSES for _ in range(n // len(COURSES) + 1)]
    for i in range(n):
        stu_id = str(2301 + i)
        crs = course_cycle[i]
        dept = next(d for d in DEPARTMENTS if d["DeptID"] == crs["DeptID"])
        female = random.random() < 0.5
        fam = random.choice(FAMILY)
        giv = random.choice(GIVEN_F if female else GIVEN_M)
        dob_year = random.randint(2003, 2006)
        email = f"{giv.lower()}.{fam.lower()}{i}@student.rule.edu.kh"
        students.append({
            "CmStudID": stu_id, "StuID": stu_id, "RollNo": f"REG{i+1:04d}",
            "InId": "IN001", "InName": INSTITUTE["InNa"],
            "CurPrID": "PR001", "CurPrNm": PROGRAM["PrNa"],
            "CurCrID": crs["CrID"], "CurCrNm": crs["CrNa"], "CurCrCd": crs["CrCd"],
            "CurDeptID": dept["DeptID"], "CurDeptNm": dept["DeptNa"],
            "CurSemID": "SM001", "CurSemNm": "Semester 1",
            "CurSecID": "SC001", "CurSecNm": "Section A",
            "CurAcYr": "AY2526", "CurAcYrNm": "2025/26",
            "Title": "Ms." if female else "Mr.", "FNa": giv, "LNa": fam, "Sex": "F" if female else "M",
            "DOB": f"{dob_year}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "Email": email, "PhotoImgID": None, "stuSts": "A", "StFl": "A", "CrAt": NOW,
        })
        logins.append(_login(f"LG-{stu_id}", f"{giv} {fam}", email, "student", stu_id=stu_id))
        access.append({"InId": "IN001", "LoginID": f"LG-{stu_id}", "StuID": stu_id,
                       "stuSts": "A", "menus": [{"code": c} for c in MENU_STUDENT],
                       "StFl": "A", "CrAt": NOW})
    return students, logins, access


def working_days(end, count):
    days, d = [], end
    while len(days) < count:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return sorted(days)


def gen_attendance(students, staff):
    days = working_days(datetime(2026, 7, 10), 10)  # last 2 weeks of school
    sub_by_course = {}
    for s in SUBJECTS:
        sub_by_course.setdefault(s["CrID"], s)  # first subject per course
    staff_by_dept = {s["Dept"]: s for s in staff}
    records = []
    for stu in students:
        sub = sub_by_course[stu["CurCrID"]]
        teacher = staff_by_dept.get(stu["CurDeptID"], staff[0])
        for day in days:
            r = random.random()
            status = "P" if r < 0.82 else ("L" if r < 0.9 else "A")
            records.append({
                "InId": "IN001", "PrID": "PR001", "CrID": stu["CurCrID"],
                "DeptID": stu["CurDeptID"], "SemID": "SM001", "SecID": "SC001", "AcYr": "AY2526",
                "StuID": stu["StuID"], "StuNa": f"{stu['FNa']} {stu['LNa']}",
                "SubID": sub["SubID"], "SubNa": sub["SubNa"],
                "date": day,  # readable "YYYY-MM-DD"
                "dateAt": datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc),  # BSON Date for range queries
                "session": "Morning", "status": status,
                "markedBy": teacher["StaffID"],
                "source": "face" if status == "P" and random.random() < 0.5 else "manual",
                "CrAt": NOW,
            })
    return records, days


def gen_assignments(students, staff):
    cats = ["Homework", "Quiz", "Project", "Seminar"]
    staff_by_sub = {}
    for s in staff:
        for e in s.get("SubE", []):
            staff_by_sub[e["SubID"]] = s
    assignments = []
    stu_by_course = {}
    for stu in students:
        stu_by_course.setdefault(stu["CurCrID"], []).append(stu)
    idx = 1
    for sub in SUBJECTS:
        # one or two assignments for subjects that have students
        crs_students = stu_by_course.get(sub["CrID"], [])
        if not crs_students:
            continue
        teacher = staff_by_sub.get(sub["SubID"], staff[0])
        for k in range(random.randint(1, 2)):
            assigned = datetime(2026, 6, 20) + timedelta(days=idx)
            due = assigned + timedelta(days=10)
            assignments.append({
                "CmAssID": f"AS{idx:03d}", "InId": "IN001", "PrID": "PR001",
                "CrID": sub["CrID"], "DeptID": sub["DeptID"], "SemID": "SM001",
                "SecID": "SC001", "AcYr": "AY2526",
                "Title": f"{sub['SubNa']} — {cats[k % len(cats)]} {k+1}",
                "Catry": cats[k % len(cats)], "SubID": sub["SubID"], "SubNa": sub["SubNa"],
                "StaffID": teacher["StaffID"], "StaffNa": f"{teacher['FoNa']} {teacher['SuNa']}",
                "assgndDt": assigned.strftime("%Y-%m-%d"),
                "assgnDueDt": due.strftime("%Y-%m-%d"), "endTme": "23:59",
                "Students": [
                    {"StuID": st["StuID"],
                     "status": random.choice(["assigned", "submitted", "graded"]),
                     "marks": random.choice([None, None, random.randint(60, 100)])}
                    for st in crs_students
                ],
                "StFl": "A", "CrAt": NOW,
            })
            idx += 1
    return assignments


# --------------------------------------------------------------------------- #
# Assemble
# --------------------------------------------------------------------------- #
def build():
    staff, staff_logins = gen_staff()
    students, stu_logins, access = gen_students(30)
    attendance, days = gen_attendance(students, staff)
    assignments = gen_assignments(students, staff)
    admin_login = _login("LG-ADMIN", "System Administrator", "admin@rule.edu.kh", "admin")

    return {
        "institutes": [INSTITUTE],
        "academic_years": [ACADEMIC_YEAR],
        "programs": [PROGRAM],
        "departments": DEPARTMENTS,
        "courses": COURSES,
        "semesters": [SEMESTER],
        "sections": [SECTION],
        "subjects": SUBJECTS,
        "roles": ROLES,
        "staffs": staff,
        "students": students,
        "logins": [admin_login] + staff_logins + stu_logins,
        "access_control": access,
        "attendance": attendance,
        "assignments": assignments,
    }, days


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def export_json(data, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, docs in data.items():
        with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(docs, f, ensure_ascii=False, indent=2, default=str)


def insert_mongo(data, uri, db_name):
    from pymongo import MongoClient
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    db = client[db_name]
    for name, docs in data.items():
        db[name].delete_many({})           # only clears the sample collections
        if docs:
            db[name].insert_many([dict(d) for d in docs])
    return db_name


def main():
    ap = argparse.ArgumentParser(description="Seed sample mycamu-like data")
    ap.add_argument("--no-db", action="store_true", help="only write JSON exports")
    ap.add_argument("--uri", default=os.getenv("MONGO_URI", "mongodb://admin:mysecurepassword@localhost:27017"))
    ap.add_argument("--db", default=os.getenv("FACE_DB_NAME", "rean_face_poc"))
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "exports"))
    args = ap.parse_args()

    data, days = build()
    export_json(data, args.out)

    print("Generated sample dataset (small demo):")
    for name, docs in data.items():
        print(f"  {name:16s} {len(docs):>5d}")
    print(f"  attendance days: {days[0]} .. {days[-1]}")
    print(f"JSON exports -> {args.out}")

    if not args.no_db:
        try:
            db = insert_mongo(data, args.uri, args.db)
            print(f"Inserted into MongoDB '{db}' at {args.uri} "
                  f"(existing sample collections were replaced; face_students untouched).")
        except Exception as e:
            print(f"\nMongo insert skipped/failed: {e}")
            print("Data was still exported to JSON. Set MONGO_URI (with credentials) and retry,"
                  " or run with --no-db.")


if __name__ == "__main__":
    main()
