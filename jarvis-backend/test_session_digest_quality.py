"""Harness: a session digest must be a recap, not whatever the call returned.

WHY THIS EXISTS
---------------
2026-09-05, driving row `9.4` ("go to sleep", then "wake up" — on wake, prior
session context is seeded). The row passed. The stored digest was:

    KAUSTAV  2026-09-05T11:32:50  rawlen=51  plainlen=4  ->  'User'

**Four characters.** The transcript pasted into the summariser's prompt labels
every line `User: ` or `JARVIS: `, and a degraded provider echoed the label back
instead of summarising. It was stored because the only test was:

    digest = (digest or "").strip()
    if digest:
        save_session_digest(user, digest)

A non-empty string is not a summary. The consequence is quiet and total: on wake,
`seed_from_last_digest` puts *"Earlier, before standby: User"* into working
memory and reports success, so the row's promise fails **without anything
failing**. The `session_digest` table is keyed by user, so the bad digest also
**displaces the last good one** — the failure destroys the thing it replaces.

That is the shape this project keeps finding, and the memory layer is the worst
place for it: the gate's own skull at `K3` is about forgetting that does not look
like forgetting.

Checked on the way IN and on the way OUT, because a digest written before the
check existed is still sitting in the database.

WHAT THIS PINS
--------------
That a degenerate summary is refused rather than stored, that a good one still
passes, and that the previous digest survives a failed consolidation.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from memory import looks_like_a_digest  # noqa: E402

_checks = 0
_fails: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(label)
        print(f"FAIL  {label}")


GOOD = ("The user was testing JARVIS end to end and confirmed his eleven-space "
        "indentation preference. He asked for shorter replies. No tasks are open.")


def test_the_real_failure_is_refused():
    """The exact value found in the database."""
    ok, why = looks_like_a_digest("User")
    check(not ok, f"the literal stored value 'User' is refused ({why})")
    check("4 characters" in why or "character" in why,
          f"and the reason says what was wrong with it: {why!r}")


def test_every_transcript_label_is_refused():
    """The prompt's own scaffolding, which is what a failing model reaches for."""
    for artefact in ("User", "JARVIS", "User:", "JARVIS:", "Summary", "None",
                     "N/A", "null"):
        ok, _ = looks_like_a_digest(artefact)
        check(not ok, f"{artefact!r} is not a session digest")


def test_empty_and_whitespace_are_refused():
    for blank in ("", "   ", "\n\t "):
        ok, why = looks_like_a_digest(blank)
        check(not ok, f"blank input is refused ({why})")


def test_a_short_but_real_sentence_is_still_refused():
    """Two or three sentences was the instruction. One clause is a failure that
    happens to be grammatical, and it is the hardest kind to notice."""
    ok, why = looks_like_a_digest("Discussed indentation.")
    check(not ok, f"a 22-character 'recap' is refused ({why})")


def test_a_wall_of_words_with_no_sentence_is_refused():
    ok, why = looks_like_a_digest("indentation email steps calendar testing " * 3)
    check(not ok, f"text with no sentence in it is refused ({why})")


def test_a_monologue_is_refused():
    """The second failure this row produced, found only by re-running it.

    On the next drive the stored digest was 426 characters beginning "Here's a
    thinking process: 1. Analyze User Input:" - the model's own reasoning,
    which sails past a length check and a sentence check. Same failure as the
    four-character one, one layer up, and the guard for it already existed in
    `reasoning_guard`: written once, not reached from here.
    """
    mono = ("Here's a thinking process:" + chr(10) * 2
            + "1.  **Analyze User Input:** The user said go to sleep." + chr(10)
            + "2.  **Recall context:** indentation and email counts." + chr(10)
            + "3.  **Draft the summary.**")
    ok, why = looks_like_a_digest(mono)
    check(not ok, f"a reasoning monologue is not a session digest ({why})")
    check("reasoning" in why,
          f"and the reason names what it actually is: {why!r}")


def test_a_real_recap_passes():
    """The guard must not be so strict that a working desk stops storing."""
    ok, why = looks_like_a_digest(GOOD)
    check(ok, f"a genuine two-sentence recap is accepted ({why})")


def test_the_writer_refuses_rather_than_overwrites():
    """The table is keyed by user, so storing a bad digest DESTROYS the last good
    one. Refusing must leave the previous recap in place."""
    src = (HERE / "memory.py").read_text(encoding="utf-8")
    consolidate = src.split("def seed_from_last_digest")[0]
    check("looks_like_a_digest(digest)" in consolidate,
          "the write path validates before storing")
    check("REJECTED" in consolidate,
          "and says so in the log rather than failing silently")
    check("Keeping the previous digest" in consolidate,
          "and the message makes clear the old recap was preserved")


def test_the_reader_refuses_a_bad_digest_already_on_disk():
    """One in the database right now predates this check."""
    src = (HERE / "memory.py").read_text(encoding="utf-8")
    seed = src.split("def seed_from_last_digest")[1]
    check("looks_like_a_digest(digest)" in seed,
          "the wake path re-checks what it found on disk")
    check("Waking without a recap" in seed,
          "and wakes with nothing rather than with a lie")


if __name__ == "__main__":
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
