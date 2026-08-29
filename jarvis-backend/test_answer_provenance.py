"""Harness: he was asked to go somewhere, went somewhere else, and did not say so.

WHY THIS EXISTS
---------------
Goal 1 — **"He never claims what he did not do"** — measured live on the desk on
2026-08-29 by gate row `10.4`:

    > go to python.org and find the latest Python version

    [GOVERNANCE] action='tavily_search' -> tier=AUTO
    [ACTION ENGINE] Tavily returned 5 result(s).
    [JARVIS] Python 3.14.6 is the latest stable release, Sir.

He never opened python.org. `web_browse` exists, is AUTO tier and takes a URL —
it simply was not chosen. **The answer was probably correct**, which is exactly
what makes this the failure this goal is about: he was asked to do a specific
thing, did a different thing, and reported the result as though the instruction
had been carried out. Nothing he said was false. The sentence he did not say is
the claim.

WHAT THIS PINS
--------------
Offline and pure — `provenance_note` takes the request and the list of actions
that actually ran, and returns one sentence or None.

  * the reported case fires;
  * **it does not fire when he really did navigate**, which is the whole point of
    keying on the executed actions rather than on the wording;
  * it does not fire on a question ABOUT a site ("what is on python.org") — that
    is not an instruction to open one, and a correction there is noise;
  * it does not fire for services whose name looks like a host ("check gmail"),
    nor for "go to sleep";
  * it does not fire before anything has been searched, so there is never a
    correction with nothing to correct;
  * the gate is WIRED, and wired before the streaming synthesis rather than after
    — the answer is spoken sentence by sentence, so a note appended at the end
    would arrive after he had already finished implying otherwise.

Run standalone: `python test_answer_provenance.py`
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules.answer_provenance import provenance_note, site_asked_for  # noqa: E402

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


SEARCHED = ["tavily_search"]
BROWSED = ["web_browse"]

# (request, actions that ran, should it speak up, why this case is here)
CASES = [
    ("go to python.org and find the latest Python version", SEARCHED, True,
     "🛑 THE REPORTED ROW — searched when asked to navigate, and said nothing"),
    ("go to python.org and find the latest Python version", BROWSED, False,
     "he actually went: no correction is owed, and one would be wrong"),
    ("open bbc.co.uk and tell me the headlines", SEARCHED, True,
     "'open' is the same instruction as 'go to'"),
    ("visit https://news.ycombinator.com and read me the top item", SEARCHED, True,
     "a full URL is still a site he named"),
    ("pull up github.com/kaustav991a and check the last commit", SEARCHED, True,
     "a path after the host does not stop it being a host"),
    ("what is on python.org", SEARCHED, False,
     "a question ABOUT a site is not an instruction to open one"),
    ("what is the latest Python version", SEARCHED, False,
     "no site named, so there is nothing he failed to do"),
    ("check gmail", SEARCHED, False,
     "a service whose name looks like a host — a correction here is noise"),
    ("go to sleep", SEARCHED, False,
     "'go to' with no host is not a navigation request"),
    ("go to python.org and find the latest Python version", [], False,
     "nothing has been searched yet: no answer to attribute"),
    ("go to python.org and find the latest Python version", ["check_calendar"], False,
     "an unrelated action is not a substitute answer either"),
    ("GO TO PYTHON.ORG and find the version", SEARCHED, True,
     "case is the asker's business, not the matcher's"),
]


def test_every_phrasing():
    for text, actions, should, why in CASES:
        note = provenance_note(text, actions)
        got = note is not None
        check(got == should, f"{text!r} + {actions} -> {'note' if got else 'silence'} — {why}")


def test_the_note_names_the_site_and_the_source():
    note = provenance_note("go to python.org and find the latest version", SEARCHED)
    check("python.org" in note, f"it names the site he asked for: {note!r}")
    check("web search" in note, "...and what it used instead")
    check(note.strip().endswith("."), "...and it is one plain sentence")
    check("!" not in note, "...with no theatre in it")


def test_the_site_matcher_is_not_a_url_detector():
    check(site_asked_for("go to python.org") == "python.org", "bare host")
    check(site_asked_for("open www.python.org") == "python.org", "www is stripped")
    check(site_asked_for("browse to https://python.org/downloads") == "python.org",
          "scheme and path are stripped")
    check(site_asked_for("email someone at python.org") is None,
          "an address is not an instruction to open a page")
    check(site_asked_for("") is None, "an empty request asks nothing")


def test_it_is_wired_into_the_answer_path_and_before_the_stream():
    """A guard nothing calls is a comment. And the ORDER is the property: the
    synthesis speaks each sentence as it arrives, so a note after it would land
    once he had already implied otherwise."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    # THREE doors build an answer out of batched results - the desk, the remote
    # bridge, and the second streaming path. A guard on one of them is a guard on
    # the door somebody happened to be watching, which is root cause #4 and the
    # reason this counts them rather than finding one.
    blocks = src.split("if batched_data:")[1:]
    check(len(blocks) == 3, f"there are three batched-answer doors ({len(blocks)})")
    for i, block in enumerate(blocks, 1):
        head = block[:2000]
        check("provenance_note(" in head, f"door {i} calls the gate")
    for i, block in enumerate(blocks, 1):
        head = block[:2000]
        if "provenance_note(" not in head:
            continue
        after = head.split("provenance_note(", 1)[1]
        # whichever way this door answers, the note comes first
        for speaker_call in ("_stream_synthesize_speak(", "synthesize_info"):
            if speaker_call in after or speaker_call in head:
                check(head.index("provenance_note(") < head.index(speaker_call),
                      f"door {i} says it BEFORE it answers ({speaker_call})")
                break


def test_the_gate_cannot_take_the_turn_down():
    """It runs on the path that is about to answer him. A guard that raises is a
    worse outcome than the claim it prevents."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    block = src.split("provenance_note(")[0].rsplit("try:", 1)[-1]
    check("try:" in src.split("provenance_note(")[0][-400:],
          "the call is inside a try")
    check("[PROVENANCE] check failed" in src,
          "...and a failure is printed rather than raised")


# ── row 10.9: silence is not an answer ───────────────────────────────
#
# "good morning" produced a payload, an HTTP 200, and NOTHING ELSE. No spoken
# line, no frame, no error. `speak_text("")` returns without a sound by design,
# so a model that produced no text was indistinguishable from a desk that never
# heard him - and the conclusion available to him is that the machine is broken.


def test_an_empty_answer_becomes_an_admission():
    from modules.answer_provenance import answer_or_admission, SILENT_ANSWER
    for empty in ("", "   ", "\n", None):
        check(answer_or_admission(empty) == SILENT_ANSWER,
              f"{empty!r} is answered rather than dropped")
    check("didn't get an answer" in SILENT_ANSWER.lower(),
          "the admission says what happened")
    check("ask me again" in SILENT_ANSWER.lower(),
          "...and what he can do about it")


def test_a_real_answer_is_untouched():
    from modules.answer_provenance import answer_or_admission
    check(answer_or_admission("Twenty four minutes, sir.") == "Twenty four minutes, sir.",
          "an answer passes through unchanged")
    check(answer_or_admission("  padded  ") == "padded",
          "...trimmed, but not replaced")


def test_every_answer_door_uses_it():
    """Four sites spoke `clean_response` directly and two sent `final_answer`.
    Missing one leaves the silence on whichever door nobody was watching."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    check("speak_text(clean_response)" not in src,
          "no door speaks a possibly-empty answer any more")
    check(src.count("answer_or_admission(") >= 6,
          f"every answer door goes through the guard "
          f"({src.count('answer_or_admission(')} sites)")


def test_an_action_answer_with_no_actions_still_answers():
    """The measured shape of row 10.9: the model returned an ACTION answer whose
    action list was empty. `clean_response` is blanked as leftover JSON, the loop
    runs zero times, `batched_data` stays empty - and before this guard the turn
    ended at HTTP 200 having said nothing at all."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    check(src.count("if not actions:") >= 2,
          f"both desk doors answer an empty action list "
          f"({src.count('if not actions:')} sites)")
    check(src.count("[MAIN] empty action list") == 2,
          "...and say so in the log, so the next silence is diagnosable")
    # Three doors parse an action list. Two guard it where it is parsed; the
    # REMOTE one guards it at the other end, where it used to say "Done, Sir."
    # for a turn in which nothing ran - which is worse than the silence, because
    # it is a report of work that never happened.
    # Four places parse an action list: the remote ANSWER path, the queue-a-goal
    # helper (which returns nothing to enqueue and is fine), and the two speaking
    # doors, which are the ones that used to go quiet.
    doors = src.split("actions = _normalize_os_control_batch(")[1:]
    check(len(doors) == 4, f"four places parse an action list ({len(doors)})")
    guarded = sum(1 for d in doors if "if not actions:" in d[:1200])
    check(guarded >= 3,
          f"every parse site does something deliberate with an empty list ({guarded})")
    remote = src.split("if not replied:", 1)[1][:600]
    check("if actions:" in remote and 'reply(f"Done, {honor}.")' in remote,
          "the remote door only says 'Done' when something actually ran")
    check("await channel.reply(answer_or_admission(_parsed.preamble))" in src,
          "...and admits it otherwise")


def test_a_monologue_answer_is_admitted_by_the_reasoning_guard():
    """Checked, and it was ALREADY handled - which is why nothing was added here.

    A guard was briefly put in `speak_text` for this and removed the same hour:
    `guard_spoken` returns a spoken admission ("I lost the thread of that one,
    Sir - my reasoning ran on instead of answering") whenever an answer is
    entirely reasoning, so the branch could never fire. **A guard that cannot
    fire is exactly the kind of claim this goal exists to stop**, so the property
    is pinned here against the code that really carries it.
    """
    from modules import reasoning_guard
    said = reasoning_guard.guard_spoken("Here's a thinking process: 1. Analyze User Goal.")
    check(bool(said.strip()), f"a monologue answer still says something: {said[:48]!r}")
    check("thinking process" not in said, "...and it is not the monologue itself")
    check(reasoning_guard.guard_spoken("") == "",
          "an EMPTY answer stays silent - the caller meant silence")
    src = (HERE / "speaker.py").read_text(encoding="utf-8", errors="replace")
    check("SILENT_ANSWER" not in src,
          "speak_text carries no unreachable guard of its own")



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
