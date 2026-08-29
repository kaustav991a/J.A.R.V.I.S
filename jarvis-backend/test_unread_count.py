"""Harness: "201 unread emails" for a mailbox holding 66,373.

WHY THIS EXISTS
---------------
F-77, measured 2026-08-29, **while checking a figure in a row that had already
passed** — the discipline written down an hour earlier after F-76: *a row whose
evidence is a sentence needs its numbers checked against the source.*

Row `10.5` passed on this sentence, in this session and in the 2026-08-22 gate
session before it:

    "You have 201 unread emails, Sir."

Every count in `gmail_agent` came from
`messages().list(...).resultSizeEstimate`. **That is not a count.** Measured
against the same account in the same minute:

    maxResults=1    -> resultSizeEstimate  201
    maxResults=5    -> resultSizeEstimate  201
    maxResults=100  -> resultSizeEstimate  201
    maxResults=500  -> resultSizeEstimate  501     <- it tracks the PAGE SIZE
    labels().get("INBOX").messagesUnread  ->  66373

Wrong by **66,172**, and the number would have changed if anyone had tuned a page
size for an unrelated reason. `labels().get` returns the counter Gmail itself
maintains — one call, no pagination, and the number the Gmail UI shows.

Third figure-shaped defect of the day, and the same shape as F-76: a plausible
number, lifted from the wrong field, narrated with total confidence.

WHAT THIS PINS
--------------
Offline and deterministic. The Gmail service is a fake that returns the two
fields with the values really measured, so the test is about which one is read.

  * the count comes from the LABEL, not from a list estimate;
  * `resultSizeEstimate` is not read by any live path — asserted over the source,
    because there were three call sites and fixing two would have left the
    briefing saying one thing and the widget another;
  * an unavailable count is **-1**, never 0 — "you have no unread mail" is a
    claim, and a failed lookup must not be able to make it;
  * the spoken sentence carries the real figure.

Run standalone: `python test_unread_count.py`
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules import gmail_agent as g  # noqa: E402

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


REAL_UNREAD = 66373
ESTIMATE = 201          # what the old code read, at every page size below 500


class _Fake:
    """Gmail, with the two fields at the values actually measured."""

    def __init__(self, label_unread=REAL_UNREAD, raise_on_label=False):
        self._label_unread = label_unread
        self._raise = raise_on_label

    # -- the shape google-api-python-client presents -------------------------
    def users(self):
        return self

    def labels(self):
        return self

    def messages(self):
        return self

    def get(self, userId=None, id=None):
        outer = self

        class _Ex:
            def execute(self_inner):
                if outer._raise:
                    raise RuntimeError("label lookup failed")
                return {"messagesUnread": outer._label_unread}
        return _Ex()

    def list(self, userId=None, labelIds=None, maxResults=None, q=None):
        class _Ex:
            def execute(self_inner):
                return {"messages": [{"id": f"m{i}"} for i in range(maxResults or 1)],
                        "resultSizeEstimate": ESTIMATE}
        return _Ex()


def test_the_count_comes_from_the_label_not_the_estimate():
    got = g._unread_total(_Fake())
    check(got == REAL_UNREAD, f"the real figure is reported: {got}")
    check(got != ESTIMATE,
          "...and NOT the list estimate, which is what said 201 for 66,373")


def test_an_unavailable_count_is_minus_one_never_zero():
    """"You have no unread mail" is a claim. A failed lookup must not make it."""
    got = g._unread_total(_Fake(raise_on_label=True))
    check(got == -1, f"a failed lookup yields -1: {got}")
    check(got != 0, "...never 0, which would read as an empty inbox")


def test_a_genuinely_empty_inbox_still_reads_as_zero():
    check(g._unread_total(_Fake(label_unread=0)) == 0,
          "an inbox that really is empty is still allowed to say so")


def test_no_live_path_reads_the_estimate():
    """There were THREE call sites. Fixing two would have left the briefing and
    the widget disagreeing about the same mailbox."""
    src = (HERE / "modules" / "gmail_agent.py").read_text(encoding="utf-8",
                                                          errors="replace")
    live = [ln for ln in src.splitlines()
            if "resultSizeEstimate" in ln
            and not ln.lstrip().startswith("#")
            and "->" not in ln
            and "`" not in ln]
    check(not live, f"no live code reads resultSizeEstimate: {live}")
    check(src.count("_unread_total(service)") >= 2,
          f"the narrating call sites use the shared counter "
          f"({src.count('_unread_total(service)')})")


def test_the_reason_is_recorded_where_the_next_reader_looks():
    src = (HERE / "modules" / "gmail_agent.py").read_text(encoding="utf-8",
                                                          errors="replace")
    check("F-77" in src, "the finding id is in the module")
    check("66373" in src or "66,373" in src,
          "...with the measurement, so the next reader does not have to trust it")
    check("page size" in src.lower() or "PAGE SIZE" in src,
          "...and what the wrong field actually tracked")


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
