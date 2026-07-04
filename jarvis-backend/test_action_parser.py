"""Reliability harness for modules/action_parser.

Feeds the canonical parser the messy shapes a live LLM actually emits and asserts
each normalises to the right action(s). This is how "flawless parsing" becomes
MEASURABLE — run it after any change to the parse spine to catch regressions.

Run:  python test_action_parser.py      (no hardware, no network, no pytest)
Exit code 0 = all passed, 1 = at least one failed.
"""
import sys

from modules.action_parser import parse, extract_actions, extract_react_decision

# Each case: (name, raw_reply, expected_action_types)
# expected_action_types == []  ⇒  purely conversational (no actions).
CASES = [
    # ── The canonical brain contract ─────────────────────────────────────────
    ("clean_actions_array",
     '{"actions": [{"action_type": "web_search", "target": "python news"}]}',
     ["web_search"]),

    ("multi_action_batch",
     '{"actions": [{"action_type": "native_app_launcher", "target": "Notepad"},'
     ' {"action_type": "ghost_type", "target": "hi|^s"}]}',
     ["native_app_launcher", "ghost_type"]),

    # ── Code-fence wrapping (70B models love this) ───────────────────────────
    ("json_fence",
     '```json\n{"actions": [{"action_type": "tavily_search", "target": "score"}]}\n```',
     ["tavily_search"]),

    ("uppercase_fence",
     '```JSON\n{"actions":[{"action_type":"check_vitals","target":"vitals"}]}\n```',
     ["check_vitals"]),

    ("bare_fence",
     '```\n{"actions":[{"action_type":"lock_screen","target":""}]}\n```',
     ["lock_screen"]),

    # ── Prose around the JSON (json_mode off, temp 0.7) ──────────────────────
    ("prose_before_json",
     'Right away, Sir. {"actions": [{"action_type": "open_calculator", "target": ""}]}',
     ["open_calculator"]),

    ("prose_after_json",
     '{"actions": [{"action_type": "mute", "target": ""}]} Done, Sir.',
     ["mute"]),

    # ── Alternate shapes the parser must absorb ──────────────────────────────
    ("bare_single_action",
     '{"action_type": "web_search", "target": "cat videos"}',
     ["web_search"]),

    ("planner_singular_action",
     '{"thought": "I should search", "action": {"action_type": "tavily_search", "target": "x"}}',
     ["tavily_search"]),

    ("top_level_array",
     '[{"action_type": "mute", "target": ""}, {"action_type": "lock_screen", "target": ""}]',
     ["mute", "lock_screen"]),

    # ── Malformed but recoverable ────────────────────────────────────────────
    ("trailing_comma",
     '{"actions": [{"action_type": "web_search", "target": "x"},]}',
     ["web_search"]),

    ("truncated_missing_braces",
     '{"actions": [{"action_type": "web_search", "target": "long query that got cut',
     ["web_search"]),

    ("double_encoded_quoted",
     '"{\\"actions\\": [{\\"action_type\\": \\"mute\\", \\"target\\": \\"\\"}]}"',
     ["mute"]),

    # ── action_type aliasing / typos → canonical ─────────────────────────────
    ("alias_websearch",
     '{"actions": [{"action_type": "websearch", "target": "x"}]}',
     ["web_search"]),

    ("alias_open_app",
     '{"actions": [{"action_type": "open_app", "target": "Spotify"}]}',
     ["native_app_launcher"]),

    # ── Braces inside a string value must NOT break the scanner ──────────────
    ("braces_inside_code_target",
     '{"actions": [{"action_type": "workspace_write",'
     ' "target": "f.py|def x(): return {\\"a\\": [1,2]}"}]}',
     ["workspace_write"]),

    # ── Purely conversational — must yield NO actions ────────────────────────
    ("pure_prose",
     "Merely functioning as designed, Sir.",
     []),

    ("prose_with_brace_but_no_action",
     "The set {1, 2, 3} has three elements, Sir.",
     []),

    ("empty_actions_array",
     '{"actions": []}',
     []),

    ("empty_string",
     "",
     []),
]

# Cases specific to the planner decision extractor (keeps thought/final_answer).
REACT_CASES = [
    ("react_action",
     '{"thought": "search first", "action": {"action_type": "tavily_search", "target": "x"}}',
     "action"),
    ("react_final_fenced",
     '```json\n{"thought": "done", "final_answer": "All set, Sir."}\n```',
     "final_answer"),
    ("react_truncated",
     '{"thought": "I will search", "action": {"action_type": "web_search", "target": "cut off',
     "action"),
]


def _run() -> int:
    failures = 0
    print("=== action_parser reliability harness ===\n")

    for name, raw, expected in CASES:
        got = [a.get("action_type") for a in extract_actions(raw)]
        ok = got == expected
        if not ok:
            failures += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            print(f"       expected: {expected}")
            print(f"       got:      {got}")

    print()
    for name, raw, expect_key in REACT_CASES:
        decision = extract_react_decision(raw)
        ok = isinstance(decision, dict) and expect_key in decision
        if not ok:
            failures += 1
        print(f"[{'PASS' if ok else 'FAIL'}] react::{name}")
        if not ok:
            print(f"       expected key '{expect_key}' in decision, got: {decision}")

    # preamble extraction spot-check
    pr = parse('Right away, Sir. {"actions": [{"action_type": "mute", "target": ""}]}')
    if pr.preamble != "Right away, Sir.":
        failures += 1
        print(f"[FAIL] preamble_extraction  got: {pr.preamble!r}")
    else:
        print("[PASS] preamble_extraction")

    total = len(CASES) + len(REACT_CASES) + 1
    print(f"\n{total - failures}/{total} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
