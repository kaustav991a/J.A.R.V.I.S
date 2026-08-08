"""Harness for modules/agent_schema.py — §6.8.1 gap A (rules 16 + 6).

What is actually being proven here, in order of how much it matters:

  1. A wrongly-TYPED argument is refused. Before 2026-08-08 only *presence* was
     checked, so `{"path": 42}` was authorised, reached `action_engine`, and
     failed somewhere with no usable complaint.
  2. The refusal is an INSTRUCTION, not a status. It names the tool, the
     argument, what was expected, what arrived, and what to do next — rule 6.
  3. Adding the check could not retroactively refuse a call that worked
     yesterday. Every previously-valid shape still validates, and unknown schema
     keywords are ignored rather than treated as failures.
  4. The check is wired into the REAL `ToolRegistry.authorizer`, not merely
     available beside it. A validator nothing calls proves nothing — the
     `f84f644` lesson.

No model, no network, no engine.
"""

import sys

from modules import agent_schema as sch
from modules import agent_tools as at
from modules.tool_calls import ToolCall

STRING_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "A file path."}},
    "required": ["path"],
}

from agent_tier_fixture import TIERS


def tiers(mapping=None):
    m = TIERS if mapping is None else mapping
    return lambda action_type: m.get(action_type, "BLOCK")


def call(name, **args):
    return ToolCall(id="c1", name=name, arguments=args)


# ── 1. the hole that existed: a wrongly-typed argument ───────────────────────

def test_a_number_where_a_string_belongs_is_refused():
    """The exact call that used to reach action_engine and fail far from here."""
    problem = sch.validate_arguments("workspace_read", {"path": 42}, STRING_SCHEMA)
    assert problem is not None, "a number passed as a string argument was accepted"
    assert "'path' must be a string" in problem, problem
    assert "integer" in problem, "the message does not say what actually arrived"


def test_an_object_where_a_string_belongs_is_refused():
    problem = sch.validate_arguments("workspace_read", {"path": {"a": 1}},
                                     STRING_SCHEMA)
    assert problem is not None and "must be a string" in problem, problem


def test_a_boolean_is_not_accepted_as_a_number():
    """In Python `True` IS an int. Without the explicit guard, {"limit": true}
    validates as a number and reaches the handler as 1 — a silent coercion, the
    whole class of bug this module exists to stop."""
    schema = {"type": "object", "properties": {"limit": {"type": "integer"}}}
    problem = sch.validate_arguments("read", {"limit": True}, schema)
    assert problem is not None and "must be a integer" in problem, problem
    assert sch.validate_arguments("read", {"limit": 5}, schema) is None


def test_a_non_object_argument_payload_is_refused():
    problem = sch.validate_arguments("workspace_read", ["path"], STRING_SCHEMA)
    assert problem is not None and "must be a JSON object" in problem, problem


# ── 2. the refusal is an instruction (rule 6) ────────────────────────────────

def test_the_error_carries_the_schema_and_what_was_sent():
    """Rule 6: a model that gets an instructive error self-corrects in one turn.
    So the message must contain BOTH sides of the diff, not just a complaint."""
    problem = sch.validate_arguments("workspace_read", {"path": 42}, STRING_SCHEMA)
    assert "Expected:" in problem and "path: string" in problem, problem
    assert "You sent:" in problem and "42" in problem, problem
    assert "call the tool once more" in problem.lower(), \
        "the error does not say what to do next"


def test_a_quoting_hint_is_given_for_the_commonest_mistake():
    """Number-instead-of-string is the single most common mid-tier-model error
    and the fix is mechanical, so the message states it rather than leaving it
    to be inferred."""
    problem = sch.validate_arguments("workspace_read", {"path": 42}, STRING_SCHEMA)
    assert 'Send it quoted, as "42"' in problem, problem


def test_every_problem_is_reported_not_just_the_first():
    """One round trip per problem is how a 3-argument call burns its single
    repair and dies."""
    schema = {"type": "object", "properties": {
        "a": {"type": "string"}, "b": {"type": "integer"}}}
    problem = sch.validate_arguments("t", {"a": 1, "b": "x"}, schema)
    assert "'a'" in problem and "'b'" in problem, problem


# ── 3. it cannot refuse what used to work ────────────────────────────────────

def test_valid_arguments_pass():
    assert sch.validate_arguments("workspace_read", {"path": "F:/x.txt"},
                                  STRING_SCHEMA) is None


def test_a_zero_argument_tool_accepts_empty_and_none():
    schema = {"type": "object", "properties": {}, "required": []}
    assert sch.validate_arguments("system_status", {}, schema) is None
    assert sch.validate_arguments("system_status", None, schema) is None


def test_extra_arguments_are_allowed_unless_the_schema_closes_the_object():
    """JSON Schema's own default, and the property that made this change safe to
    add: no previously-working call becomes a refusal."""
    assert sch.validate_arguments("workspace_read",
                                  {"path": "x", "chatter": "hi"},
                                  STRING_SCHEMA) is None
    closed = dict(STRING_SCHEMA, additionalProperties=False)
    problem = sch.validate_arguments("workspace_read",
                                     {"path": "x", "chatter": "hi"}, closed)
    assert problem is not None and "chatter" in problem, problem
    assert "Accepted arguments: path" in problem, problem


def test_an_unknown_keyword_is_ignored_rather_than_failed():
    """A schema this module does not understand must never block a call."""
    schema = {"type": "object",
              "properties": {"path": {"type": "string", "pattern": "^/", "format": "uri"}}}
    assert sch.validate_arguments("t", {"path": "not-a-uri"}, schema) is None


def test_a_missing_schema_never_blocks():
    assert sch.validate_arguments("t", {"anything": 1}, None) is None
    assert sch.validate_arguments("t", {"anything": 1}, "nonsense") is None


def test_every_real_registry_schema_accepts_its_own_documented_shape():
    """Drives the SHIPPING registry, not a fixture: if a real tool's schema were
    written in a way this validator rejects, the agent loop would lose that tool
    the moment the check went live."""
    reg = at.build_default_registry(tiers())
    good = {
        "tavily_search": {"query": "weather"},
        "web_browse": {"url": "https://example.com"},
        "search_documents": {"query": "invoice"},
        "memory_recall": {"query": "his sister"},
        "workspace_read": {"path": "F:/work/x.py"},
        "list_directory": {"path": "C:/Users/K"},
        "find_file": {"name": "notes.md"},
        "system_status": {},
        "read_screen": {},
        "workspace_write": {"path": "F:/x.txt", "content": "hello"},
        "edit_file": {"path": "F:/x.py", "old_string": "a", "new_string": "b"},
        # wave 1 — email + calendar
        "gmail_read_unread": {"count": 5},
        "gmail_read": {"query": "from:mum newer_than:7d", "max_results": 5},
        "search_email": {"query": "invoice"},
        "read_email": {"which": "latest"},
        "check_calendar": {},
        "morning_briefing": {},
        "gmail_send": {"to": "a@b.com", "subject": "Hi", "body": "Text."},
        "gmail_reply": {"thread_id": "abc123", "body": "Thanks."},
        "create_event": {"description": "dentist Thursday 4pm"},
        "clear_schedule": {},
        # wave 2 — television + music
        "tv_power": {},
        "tv_volume": {"direction": "up", "steps": 3},
        "tv_control": {"key": "play_pause"},
        "tv_launch_app": {"app": "netflix"},
        "tv_play_media": {"title": "Stranger Things", "app": "netflix"},
        "tv_type": {"text": "stranger things"},
        "play_music": {"query": "moonlight", "service": "spotify"},
        # wave 3 — apps, the desk display, the machine
        "native_app_launcher": {"app": "notepad"},
        "close_app": {"app": "chrome"},
        "hud_open_widget": {"widget": "calendar"},
        "hud_close_widget": {"widget": "vitals"},
        "os_control": {"command": "lock_screen"},
        "os_macro": {"macro": "deep_work", "url": "http://localhost:5173"},
        "open_link": {"url": "https://example.com"},
    }
    for name in reg.names():
        entry = reg.get(name)
        args = good.get(name)
        assert args is not None, f"no sample arguments written for tool '{name}'"
        problem = sch.validate_arguments(name, args, entry.input_schema)
        assert problem is None, f"real schema for '{name}' rejects a valid call: {problem}"


# ── 4. it is actually WIRED, not merely available ────────────────────────────

def test_the_real_authorizer_refuses_a_wrongly_typed_call():
    """The f84f644 lesson: a validator nothing calls proves nothing. This drives
    the shipping `ToolRegistry.authorizer`."""
    reg = at.build_default_registry(tiers())
    decision = reg.authorizer()(call("workspace_read", path=42))
    assert decision.allowed is False, "the authorizer let a wrongly-typed call through"
    assert "must be a string" in decision.reason, decision.reason


def test_a_valid_call_is_still_authorised():
    reg = at.build_default_registry(tiers())
    decision = reg.authorizer()(call("workspace_read", path="F:/x.py"))
    assert decision.allowed is True, decision.reason


def test_a_missing_argument_still_wins_over_a_type_complaint():
    """Ordering matters: "you left out `path`" is a better complaint than
    "`path` must be a string" when the argument simply is not there."""
    reg = at.build_default_registry(tiers())
    decision = reg.authorizer()(call("workspace_read"))
    assert decision.allowed is False
    assert "missing required argument" in decision.reason, decision.reason


def test_a_malformed_confirm_tier_call_is_corrected_not_confirmed():
    """A CONFIRM-tier tool must not ask the owner to approve arguments that were
    never going to work. Shape is checked BEFORE the tier branch."""
    reg = at.build_default_registry(tiers())
    decision = reg.authorizer(allow_confirm=True)(
        call("workspace_write", path=7, content="x"))
    assert decision.allowed is False, "a malformed write was sent for approval"
    assert "must be a string" in decision.reason, decision.reason


def test_schema_problem_is_none_for_an_unknown_tool():
    """Unknown-tool is the loop's complaint to make, not this one's."""
    reg = at.build_default_registry(tiers())
    assert reg.schema_problem(call("no_such_tool", x=1)) is None


# ── describe_schema, used inside every error ─────────────────────────────────

def test_describe_schema_marks_optional_arguments():
    schema = {"type": "object",
              "properties": {"path": {"type": "string"},
                             "offset": {"type": "integer"}},
              "required": ["path"]}
    text = sch.describe_schema(schema)
    assert "path: string" in text and "offset: integer (optional)" in text, text


def test_describe_schema_says_so_when_there_are_no_arguments():
    assert "no arguments" in sch.describe_schema({"type": "object", "properties": {}})


def test_describe_schema_renders_an_enum_as_its_choices():
    schema = {"type": "object", "properties": {"mode": {"enum": ["a", "b"]}}}
    assert "'a'|'b'" in sch.describe_schema(schema)


def test_enum_and_bounds_are_enforced():
    schema = {"type": "object", "properties": {
        "mode": {"enum": ["read", "write"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        "name": {"type": "string", "minLength": 2}}}
    assert sch.validate_arguments("t", {"mode": "delete"}, schema) is not None
    assert sch.validate_arguments("t", {"limit": 0}, schema) is not None
    assert sch.validate_arguments("t", {"limit": 99}, schema) is not None
    assert sch.validate_arguments("t", {"name": "x"}, schema) is not None
    assert sch.validate_arguments("t", {"mode": "read", "limit": 5,
                                        "name": "ok"}, schema) is None


def test_array_items_are_checked_elementwise():
    schema = {"type": "object", "properties": {
        "paths": {"type": "array", "items": {"type": "string"}}}}
    problem = sch.validate_arguments("t", {"paths": ["a", 7]}, schema)
    assert problem is not None and "paths[1]" in problem, problem
    assert sch.validate_arguments("t", {"paths": ["a", "b"]}, schema) is None


def test_a_union_type_accepts_either_member():
    schema = {"type": "object", "properties": {"x": {"type": ["string", "null"]}}}
    assert sch.validate_arguments("t", {"x": "s"}, schema) is None
    assert sch.validate_arguments("t", {"x": None}, schema) is None
    assert sch.validate_arguments("t", {"x": 5}, schema) is not None


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
