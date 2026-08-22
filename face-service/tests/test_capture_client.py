"""Classroom capture client: retry policy and frame sourcing (no network).

The client runs unattended in a room for a whole lesson, so the interesting logic
is what it does when things go wrong — whether it recovers, gives up, or hammers
the server.

    cd face-service
    python -m pytest tests/test_capture_client.py -v
"""
import os
import sys

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "tools")
sys.path.insert(0, _TOOLS)

from class_camera import (backoff_delay, should_stop,      # noqa: E402
                          DirectorySource, post_frame)


# --------------------------------------------------------------------------- #
# backoff
# --------------------------------------------------------------------------- #
def test_backoff_grows_exponentially():
    assert [backoff_delay(a, base=2, cap=1e9) for a in (1, 2, 3, 4)] == [2, 4, 8, 16]


def test_backoff_is_capped():
    # Without a cap a long outage would push the retry interval past the lesson.
    assert backoff_delay(20, base=2, cap=60) == 60
    assert all(backoff_delay(a, cap=60) <= 60 for a in range(1, 50))


def test_backoff_never_negative():
    assert backoff_delay(0) == 0.0
    assert backoff_delay(-3) == 0.0


# --------------------------------------------------------------------------- #
# stop vs retry — the difference between recovering and pointless hammering
# --------------------------------------------------------------------------- #
def test_auth_and_lifecycle_errors_stop():
    for code in (401, 403, 404, 409):
        assert should_stop(code) is True, code


def test_transient_errors_are_retried():
    # 0 is "could not reach the server", which is exactly the recoverable case.
    for code in (0, 408, 429, 500, 502, 503, 504):
        assert should_stop(code) is False, code


def test_success_is_not_a_stop_condition():
    assert should_stop(200) is False


# --------------------------------------------------------------------------- #
# directory source — lets a room be rehearsed before a camera is mounted
# --------------------------------------------------------------------------- #
def _mkframes(tmp, n=3):
    os.makedirs(tmp, exist_ok=True)
    for i in range(n):
        with open(os.path.join(tmp, f"{i:03d}.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff\xdb" + bytes([i]))       # tiny fake JPEG
    return tmp


def test_directory_source_reads_in_order_then_stops(tmp_path=None):
    import tempfile
    d = _mkframes(os.path.join(tempfile.mkdtemp(), "frames"))
    src = DirectorySource(d)
    got = [src.read_jpeg(), src.read_jpeg(), src.read_jpeg()]
    assert all(g is not None for g in got)
    assert got[0] != got[1]                      # distinct files, in order
    assert src.read_jpeg() is None               # exhausted, does not wrap


def test_directory_source_loops_when_asked():
    import tempfile
    d = _mkframes(os.path.join(tempfile.mkdtemp(), "frames"), n=2)
    src = DirectorySource(d, loop=True)
    first = src.read_jpeg()
    src.read_jpeg()
    assert src.read_jpeg() == first              # wrapped back to the start


def test_empty_directory_is_rejected_loudly():
    import tempfile
    d = tempfile.mkdtemp()
    try:
        DirectorySource(d)
        assert False, "expected SystemExit for an empty folder"
    except SystemExit:
        pass


# --------------------------------------------------------------------------- #
# request shape — the device must authenticate as a DEVICE, not a person
# --------------------------------------------------------------------------- #
def test_post_frame_sends_multipart_with_bearer_token(monkeypatch=None):
    import class_camera
    captured = {}

    class _Resp:
        status = 200
        def read(self): return b'{"ok":true}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["body"] = req.data
        return _Resp()

    orig = class_camera.urllib.request.urlopen
    class_camera.urllib.request.urlopen = fake_urlopen
    try:
        status, body = post_frame("http://x:8000", "SESS1", "DEVTOK", b"\xff\xd8jpeg")
    finally:
        class_camera.urllib.request.urlopen = orig

    assert status == 200 and "ok" in body
    assert captured["url"].endswith("/api/class-sessions/SESS1/frame")
    assert captured["headers"]["authorization"] == "Bearer DEVTOK"
    assert captured["headers"]["content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"' in captured["body"] and b"\xff\xd8jpeg" in captured["body"]


def test_post_frame_reports_unreachable_as_zero():
    import class_camera
    def boom(req, timeout=None):
        raise class_camera.urllib.error.URLError("no route to host")
    orig = class_camera.urllib.request.urlopen
    class_camera.urllib.request.urlopen = boom
    try:
        status, body = post_frame("http://x:8000", "S", "T", b"j")
    finally:
        class_camera.urllib.request.urlopen = orig
    assert status == 0 and "no route" in body
    assert should_stop(status) is False          # ...so it will keep trying


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
