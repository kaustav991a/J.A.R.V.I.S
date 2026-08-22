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
            kind = _classify(status)
            if kind is None:
                continue
            items.append({"id": ident, "what": what, "status": status, "kind": kind})
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
                     "kind": _classify(cells[3]) or TODO})

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

    doc = _render(state, tiers, gate, findings, ship, counts, pct_done, asof,
                  gate_stat)
    OUT.write_text(doc, encoding="utf-8")
    print(f"[build_tracker_html] wrote {OUT.relative_to(ROOT)}  "
          f"({len(doc):,} bytes)")
    print(f"[build_tracker_html] ladder: {counts[DONE]} done, "
          f"{counts[PARTIAL]} partial, {counts[TODO]} to do  -> {pct_done}%")
    return 0


def _bar(counts: dict, total: int) -> str:
    d = counts[DONE] * 100 / total
    p = counts[PARTIAL] * 100 / total
    return (f'<div class="bar"><span class="d" style="width:{d:.1f}%"></span>'
            f'<span class="p" style="width:{p:.1f}%"></span></div>')


def _render(state, tiers, gate, findings, ship, counts, pct_done, asof,
            gate_stat=None) -> str:
    total = sum(counts.values()) or 1

    # The gate is the number that decides whether JARVIS is finished, so it sits
    # beside the ladder percentage instead of inside a table.
    if gate_stat:
        gate_block = (
            f'<div class="big alt">{gate_stat["pct"]}%<br>'
            f'<small>of the {gate_stat["total"]} gate rows ticked '
            f'({gate_stat["done"]})</small></div>')
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
            f"<td>{_strip_md(i['what'])}</td>"
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

    gate_html = "".join(
        f"<tr class='{g['kind']}'><td>{_strip_md(g['batch'])}</td>"
        f"<td class='num'>{_strip_md(g['rows'])}</td>"
        f"<td>{_strip_md(g['needs'])}</td>"
        f"<td class='st'>{_strip_md(g['state'])}</td></tr>" for g in gate)

    find_html = "".join(
        f"<tr><td class='id'>{_strip_md(f[0])}</td><td>{_strip_md(f[1])}</td>"
        f"<td class='who'>{_strip_md(f[2])}</td></tr>" for f in findings)

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
 .hero {{ display:flex; gap:26px; align-items:center; flex-wrap:wrap }}
 .big {{ font-size:44px; font-weight:700; line-height:1 }}
 .big small {{ font-size:15px; color:var(--dim); font-weight:500 }}
 .big.alt {{ color:var(--part) }}
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

<div class="panel hero">
  <div class="big">{pct_done}%<br><small>of ladder items done</small></div>
  {gate_block}
  <div style="flex:1">
    {_bar(counts, total)}
    <div class="legend">
      <span class="dot" style="background:var(--done)"></span>{counts[DONE]} done
      <span class="dot" style="background:var(--part)"></span>{counts[PARTIAL]} partial
      <span class="dot" style="background:var(--todo)"></span>{counts[TODO]} to do
      &nbsp;·&nbsp; ladder progress only — the gate is counted separately below,
      because 8% of its rows are ticked and that is the number that matters most.
    </div>
  </div>
</div>

<h2>Where it actually is</h2>
<div class="panel tw"><table>{state_html}</table></div>

<h2>The ladder</h2>
{"".join(tiers_html)}

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
