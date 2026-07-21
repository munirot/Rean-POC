"""Fetch a public face dataset (LFW) so you can benchmark accuracy with no volunteers.

LFW = Labeled Faces in the Wild: ~13k photos of ~5.7k people, already laid out as
one folder per person — exactly what eval/benchmark.py expects. Many people have only
one photo, so this script selects identities that have several images (needed to form
genuine match pairs) and copies a manageable subset into ./dataset_lfw/.

Usage (from face-service/, inside the venv):
    python -m eval.get_lfw                       # ~60 identities with >=4 photos
    python -m eval.get_lfw --max-ids 100 --min-images 5
    python -m eval.get_lfw --tgz /path/to/lfw.tgz   # use an already-downloaded archive

Then:
    python -m eval.benchmark --data ./dataset_lfw --far-target 0.01

Note: LFW is web/celebrity photos in varied conditions — treat the resulting number as a
model sanity check. Your real deployment accuracy depends on your camera and lighting, so
supplement with a few self-captured photos when you can.
"""
import argparse
import os
import shutil
import sys
import tarfile
import urllib.request

# Official + mirror locations for lfw.tgz (~180 MB).
LFW_URLS = [
    "https://ndownloader.figshare.com/files/5976018",       # sklearn's mirror
    "http://vis-www.cs.umass.edu/lfw/lfw.tgz",              # original
]
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".lfw_cache")


def _download(dest):
    last = None
    for url in LFW_URLS:
        try:
            print(f"Downloading LFW from {url} …")
            with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length", 0))
                got = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk); got += len(chunk)
                    if total:
                        pct = got * 100 // total
                        print(f"\r  {got // (1<<20)} / {total // (1<<20)} MB ({pct}%)",
                              end="", flush=True)
            print("\n  done.")
            return True
        except Exception as e:  # try next mirror
            last = e
            print(f"\n  failed: {e}")
    print(f"Could not download LFW automatically ({last}).\n"
          f"Download lfw.tgz manually from http://vis-www.cs.umass.edu/lfw/ and re-run with "
          f"--tgz /path/to/lfw.tgz")
    return False


def main():
    ap = argparse.ArgumentParser(description="Fetch LFW subset for benchmarking")
    ap.add_argument("--out", default="dataset_lfw", help="output dataset dir")
    ap.add_argument("--max-ids", type=int, default=60, help="how many identities to keep")
    ap.add_argument("--min-images", type=int, default=4,
                    help="only keep identities with at least this many photos")
    ap.add_argument("--tgz", help="path to an already-downloaded lfw.tgz")
    ap.add_argument("--keep-archive", action="store_true",
                    help="don't delete the extracted cache afterwards")
    args = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    tgz = args.tgz or os.path.join(CACHE, "lfw.tgz")
    if not os.path.exists(tgz):
        if not _download(tgz):
            sys.exit(1)

    extract_root = os.path.join(CACHE, "lfw")
    if not os.path.isdir(extract_root):
        print("Extracting archive …")
        with tarfile.open(tgz, "r:gz") as t:
            t.extractall(CACHE)
    # archive top folder is "lfw/"
    if not os.path.isdir(extract_root):
        # some mirrors extract to a different top dir; find it
        subs = [d for d in os.listdir(CACHE) if os.path.isdir(os.path.join(CACHE, d))]
        extract_root = os.path.join(CACHE, subs[0]) if subs else extract_root

    # rank identities by photo count, keep those with enough images
    people = []
    for name in os.listdir(extract_root):
        pdir = os.path.join(extract_root, name)
        if not os.path.isdir(pdir):
            continue
        imgs = [f for f in os.listdir(pdir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if len(imgs) >= args.min_images:
            people.append((name, len(imgs), pdir))
    people.sort(key=lambda x: x[1], reverse=True)
    chosen = people[:args.max_ids]
    if not chosen:
        sys.exit(f"No identities with >= {args.min_images} images found — lower --min-images.")

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    os.makedirs(args.out)
    total_imgs = 0
    for name, n, pdir in chosen:
        dst = os.path.join(args.out, name)
        os.makedirs(dst)
        for f in os.listdir(pdir):
            if f.lower().endswith((".jpg", ".jpeg", ".png")):
                shutil.copy2(os.path.join(pdir, f), os.path.join(dst, f))
                total_imgs += 1

    if not args.keep_archive:
        shutil.rmtree(extract_root, ignore_errors=True)

    data_arg = args.out if os.path.isabs(args.out) else f"./{args.out}"
    print(f"\nBuilt {args.out}/  ->  {len(chosen)} identities, {total_imgs} images "
          f"(>= {args.min_images} each).")
    print("Now run:")
    print(f"  python -m eval.benchmark --data {data_arg} --far-target 0.01")


if __name__ == "__main__":
    main()
