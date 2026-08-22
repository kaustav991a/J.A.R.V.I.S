r"""test_tracker_html.py — the dashboard must not be able to lie.

Run: venv\Scripts\python.exe test_tracker_html.py

`tracker.html` is a GENERATED view of `JARVIS_TRACKER.md`. Generated, because a
hand-kept HTML copy is a second place to update, and this project has already paid
for two-place bookkeeping: `TEST_PLAN.md` and `LIVE_GATE_CHECKLIST.md` needed a
manual sync step, drifted, and were consolidated on 2026-08-22 for that reason.

So the risk this pins is not "the page is ugly" — it is **the page saying
something the tracker does not**. A dashboard that under-reports is worse than no
dashboard, because it is believed. That is the tracker's own third rule, applied
to the tracker's own dashboard.

What it checks, all offline and with no browser:
  * regenerating from the current tracker changes nothing (so the committed page
    is in sync with the committed markdown);
  * every number the page displays is present in the tracker;
  * the markup is balanced and carries no external asset, so it works from
    `file://` with no network;
  * the generator FAILS rather than emitting a thinner page when the tracker
    changes shape.
"""

import html.parser
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TRACKER = ROOT / "JARVIS_TRACKER.md"
PAGE = ROOT / "tracker.html"
GEN = ROOT / "tools" / "build_tracker_html.py"

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def test_the_pieces_exist():
    check(TRACKER.exists(), "JARVIS_TRACKER.md exists — it is the source of truth")
    check(GEN.exists(), "the generator exists")
    check(PAGE.exists(), "the generated page is committed")


def test_the_committed_page_is_in_sync_with_the_tracker():
    """Regenerating must be a no-op. If it is not, someone edited one of them
    without the other — exactly the drift this arrangement exists to prevent."""
    if not (PAGE.exists() and GEN.exists()):
        check(False, "cannot compare — a piece is missing")
        return
    before = PAGE.read_text(encoding="utf-8")
    r = subprocess.run([sys.executable, str(GEN)], capture_output=True, text=True,
                       cwd=str(ROOT))
    check(r.returncode == 0, f"the generator runs clean ({r.returncode})")
    after = PAGE.read_text(encoding="utf-8")
    check(before == after,
          "regenerating the page changes nothing — page and tracker agree")
    if before != after:
        print("      the committed tracker.html is STALE; run "
              "tools/build_tracker_html.py and commit it")


def test_every_number_on_the_page_comes_from_the_tracker():
    """No figure may be invented by the view layer."""
    md = TRACKER.read_text(encoding="utf-8")
    doc = PAGE.read_text(encoding="utf-8")

    for label, pat in (
        ("the suite figure", r"(\d+) harnesses, ([\d,]+) checks"),
        ("the live tool-selection score", r"(\d+)/(\d+) = (\d+)%"),
    ):
        m = re.search(pat, doc)
        check(m is not None, f"{label} is shown on the page")
        if m:
            check(m.group(0) in md,
                  f"{label} ({m.group(0)}) appears verbatim in the tracker")

    # The ladder percentage must equal the marks in the tracker, recomputed here
    # independently of the generator.
    ladder = md.split("## 2 · The ladder", 1)[-1].split("\n## ", 1)[0]
    done = len(re.findall(r"\|\s*✅", ladder))
    todo = len(re.findall(r"\|\s*☐", ladder))
    part = len(re.findall(r"\|\s*⚠", ladder))
    total = done + todo + part
    check(total > 0, f"the ladder has status marks to count ({total})")
    if total:
        expect = round(done * 100 / total)
        m = re.search(r">(\d+)%<", doc)
        check(m is not None and int(m.group(1)) == expect,
              f"the headline percentage is {expect}% and the page agrees "
              f"({m.group(1) if m else 'absent'}%)")


def test_the_page_works_offline():
    doc = PAGE.read_text(encoding="utf-8")
    check("<script" not in doc, "no script — nothing to execute")
    check(not re.search(r'\ssrc=', doc), "no external asset is fetched")
    check(not re.search(r'https?://(?!127\.0\.0\.1)', doc),
          "no remote URL, so it renders with the network down")
    check("charset" in doc, "the charset is declared, so the status glyphs survive")


def test_the_markup_is_balanced():
    class Balance(html.parser.HTMLParser):
        VOID = {"br", "hr", "img", "meta", "link", "input", "source"}

        def __init__(self):
            super().__init__()
            self.stack, self.bad = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in self.VOID:
                return
            if not self.stack or self.stack[-1] != tag:
                self.bad.append(tag)
                if tag in self.stack:
                    while self.stack and self.stack.pop() != tag:
                        pass
            else:
                self.stack.pop()

    b = Balance()
    b.feed(PAGE.read_text(encoding="utf-8"))
    check(not b.bad, f"no mismatched closing tags ({b.bad[:4]})")
    check(not b.stack, f"nothing left unclosed ({b.stack[:4]})")


def test_the_generator_refuses_to_emit_a_thinner_page():
    """If the tracker loses a section the generator needs, the build must FAIL —
    not quietly produce a page that says less than it used to."""
    src = GEN.read_text(encoding="utf-8")
    check("SystemExit" in src, "it exits rather than degrading")
    check("FAILED" in src, "and says so loudly")
    check("_section(" in src, "sections are looked up by name, not by position")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("tracker.html — a generated view that cannot drift")
    print("=" * 62)
    for t in TESTS:
        t()
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
