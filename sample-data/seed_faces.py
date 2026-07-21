"""Pre-enroll face profiles for a subset of students.

Two sources:
  synthetic  (default) — downloads GAN-generated faces from thispersondoesnotexist.com.
                         These are NOT real people (no privacy/licensing issues), ideal for
                         a demo. One unique face per student.
  dir                  — reads your own real photos from a folder, matched by file name to
                         the student ID (e.g. 2301.jpg, 2302.png).

Either way it runs the image through the SAME InsightFace engine the face-service uses,
computes the 512-d embedding + thumbnail, and stores it in the `face_embeddings` collection
(keyed by StuID). Students you don't enroll here stay empty for you to enroll later — in the
UI or with `--source dir`.

Run it with the face-service's Python so InsightFace + config are available:

    cd sample-data
    ../face-service/.venv/bin/python seed_faces.py --count 12          # 12 synthetic faces
    ../face-service/.venv/bin/python seed_faces.py --source dir --dir ./my_photos
    ../face-service/.venv/bin/python seed_faces.py --students 2301,2305 --overwrite
    ../face-service/.venv/bin/python seed_faces.py --list              # show targets, do nothing

Respects MONGO_URI / MODEL_PACK from face-service/.env.
"""
import argparse
import glob
import os
import sys
import time
import urllib.request

# reuse the face-service engine + store (same model pack, same Mongo)
FACE_SERVICE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "face-service")
sys.path.insert(0, FACE_SERVICE)

UA = "Mozilla/5.0 (Rean seed script)"
# magic-byte signatures for common image formats
_IMG_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n", b"GIF8", b"BM")


def _looks_like_image(data: bytes) -> bool:
    if not data or len(data) < 12:
        return False
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return any(data.startswith(sig) for sig in _IMG_MAGIC)


def _http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_face(source, idx, timeout=30):
    """Return image bytes for one student. `idx` gives per-student variety.
    - pravatar : real portrait photos (i.pravatar.cc), reliable + face-detectable
    - tpdne    : GAN faces (thispersondoesnotexist.com) — often blocks bots now
    """
    if source == "pravatar":
        url = f"https://i.pravatar.cc/640?img={(idx % 70) + 1}"
    elif source == "tpdne":
        url = "https://thispersondoesnotexist.com/"
    else:
        raise ValueError(f"unknown source {source}")
    data = _http_get(url, timeout)
    if not _looks_like_image(data):
        head = data[:60].decode("latin-1", "replace").replace("\n", " ")
        raise ValueError(f"{source} returned non-image data (blocked?): {head!r}")
    return data


def find_local(dir_path, sid):
    for ext in ("jpg", "jpeg", "png", "webp", "bmp"):
        hits = glob.glob(os.path.join(dir_path, f"{sid}.{ext}"))
        if hits:
            return hits[0]
    return None


def main():
    ap = argparse.ArgumentParser(description="Pre-enroll face profiles for sample students")
    ap.add_argument("--count", type=int, default=12, help="how many students to enroll (auto sources)")
    ap.add_argument("--source", choices=["pravatar", "tpdne", "dir"], default="pravatar",
                    help="pravatar=real portraits (reliable), tpdne=GAN faces (flaky), dir=local files")
    ap.add_argument("--dir", help="folder of real photos named <StuID>.<ext> (dir mode)")
    ap.add_argument("--students", help="comma-separated StuIDs to enroll (overrides --count)")
    ap.add_argument("--overwrite", action="store_true", help="re-enroll even if already enrolled")
    ap.add_argument("--save-dir", default="seed_faces", help="where to keep the images used")
    ap.add_argument("--sleep", type=float, default=1.0, help="delay between synthetic downloads")
    ap.add_argument("--list", action="store_true", help="print target students and exit")
    args = ap.parse_args()

    from app.db import get_store
    store = get_store()
    store.ping()
    roster = store.list_students()
    by_sid = {s["sid"]: s for s in roster}

    # choose targets
    if args.students:
        want = [x.strip() for x in args.students.split(",") if x.strip()]
        targets = [by_sid[s] for s in want if s in by_sid]
    else:
        pool = [s for s in roster if args.overwrite or not s["enrolled"]]
        targets = pool[: args.count]

    print(f"{len(targets)} student(s) targeted for enrollment "
          f"(source={args.source}, overwrite={args.overwrite}):")
    for s in targets:
        print(f"  {s['sid']:>6}  {s['name']}  {'[enrolled]' if s['enrolled'] else ''}")
    if args.list:
        return
    if not targets:
        print("Nothing to do. (All targeted students already enrolled? use --overwrite)")
        return

    from app import engine as eng
    engine = eng.get_engine()
    os.makedirs(args.save_dir, exist_ok=True)

    done, skipped = 0, []
    for idx, s in enumerate(targets):
        sid = s["sid"]
        try:
            if args.source == "dir":
                if not args.dir:
                    sys.exit("--source dir requires --dir PATH")
                path = find_local(args.dir, sid)
                if not path:
                    skipped.append((sid, "no file named <sid>.<ext>")); continue
                data = open(path, "rb").read()
            else:
                data = fetch_face(args.source, idx)
                time.sleep(args.sleep)

            img = engine.decode(data)
            faces = engine.detect(img)
            if not faces:
                skipped.append((sid, "no face detected")); continue
            face = faces[0]
            store.set_embedding(sid, engine.embedding(face), engine.quality(face),
                                engine.thumbnail(img, face), engine.model_pack)
            with open(os.path.join(args.save_dir, f"{sid}.jpg"), "wb") as f:
                f.write(data)
            done += 1
            print(f"  ✓ enrolled {sid} {s['name']} (quality {engine.quality(face)})")
        except Exception as e:
            skipped.append((sid, str(e)))

    print(f"\nEnrolled {done} student(s). Images saved to {args.save_dir}/")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for sid, why in skipped:
            print(f"  {sid}: {why}")


if __name__ == "__main__":
    main()
