"""Harness: the spoken admin override is authenticated, or it is not an override.

F-27. Three doors reach "boot me as the owner". Two were closed, and the project
closed them deliberately and knew why:

  * `/api/backdoor` refuses behind `JARVIS_ALLOW_BACKDOOR` and answers with a
    message telling you to go and do the face scan;
  * click-to-talk refuses on purpose — `wakeword.py` carries the comment "a click
    must not hand out admin", and `test_listen_request.py` fails if the override
    phrase ever appears on that path.

The third door — the loudest and most reachable one — did this:

    if "admin override" in wake_phrase.lower():
        active_user = "KAUSTAV"

Unconditional, and a substring. And `wakeword.py` printed *"Waiting for 'wake up'
or 'initiate admin override'"* on every idle cycle, on a screen anyone in the
room can read. So the security ordering was exactly inverted: the hardened door
sent you to a door that was broken — camera off, and F-23 terminating the real
owner on a mis-transcribed name — while the unhardened door let anyone in.

Root cause #4 for the third time in one session, with F-21 and F-25: a class
fixed one site at a time stays open.

WHAT THIS PINS
--------------
The override is NOT removed — it is the recovery path for exactly the state F-23
and F-25 describe, and that state is real. It is authenticated: a secret spoken
with the phrase, refused when unset, never printed, loud in the log either way.
The authenticator is lifted out of `main.py` and CALLED, so its properties are
tested rather than asserted about; the wiring is structural, because reaching the
wake branch needs a microphone.
"""

import ast
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_passed = 0
_failed = 0

ENV = "JARVIS_ADMIN_OVERRIDE_CODE"
PHRASE = "initiate admin override"


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def _main_src() -> str:
    return (HERE / "main.py").read_text(encoding="utf-8", errors="replace")


def _lift():
    """The authenticator and the tokeniser it uses, without importing main.py.

    main.py boots a great deal on import; this needs two functions and a
    frozenset. Lifting them by AST keeps the test honest — it is the real source,
    not a copy typed out beside it, which is how the shared-memory merge came to
    be "verified" without ever running.
    """
    tree = ast.parse(_main_src())
    keep = []
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(
                getattr(t, "id", "").startswith(("_CONFIRM_APOS", "_ADMIN_OVERRIDE_ENV"))
                for t in n.targets):
            keep.append(n)
        if isinstance(n, ast.FunctionDef) and n.name in (
                "_confirm_tokens", "_admin_override_granted"):
            keep.append(n)
    ns = {"os": os}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "lifted", "exec"), ns)
    return ns["_admin_override_granted"]


def _with_code(code):
    if code is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = code


# ── off unless it is switched on ──────────────────────────────────────────────

def test_an_unset_code_refuses():
    """An escape hatch whose default is "open" is not an escape hatch. This is
    also the state every existing install is in, so the fix must be safe there."""
    g = _lift()
    _with_code(None)
    ok, why = g(PHRASE)
    check(ok is False, "with no code set, the phrase authorises nothing")
    check(ENV in why, "...and the log says which variable would open it")


def test_a_blank_or_whitespace_code_refuses():
    g = _lift()
    for blank in ("", "   ", "\t"):
        _with_code(blank)
        check(g(PHRASE)[0] is False, f"a code of {blank!r} refuses")


def test_a_punctuation_only_code_refuses():
    """`_confirm_tokens` strips punctuation, so a code of "!!!" tokenises to
    nothing — and an empty requirement is satisfied by every utterance. That is
    the subset check quietly inverting into "always true"."""
    g = _lift()
    _with_code("!!!")
    ok, why = g(PHRASE)
    check(ok is False, "a code that tokenises to nothing refuses")
    check("punctuation" in why, "...and says so, rather than failing silently")


# ── the phrase alone is not the secret ────────────────────────────────────────

def test_the_phrase_without_the_code_refuses():
    """The bug, stated directly. This exact utterance booted a full admin
    session with a briefing, no scan and no name."""
    g = _lift()
    _with_code("tiberius")
    check(g(PHRASE)[0] is False,
          "the advertised phrase on its own no longer grants anything")
    check(g("initiate admin override please")[0] is False,
          "...nor with a polite word after it")


def test_the_phrase_with_the_code_grants():
    g = _lift()
    _with_code("tiberius")
    check(g("initiate admin override tiberius")[0] is True,
          "the phrase plus the code grants")


def test_the_code_survives_a_transcriber():
    """It arrives through speech-to-text, so case and punctuation are not signal
    and word order is not either."""
    g = _lift()
    _with_code("tiberius")
    for said in ("initiate admin override, Tiberius.",
                 "INITIATE ADMIN OVERRIDE TIBERIUS",
                 "tiberius, initiate admin override"):
        check(g(said)[0] is True, f"{said!r} grants")


def test_a_code_inside_a_longer_word_is_not_the_code():
    """The same substring bug as F-42, on a door where it would be worse. A
    transcriber that hears "tiberiusx" has not heard the code."""
    g = _lift()
    _with_code("tiberius")
    for said in ("initiate admin override tiberiusx",
                 "initiate admin override tibe rius",
                 "initiate admin override tiberi"):
        check(g(said)[0] is False, f"{said!r} does not grant")


def test_every_word_of_a_multi_word_code_is_required():
    g = _lift()
    _with_code("blue horizon")
    check(g("initiate admin override blue horizon")[0] is True, "both words grant")
    check(g("initiate admin override blue")[0] is False, "one word does not")
    check(g("initiate admin override horizon")[0] is False, "the other does not either")
    check(g("initiate admin override horizon blue")[0] is True,
          "order is not the signal, presence is")


# ── the wiring ────────────────────────────────────────────────────────────────

def test_the_wake_branch_gates_on_the_authenticator_and_not_the_substring():
    src = _main_src()
    check("if _override_ok:" in src,
          "the boot branch turns on the authorisation, not the phrase")
    check('active_user = "KAUSTAV"' in src.split("if _override_ok:", 1)[1][:400],
          "...and the identity is assigned inside it")
    bad = 'if "admin override" in wake_phrase.lower():\n                active_user'
    check(bad not in src, "the unconditional substring grant is gone")


def test_a_refused_attempt_is_told_to_the_owner_and_the_log():
    """A refusal he cannot hear is a system that ignored him — the F-43 lesson,
    on a different door."""
    src = _main_src()
    block = src[src.index("_override_attempt = "):]
    block = block[:block.index("if _override_ok:")]
    check("speak_text" in block, "a refused override is spoken, not just logged")
    check("security_locked" in block, "...and the HUD is told")
    check("F-27" in block, "...and the log line names the finding")


def test_both_outcomes_are_logged():
    """A granted override is the single most security-relevant event this process
    can have. It must not be quieter than a refusal."""
    src = _main_src()
    block = src[src.index("_override_attempt = "):]
    block = block[:block.index("if _override_ok:")]
    check("GRANTED" in block and "REFUSED" in block,
          "the log names which way it went")
    check("flush=True" in block, "...unbuffered, so a crash after it keeps the line")


def test_the_idle_screen_no_longer_advertises_the_phrase():
    """It was printed on every idle cycle. A secret that is printed is not one."""
    wk = (HERE / "wakeword.py").read_text(encoding="utf-8", errors="replace")
    idle = [ln for ln in wk.splitlines()
            if "Offline. Waiting for" in ln and "print(" in ln]
    check(len(idle) == 1, f"there is one idle line ({len(idle)})")
    check("admin override" not in idle[0].lower(),
          "the idle line names only the wake word")
    check("wake up" in idle[0], "...and it still names that")


def test_the_click_path_is_still_closed():
    """It was already right, and a fix to the voice path must not make the click
    path look inconsistent enough for someone to 'align' them."""
    wk = (HERE / "wakeword.py").read_text(encoding="utf-8", errors="replace")
    check("a click must not hand out admin" in wk,
          "the click path still refuses, with its reason")


def test_the_phrase_still_reaches_the_judge():
    """The wake listener must keep returning the phrase. An authenticated
    override that never reached main.py could not boot at all, and the recovery
    path would be gone rather than gated."""
    wk = (HERE / "wakeword.py").read_text(encoding="utf-8", errors="replace")
    check('"admin override" in text' in wk,
          "the listener still returns an override utterance for judging")


def test_main_still_parses():
    ast.parse(_main_src())
    check(True, "main.py parses after the edits")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Admin override — F-27")
    print("=" * 62)
    saved = os.environ.get(ENV)
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            global _failed
            _failed += 1
            print(f"FAIL  {t.__name__} raised {type(e).__name__}: {e}")
    if saved is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = saved
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
