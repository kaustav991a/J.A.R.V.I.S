r"""build_tracker_html.py — render JARVIS_TRACKER.md as a local dashboard.

Run:  jarvis-backend\venv\Scripts\python.exe tools\build_tracker_html.py
Out:  tracker.html   (open it directly; no server, no network, no assets)

WHY IT IS GENERATED AND NOT WRITTEN
-----------------------------------
`JARVIS_TRACKER.md` stays the single source of truth. A hand-maintained HTML copy
would be a second place to update, and this project has already paid for
two-place bookkeeping once: `TEST_PLAN.md` and `LIVE_GATE_CHECKLIST.md` required a
manual sync step, drifted, and were consolidated on 2026-08-22 for exactly that
reason. So the page is derived, and regenerating it is one command.

It FAILS LOUDLY if a section it needs has gone missing, rather than emitting a
page that quietly says less than it used to. A dashboard that under-reports is
worse than no dashboard, because it is believed — the same rule the tracker
itself opens with.

WHAT THE PAGE OPENS ON
----------------------
Goals, not tiers. A tier and a batch describe how the work is *organised*; a goal
describes what is different for him when it is finished, which is the question
somebody opening a dashboard is actually asking. Section 0 of the tracker owns the
goals and their membership, and `_check_membership` REFUSES TO BUILD unless every
ladder item and every gate batch belongs to exactly one of them.

That refusal is the point. A grouped page has one failure mode that matters: a row
belonging to no goal vanishes from the view while still counting in the totals, so
the percentage and the list disagree and the page looks complete *because*
something is missing from it. No amount of reading the page reveals that, so the
build is where it has to be caught.

WHAT IT COUNTS
--------------
Status comes from the marks already in the markdown: ✅ done, ⚠️ partial,
☐ / ⬜ not started. Nothing is inferred and no number is invented here; if a
percentage looks wrong, the fix is in the tracker.
"""

from __future__ import annotations

import html
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACKER = ROOT / "JARVIS_TRACKER.md"
OUT = ROOT / "tracker.html"

DONE, PARTIAL, TODO = "done", "partial", "todo"


def _classify(cell: str) -> str | None:
    if "✅" in cell:
        return DONE
    if "⚠️" in cell or "⚠" in cell:
        return PARTIAL
    if "☐" in cell or "⬜" in cell:
        return TODO
    return None


def _gate_kind(rows_cell: str, state_cell: str) -> str:
    """How complete a gate BATCH is — which is not what `_classify` answers.

    `_classify` looks for a mark anywhere in the cell, so `✅ 10.2, 10.5, 10.7,
    10.9` — four rows ticked out of nine — read as DONE. In the flat gate table
    that was a wrong colour on one row. Under the goal cards it became a false
    CLAIM: the goal owning `A11 information` rendered "3/3 held" with a full bar
    while five of that batch's nine rows had never been run. A dashboard that
    under-reports is worse than none because it is believed, and this is the same
    thing pointing the other way.

    So a batch counts as DONE only when its state says `all N` and `N` is the row
    count. Anything else carrying a tick or a warning is PARTIAL — including
    `✅ 5 via the text door`, which is right: the tracker's own standing rule is
    that every PASS-SUB row still owes its own door.
    """
    base = _classify(state_cell)
    if base is None:
        return TODO
    if base != DONE:
        return base
    want = re.search(r"\d+", rows_cell or "")
    got = re.search(r"\ball\s+(\d+)\b", state_cell, re.I)
    if got and want and got.group(1) == want.group(0):
        return DONE
    return PARTIAL


def _rows(section: str) -> list[list[str]]:
    """Every table row in a markdown section, as a list of stripped cells."""
    out = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|--") or set(line) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 2 and not all(re.fullmatch(r"-{2,}|:?-+:?", c) for c in cells):
            out.append(cells)
    return out


def _section(md: str, heading_startswith: str) -> str:
    """The text of one `## ` section, by the start of its heading."""
    parts = re.split(r"\n(?=## )", md)
    for p in parts:
        first = p.splitlines()[0] if p.splitlines() else ""
        if first.startswith("## ") and heading_startswith.lower() in first.lower():
            return p
    raise SystemExit(
        f"[build_tracker_html] FAILED: no '## …{heading_startswith}…' section in "
        f"{TRACKER.name}. The tracker changed shape; fix this generator rather "
        f"than shipping a page that says less than the tracker does.")


def _strip_md(text: str) -> str:
    """Markdown inline -> HTML, conservatively."""
    t = html.escape(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def _decision(md: str) -> tuple[str, str] | None:
    """The standing decision from §0.5, as (heading, the one bold sentence).

    Derived rather than written into this file, for the reason the subtitle
    already gives: a second place to update is how the last set of docs drifted.
    Absent when there is no such section, which is the ordinary state — a
    decision that governs every session is not a permanent fixture of the page.
    """
    head = re.search(r"^## 0\.5 · (.+)$", md, re.M)
    if not head:
        return None
    body = _section(md, "## 0.5")
    lines = body.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("**")), None)
    if start is None:
        return None
    # the whole PARAGRAPH, not the first line of it: the source is hard-wrapped,
    # and a bold sentence cut at the wrap reads as a sentence that stops halfway
    para = []
    for ln in lines[start:]:
        if not ln.strip():
            break
        para.append(ln.strip())
    return _strip_md(head.group(1)), _strip_md(" ".join(para))


def _goals(md: str) -> list[dict]:
    """The goal table from section 0, in the order it is written.

    Members are the backticked tokens in the third cell — the ladder's own ids and
    the gate's own batch labels, so a goal never renames the thing it groups.
    """
    out = []
    for cells in _rows(_section(md, "The goals")):
        if len(cells) < 3 or cells[0].lower().startswith("goal"):
            continue
        members = re.findall(r"`([^`]+)`", cells[2])
        if not members:
            raise SystemExit(
                f"[build_tracker_html] FAILED: the goal {cells[0]!r} lists no "
                f"members. A goal that groups nothing is a heading, and the page "
                f"would show it as complete.")
        # A 4th column, when present, is the BUG-FREE claim: a date means the
        # goal has been driven top to bottom with nothing outstanding. It is
        # written by hand and then checked by `_check_bugfree`, which refuses to
        # build if the claim and the member states disagree. An indicator that
        # cannot be wrong is worth having; one that can is worse than none.
        claim = _strip_md(cells[3]).strip() if len(cells) > 3 else ""
        out.append({"title": cells[0], "why": cells[1], "members": members,
                    "bugfree": claim})
    if not out:
        raise SystemExit(
            "[build_tracker_html] FAILED: section 0 has no goal rows. The page "
            "opens on goals; without them it would silently fall back to the "
            "ladder, which is the view this arrangement replaced.")
    return out


def _check_bugfree(goals: list[dict], by_id: dict, by_batch: dict) -> None:
    """A goal may only claim BUG-FREE when every member is actually done.

    The whole project's failure mode, stated once more: a page that says a thing
    is finished because someone wrote that it was finished. The membership check
    already refuses to build on an ungrouped row; this refuses to build on a
    green badge over an unfinished goal, which is the same lie with a nicer
    colour.
    """
    liars = []
    for g in goals:
        claim = (g.get("bugfree") or "").strip()
        if not claim or claim in ("-", "\u2014", "\u2013"):
            continue
        unfinished = []
        for m in g["members"]:
            item = by_id.get(m) or by_batch.get(m)
            if item is None:
                continue  # membership check owns this one
            if item["kind"] != DONE:
                unfinished.append(m)
        if unfinished:
            liars.append(f"  {g['title']!r} claims BUG-FREE ({claim}) but "
                         f"{len(unfinished)} member(s) are not done: "
                         f"{', '.join(unfinished)}")
    if liars:
        raise SystemExit(
            "[build_tracker_html] FAILED: a goal claims to be bug-free while "
            "its own rows say otherwise.\n" + "\n".join(liars) +
            "\n  Either finish the rows or clear the claim. The badge exists to "
            "be trusted at a glance, which it cannot be if it can be wrong.")


def _check_membership(goals: list[dict], tiers: list[dict],
                      gate: list[dict]) -> dict:
    """Every ladder item and gate batch in EXACTLY one goal, or the build fails.

    This is the whole reason the grouping is safe to trust. A grouped page has one
    failure mode that matters: a row belonging to no goal vanishes from the view
    while still counting in the totals, so the percentage and the list disagree and
    the page looks complete *because* something is missing from it. There is no way
    to notice that by reading the page — the only defence is to refuse to build.

    Returns member label -> goal title.
    """
    ladder = [i["id"] for t in tiers for i in t["items"]]
    batches = [g["batch"] for g in gate]
    universe = list(dict.fromkeys(ladder + batches))

    owner: dict = {}
    twice: list[str] = []
    for g in goals:
        for m in g["members"]:
            if m in owner:
                twice.append(f"{m!r} (in {owner[m]!r} and {g['title']!r})")
            else:
                owner[m] = g["title"]

    orphans = [m for m in universe if m not in owner]
    unknown = [m for m in owner if m not in universe]

    problems = []
    if orphans:
        problems.append("belong to NO goal, so a grouped page would drop them "
                        "while still counting them:\n    " + "\n    ".join(orphans))
    if unknown:
        problems.append("are named by a goal but exist in neither the ladder nor "
                        "the gate — a typo, or something renamed:\n    "
                        + "\n    ".join(unknown))
    if twice:
        problems.append("belong to TWO goals, so they would be counted twice:\n    "
                        + "\n    ".join(twice))
    if problems:
        raise SystemExit(
            "[build_tracker_html] FAILED: the goal grouping in section 0 does not "
            "cover the work.\n  These " + "\n  These ".join(problems)
            + "\n\n  Fix section 0 of JARVIS_TRACKER.md. Every ladder id and every "
              "gate batch label must appear under exactly one goal.")
    return owner


def main() -> int:
    if not TRACKER.exists():
        raise SystemExit(f"[build_tracker_html] {TRACKER} not found")
    md = TRACKER.read_text(encoding="utf-8")

    # ── the measured header numbers ────────────────────────────────────────
    state_rows = [r for r in _rows(_section(md, "Where JARVIS actually is"))
                  if len(r) >= 2 and r[0] and not r[0].startswith("---")]
    state = [(r[0], r[1], r[2] if len(r) > 2 else "") for r in state_rows
             if r[0].lower() not in ("", "measured", "how it was measured")]

    # ── the ladder ─────────────────────────────────────────────────────────
    ladder_md = _section(md, "The ladder")
    tiers = []
    for block in re.split(r"\n(?=### )", ladder_md):
        head = block.splitlines()[0] if block.splitlines() else ""
        if not head.startswith("### "):
            continue
        items, scores = [], []
        for cells in _rows(block):
            if len(cells) < 2:
                continue
            # A competence row looks like  | calendar | 0/3 | 🔴 |  — the second
            # cell is a score, not a description. The generator used to skip
            # these, so the one table that says WHY tier 2 exists never reached
            # the page.
            m = re.fullmatch(r"\**(\d+)/(\d+)\**", cells[1].strip())
            if m:
                got, want = int(m.group(1)), int(m.group(2))
                scores.append({"what": cells[0], "got": got, "want": want,
                               "pct": round(got * 100 / want) if want else 0})
                continue
            if len(cells) < 3:
                continue
            ident, what, status = cells[0], cells[1], cells[2]
            verified = cells[3] if len(cells) > 3 else ""
            kind = _classify(status)
            if kind is None:
                continue
            sealed = "SEALED" in verified.upper()
            items.append({"id": ident, "what": what, "status": status,
                          "kind": kind, "verified": verified, "sealed": sealed})
        if items or scores:
            tiers.append({"title": head[4:].strip(), "items": items,
                          "scores": scores})
    if not tiers:
        raise SystemExit("[build_tracker_html] FAILED: the ladder has no "
                         "status-marked rows. Did the marks change?")

    # ── the gate ───────────────────────────────────────────────────────────
    gate = []
    for cells in _rows(_section(md, "The gate")):
        if len(cells) < 4 or cells[0].lower().startswith("batch"):
            continue
        gate.append({"batch": cells[0], "rows": cells[1],
                     "needs": cells[2], "state": cells[3],
                     # NOT _classify: see _gate_kind. A batch with four of nine
                     # rows ticked is not done, and a goal card aggregating it
                     # would say "held".
                     "kind": _gate_kind(cells[1], cells[3])})

    # ── the two open loops, from section 2b ───────────────────
    sealed_md = _section(md, "What is SEALED")
    open_loops, cur = [], None
    for line in sealed_md.splitlines():
        m = re.match(r'^(\d+)\. \*\*(.+?)\*\*(.*)$', line.strip())
        if m:
            if cur:
                open_loops.append(cur)
            cur = {"title": m.group(2), "body": m.group(3).strip()}
        elif cur is not None and line.startswith("   "):
            cur["body"] += " " + line.strip()
        elif cur is not None and not line.strip():
            open_loops.append(cur)
            cur = None
    if cur:
        open_loops.append(cur)

    # ── the sealed table itself, from the same section ─────────────────────
    # The open-loop list above was the only thing read out of section 2b, so
    # everything RECORDED AS SEALED there was invisible on the page: work with
    # real evidence behind it, present in the tracker, absent from the view of
    # the tracker. Found by adding a sealed row and noticing the page did not
    # change size.
    sealed_rows = [c for c in _rows(sealed_md)
                   if len(c) >= 3 and "sealed" not in c[0].lower()]
    if not sealed_rows:
        raise SystemExit("build_tracker_html: section 2b has no sealed table "
                         "rows — the parser or the heading changed")

    # ── findings + ship gates ──────────────────────────────────────────────
    findings = [c for c in _rows(_section(md, "Open findings"))
                if len(c) >= 3 and not c[0].lower().startswith("id")]
    ship = []
    for line in _section(md, "Ship").splitlines():
        m = re.match(r"^- (☐|✅|⚠️?)\s+(.*)$", line.strip())
        if m:
            ship.append({"mark": m.group(1), "what": m.group(2),
                         "kind": _classify(m.group(1)) or TODO})

    counts = {DONE: 0, PARTIAL: 0, TODO: 0}
    for t in tiers:
        for i in t["items"]:
            counts[i["kind"]] += 1
    total = sum(counts.values()) or 1
    pct_done = round(counts[DONE] * 100 / total)

    # The gate figure is the one that matters most, so it gets its own stat
    # rather than being a row in a table the eye slides past.
    gate_stat = None
    m = re.search(r"gate rows ticked \|\s*\*\*~?(\d+) of (\d+)\*\*\s*\((\d+)%\)", md)
    if m:
        gate_stat = {"done": int(m.group(1)), "total": int(m.group(2)),
                     "pct": int(m.group(3))}

    stamp = re.search(r"^## 1 · Where JARVIS actually is — (.+)$", md, re.M)
    asof = stamp.group(1).strip() if stamp else "see tracker"

    goals = _goals(md)
    goal_owner = _check_membership(goals, tiers, gate)
    _check_bugfree(goals, {i["id"]: i for t in tiers for i in t["items"]},
                   {b["batch"]: b for b in gate})

    doc = _render(state, tiers, gate, findings, ship, counts, pct_done, asof,
                  gate_stat, open_loops, sealed_rows, goals, _decision(md))
    OUT.write_text(doc, encoding="utf-8")
    print(f"[build_tracker_html] wrote {OUT.relative_to(ROOT)}  "
          f"({len(doc):,} bytes)")
    print(f"[build_tracker_html] ladder: {counts[DONE]} done, "
          f"{counts[PARTIAL]} partial, {counts[TODO]} to do  -> {pct_done}%")
    print(f"[build_tracker_html] goals: {len(goals)}, covering "
          f"{len(goal_owner)} ladder items and gate batches")
    return 0


def _bar(counts: dict, total: int) -> str:
    d = counts[DONE] * 100 / total
    p = counts[PARTIAL] * 100 / total
    return (f'<div class="bar"><span class="d" style="width:{d:.1f}%"></span>'
            f'<span class="p" style="width:{p:.1f}%"></span></div>')


def _render(state, tiers, gate, findings, ship, counts, pct_done, asof,
            gate_stat=None, open_loops=None, sealed_rows=(),
            goals=(), decision=None) -> str:
    total = sum(counts.values()) or 1

    # Above the percentage, because it decides what may be worked on at all, and
    # a page that shows the score while hiding the rule is the more misleading of
    # the two.
    # `_strip_md` has already escaped these; escaping again prints the markup
    decision_html = ("" if not decision else
                     f'<div class="panel hold"><b>{decision[0]}</b>'
                     f'<span>{decision[1]}</span></div>')

    # The gate is the number that decides whether JARVIS is finished, so it sits
    # beside the ladder percentage instead of inside a table.
    sealed_done = sum(1 for t in tiers for i in t["items"]
                      if i["kind"] == DONE and i.get("sealed"))
    all_done = sum(1 for t in tiers for i in t["items"] if i["kind"] == DONE)

    if gate_stat:
        gate_block = (
            f'<div class="stat alt"><b>{gate_stat["pct"]}%</b>'
            f'<span>gate — {gate_stat["done"]} of {gate_stat["total"]} rows '
            f'ticked</span></div>')
    else:
        gate_block = ""

    state_html = "".join(
        f"<tr><td>{_strip_md(k)}</td><td class='num'>{_strip_md(v)}</td>"
        f"<td class='how'>{_strip_md(h)}</td></tr>"
        for k, v, h in state)

    tiers_html = []
    for t in tiers:
        done = sum(1 for i in t["items"] if i["kind"] == DONE)
        n = len(t["items"])
        rows = "".join(
            f"<tr class='{i['kind']}'><td class='id'>{_strip_md(i['id'])}</td>"
            f"<td>{_strip_md(i['what'])}"
            + (f"<div class='ver {'sealed' if i['sealed'] else 'unsealed'}'>"
               f"<span class='vb'>{'SEALED' if i['sealed'] else 'verified?'}</span>"
               f"{_strip_md(i['verified'])}</div>"
               if i.get("verified") and i["verified"] != "—" else "")
            + f"</td>"
            f"<td class='st'>{_strip_md(i['status'])}</td></tr>"
            for i in t["items"])
        scores_html = ""
        if t.get("scores"):
            bars = "".join(
                f"<tr><td class='sw'>{_strip_md(sc['what'])}</td>"
                f"<td class='num'>{sc['got']}/{sc['want']}</td>"
                f"<td class='sbar'><span style='width:{sc['pct']}%;"
                f"background:{'var(--done)' if sc['pct'] >= 80 else ('var(--part)' if sc['pct'] >= 50 else 'var(--bad)')}'>"
                f"</span></td></tr>"
                for sc in t["scores"])
            got = sum(sc["got"] for sc in t["scores"])
            want = sum(sc["want"] for sc in t["scores"]) or 1
            scores_html = (
                f"<div class='scores'><div class='shead'>live tool selection "
                f"— <strong>{got}/{want}</strong> "
                # round(), not //: the tracker says 56% for 19/34 and a page
                # that renders 55% is the drift this arrangement exists to stop.
                f"({round(got * 100 / want)}%), by category</div>"
                f"<table>{bars}</table></div>")
        tiers_html.append(
            f"<section class='tier'><h3>{_strip_md(t['title'])}"
            f"<span class='tally'>{done}/{n}</span></h3>"
            f"<div class='tw'><table>"
            f"<colgroup><col class='c1'><col class='c2'><col class='c3'></colgroup>"
            f"{rows}</table></div>{scores_html}</section>")

    # ── the goals, which the page now opens on ─────────────────────────
    #
    # Percentages are deliberately NOT printed here. Two reasons, and the second
    # is the load-bearing one: a gate batch is a bundle of rows at differing
    # completion, so one number over mixed units would be false precision — and
    # `test_tracker_html` reads the FIRST `>NN%<` on the page as the ladder
    # headline, so a goal percentage above it would silently retarget that check.
    by_id = {i["id"]: i for t in tiers for i in t["items"]}
    by_batch = {g["batch"]: g for g in gate}

    goals_html = []
    for g in goals or ():
        mrows, kinds = [], []
        for m in g["members"]:
            item = by_id.get(m)
            if item is not None:
                kinds.append(item["kind"])
                mrows.append(
                    f"<tr class='{item['kind']}'>"
                    f"<td class='id'>{_strip_md(m)}</td>"
                    f"<td class='gm'>{_strip_md(item['what'])}</td>"
                    f"<td class='st'>{_strip_md(item['status'])}</td></tr>")
                continue
            b = by_batch.get(m)
            if b is not None:
                kinds.append(b["kind"])
                mrows.append(
                    f"<tr class='{b['kind']}'>"
                    f"<td class='id'>{_strip_md(m)}</td>"
                    f"<td class='gm'>{_strip_md(b['rows'])} rows — needs "
                    f"{_strip_md(b['needs'])}</td>"
                    f"<td class='st'>{_strip_md(b['state'])}</td></tr>")
                continue
            # unreachable: _check_membership refuses to build on an unknown
            # member. Kept so a future edit that loosens that check cannot make
            # a member disappear from the page in silence.
            raise SystemExit(
                f"[build_tracker_html] FAILED: goal {g['title']!r} names {m!r}, "
                f"which is neither a ladder item nor a gate batch.")

        done = sum(1 for k in kinds if k == DONE)
        part = sum(1 for k in kinds if k == PARTIAL)
        n = len(kinds) or 1
        state_word = ("held" if done == len(kinds) else
                      "started" if done or part else "not begun")
        claim = (g.get("bugfree") or "").strip()
        badge = ""
        if claim and claim not in ("-", "\u2014", "\u2013"):
            badge = (f"<span class='bugfree' title='every row driven and "
                     f"verified; no finding open against this goal'>"
                     f"BUG-FREE {_strip_md(claim)}</span>")
        goals_html.append(
            f"<section class='goal {state_word.replace(' ', '-')}"
            f"{' isclean' if badge else ''}'>"
            f"<h3><span>{_strip_md(g['title'])}</span>"
            f"{badge}"
            f"<span class='tally'>{done}/{len(kinds)} {state_word}</span></h3>"
            f"<div class='gbody'>"
            f"<p class='why'>{_strip_md(g['why'])}</p>"
            f"{_bar({DONE: done, PARTIAL: part, TODO: len(kinds) - done - part}, n)}"
            f"<div class='tw'><table>"
            f"<colgroup><col class='c1'><col class='c2'><col class='c3'></colgroup>"
            f"{''.join(mrows)}</table></div></div></section>")

    gate_html = "".join(
        f"<tr class='{g['kind']}'><td>{_strip_md(g['batch'])}</td>"
        f"<td class='num'>{_strip_md(g['rows'])}</td>"
        f"<td>{_strip_md(g['needs'])}</td>"
        f"<td class='st'>{_strip_md(g['state'])}</td></tr>" for g in gate)

    find_html = "".join(
        f"<tr><td class='id'>{_strip_md(f[0])}</td><td>{_strip_md(f[1])}</td>"
        f"<td class='who'>{_strip_md(f[2])}</td></tr>" for f in findings)

    loops_html = ("".join(
        f"<div class='loop'><strong>{_strip_md(l['title'])}</strong>"
        f"<div>{_strip_md(l['body'])}</div></div>" for l in (open_loops or []))
        or "<div class='loop'>Nothing open — every completed item is sealed.</div>")

    sealed_html = "".join(
        f"<tr><td class='id'>{_strip_md(r[0])}</td>"
        f"<td class='who'>{_strip_md(r[1])}</td>"
        f"<td class='st'>{_strip_md(r[2])}</td></tr>" for r in sealed_rows)

    ship_html = "".join(
        f"<li class='{s['kind']}'><span class='mark'>{s['mark']}</span>"
        f"{_strip_md(s['what'])}</li>" for s in ship)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS — tracker</title>
<style>
 :root {{
   --bg:#0e1116; --panel:#161b22; --line:#232a34; --ink:#e6edf3;
   --dim:#8b949e; --done:#2ea043; --part:#d29922; --todo:#484f58;
   --accent:#58a6ff; --bad:#f85149;
 }}
 * {{ box-sizing:border-box }}
 body {{ margin:0; background:var(--bg); color:var(--ink);
   font:14px/1.55 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif; }}
 .wrap {{ max-width:1080px; margin:0 auto; padding:28px 20px 72px }}
 h1 {{ font-size:22px; margin:0 0 4px; letter-spacing:-.2px }}
 .sub {{ color:var(--dim); font-size:13px; margin-bottom:22px }}
 .sub code {{ color:var(--accent) }}
 h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.08em;
   color:var(--dim); margin:30px 0 10px; font-weight:600 }}
 .panel {{ background:var(--panel); border:1px solid var(--line);
   border-radius:10px; padding:16px 18px }}
 /* Three stats in ONE shape. They used to be a percentage, a percentage with a
    bare count in brackets, and a raw ratio with a caption — three formats for
    three numbers of equal standing, which is why the block read as clutter. */
 .hero {{ display:flex; gap:14px; align-items:stretch; flex-wrap:wrap }}
 .hold {{ border-left:4px solid var(--part); display:flex; flex-direction:column;
   gap:6px }}
 .hold b {{ font-size:15px }}
 .hold span {{ color:var(--dim); font-size:13px; line-height:1.5 }}
 .stat {{ flex:1 1 190px; background:#1b2230; border:1px solid var(--line);
   border-radius:9px; padding:13px 15px }}
 .stat b {{ display:block; font-size:34px; font-weight:700; line-height:1.05 }}
 .stat span {{ display:block; color:var(--dim); font-size:12.5px; margin-top:5px }}
 .stat em {{ display:block; font-style:normal; color:var(--todo);
   font-size:11.5px; margin-top:2px }}
 .stat.alt b {{ color:var(--part) }}
 .stat.ok2 b {{ color:var(--done) }}
 .barwrap {{ flex:1 1 100%; margin-top:2px }}
 .big.alt {{ color:var(--part) }}
 .big.ok2 {{ color:var(--done); font-size:40px }}
 .big.ok2 small {{ display:block; font-size:13px }}
 .fine {{ color:var(--dim); font-size:11.5px }}
 .loop {{ padding:9px 0; border-bottom:1px solid var(--line) }}
 .loop:last-child {{ border-bottom:0 }}
 .loop strong {{ color:var(--part) }}
 .loop div {{ color:var(--dim); font-size:13px; margin-top:3px }}
 .ver {{ margin-top:5px; font-size:12px; color:var(--dim); line-height:1.45 }}
 .vb {{ display:inline-block; font-size:10px; letter-spacing:.06em;
   padding:1px 6px; border-radius:99px; margin-right:6px; font-weight:700 }}
 .ver.sealed .vb {{ background:rgba(46,160,67,.18); color:var(--done) }}
 .ver.unsealed .vb {{ background:rgba(210,153,34,.18); color:var(--part) }}
 .scores {{ border-top:1px solid var(--line); padding:10px 0 2px }}
 .shead {{ color:var(--dim); font-size:12.5px; padding:2px 9px 6px }}
 .sw {{ width:34% }}
 .sbar {{ width:46% }}
 .sbar span {{ display:block; height:9px; border-radius:99px; min-width:2px }}
 .scores td {{ border-bottom:0; padding:3px 9px }}
 .bar {{ flex:1; min-width:240px; height:12px; background:var(--todo);
   border-radius:99px; overflow:hidden; display:flex }}
 .bar .d {{ background:var(--done) }} .bar .p {{ background:var(--part) }}
 .legend {{ color:var(--dim); font-size:12px; margin-top:8px }}
 .dot {{ display:inline-block; width:8px; height:8px; border-radius:99px;
   margin:0 5px 0 12px }}
 /* table-layout:fixed and a wrappable status column, because the status cells
    carry whole sentences now ("still owed - re-measured after his .env update:
    ...") and `white-space:nowrap` on that column pushed the whole page sideways.
    Content decides the height; the container decides the width. */
 table {{ width:100%; border-collapse:collapse; table-layout:fixed }}
 td,th {{ padding:7px 9px; border-bottom:1px solid var(--line);
   vertical-align:top; text-align:left; overflow-wrap:anywhere;
   word-break:break-word }}
 .tw {{ overflow-x:auto }}
 tr:last-child td {{ border-bottom:0 }}
 .num {{ white-space:nowrap; font-variant-numeric:tabular-nums }}
 .how,.who {{ color:var(--dim); font-size:12.5px }}
 .id {{ font-family:ui-monospace,Consolas,monospace; color:var(--accent);
   white-space:nowrap }}
 /* NOT nowrap: see the note above the table rule. */
 .st {{ white-space:normal }}
 tr.done .st {{ color:var(--done) }} tr.partial .st {{ color:var(--part) }}
 tr.todo .st {{ color:var(--dim) }}
 .tier {{ margin-bottom:14px }}
 .tier h3 {{ font-size:14px; margin:0; padding:11px 14px;
   background:#1b2230; border:1px solid var(--line); border-bottom:0;
   border-radius:9px 9px 0 0; display:flex; justify-content:space-between }}
 .tally {{ color:var(--dim); font-weight:500 }}
 .tier table col.c1 {{ width:7ch }}
 .tier table col.c2 {{ width:52% }}
 .tier table col.c3 {{ width:auto }}
 .tier table {{ background:var(--panel); border:1px solid var(--line);
   border-radius:0 0 9px 9px }}
 .goal {{ background:var(--panel); border:1px solid var(--line);
   border-radius:10px; margin-bottom:12px; overflow:hidden }}
 .goal h3 {{ font-size:14.5px; margin:0; padding:12px 15px; background:#1b2230;
   border-bottom:1px solid var(--line); display:flex; gap:12px;
   justify-content:space-between; align-items:baseline }}
 .goal.held h3 {{ box-shadow:inset 3px 0 0 var(--done) }}
 .goal.started h3 {{ box-shadow:inset 3px 0 0 var(--part) }}
 .goal.not-begun h3 {{ box-shadow:inset 3px 0 0 var(--todo) }}
 .goal .tally {{ white-space:nowrap; font-size:12px }}
 .goal .bugfree {{ white-space:nowrap; font-size:10.5px; letter-spacing:.06em;
   font-weight:700; color:#04150f; background:var(--done); border-radius:3px;
   padding:2px 7px; margin-left:auto; margin-right:9px }}
 .goal.isclean h3 {{ box-shadow:inset 3px 0 0 var(--done) }}
 .gbody {{ padding:13px 15px 4px }}
 .why {{ margin:0 0 11px; color:var(--dim); font-size:13px; line-height:1.55 }}
 .goal .bar {{ min-width:0; margin-bottom:9px }}
 .goal table col.c1 {{ width:15ch }}
 .goal table col.c2 {{ width:46% }}
 .goal table col.c3 {{ width:auto }}
 .gm {{ font-size:13px }}
 ul.ship {{ list-style:none; padding:0; margin:0 }}
 ul.ship li {{ padding:7px 0; border-bottom:1px solid var(--line) }}
 ul.ship li:last-child {{ border:0 }}
 .mark {{ display:inline-block; width:22px }}
 li.done {{ color:var(--done) }} li.todo .mark {{ color:var(--dim) }}
 code {{ background:#1f2630; padding:1px 5px; border-radius:4px;
   font-family:ui-monospace,Consolas,monospace; font-size:12.5px }}
 a {{ color:var(--accent) }}
 footer {{ margin-top:34px; color:var(--dim); font-size:12px;
   border-top:1px solid var(--line); padding-top:14px }}
</style></head><body><div class="wrap">

<h1>JARVIS — complete, then reliable, then shipped</h1>
<div class="sub">Generated from <code>JARVIS_TRACKER.md</code> — the single source
of truth. State as of <strong>{html.escape(asof)}</strong>. Regenerate with
<code>python tools\\build_tracker_html.py</code>. Do not edit this file by hand:
it is derived, and a second place to update is how the last set of docs drifted.</div>

{decision_html}
<div class="panel hero">
  <div class="stat"><b>{pct_done}%</b><span>ladder — {counts[DONE]} of {total}
    items done</span></div>
  {gate_block}
  <div class="stat ok2"><b>{sealed_done}/{all_done}</b><span>done items sealed
    <em>code + harness + proven live</em></span></div>
  <div class="barwrap">
    {_bar(counts, total)}
    <div class="legend">
      <span class="dot" style="background:var(--done)"></span>{counts[DONE]} done
      <span class="dot" style="background:var(--part)"></span>{counts[PARTIAL]} partial
      <span class="dot" style="background:var(--todo)"></span>{counts[TODO]} to do
    </div>
  </div>
</div>

<h2>The goals — what has to be true</h2>
{"".join(goals_html)}

<h2>Where it actually is</h2>
<div class="panel tw"><table>{state_html}</table></div>

<h2>The ladder</h2>
{"".join(tiers_html)}

<h2>Sealed — code, harness, proven live, boundaries stated</h2>
<div class="wrap"><table>
<colgroup><col style="width:22%"><col style="width:8%"><col style="width:70%"></colgroup>
<thead><tr><th>What</th><th>Sealed</th><th>Evidence</th></tr></thead>
<tbody>{sealed_html}</tbody></table></div>

<h2>Not closed — the open loops</h2>
<div class="panel">{loops_html}</div>

<h2>The gate — 192 rows</h2>
<div class="panel tw"><table>
<tr><th>Batch</th><th>Rows</th><th>Needs</th><th>State</th></tr>
{gate_html}</table></div>

<h2>Open findings</h2>
<div class="panel tw"><table>{find_html}</table></div>

<h2>Ship gates</h2>
<div class="panel"><ul class="ship">{ship_html}</ul></div>

<footer>
Marks come from the tracker itself: ✅ done · ⚠️ partial · ☐ not started.
Nothing is inferred here and no number is invented — if a figure looks wrong,
the fix belongs in <code>JARVIS_TRACKER.md</code>.
</footer>
</div></body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
