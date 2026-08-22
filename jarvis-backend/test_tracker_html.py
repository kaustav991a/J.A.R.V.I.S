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


def test_long_status_text_wraps_instead_of_overflowing():
    """He opened the page and Tier 0 ran off the side of it.

    The status column started as short marks and grew into whole sentences -- the
    longest is over 400 characters -- while the CSS still said
    `white-space:nowrap` on it. A fixed table with wrappable cells is the fix;
    content decides the height, the container decides the width.
    """
    doc = PAGE.read_text(encoding="utf-8")

    check("table-layout:fixed" in doc,
          "tables are fixed-layout, so one long cell cannot widen the page")
    check(re.search(r"\.st \{ white-space:normal \}", doc) is not None,
          "the status column wraps")
    check("overflow-wrap:anywhere" in doc,
          "and a single long token cannot push a column open")
    check("overflow-x:auto" in doc,
          "wide tables scroll inside their own box, not the page")

    longest = max((len(c) for c in
                   re.findall(r"<td class='st'>(.*?)</td>", doc, re.S)), default=0)
    check(longest > 200,
          f"there really is long status prose to wrap ({longest} chars) — if this "
          f"ever fails, the guard above is no longer load-bearing")


def test_a_done_item_must_say_how_it_was_verified():
    """"Done" without evidence is this project's own worst habit, in a document.

    Seven of session 4's sixteen findings were a claim with nothing behind it. A
    tracker that marks work complete without saying how it was proven is that
    same habit wearing a tie. So every completed ladder item carries a Verified
    column, and this fails if one does not.
    """
    md = TRACKER.read_text(encoding="utf-8")
    ladder = md.split("## 2 · The ladder", 1)[-1].split(chr(10) + "## ", 1)[0]

    naked = []
    for line in ladder.splitlines():
        line = line.strip()
        if not line.startswith("|") or "✅" not in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or not cells[3] or cells[3] == "—":
            naked.append(cells[0] if cells else line[:24])
    check(not naked, f"every completed item states its verification ({naked})")
    check("Verified end to end?" in ladder,
          "the ladder tables carry the Verified column")

    doc = PAGE.read_text(encoding="utf-8")
    check("done items SEALED" in doc,
          "the page shows how many done items are sealed")
    check("class='ver sealed'" in doc, "and marks the sealed ones")


def test_the_open_loops_reach_the_page():
    """"What is verified" is only useful beside "what is not". If section 2b
    lists open loops and the page omits them, the page is flattering."""
    import re as _re

    md = TRACKER.read_text(encoding="utf-8")
    doc = PAGE.read_text(encoding="utf-8")
    check("What is SEALED" in md, "the tracker has the sealed/open section")
    # Scoped to section 2b, the way the generator scopes it. The first version
    # of this check scanned the whole tracker and counted the ship sequence's
    # numbered steps as open loops -- 7 listed, 2 shown, and the page was right.
    sealed_section = md.split("## 2b", 1)[-1].split(chr(10) + "## ", 1)[0]
    listed = _re.findall(r"^\d+\. \*\*(.+?)\*\*", sealed_section, _re.M)
    rendered = _re.findall(r"<div class='loop'><strong>(.*?)</strong>", doc)
    check(len(rendered) >= 1, f"the page renders the open loops ({len(rendered)})")
    check(len(rendered) >= len(listed) or not listed,
          f"and drops none ({len(listed)} listed, {len(rendered)} shown)")


def test_the_markup_is_balanced():
    class Balance(html.parser.HTMLParser):
        # The full HTML void set. `col` was missing, so a perfectly valid
        # <colgroup><col><col></colgroup> read as four mismatched tags — the
        # checker was wrong, not the page.
        VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                "link", "meta", "param", "source", "track", "wbr"}

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
