#!/usr/bin/env python3
"""Classroom capture client — posts frames to a Rean class session.

Phase 3 of docs/class-camera-attendance-plan.md. This runs on the device in the
room (a Pi with a camera, or anything that can reach an RTSP stream), NOT on the
server. It is deliberately standalone: stdlib plus OpenCV only, so a classroom
device never needs insightface, torch, or the face-service package installed.

What it does, and nothing more:
    grab a frame every --interval seconds  ->  POST it to an OPEN session

It cannot open or close a sitting, read a roster, or mark anyone — a device token
carries a room, not a person (see app/auth.py issue_device). A teacher opens the
sitting in the UI and closes it there; this just feeds it.

    # a USB/CSI camera
    python3 tools/class_camera.py --server http://rean.local:8000 \
        --session 6a89... --token "$REAN_DEVICE_TOKEN" --source 0

    # an IP camera
    ... --source "rtsp://user:pass@10.0.0.9/stream1"

    # no hardware yet: replay a folder of stills, or send one frame and stop
    ... --source ./frames --loop
    ... --source ./frames --once

The token is read from --token or, preferably, the REAN_DEVICE_TOKEN environment
variable so it never lands in shell history or a process list.

Frames are sent at FULL capture resolution on purpose. Recognition quality tracks
original face pixels, and a back-row face is only 20-60px to begin with — routing
class frames through a downscale is the fastest way to make this not work.
"""
import argparse
import io
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

# --------------------------------------------------------------------------- #
# Retry policy (pure — unit-tested in face-service/tests/test_capture_client.py)
# --------------------------------------------------------------------------- #
def backoff_delay(attempt, base=2.0, cap=60.0):
    """Seconds to wait before retry `attempt` (1-based), exponential and capped.

    A classroom device that loses the server must not hammer it every 4 seconds
    for the rest of the lesson, nor give up — it backs off to `cap` and keeps
    trying, so it recovers on its own when the network returns."""
    if attempt < 1:
        return 0.0
    return float(min(cap, base * (2 ** (attempt - 1))))


def should_stop(status):
    """Whether an HTTP status means 'stop', not 'retry'.

    401/403 — the token is wrong or revoked; retrying cannot fix it.
    404/409 — the sitting is gone or already closed; the lesson is over.
    Anything else (5xx, timeouts) is treated as transient and retried."""
    return status in (401, 403, 404, 409)


# --------------------------------------------------------------------------- #
# Frame sources
# --------------------------------------------------------------------------- #
class DirectorySource:
    """Replay stills from a folder — lets the whole path be exercised, and a room
    be rehearsed, before any camera is mounted."""

    def __init__(self, path, loop=False):
        exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        self.paths = sorted(os.path.join(path, f) for f in os.listdir(path)
                            if f.lower().endswith(exts))
        if not self.paths:
            raise SystemExit(f"No images in {path}")
        self.loop, self.i = loop, 0

    def read_jpeg(self, quality=90):
        if self.i >= len(self.paths):
            if not self.loop:
                return None
            self.i = 0
        p = self.paths[self.i]
        self.i += 1
        with open(p, "rb") as f:
            return f.read()

    def close(self):
        pass


class CameraSource:
    """A live camera: device index for USB/CSI, or a URL for RTSP/HTTP."""

    def __init__(self, source, quality=90):
        try:
            import cv2
        except ImportError:
            raise SystemExit("OpenCV is required for live capture:  pip install opencv-python")
        self.cv2 = cv2
        self.spec = int(source) if str(source).isdigit() else source
        self.quality = quality
        self.cap = None
        self._open()

    def _open(self):
        if self.cap is not None:
            self.cap.release()
        self.cap = self.cv2.VideoCapture(self.spec)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open camera source {self.spec!r}")

    def read_jpeg(self, quality=None):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            # A dropped RTSP connection reads as a stream of failures; reopening
            # is what makes an all-day sitting survive a network blip.
            self._open()
            ok, frame = self.cap.read()
            if not ok or frame is None:
                return None
        ok, buf = self.cv2.imencode(
            ".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, quality or self.quality])
        return buf.tobytes() if ok else None

    def close(self):
        if self.cap is not None:
            self.cap.release()


def make_source(spec, loop=False, quality=90):
    if os.path.isdir(spec):
        return DirectorySource(spec, loop=loop)
    return CameraSource(spec, quality=quality)


# --------------------------------------------------------------------------- #
# Posting
# --------------------------------------------------------------------------- #
def post_frame(server, session_id, token, jpeg, timeout=30):
    """POST one frame as multipart/form-data. Returns (status, body_text)."""
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="frame.jpg"\r\n',
        b"Content-Type: image/jpeg\r\n\r\n",
        jpeg, b"\r\n", f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        f"{server.rstrip('/')}/api/class-sessions/{session_id}/frame",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)          # 0 = could not reach the server at all


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #
def run(args):
    token = args.token or os.getenv("REAN_DEVICE_TOKEN", "")
    if not token:
        raise SystemExit("No device token. Pass --token or set REAN_DEVICE_TOKEN.")

    src = make_source(args.source, loop=args.loop, quality=args.quality)
    log(f"source={args.source!r} interval={args.interval}s -> {args.server} "
        f"session={args.session}")

    sent = failed = 0
    attempt = 0
    try:
        while True:
            started = time.time()
            jpeg = src.read_jpeg(args.quality)
            if jpeg is None:
                log("source exhausted" if isinstance(src, DirectorySource)
                    else "camera returned no frame")
                if isinstance(src, DirectorySource) and not args.loop:
                    break
                time.sleep(args.interval)
                continue

            if args.dry_run:
                log(f"dry-run: would post {len(jpeg)/1024:.0f}KB")
                sent += 1
            else:
                status, body = post_frame(args.server, args.session, token, jpeg,
                                          timeout=args.timeout)
                if status == 200:
                    sent += 1
                    attempt = 0
                    if args.verbose or sent == 1 or sent % args.report_every == 0:
                        log(f"frame {sent} ok ({len(jpeg)/1024:.0f}KB) {body.strip()[:120]}")
                elif should_stop(status):
                    log(f"stopping: HTTP {status} {body.strip()[:200]}")
                    break
                else:
                    failed += 1
                    attempt += 1
                    delay = backoff_delay(attempt, cap=args.backoff_cap)
                    log(f"post failed (HTTP {status or 'unreachable'}: "
                        f"{body.strip()[:120]}) — retrying in {delay:.0f}s")
                    time.sleep(delay)
                    continue

            if args.once:
                break
            time.sleep(max(0.0, args.interval - (time.time() - started)))
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        src.close()
        log(f"done: {sent} frame(s) sent, {failed} failure(s)")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Post classroom frames to an open Rean class session.")
    ap.add_argument("--server", default=os.getenv("REAN_SERVER", "http://localhost:8000"))
    ap.add_argument("--session", required=True, help="class session id (teacher opens it in the UI)")
    ap.add_argument("--token", help="device token; prefer REAN_DEVICE_TOKEN in the environment")
    ap.add_argument("--source", default="0",
                    help="camera index, RTSP/HTTP URL, or a directory of stills")
    ap.add_argument("--interval", type=float, default=4.0, help="seconds between frames")
    ap.add_argument("--quality", type=int, default=90, help="JPEG quality (default 90)")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--backoff-cap", type=float, default=60.0)
    ap.add_argument("--report-every", type=int, default=10)
    ap.add_argument("--loop", action="store_true", help="directory source: replay forever")
    ap.add_argument("--once", action="store_true", help="send a single frame and exit")
    ap.add_argument("--dry-run", action="store_true", help="capture but post nothing")
    ap.add_argument("--verbose", action="store_true")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
