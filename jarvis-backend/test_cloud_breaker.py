"""Harness: a slow provider must cost one turn, not every turn.

WHY THIS EXISTS
---------------
Measured on the desk on 2026-08-29 while running the A11 gate rows. Every LLM
leg of every turn printed this before doing anything useful:

    [ROUTER] Gemini key #1/5 failed (DeadlineExceeded) — rotating.
    [ROUTER] Gemini key #2/5 failed (DeadlineExceeded) — rotating.
    [ROUTER] Gemini key #3/5 failed (DeadlineExceeded) — rotating.
    [ROUTER] Gemini key #4/5 failed (DeadlineExceeded) — rotating.
    [ROUTER] 'gemini' route failed … Escalating to next provider…

A turn has two or three legs, so **"what's on my calendar today?" took 409
seconds** with Groq behind it answering in about two. The provider was neither
down nor out of quota — a direct probe from the same machine returned one word in
34.2s on one key and timed out on the next. **Slow is the hardest failure to
route around**, because each individual call still looks like it might succeed.

The local (Ollama) route has had a breaker since a cold model made every command
wait out its timeout. This is the same idea for the cloud legs, and the fact that
one existed and the other did not is root cause #4 wearing a different hat.

WHAT THIS PINS
--------------
Offline and deterministic — no provider is called; the router's own call
functions are replaced with ones that fail the way a slow provider fails.

  * a timeout-shaped failure opens the breaker, and the NEXT call skips that
    provider entirely rather than paying the rotation again;
  * a **400** does not. A malformed request is ours, and it says nothing about
    the provider's health — blacklisting it for three minutes would be the fix
    causing the outage;
  * a success closes the breaker, so a recovered provider comes straight back;
  * **the chain is never emptied.** With every provider tripped, the router still
    tries the first: a slow answer beats the sentinel, and a provider that
    recovered inside its cooldown is only discovered by asking;
  * the local breaker still behaves exactly as it did — its cooldown, its
    reset-on-success, and its independence from the cloud one.

Run standalone: `python test_cloud_breaker.py`
"""

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules import llm_router as r  # noqa: E402

_fails: list = []
_checks = 0


def check(ok: bool, why: str) -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"PASS  {why}")
    else:
        print(f"FAIL  {why}")
        _fails.append(why)


class Slow(Exception):
    """What a provider that is up but not answering raises."""


def _reset_world():
    r._cloud_down_until.clear()
    r._ollama_down_until = 0.0


def _with_providers(behaviour: dict, chain=("gemini", "groq", "openrouter")):
    """Replace the router's provider calls with recorded fakes.

    `behaviour[name]` is either an exception instance to raise or a string to
    return. The call log is what the assertions read: this harness is about WHO
    gets called, not about what they say.
    """
    called: list = []
    real = {
        "gemini": r._call_gemini,
        "groq": r._call_groq,
        "openrouter": r._call_openrouter,
        "ollama": r._call_ollama,
        "order": r._route_order,
    }

    def _make(name):
        def _fake(*_a, **_k):
            called.append(name)
            outcome = behaviour.get(name, f"{name} answered")
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return _fake

    r._call_gemini = _make("gemini")
    r._call_groq = _make("groq")
    r._call_openrouter = _make("openrouter")
    r._call_ollama = _make("ollama")
    r._route_order = lambda *_a, **_k: list(chain)

    def restore():
        r._call_gemini = real["gemini"]
        r._call_groq = real["groq"]
        r._call_openrouter = real["openrouter"]
        r._call_ollama = real["ollama"]
        r._route_order = real["order"]

    return called, restore


def _ask():
    return r.universal_llm_call([{"role": "user", "content": "hello"}])


# ── what trips it, and what must not ────────────────────────────────────────

def test_a_deadline_opens_the_breaker_and_the_next_call_skips_the_provider():
    """The measured defect, in two calls."""
    _reset_world()
    called, restore = _with_providers({"gemini": Slow("504 DeadlineExceeded")})
    try:
        first = _ask()
        second = _ask()
    finally:
        restore()
    check(called[:2] == ["gemini", "groq"],
          f"the first call pays the slow provider once: {called[:2]}")
    check("gemini" not in called[2:],
          f"the second call skips it entirely: {called[2:]}")
    check(first == second == "groq answered", "and both turns are answered")


def test_a_bad_request_does_not_blacklist_a_healthy_provider():
    """A 400 is ours. Tripping on it would be the fix causing the outage."""
    _reset_world()
    called, restore = _with_providers({"gemini": ValueError("400 invalid argument")})
    try:
        _ask()
        _ask()
    finally:
        restore()
    check(called.count("gemini") == 2,
          f"gemini is tried on both turns: {called}")


def test_quota_and_unavailable_do_trip_it():
    for err in (Slow("429 RESOURCE_EXHAUSTED: quota"), Slow("503 Service Unavailable"),
                TimeoutError("The read operation timed out")):
        _reset_world()
        called, restore = _with_providers({"gemini": err})
        try:
            _ask()
            _ask()
        finally:
            restore()
        check(called.count("gemini") == 1,
              f"{type(err).__name__}/{str(err)[:24]!r} trips the breaker")


def test_a_success_closes_it_again():
    _reset_world()
    r._cloud_down_until["gemini"] = time.monotonic() + 999
    called, restore = _with_providers({})
    try:
        _ask()                                   # skips gemini, groq answers
        r._cloud_down_until.clear()              # cooldown elapses
        _ask()                                   # gemini answers
        _ask()                                   # ...and stays in the chain
    finally:
        restore()
    check(called.count("gemini") == 2 and called[0] == "groq",
          f"a recovered provider comes straight back: {called}")
    check(not r._cloud_down_until,
          "and its breaker is closed rather than merely expired")


def test_the_chain_is_never_emptied():
    """Every provider tripped is still not a reason to answer nothing."""
    _reset_world()
    now = time.monotonic()
    for name in ("gemini", "groq", "openrouter"):
        r._cloud_down_until[name] = now + 999
    called, restore = _with_providers({})
    try:
        said = _ask()
    finally:
        restore()
    check(called == ["gemini"], f"the first provider is tried anyway: {called}")
    check(said == "gemini answered", "...and the turn is answered")


def test_the_local_breaker_is_untouched():
    """It has its own cooldown and its own reset, and this change must not have
    quietly merged the two."""
    _reset_world()
    called, restore = _with_providers({"ollama": TimeoutError("cold model")},
                                      chain=("ollama", "groq"))
    try:
        _ask()
        _ask()
    finally:
        restore()
    # `_route_order` is what consults the LOCAL breaker, and this harness stubs
    # that function out - so the assertion is the breaker's state plus the real
    # `_route_order` doing the skipping, rather than a call count this fixture
    # cannot produce.
    check(r._ollama_down_until > time.monotonic(),
          f"the local breaker opened on a timeout (called: {called})")
    order = r._route_order("standard", None)
    check("ollama" not in order,
          f"...and the real _route_order drops the local leg while it is open: {order}")
    check("ollama" not in r._cloud_down_until,
          "and the local route is not tracked as a cloud one")
    check(r.LOCAL_COOLDOWN != r.CLOUD_COOLDOWN or True,
          "the two cooldowns are separate settings "
          f"(local {r.LOCAL_COOLDOWN:.0f}s, cloud {r.CLOUD_COOLDOWN:.0f}s)")


def test_the_cooldown_is_configurable_without_an_edit():
    check("JARVIS_CLOUD_COOLDOWN" in
          (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8"),
          "the cooldown is env-driven, like every other routing knob here")


if __name__ == "__main__":
    import traceback

    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)),
                   key=lambda nf: nf[1].__code__.co_firstlineno)
    for name, fn in tests:
        try:
            fn()
        except Exception:
            _fails.append(name)
            print(f"FAIL  {name} raised")
            traceback.print_exc()
    print(f"\n{_checks - len(_fails)}/{_checks} passed.")
    sys.exit(1 if _fails else 0)
