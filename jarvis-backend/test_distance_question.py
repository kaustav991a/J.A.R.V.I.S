"""Harness: the distance question, and the stale fact that made it worse.

WHY THIS EXISTS
---------------
Queue item 24 from `jarvis-mobile`'s brain handoff. Asked on the phone, standing in
the office:

    "how far is home from here"

The reply named a distance and a train, with total confidence, and added that he had
*already arrived at home* — while the phone's own header read **Office**.

**One bug in two halves, and fixing either alone leaves the other able to produce the
same confident wrong answer.**

*Half one.* `_FAR_RE` was a single pattern:

    r"\\b(?:how far|distance|how long)\\b.{0,20}?\\b(?:to|from|until)\\s+(?P<dest>...)"

It put whatever followed the **first** preposition into the destination slot. So
"how far is home from here" asked for a route to a place called `here`, matched no
known place, injected no route fact — and the model answered out of its weights.
It answers "how far to home" correctly, which is why every earlier check passed:
**the feature worked on the phrasing the test script used and failed on the phrasing
a person used.** There was no test script. `_FAR_RE`, `_NEAR_RE` and
`_local_lookups` were named by no harness in the suite, which is the finding behind
the finding.

*Half two.* With the lookup failing quietly, the model reached for a stored fact —
`Kaustav is currently in Ichapur, West Bengal, India` — a time-sensitive claim
living in a permanent store, and concluded he was home. The persona already forbids
these ("Only lasting things: not what he asked just now, not the weather"). It was
not enough and could not be: **an instruction is not a gate.** So there is a gate
now, at the sink, and it is asymmetric on purpose — see below.

WHAT THIS PINS
--------------
Offline and deterministic. No network, no model, no geocoder: `_far_dest` is pure,
and the fact sink's two dependencies are stubbed.

  * **one case per phrasing**, because the entire defect is that one phrasing was
    never tried — including the two that were wrong and the one that matched nothing
    at all;
  * `from` is still read, because "how far from home to the office" needs it to mark
    an ORIGIN — deleting it is the obvious fix and the wrong one;
  * a self-referential destination is refused, and refusing yields **None** rather
    than a guess: no fact injected beats a fact from a bad lookup;
  * the bare-name fallback accepts **only places he has named** — an arbitrary noun
    is not handed to a geocoder;
  * time-sensitive claims and conversation trivia are refused at the sink;
  * **the operator's own door is exempt, and the exemption is pinned at its call
    site.** He types into the Memory screen behind `APP_TOKEN`; if he wants to note
    something time-bound there, that is his call. The guard exists for the model's
    automatic extraction, which is where every bad fact so far came from. A refactor
    that made the Memory screen refuse his notes would be a regression nothing else
    would catch.

Run standalone: `python test_distance_question.py`
"""

import asyncio
import os
import re

os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402

# the places he has named, in the shape the phone sends them
KNOWN = [
    {"label": "home", "lat": 22.7900, "lon": 88.3700},
    {"label": "the office", "lat": 22.5700, "lon": 88.3600},
]

_fails: list[str] = []
_checks = 0


def check(ok: bool, why: str) -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"PASS  {why}")
    else:
        print(f"FAIL  {why}")
        _fails.append(why)


def dest(q: str, known=KNOWN):
    return cg._far_dest(q, known)


# ── half one: which place is being asked about ───────────────────────────────
#
# (question, expected destination, why this case is here)
PHRASINGS = [
    ("how far to the office", "the office",
     "the phrasing that already worked, and must keep working"),
    ("how far to the office from here", "the office",
     "a `to` destination wins over a trailing `from` — the old greedy capture "
     "swallowed 'the office from here' whole"),
    ("how far from home to the office", "the office",
     "`from` marks the ORIGIN: this is why `from` cannot just be deleted"),
    ("how far is home from here", "home",
     "🛑 THE REPORTED DEFECT — it resolved to 'here' and invented a distance"),
    ("how far is home", "home",
     "🛑 no preposition at all: the old pattern matched NOTHING"),
    ("how long to the office", "the office",
     "'how long' is the same question"),
    ("distance to the office", "the office",
     "'distance' is the same question"),
    ("How Far To THE OFFICE", "THE OFFICE",
     "case is the asker's business, not the matcher's"),
    ("how far is it to Digha", "Digha",
     "a place he has NOT named still reaches the geocoder — the feature is not "
     "narrowed to his own list"),
    ("how far to the office?", "the office",
     "a question mark is not part of the place name"),
    ("what is the weather", None,
     "not a distance question: no trigger, no lookup"),
    ("how far is my location", None,
     "the destination slot held only a self-reference and no known place was "
     "named — None, not a guess"),
    ("how far from here", None,
     "same, with the bare word 'here'"),
    ("how far is Barrackpore from here", None,
     "🛑 the honest limit, stated rather than discovered: the bare-name fallback "
     "reads only places he NAMED, so an unknown one yields no fact. A geocoder "
     "handed a noun from mid-sentence returns somebody else's town, and the "
     "defect being repaired IS a confident answer built on a bad lookup"),
]


def run_phrasings() -> None:
    for q, want, why in PHRASINGS:
        got = dest(q)
        check(got == want, f"{q!r} -> {got!r} (want {want!r}) — {why}")


def run_no_known_places() -> None:
    """With nothing named, the prepositional path still works and the fallback cannot."""
    check(dest("how far to the office", []) == "the office",
          "no named places: a `to` destination still resolves, for the geocoder")
    check(dest("how far is home from here", []) is None,
          "no named places: the bare-name fallback has nothing to read, so None")


def run_origin_is_not_the_destination() -> None:
    """The ordering rule, asserted directly rather than only through phrasings."""
    check(dest("how far from home to the office") != "home",
          "the ORIGIN is never returned when a `to` destination exists")
    check(dest("how far from the office") == "the office",
          "with only a `from`, it is the destination — 'how far from the office' "
          "does mean the office")


# ── half two: a claim that is only true for a while ──────────────────────────
PERISHABLE = [
    ("Kaustav is currently in Ichapur, West Bengal, India", True,
     "🛑 THE FACT THAT PRODUCED 'you have already arrived at home'"),
    ("Kaustav asked about Marco Polo", True,
     "🛑 conversation trivia, stored beside load-bearing records"),
    ("Kaustav is at work right now", True, "'right now' is not lasting"),
    ("Kaustav is travelling to Delhi tomorrow", True, "'tomorrow' is not lasting"),
    ("Kaustav had a rough day today", True, "'today' is not lasting"),
    ("Kaustav wanted to know how tall the tower is", True,
     "what he wanted to know is not a fact about him"),
    ("Kaustav lives in Ichapur", False,
     "where he LIVES is lasting — the guard must not eat the useful version of "
     "the same subject"),
    ("Kaustav has a nine-month-old black Indie called Kitty", False,
     "the persona's own worked example must survive"),
    ("Mousumi is his partner", False, "a relationship is lasting"),
    ("His dog Indie died on 5 August 2025", False,
     "a dated past event is lasting; a date is not a deadline"),
    ("Kaustav prefers his coffee black", False, "a preference is lasting"),
    ("Kaustav works at Fortmindz", False, "a job is lasting"),
]


def run_perishable() -> None:
    for fact, refuse, why in PERISHABLE:
        got = cg._perishable(fact) is not None
        check(got == refuse,
              f"{'refused' if got else 'kept   '} {fact!r} — {why}")


# ── the sink, and the one door that is exempt ────────────────────────────────
def run_the_sink() -> None:
    written: list[str] = []

    real_ready, real_add, real_cache = (
        cg._memory_ready, cg._db_add_fact_blocking, cg._facts_cache)

    async def _ready():
        return True

    def _add(said):
        written.append(said)

    cg._memory_ready = _ready
    cg._db_add_fact_blocking = _add
    cg._facts_cache = []
    try:
        async def go():
            stale = "Kaustav is currently in Ichapur"
            lasting = "Kaustav lives in Ichapur"

            check(await cg.remember_fact(stale) is False,
                  "the model's default path REFUSES a perishable claim")
            check(stale not in written,
                  "and nothing reached the database")
            check(cg._facts_cache == [],
                  "and nothing reached the in-process cache either — a refused "
                  "fact must not survive until the next restart")

            check(await cg.remember_fact(lasting) is True,
                  "the model's default path STORES a lasting claim")
            check(lasting in written, "and it reached the database")

            check(await cg.remember_fact(stale, source="operator") is True,
                  "he typed it into the Memory screen: his call, not the guard's")
            check(stale in written, "and it reached the database")

        asyncio.run(go())
    finally:
        cg._memory_ready = real_ready
        cg._db_add_fact_blocking = real_add
        cg._facts_cache = real_cache


def run_the_doors_are_wired() -> None:
    """Root cause #4, asked mechanically: which callers reach the sink, and how?

    Read off the source rather than trusted, because this is exactly the shape that
    has gone wrong repeatedly here — a guard added at one door while a sibling keeps
    its own way in.
    """
    src = open(cg.__file__, encoding="utf-8").read()
    calls = re.findall(r"(?<!def )\bremember_fact\(([^)]*)\)", src)
    # two callers: the `[[REMEMBER: …]]` marker loop, and the /app-fact endpoint
    check(len(calls) == 2,
          f"exactly two callers reach remember_fact (found {len(calls)}): a third "
          "one must decide, in writing, which side of the guard it is on")
    operator = [c for c in calls if "operator" in c]
    check(len(operator) == 1,
          f"exactly one caller declares source='operator' (found {len(operator)}) "
          "— the Memory screen, and nothing else")
    check(cg.remember_fact.__kwdefaults__ == {"source": "model"},
          "the strict behaviour is the DEFAULT, so an extraction path added later "
          "is guarded unless somebody writes down that it should not be")


def run_route_call_site() -> None:
    """`_local_lookups` must go through the resolver, not around it."""
    src = open(cg.__file__, encoding="utf-8").read()
    body = src.split("async def _local_lookups", 1)[1].split("\ndef ", 1)[0]
    check("_far_dest(" in body,
          "_local_lookups resolves its destination through _far_dest")
    # an ASSIGNMENT or a USE, not the bare string: the replacement's own comment
    # names `_FAR_RE` to explain what it replaced, and that prose is the record of
    # why this shape is wrong. A check that forbids naming the mistake would delete
    # the reason it was made.
    check(re.search(r"^_FAR_RE\s*=", src, re.M) is None,
          "the old single-pattern _FAR_RE is no longer defined")
    check("_FAR_RE." not in src and "_FAR_RE(" not in src,
          "and nothing calls it — a dead pattern left reachable is the next "
          "person's shortcut")


def main() -> int:
    run_phrasings()
    run_no_known_places()
    run_origin_is_not_the_destination()
    run_perishable()
    run_the_sink()
    run_the_doors_are_wired()
    run_route_call_site()

    print()
    print(f"{_checks - len(_fails)}/{_checks} passed")
    for why in _fails:
        print(f"  still failing: {why}")
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
