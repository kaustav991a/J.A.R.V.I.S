"""Harness: whether an unprompted remark is warranted at all.

WHY THIS EXISTS
---------------
On Friday 2026-08-21 the gateway spoke first, unprompted, for the first time ever,
and said this:

    J.A.R.V.I.S.
    It's Friday, Sir, so hopefully you won't have to head in for a Saturday shift
    tomorrow.

There is no Saturday shift. The operator does not have one. The remark was invented,
and it was invented because `_nudge_subject` decided whether to speak with a bare
substring test:

    named_day = weekday in low

A stored fact describing a Monday-to-Friday pattern contains the word "friday". The
prompt then told the model "Something you were told about him is true TODAY", handed
it that fact, and asked for a remark. The model obeyed — and the only remarkable thing
left in a Mon-Fri fact, on a Friday, is the weekend. So it produced one.

The function's own docstring claimed "The judgement of WHETHER to speak is made here,
in code". It was not. This harness is what makes that sentence true.

WHAT THIS PINS
--------------
Offline and deterministic. No model, no network, no clock — the date is passed in.

  * a weekday named as RECURRING or as today's own business counts;
  * a weekday merely MENTIONED does not;
  * **a fact naming more than one weekday is refused outright** — that is the shape
    the Saturday remark came from, and it is the important one;
  * a fact naming a different weekday is refused;
  * dated facts still work, in either order people write dates.

Run standalone: `python test_nudge_subject.py`
"""
from typing import Optional
import datetime as dt

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday")


def _asserts_today(low: str, weekday: str) -> bool:
    """Whether a fact claims something about TODAY, rather than merely mentioning it.

    Two rules, and the second is the one that mattered:

    1. The day has to be named as something RECURRING or as today's own business:
       "every friday", "fridays", "on friday", "this friday".
    2. **A fact naming any OTHER weekday is refused outright.** "Mon-Fri" mentions
       Friday and is not about Friday; it is about a week. Anything listing more than
       one day is describing a pattern whose interesting part is usually the day that
       is NOT today — which is exactly the trap that produced the Saturday remark.
    """
    named = [d for d in WEEKDAYS if d in low]
    if len(named) != 1 or named[0] != weekday:
        return False
    return any(
        p in low
        for p in (f"every {weekday}", f"{weekday}s", f"on {weekday}", f"this {weekday}")
    )


def _nudge_subject(facts: list, now) -> Optional[tuple]:
    """What is worth remarking on today, as (subject, prompt), or None."""
    today = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A").lower()
    day_num = str(now.day)
    month = now.strftime("%B").lower()

    for fact in facts:
        low = str(fact).lower()
        dated = (f"{day_num} {month}" in low or f"{month} {day_num}" in low
                 or today in low)
        if not (dated or _asserts_today(low, weekday)):
            continue
        return (
            str(fact)[:120],
            f"Today is {now.strftime('%A %d %B')}. You were once told: "
            f"\"{fact}\". If — and ONLY if — that fact is about today, remark on it "
            "in ONE short sentence, as though you had just remembered it, the way "
            "someone who knows him would mention it in passing. Do not greet him, "
            "do not list anything, do not offer help, and do not explain that you "
            "remembered. **Never state anything the fact does not say**, and never "
            "infer another day from it. If it is not about today, or does not "
            "warrant saying out loud, reply with exactly: SKIP",
        )
    return None


FRIDAY = dt.datetime(2026, 8, 21, 11, 20)      # the day it went wrong
SATURDAY = dt.datetime(2026, 8, 22, 11, 20)

CASES = [
    # (facts, now, should_speak, what it is checking)
    (["Works Monday to Friday, no Saturday shift"], FRIDAY, False,
     "THE BUG: a Mon-Fri pattern is about a week, not about this Friday"),
    (["Mon-Fri at the office, Saturdays off"], FRIDAY, False,
     "the same shape written shorter"),
    (["Plays football every Friday evening"], FRIDAY, True,
     "recurring, and about today"),
    (["Fridays are gym days"], FRIDAY, True,
     "pluralised weekday reads as recurring"),
    (["The office closes early on Friday"], FRIDAY, True,
     "'on friday' is today's own business"),
    (["Dentist this Friday"], FRIDAY, True,
     "'this friday' is today"),
    (["Gym on Tuesday and Thursday"], FRIDAY, False,
     "a different weekday, twice over"),
    (["Plays football every Friday evening"], SATURDAY, False,
     "right fact, wrong day"),
    (["Met her on a Friday in 2019"], FRIDAY, False,
     "incidental mention: 'on a friday' is not 'on friday'"),
    (["The Friday deadline slipped"], FRIDAY, False,
     "a weekday used as an adjective is not a claim about today"),
    (["Mum's birthday is 21 August"], FRIDAY, True,
     "dated fact, day-first"),
    (["Mum's birthday is August 21"], FRIDAY, True,
     "dated fact, month-first"),
    (["Mum's birthday is 22 August"], FRIDAY, False,
     "tomorrow is not today"),
    ([], FRIDAY, False,
     "no facts, no remark"),
    (["Likes his coffee black"], FRIDAY, False,
     "a fact about him is not a fact about today — silence is the default"),
]


def main() -> int:
    bad = 0
    for facts, now, should, why in CASES:
        got = _nudge_subject(facts, now) is not None
        ok = got == should
        if not ok:
            bad += 1
        print(f"{'PASS' if ok else 'FAIL'}  spoke={got!s:<5} want={should!s:<5}  {why}")

    print()
    # `run_harnesses.py` reads the count off THIS line, matching either
    # `N/M passed` or `N passed, M failed`. It printed "all 15 passed", which
    # matches neither, so the runner scored the harness at **0 checks** and
    # reported it BROKEN while its own output said everything had passed. A
    # harness at 0 checks is not green, and that is the whole reason the runner
    # says so out loud.
    print(f"{len(CASES) - bad}/{len(CASES)} passed"
          f"{'' if bad else ' — the Saturday remark cannot happen again'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
