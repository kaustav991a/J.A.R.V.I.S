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
    # Case-insensitive on purpose: this check broke once when the hero copy was
    # reworded from "SEALED" to "sealed". A pin on presentation wording rather
    # than on the FACT is a pin that cries wolf.
    check("done items sealed" in doc.lower(),
          "the page shows how many done items are sealed")
    check(re.search(r"<b>\d+/\d+</b>", doc) is not None,
          "and shows it as a ratio of the done items")
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


def test_the_sealed_table_reaches_the_page():
    """Section 2b holds TWO things: a numbered list of open loops and a table of
    work that is sealed. Only the first was ever rendered, so every sealed row --
    real evidence, present in the tracker -- was invisible on the page. It was
    found by adding a row and noticing the file did not change size, which is not
    a way to find things. Pinned in both directions now."""
    import re as _re

    md = TRACKER.read_text(encoding="utf-8")
    doc = PAGE.read_text(encoding="utf-8")
    section = md.split("## 2b", 1)[-1].split(chr(10) + "## ", 1)[0]
    rows = [ln for ln in section.splitlines()
            if ln.strip().startswith("|") and ln.count("|") >= 4]
    titles = []
    for ln in rows:
        first = ln.strip().strip("|").split("|")[0].strip()
        plain = _re.sub(r"[*`]", "", first).strip()
        if not plain or set(plain) <= set("- :"):
            continue                        # the table's separator row
        if plain.lower() in ("sealed", "evidence", "what"):
            continue                        # its header
        titles.append(plain)
    check(len(titles) >= 5, f"section 2b records {len(titles)} sealed items")
    # Compare against the page's TEXT, not its markup: a title renders as
    # `<strong>0.3</strong> model liveness`, so the words are all present while
    # the phrase never appears contiguously in the raw HTML. The first version of
    # this check compared raw and reported six false absences.
    # Unescape too: an apostrophe in a title renders as &#x27;, so a raw text
    # comparison reports "the vision cascade's middle leg" as absent when it is
    # right there on the page.
    import html as _html
    text = " ".join(_html.unescape(_re.sub(r"<[^>]*>", " ", doc)).split())
    missing = [t for t in titles if " ".join(t.split()) not in text]
    check(not missing, f"and every one of them appears on the page ({missing})")
    check("Sealed —" in doc or "Sealed &" in doc,
          "the page gives them a heading of their own")


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


def test_the_page_opens_on_goals_not_on_the_ladder():
    """The reordering is the whole point of the goal view, so it is pinned.

    A dashboard is opened to answer "what is different for him when this is done",
    and the ladder answers a different question — how the work is organised. If the
    ladder drifts back above the goals the page still contains everything and has
    quietly stopped answering the first question, which no other check would see.
    """
    doc = PAGE.read_text(encoding="utf-8")
    g = doc.find("<h2>The goals")
    l = doc.find("<h2>The ladder")
    check(g != -1, "the page has a goals section")
    check(l != -1, "the page still has the ladder")
    check(g != -1 and l != -1 and g < l,
          f"and the goals come FIRST (goals at {g}, ladder at {l})")


def test_no_goal_percentage_preempts_the_ladder_headline():
    """Why the goal cards print counts and not percentages.

    `test_every_number_on_the_page_comes_from_the_tracker` reads the FIRST `>NN%<`
    on the page as the ladder headline. A percentage rendered inside a goal card —
    above the ladder now — would silently retarget that check at a different
    number, and it would keep passing while measuring the wrong thing. This states
    the coupling out loud so the next person to add a `%` to a goal card is told
    why it broke.
    """
    doc = PAGE.read_text(encoding="utf-8")
    first = re.search(r">(\d+)%<", doc)
    check(first is not None, "the page has a headline percentage")
    if first:
        hero = doc.find('class="panel hero"')
        check(hero != -1 and first.start() > hero,
              "the first percentage on the page is inside the hero block")
        goals_at = doc.find("<h2>The goals")
        check(goals_at == -1 or first.start() < goals_at,
              "and it precedes the goals section, so no goal card can claim it")


def test_every_goal_and_member_reaches_the_page():
    """A goal that groups work invisibly is worse than no grouping at all.

    The generator refuses to build unless every ladder item and gate batch belongs
    to exactly one goal — but that says nothing about whether the RENDER then drew
    them. This checks the output, independently of the generator's own bookkeeping.
    """
    md = TRACKER.read_text(encoding="utf-8")
    doc = PAGE.read_text(encoding="utf-8")

    section0 = md.split("## 0 · The goals", 1)[-1].split(chr(10) + "## ", 1)[0]
    goal_rows = [ln for ln in section0.splitlines()
                 if ln.startswith("| **") and ln.count("|") >= 4]
    check(len(goal_rows) >= 5, f"section 0 lists goals ({len(goal_rows)})")

    members = []
    for ln in goal_rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        members += re.findall(r"`([^`]+)`", cells[2])
    check(len(members) >= 20, f"and they name members ({len(members)})")
    check(len(members) == len(set(members)),
          "no member is claimed twice in the markdown")

    rendered_goals = re.findall(r"<section class='goal[^']*'><h3><span>(.*?)</span>", doc)
    check(len(rendered_goals) == len(goal_rows),
          f"every goal is drawn ({len(goal_rows)} listed, "
          f"{len(rendered_goals)} drawn)")

    # each member must appear as an id cell inside a goal card
    goals_blob = "".join(re.findall(r"<section class='goal.*?</section>", doc, re.S))
    ids_in_goals = re.findall(r"<td class='id'>(.*?)</td>", goals_blob)
    stripped = [re.sub(r"<[^>]+>", "", i) for i in ids_in_goals]
    missing = [m for m in members if m not in stripped]
    check(not missing, f"and every member is drawn inside one ({missing[:4]})")


def test_a_partly_ticked_batch_is_not_shown_as_done():
    """The goal cards found a flattering bug that the flat gate table had hidden.

    `_classify` looks for a mark ANYWHERE in a cell, so `✅ 10.2, 10.5, 10.7, 10.9`
    — four rows of nine — read as DONE. In the gate table that was one wrong
    colour. Under a goal card it became a false CLAIM: the goal owning
    `A11 information` rendered **3/3 held** with a full bar while five of that
    batch's nine rows had never been run. Aggregation is what turned a mild
    imprecision into the exact thing this document forbids.

    So `_gate_kind` requires the state to say `all N` with `N` equal to the row
    count. Driven against the real generator's own function rather than inferred
    from the page, and then confirmed on the page.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_gen", GEN)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    # The state glyphs stay in the DATA and out of the printed label: this harness
    # does not import the UTF-8 stdout shim that `cloud_gateway` installs, and a
    # cp1252 console raises UnicodeEncodeError on a tick. A test that cannot print
    # its own result is a broken test, whatever it was checking.
    TICK, WARN, BOX = "\u2705", "\u26a0\ufe0f", "\u2610"
    cases = [
        ("5", f"{TICK} all 5", gen.DONE, "'all 5' over 5 rows is done"),
        ("5", f"{TICK} 3 of 5 (`1.5` needs a real Ctrl+C)", gen.PARTIAL,
         "'3 of 5' is not done"),
        ("9", f"{TICK} 10.2, 10.5, 10.7, 10.9", gen.PARTIAL,
         "THE CASE THAT RENDERED AS 3/3 HELD: four named rows out of nine"),
        ("5", f"{TICK} 5 via the text door", gen.PARTIAL,
         "PASS-SUB still owes its own door, which is the tracker's own rule"),
        ("5", f"{TICK} 4, `4.3` owed - patch stages and never applies", gen.PARTIAL,
         "one owed row keeps the batch open"),
        ("14", f"{BOX} none", gen.TODO, "nothing run"),
        ("2", f"{WARN} refused, but via the wrong path - F-57", gen.PARTIAL,
         "a warning is never done"),
        ("5", f"{TICK} all 6", gen.PARTIAL,
         "'all 6' over 5 rows is a mismatch, not a pass - the count has to agree"),
    ]
    for rows, state, want, why in cases:
        got = gen._gate_kind(rows, state)
        # `state` is deliberately NOT interpolated - see the note above.
        check(got == want, f"{rows} rows -> {got} (want {want}): {why}")

    # and the page must not carry a goal claiming completeness while the tracker
    # says otherwise
    doc = PAGE.read_text(encoding="utf-8")
    md = TRACKER.read_text(encoding="utf-8")
    held = re.findall(r"<section class='goal held'>", doc)
    gate_section = md.split("## 3 · The gate", 1)[-1].split(chr(10) + "## ", 1)[0]
    all_done = re.findall(r"\|\s*✅\s*all\s+\d+\s*\|", gate_section)
    check(len(held) == 0 or len(all_done) > 0,
          f"a goal is only 'held' when a batch really says 'all N' "
          f"({len(held)} held, {len(all_done)} batches fully ticked)")


def test_the_generator_refuses_an_ungrouped_row():
    """Driven, not asserted — the refusal is the safety property of the goal view.

    A row belonging to no goal vanishes from the view while still counting in the
    totals, so the page's percentage and the page's list disagree and it looks
    complete BECAUSE something is missing. Reading the page cannot reveal that, so
    the build is the only place to catch it — and a guard nothing exercises is a
    claim, which is this project's own worst habit.

    Run against a COPY in a temp tree, so a failure here cannot leave the real
    tracker mutated.
    """
    import shutil
    import tempfile

    src_md = TRACKER.read_text(encoding="utf-8")
    # drop one ladder id from whichever goal owns it
    m = re.search(r"`(3\.2|0\.3|1\.1)`", src_md.split("## 1 ·", 1)[0])
    check(m is not None, "there is a member id in section 0 to remove")
    if not m:
        return
    broken = src_md.replace(m.group(0), "", 1)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="jarvis-tracker-"))
    try:
        (tmp / "tools").mkdir()
        shutil.copy2(GEN, tmp / "tools" / GEN.name)
        (tmp / "JARVIS_TRACKER.md").write_text(broken, encoding="utf-8")
        r = subprocess.run([sys.executable, str(tmp / "tools" / GEN.name)],
                           capture_output=True, text=True, cwd=str(tmp))
        out = (r.stdout or "") + (r.stderr or "")
        check(r.returncode != 0,
              f"the build FAILS on an ungrouped row (exit {r.returncode})")
        check("does not cover the work" in out,
              "and says the grouping does not cover the work")
        check(m.group(1) in out, f"naming the row it dropped ({m.group(1)})")
        check(not (tmp / "tracker.html").exists(),
              "and writes no page at all, rather than a thinner one")

        # and the same tree builds clean once the grouping is whole again
        (tmp / "JARVIS_TRACKER.md").write_text(src_md, encoding="utf-8")
        r2 = subprocess.run([sys.executable, str(tmp / "tools" / GEN.name)],
                            capture_output=True, text=True, cwd=str(tmp))
        check(r2.returncode == 0,
              f"and the unmodified tracker builds clean ({r2.returncode}) — so "
              f"the refusal above was the missing member, not the temp tree")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
