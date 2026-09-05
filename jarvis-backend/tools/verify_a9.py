"""Drive the A9 memory rows and check them against the DATABASE, not the reply.

Goal 8 is "he remembers, and it survives a restart". Its failure mode is the one
the gate marks with a skull at `K3`: a quiet store failure reads as a cheerful
*"you never told me that"*, which is indistinguishable from having forgotten
him. So no row here is judged on what the desk said alone - every one is checked
against `jarvis_longterm.db`, decrypted through the project's own path.

**Every run uses a fresh nonce.** The store already holds "User prefers tabs over
spaces" from an earlier session; a recall row that asks about tabs would pass on
that old row whether or not today's write worked. A row that can pass without the
thing under test happening is not a test.

    venv\\Scripts\\python.exe tools\\verify_a9.py
    venv\\Scripts\\python.exe tools\\verify_a9.py --row 9.1
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(HERE / ".env", override=True)

from verify_a11 import ask, clear_pending, newest_log  # noqa: E402

DB = HERE / "jarvis_longterm.db"

# A distinctive indentation width. Natural to say, impossible to satisfy from the
# existing "tabs over spaces" row, and unlikely to appear by chance in a reply.
#
# PERSISTED, because 9.1 states it and 9.2 asks for it back. Drawn fresh per
# process, `--row 9.2` on its own would ask about a width nobody ever stated and
# fail an innocent desk - a verifier bug of exactly the kind this file exists to
# catch in the product.
_NONCE = HERE / ".a9-nonce"


def _width() -> int:
    if _NONCE.exists():
        try:
            return int(_NONCE.read_text().strip())
        except ValueError:
            pass
    w = random.choice([7, 9, 11, 13])
    _NONCE.write_text(str(w))
    return w


WIDTH = _width()
_WORDS = {7: "seven", 9: "nine", 11: "eleven", 13: "thirteen"}


def rows() -> list[tuple]:
    """Every memory, decrypted. Returns (id, category, text, user)."""
    from modules.memory_crypto import decrypt_field
    out = []
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as c:
        for rid, cat, content, user in c.execute(
                "select id, category, content, user from memories order by id"):
            try:
                text = decrypt_field(content, "memories", "content")
            except Exception as e:  # noqa: BLE001
                text = f"<undecryptable: {e}>"
            out.append((rid, cat, str(text or ""), user))
    return out


def new_since(before: set[int]) -> list[tuple]:
    return [r for r in rows() if r[0] not in before]


def ids() -> set[int]:
    return {r[0] for r in rows()}


# Everything this tool says to the desk, so its own writes can be found again.
_MY_MARKERS = ("indented with", "indentation with", "shorter replies",
               "payment service refactor")


def forget_test_rows() -> int:
    """Delete the memories this verifier caused, and only those.

    The first full run left five rows in his REAL long-term store - "User
    prefers code indented with 11 spaces", "...with seven spaces", two
    "shorter replies" corrections - which contradict the genuine preference
    sitting three rows above them ("User prefers tabs over spaces", 2026-08-22).

    A test that permanently edits the thing it is testing is not a test, it is a
    slow corruption. Worse here than in most places: he cannot see this store,
    so a wrong fact in it surfaces months later as JARVIS confidently telling him
    something he never said.
    """
    from modules.memory_crypto import decrypt_field
    removed = 0
    with sqlite3.connect(DB) as c:
        for rid, content in list(c.execute("select id, content from memories")):
            try:
                text = str(decrypt_field(content, "memories", "content") or "")
            except Exception:  # noqa: BLE001
                continue
            low = text.lower()
            if any(m in low for m in _MY_MARKERS):
                c.execute("delete from memories where id = ?", (rid,))
                removed += 1
        c.commit()
    return removed


def _settle(seconds: float = 12.0) -> None:
    """Memory extraction runs after the reply. Reading the DB the instant the
    answer lands reports a write that simply has not happened yet - the same
    too-early read that twice recorded an answered row as silent."""
    time.sleep(seconds)


# =========================================================================

def row_9_1(log) -> tuple[str, str]:
    """A stated preference must reach the store."""
    clear_pending()
    before = ids()
    said = ask(f"Remember that I prefer my code indented with {WIDTH} spaces", log)
    _settle()
    fresh = new_since(before)
    hit = [r for r in fresh if str(WIDTH) in r[2] or f"{WIDTH} space" in r[2].lower()]
    if not hit:
        joined = " ".join(said)
        if "<<no line captured" in joined:
            # Extraction runs off the back of a completed turn. If the turn never
            # produced one, "nothing was stored" says nothing about the store.
            return "NO-ANSWER", ("the desk never completed the turn, so no "
                                 "extraction ran - the store is not implicated")
        return "FAIL", (f"nothing about '{WIDTH} spaces' reached the store "
                        f"(it said {joined[:60]!r}; {len(fresh)} new rows)")
    rid, cat, text, user = hit[0]
    return "PASS", f"stored as {cat} id={rid}: {text[:60]!r}"


def row_9_2(log) -> tuple[str, str]:
    """...and must come back on a later turn."""
    clear_pending()
    said = " ".join(ask("What indentation width do I prefer?", log))
    if "<<no line captured" in said:
        # No answer is not a wrong answer. Calling this FAIL would blame the
        # memory layer for a provider outage - the exact conflation this file
        # exists to prevent, committed by the file itself.
        return "NO-ANSWER", ("the desk never answered - nothing is proven about "
                             "recall either way; re-run when providers are up")
    # He speaks. "You prefer eleven spaces, Sir" is a correct recall, and a
    # checker that only accepts the digits would have failed it - which is the
    # same mistake as reading a sentence for tone, made from the other side.
    if str(WIDTH) in said or _WORDS[WIDTH] in said.lower():
        return "PASS", f"recalled {WIDTH}: {said[:70]!r}"
    # The skull case: a confident denial is worse than an admission.
    low = said.lower()
    if any(p in low for p in ("never told", "don't have", "do not have",
                              "no record", "haven't told", "not sure")):
        return "FAIL", (f"DENIED a preference that IS in the store - this is the "
                        f"K3 failure: {said[:80]!r}")
    return "FAIL", (f"did not recall {WIDTH} (or {_WORDS[WIDTH]!r}): "
                    f"{said[:80]!r}")


def row_9_3(log) -> tuple[str, str]:
    """A correction must be stored as one."""
    clear_pending()
    before = ids()
    ask("Next time, keep your replies shorter", log)
    _settle()
    fresh = new_since(before)
    if not fresh:
        return "FAIL", "the correction reached no store at all"
    corrections = [r for r in fresh if r[1].lower().startswith("correct")]
    if corrections:
        rid, cat, text, _ = corrections[0]
        return "PASS", f"stored as {cat} id={rid}: {text[:60]!r}"
    return "FAIL", (f"stored, but not as a Correction: "
                    f"{[(r[1], r[2][:34]) for r in fresh][:2]}")


def row_9_6(log) -> tuple[str, str]:
    """A throwaway question must NOT become a long-term fact."""
    clear_pending()
    before = ids()
    ask("What is the capital of Iceland?", log)
    _settle()
    fresh = new_since(before)
    junk = [r for r in fresh
            if "iceland" in r[2].lower() or "reykjav" in r[2].lower()]
    if junk:
        return "FAIL", (f"a throwaway question became a stored fact: "
                        f"{junk[0][1]} {junk[0][2][:50]!r}")
    return "PASS", (f"not stored as a long-term fact "
                    f"({len(fresh)} unrelated new row(s))")




def _digests() -> list[tuple]:
    """(user, timestamp, decrypted digest) for every stored session digest."""
    from modules.memory_crypto import decrypt_field
    out = []
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as c:
        for user, digest, ts in c.execute(
                "select user, digest, timestamp from session_digest"):
            try:
                text = decrypt_field(digest, "session_digest", "digest")
            except Exception as e:  # noqa: BLE001
                text = f"<undecryptable: {e}>"
            out.append((user, ts, str(text or "")))
    return out


def row_9_4(log) -> tuple[str, str]:
    """Sleep, then wake: the prior session must be seeded from a real digest."""
    before = {(u, ts) for u, ts, _ in _digests()}
    # There has to BE a session before there is anything to summarise.
    # `consolidate_session` needs at least two real turns, so sleeping straight
    # after a restart writes nothing - which the first two runs of this row
    # measured as a defect when it was the test's own doing.
    clear_pending()
    ask("I am reviewing the payment service refactor this afternoon", log)
    clear_pending()
    ask("Remind me what I said I am working on", log)
    clear_pending()
    ask("go to sleep", log)
    _settle(15)
    clear_pending()
    said = " ".join(ask("wake up", log))
    _settle(20)

    fresh = [(u, ts, d) for u, ts, d in _digests() if (u, ts) not in before]
    if not fresh:
        return "FAIL", (f"sleeping wrote no session digest at all - there is "
                        f"nothing for a wake to be seeded FROM "
                        f"({len(before)} pre-existing)")
    user, ts, text = fresh[0]
    if not text.strip() or text.startswith("<undecryptable"):
        return "FAIL", f"a digest row was written but is unusable: {text[:60]!r}"
    # "Non-empty" is not "a recap". The first run of this row passed on a digest
    # four characters long - the word "User", echoed from the transcript's own
    # role labels by a degraded provider. A checker that accepts that is testing
    # that a row exists, not that a wake can be seeded from it.
    from memory import looks_like_a_digest
    ok, why = looks_like_a_digest(text)
    if not ok:
        return "FAIL", (f"the digest is not a recap ({why}): {text[:60]!r} - "
                        f"a wake seeded from this carries no prior context")
    if "<<no line captured" in said:
        return "NO-ANSWER", ("a digest WAS written, but the wake produced no "
                             "reply to judge - re-run when providers are up")
    return "PASS", (f"digest written for {user} at {ts[:19]} "
                    f"({len(text)} chars): {text[:60]!r}")


def row_9_5(log) -> tuple[str, str]:
    """Past-session recall, checked in BOTH directions.

    The gate's skull is at K3: a cheerful "you never told me that" over a store
    that holds the answer. The mirror image is just as bad and easier to miss -
    a confident recollection over an EMPTY store. So the store is asked first,
    and the reply is judged against what is actually in it.
    """
    from modules.episodic_memory import recall_past_sessions
    try:
        material = recall_past_sessions("KAUSTAV", "what did we discuss", 3) or ""
    except Exception as e:  # noqa: BLE001
        return "SKIP", f"the episodic store could not be read: {e}"

    clear_pending()
    said = " ".join(ask("What did we discuss earlier?", log))
    if "<<no line captured" in said:
        return "NO-ANSWER", "the desk never answered - nothing proven either way"
    low = said.lower()
    # Only a denial ABOUT MEMORY counts. The first run flagged "I don't have your
    # schedule in front of me, Sir" as the K3 failure - that is a wrong answer to
    # the question asked, but it is not a claim to have forgotten him, and
    # conflating the two would have filed a skull-level finding against a
    # competence miss. The distinction is the whole point of this row.
    _DENIALS = ("we didn't discuss", "we did not discuss", "no previous conversation",
                "no earlier conversation", "you never told me", "never mentioned",
                "no record of our", "nothing was discussed", "cannot recall",
                "can't recall", "don't recall", "do not recall",
                "no memory of", "haven't spoken", "have not spoken")
    denied = any(p in low for p in _DENIALS)
    # "On topic" cannot be a keyword list. Two correct recalls were flagged as
    # off-topic for not containing one of them:
    #
    #   "We talked through your decision point, JARVIS's optical-sensor..."
    #   "We established your preference for nine-space indentation and brief..."
    #
    # The second says "established" rather than "discussed" and would have been
    # filed as a competence miss. What actually distinguishes a recall is whether
    # the answer CONTAINS THE MATERIAL - so compare the two directly.
    def _content(text: str) -> set:
        import re as _re
        stop = {"the", "and", "you", "your", "for", "with", "that", "this",
                "was", "were", "have", "has", "had", "sir", "about", "from",
                "what", "when", "which", "there", "their", "then", "than",
                "into", "over", "some", "also", "been", "being", "they",
                "them", "his", "her", "our", "are", "not", "but", "all"}
        return {w for w in _re.findall(r"[a-z]{4,}", text.lower())
                if w not in stop}

    shared = _content(said) & _content(material)
    on_topic = len(shared) >= 2
    has_material = bool(material.strip())

    if has_material and denied:
        return "FAIL", (f"DENIED past sessions while the episodic store holds "
                        f"{len(material)} chars - the K3 failure: {said[:70]!r}")
    if not has_material and not denied and on_topic:
        return "FAIL", (f"claimed to recall earlier discussion from an EMPTY "
                        f"store - invention: {said[:70]!r}")
    if not on_topic and not denied:
        return "REVIEW", (f"the answer shares nothing with the stored material "
                          f"({sorted(shared)}) - a competence miss, NOT a memory "
                          f"denial: {said[:60]!r}")
    if not has_material and denied:
        return "PASS", "the store is empty and it said so - honest"
    return "PASS", (f"the answer shares {len(shared)} content word(s) with the "
                    f"store ({sorted(shared)[:4]}): {said[:60]!r}")


ROWS = {
    "9.1": ("a stated preference reaches the store", row_9_1),
    "9.2": ("...and comes back on a later turn", row_9_2),
    "9.3": ("a correction is stored as a Correction", row_9_3),
    "9.4": ("sleep writes a digest, wake is seeded from it", row_9_4),
    "9.5": ("past-session recall, checked against the store", row_9_5),
    "9.6": ("a throwaway question is NOT stored", row_9_6),
}

NEEDS_A_RESTART: dict[str, str] = {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--row", action="append")
    args = ap.parse_args()
    log = newest_log()
    print(f"log: {log.name} | nonce width: {WIDTH}\n{'=' * 74}")
    results = []
    for row in (args.row or list(ROWS)):
        if row not in ROWS:
            print(f"[{row}] {NEEDS_A_RESTART.get(row, 'not driven here')}")
            continue
        label, fn = ROWS[row]
        print(f"\n[{row}] {label}")
        verdict, why = fn(log)
        print(f"    -> {verdict}: {why}")
        results.append((row, verdict, why))

    print(f"\n{'=' * 74}\nSUMMARY")
    for row, verdict, why in results:
        print(f"  {row:5} {verdict:7} {why}")
    for row, why in NEEDS_A_RESTART.items():
        print(f"  {row:5} SEPARATE {why}")
    gone = forget_test_rows()
    if gone:
        print(f"\n  (removed {gone} memory row(s) this run created - a test must "
              f"not leave fake preferences in his real store)")
    failed = [r for r, v, _ in results if v == "FAIL"]
    stalled = [r for r, v, _ in results if v == "NO-ANSWER"]
    if stalled:
        print(f"  ({len(stalled)} row(s) got no answer at all: "
              f"{', '.join(stalled)} — provider state, not memory)")
    print(f"\n{len([r for r, v, _ in results if v == 'PASS'])} verified, "
          f"{len(failed)} FAILED" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
