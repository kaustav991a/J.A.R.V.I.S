"""
test_truncated_actions.py — a cut-off reply must not delete the parent folder
=============================================================================

Pre-Electron review, 2026-08-15, finding 9.

`action_parser.heal_and_load` repairs the JSON that 8B/70B models actually
produce, including truncation: it closes the unterminated string and every open
bracket at the depth the model was cut off at. That is the right call for the
common case — a reply clipped by `max_tokens` still yields a usable action.

It is the wrong call for a destructive one, because a truncated target is not a
BROKEN target. It is a different, perfectly valid one — and for a path it is a
PARENT:

    {"actions":[{"action_type":"delete_file",
                 "target":"C:\\Users\\K\\Docs\\Project\\notes.txt

cut off mid-value heals to `C:\\Users\\K\\Docs\\Project`, and `_delete_file`
calls `shutil.rmtree` on a directory. The action then succeeds, reports success,
and removed the wrong thing — the failure shape this project keeps finding:
indistinguishable from working.

So a repaired-by-truncation reply drops its destructive actions and keeps the
rest. A trailing-comma repair is lossless and stays allowed; a truncated
*non*-destructive action stays allowed too, because a half-finished search is
harmless.
"""

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

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


from modules import action_parser as ap  # noqa: E402


# ── the repair still works, which is the point of it existing ────────────────

def test_a_clean_reply_is_unaffected():
    raw = '{"actions":[{"action_type":"delete_file","target":"C:/tmp/a.txt"}]}'
    acts = ap.extract_actions(raw)
    check(len(acts) == 1, "a complete destructive action still parses")
    check(acts[0]["target"] == "C:/tmp/a.txt", "...with its real target intact")


def test_a_trailing_comma_repair_is_still_lossless_and_allowed():
    # Nothing about any VALUE changes, so a destructive action survives it.
    raw = '{"actions":[{"action_type":"delete_file","target":"C:/tmp/a.txt",},]}'
    acts = ap.extract_actions(raw)
    check(len(acts) == 1, "a trailing-comma repair still yields the action")
    check(acts[0]["target"] == "C:/tmp/a.txt", "...with the target unchanged")


def test_truncation_repair_still_works_for_harmless_actions():
    raw = '{"actions":[{"action_type":"web_search","target":"weather in kolk'
    acts = ap.extract_actions(raw)
    check(len(acts) == 1, "a truncated harmless action is still recovered")
    check(acts[0]["target"].startswith("weather in kolk"),
          "...with whatever prefix survived, which is fine for a search")


# ── the finding ──────────────────────────────────────────────────────────────

def test_a_truncated_delete_is_refused():
    raw = ('{"actions":[{"action_type":"delete_file",'
           '"target":"C:\\\\Users\\\\K\\\\Docs\\\\Project\\\\notes.txt')
    acts = ap.extract_actions(raw)
    check(acts == [],
          "a delete whose target was cut off is dropped, not executed on the parent")


def test_every_destructive_type_is_refused_when_truncated():
    for atype in ("delete_file", "workspace_write", "workspace_patch",
                  "ghost_save_file", "close_app", "move_file", "rename_file"):
        raw = f'{{"actions":[{{"action_type":"{atype}","target":"C:/Users/K/Docs/Proj'
        check(ap.extract_actions(raw) == [],
              f"truncated '{atype}' is refused")


def test_an_unlisted_removal_verb_is_refused_too():
    # The registry grows; a hand-kept list is the thing that fails quietly. Any
    # action type that READS as a removal is caught without being enumerated.
    for atype in ("delete_calendar_entry", "remove_widget", "purge_deleted_items"):
        raw = f'{{"actions":[{{"action_type":"{atype}","target":"C:/Users/K/Doc'
        acts = ap.extract_actions(raw)
        if "delete" in atype or "remove" in atype:
            check(acts == [], f"truncated '{atype}' is refused by the substring rule")
        else:
            check(True, f"{atype} not covered by the substring rule (expected)")


def test_a_truncated_batch_keeps_the_safe_actions():
    # Dropping the destructive one must not throw away the rest of the turn.
    raw = ('{"actions":[{"action_type":"web_search","target":"tea"},'
           '{"action_type":"delete_file","target":"C:/Users/K/Docs/Proj')
    acts = ap.extract_actions(raw)
    types = [a.get("action_type") for a in acts]
    check("web_search" in types, "the harmless action in the batch survives")
    check("delete_file" not in types, "...and the truncated destructive one does not")


# ── the reporting flag itself ────────────────────────────────────────────────

def test_heal_and_load_reports_truncation_only_when_it_closed_something():
    _, trunc = ap.heal_and_load('{"a": 1}', report=True)
    check(trunc is False, "a clean parse reports no truncation")
    _, trunc = ap.heal_and_load('{"a": 1,}', report=True)
    check(trunc is False, "a trailing-comma repair reports no truncation")
    obj, trunc = ap.heal_and_load('{"a": "unfinis', report=True)
    check(trunc is True, "a closed-off value reports truncation")
    check(obj == {"a": "unfinis"}, "...and still returns what survived")


def test_the_default_signature_is_unchanged_for_other_callers():
    # extract_react_decision and any other caller pass no `report`.
    check(ap.heal_and_load('{"a": 1}') == {"a": 1},
          "heal_and_load without report= still returns the object alone")
    check(ap.heal_and_load("") is None, "an empty span still returns None")


def test_parse_never_raises_on_any_of_this():
    for raw in ('', '{', '{"actions":', '[[[', '{"actions":[{"action_type":',
                'not json at all', '{"actions":[{"action_type":"delete_file"'):
        try:
            ap.parse(raw)
            check(True, f"parse survived {raw[:24]!r}")
        except Exception as e:  # noqa: BLE001
            check(False, f"parse raised on {raw[:24]!r}: {e}")


TESTS = [
    test_a_clean_reply_is_unaffected,
    test_a_trailing_comma_repair_is_still_lossless_and_allowed,
    test_truncation_repair_still_works_for_harmless_actions,
    test_a_truncated_delete_is_refused,
    test_every_destructive_type_is_refused_when_truncated,
    test_an_unlisted_removal_verb_is_refused_too,
    test_a_truncated_batch_keeps_the_safe_actions,
    test_heal_and_load_reports_truncation_only_when_it_closed_something,
    test_the_default_signature_is_unchanged_for_other_callers,
    test_parse_never_raises_on_any_of_this,
]


def main():
    print("=" * 60)
    print("truncated-action harness (pre-Electron review)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
