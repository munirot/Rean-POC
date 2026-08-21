"""Chat isolation & fairness: bounded LLM concurrency, per-login rate limiting,
and the script-aware token estimate (no network, no Mongo).

    cd face-service
    python -m pytest tests/test_chat_isolation.py -v
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import chat                    # noqa: E402
from app.config import settings         # noqa: E402


# --------------------------------------------------------------------------- #
# Rate limiting (token bucket, per login)
# --------------------------------------------------------------------------- #
def test_rate_limit_allows_up_to_cap_then_blocks():
    chat._rate_state.clear()
    cap = settings.chat_rate_per_min
    assert cap > 0
    t = 1000.0
    assert all(chat.rate_limit_ok("L1", now=t) for _ in range(cap))  # cap allowed
    assert chat.rate_limit_ok("L1", now=t) is False                  # next blocked
    assert chat.rate_limit_ok("L1", now=t + 60) is True              # refills in a minute


def test_rate_limit_is_per_login():
    chat._rate_state.clear()
    t = 0.0
    for _ in range(settings.chat_rate_per_min):
        chat.rate_limit_ok("A", now=t)
    assert chat.rate_limit_ok("A", now=t) is False   # A exhausted
    assert chat.rate_limit_ok("B", now=t) is True    # B independent


def test_rate_limit_disabled_when_cap_zero():
    chat._rate_state.clear()
    old = settings.chat_rate_per_min
    settings.chat_rate_per_min = 0
    try:
        assert all(chat.rate_limit_ok("X", now=0.0) for _ in range(100))
    finally:
        settings.chat_rate_per_min = old


# --------------------------------------------------------------------------- #
# Bounded LLM concurrency — fail fast instead of pinning a worker
# --------------------------------------------------------------------------- #
def test_chat_completion_fails_fast_when_slots_exhausted():
    old_sem, old_to = chat._llm_slots, settings.chat_acquire_timeout
    chat._llm_slots = threading.BoundedSemaphore(1)
    settings.chat_acquire_timeout = 0.02
    try:
        assert chat._llm_slots.acquire()             # take the only slot
        raised = False
        try:
            chat._chat_completion([{"role": "user", "content": "hi"}])  # no slot free
        except chat.ChatBusy:
            raised = True
        assert raised, "expected ChatBusy when no LLM slot is available"
    finally:
        chat._llm_slots = old_sem
        settings.chat_acquire_timeout = old_to


def test_answer_returns_busy_on_chatbusy():
    orig = chat._chat_completion
    chat._chat_completion = lambda *a, **k: (_ for _ in ()).throw(chat.ChatBusy())
    try:
        # a general question so the deterministic pre-router doesn't short-circuit
        r = chat.answer(None, "tell me a joke",
                        scope={"InId": "IN1", "type": "staff", "loginId": "L", "courses": None})
        assert r.get("error") == "busy"
        assert "moment" in r["answer"].lower()
    finally:
        chat._chat_completion = orig


# --------------------------------------------------------------------------- #
# Script-aware token estimate (Khmer must not be under-counted)
# --------------------------------------------------------------------------- #
def test_est_tokens_counts_khmer_heavier_than_ascii():
    ascii_s = "a" * 40           # ~10 tokens at 4 chars/token
    khmer_s = "ក" * 40           # 40 non-ASCII codepoints -> ~40 tokens
    assert chat._est_tokens(ascii_s) == 10
    assert chat._est_tokens(khmer_s) > chat._est_tokens(ascii_s)


def test_est_tokens_never_zero():
    assert chat._est_tokens("") == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
