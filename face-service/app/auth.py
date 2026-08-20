"""Signed session tokens.

The previous scheme was base64("loginId:type") with no signature and no expiry:
anyone who could guess a login id could mint themselves an admin token with a
single base64 call, because the server had no way to tell its own tokens from
hand-made ones. Tokens are now HMAC-SHA256 signed over a JSON payload, so the
server verifies both that it issued the token and that it hasn't expired.

Format (both parts base64url, padding stripped):

    <payload>.<signature>        payload = {"sub": loginId, "typ": type, "exp": epoch}

The token still carries no authority of its own — `sub` is looked up in `logins`
on every request and the caller's scope is recomputed from the live document
(see main.current_user), so revoking or re-scoping a login takes effect at once.

Signing key: AUTH_SECRET. Without it a random per-process key is used — secure,
but sessions don't survive a restart or verify across uvicorn workers, so
startup logs a warning.
"""
import base64
import hashlib
import hmac
import json
import time

from .config import settings


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes) -> str:
    return _b64e(hmac.new(settings.auth_key, payload, hashlib.sha256).digest())


def issue(login_id: str, user_type: str, now: float = None) -> str:
    """Mint a signed, expiring token for a login."""
    exp = int((now if now is not None else time.time())
              + settings.auth_token_ttl_hours * 3600)
    payload = json.dumps({"sub": login_id, "typ": user_type, "exp": exp},
                         separators=(",", ":"), sort_keys=True).encode()
    return f"{_b64e(payload)}.{_sign(payload)}"


def verify(token: str, now: float = None) -> dict:
    """Return {loginId, type, exp} for a valid token, else None.

    Rejects anything we did not sign (compared in constant time, so the check
    can't be brute-forced a byte at a time) and anything past its expiry.
    """
    if not token or "." not in token:
        return None
    payload_b64, sig = token.rsplit(".", 1)
    try:
        payload = _b64d(payload_b64)
    except Exception:
        return None
    # Signature first: never parse a payload we haven't authenticated.
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    try:
        claims = json.loads(payload)
        login_id, user_type, exp = claims["sub"], claims["typ"], int(claims["exp"])
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        return None
    if (now if now is not None else time.time()) >= exp:
        return None
    return {"loginId": login_id, "type": user_type, "exp": exp}
