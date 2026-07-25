"""Harness for the agentic core's phase-1 plumbing: modules/tool_calls.py
(normalisation) and llm_router.universal_tool_call (routing).

Fake HTTP, fake Groq client, no keys, no network. What this is really guarding:
a tool turn must never come back ambiguous. A provider that answers with nothing
usable has to look like a FAILURE, because an empty success is exactly what gets
narrated as "Done, Sir" while nothing happened.
"""

import json
import os
import sys
import types

from modules import tool_calls as tc

# llm_router imports the Groq SDK via groq_key_manager at module scope; that is
# already the case for test_llm_failover, so importing here is safe and offline.
from modules import llm_router as lr


TOOLS = [{
    "type": "function",
    "function": {
        "name": "open_app",
        "description": "Launch an application",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"]},
    },
}]


def _msg(content=None, tool_calls=None):
    return {"content": content, "tool_calls": tool_calls}


def _resp(message, model="m", finish="tool_calls"):
    return {"model": model, "choices": [{"message": message, "finish_reason": finish}]}


def _call(name="open_app", args='{"name": "chrome"}', cid="call_1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


# ---- normalisation ------------------------------------------------------- #

def test_tool_call_arguments_decoded_from_json_string():
    turn = tc.normalise_openai_response(_resp(_msg(None, [_call()])), provider="groq")
    assert turn.ok and turn.wants_tools
    call = turn.tool_calls[0]
    assert call.name == "open_app"
    assert call.arguments == {"name": "chrome"}
    assert call.ok and call.arguments_error is None


def test_arguments_may_arrive_as_a_dict():
    args, err = tc.parse_arguments({"name": "chrome"})
    assert args == {"name": "chrome"} and err is None


def test_fenced_json_arguments_are_recovered():
    """Weak free models wrap JSON in a markdown fence even inside a tool call."""
    args, err = tc.parse_arguments('```json\n{"name": "chrome"}\n```')
    assert err is None and args == {"name": "chrome"}


def test_broken_arguments_are_flagged_not_swallowed():
    """A truncated argument blob must NOT read as 'called with no arguments' —
    that would run the tool with defaults the user never asked for."""
    turn = tc.normalise_openai_response(_resp(_msg(None, [_call(args='{"name": "chr')])))
    call = turn.tool_calls[0]
    assert call.ok is False
    assert "invalid JSON" in call.arguments_error
    assert call.raw_arguments == '{"name": "chr'      # kept for the repair prompt
    assert call.arguments == {}


def test_non_object_arguments_are_rejected():
    for raw in ('["chrome"]', '"chrome"', "42"):
        args, err = tc.parse_arguments(raw)
        assert err and args == {}, raw


def test_empty_arguments_are_fine():
    args, err = tc.parse_arguments("")
    assert args == {} and err is None


def test_unnamed_tool_call_is_an_error():
    turn = tc.normalise_openai_response(_resp(_msg(None, [_call(name="")])))
    assert turn.tool_calls[0].ok is False


def test_text_only_turn_is_a_success_with_no_calls():
    turn = tc.normalise_openai_response(_resp(_msg("All done, Sir."), finish="stop"))
    assert turn.ok and not turn.wants_tools
    assert turn.text == "All done, Sir." and turn.finish_reason == "stop"


def test_multiple_tool_calls_keep_their_ids():
    turn = tc.normalise_openai_response(_resp(_msg(None, [
        _call(cid="a", args='{"name": "chrome"}'),
        _call(cid="b", args='{"name": "code"}'),
    ])))
    assert [c.id for c in turn.tool_calls] == ["a", "b"]


def test_empty_assistant_turn_is_a_failure():
    """No text AND no tool call is the empty-200 bug in a new costume."""
    turn = tc.normalise_openai_response(_resp(_msg(None, [])), provider="groq")
    assert turn.ok is False and "empty" in turn.error
    assert turn.provider == "groq"


def test_provider_error_body_is_a_failure():
    turn = tc.normalise_openai_response({"error": {"message": "rate limited"}})
    assert turn.ok is False and "rate limited" in turn.error


def test_no_choices_is_a_failure():
    assert tc.normalise_openai_response({"choices": []}).ok is False
    assert tc.normalise_openai_response(None).ok is False


def test_sdk_objects_normalise_like_dicts():
    """Groq's SDK returns attribute objects, not dicts — same result required."""
    obj = types.SimpleNamespace(
        model="groq-model",
        choices=[types.SimpleNamespace(
            finish_reason="tool_calls",
            message=types.SimpleNamespace(content=None, tool_calls=[
                types.SimpleNamespace(id="x1", function=types.SimpleNamespace(
                    name="open_app", arguments='{"name": "chrome"}'))]))])
    turn = tc.normalise_openai_response(obj, provider="groq")
    assert turn.ok and turn.tool_calls[0].arguments == {"name": "chrome"}
    assert turn.model == "groq-model"


# ---- message round-trip -------------------------------------------------- #

def test_assistant_message_preserves_call_ids():
    """A provider rejects a tool result whose id it never issued, so the ids the
    loop echoes back must be the ones that came in."""
    turn = tc.normalise_openai_response(_resp(_msg("thinking", [_call(cid="zz")])))
    msg = tc.assistant_message(turn)
    assert msg["role"] == "assistant" and msg["content"] == "thinking"
    assert msg["tool_calls"][0]["id"] == "zz"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"name": "chrome"}


def test_tool_result_message_shape():
    turn = tc.normalise_openai_response(_resp(_msg(None, [_call(cid="q")])))
    m = tc.tool_result_message(turn.tool_calls[0], {"launched": True})
    assert m == {"role": "tool", "tool_call_id": "q", "content": '{"launched": true}'}
    assert tc.tool_result_message("raw-id", "plain")["content"] == "plain"


# ---- tool-definition validation ------------------------------------------ #

def test_validate_tool_defs_accepts_a_good_registry():
    assert tc.validate_tool_defs(TOOLS) == []


def test_validate_tool_defs_catches_the_usual_breakage():
    assert tc.validate_tool_defs([]) and tc.validate_tool_defs("nope")
    assert tc.validate_tool_defs([{"type": "function"}])                      # no fn
    assert tc.validate_tool_defs([{"type": "tool", "function": {"name": "a"}}])
    assert tc.validate_tool_defs([{"type": "function", "function": {}}])      # no name
    dup = TOOLS + TOOLS
    assert any("duplicate" in p for p in tc.validate_tool_defs(dup))


# ---- routing ------------------------------------------------------------- #

class _EnvGuard:
    """Set env for one test and always put it back (module globals are sticky)."""

    def __init__(self, **kw):
        self.kw = kw
        self.old = {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_route_order_is_tool_capable_only():
    """Ollama must never appear: a hallucinated tool call is worse than slow."""
    with _EnvGuard(GEMINI_API_KEYS="k1", GEMINI_API_KEY="", OPENROUTER_API_KEY="k2"):
        assert lr._tool_route_order() == ["groq", "gemini", "openrouter"]
    assert "ollama" not in lr._tool_route_order()


def test_route_order_drops_unconfigured_providers():
    with _EnvGuard(GEMINI_API_KEYS="", GEMINI_API_KEY="", OPENROUTER_API_KEY=""):
        assert lr._tool_route_order() == ["groq"]


def test_invalid_tools_fail_before_any_provider_is_touched():
    called = []
    orig = lr._tool_call_groq
    lr._tool_call_groq = lambda *a, **k: called.append(1)
    try:
        turn = lr.universal_tool_call([{"role": "user", "content": "hi"}], tools=[])
    finally:
        lr._tool_call_groq = orig
    assert turn.ok is False and "invalid tool definitions" in turn.error
    assert called == [], "a malformed registry must not reach the network"


def test_cascade_escalates_past_a_dead_provider():
    orig_g, orig_o = lr._tool_call_groq, lr._tool_call_openrouter
    seen = []

    def dead(*a, **k):
        seen.append("groq")
        raise RuntimeError("429 rate limited")

    def good(*a, **k):
        seen.append("openrouter")
        return tc.normalise_openai_response(_resp(_msg(None, [_call()])),
                                            provider="openrouter")

    lr._tool_call_groq, lr._tool_call_openrouter = dead, good
    try:
        with _EnvGuard(GEMINI_API_KEYS="", GEMINI_API_KEY="", OPENROUTER_API_KEY="k"):
            turn = lr.universal_tool_call([{"role": "user", "content": "open chrome"}],
                                          TOOLS)
    finally:
        lr._tool_call_groq, lr._tool_call_openrouter = orig_g, orig_o
    assert seen == ["groq", "openrouter"]
    assert turn.ok and turn.provider == "openrouter"


def test_an_empty_turn_also_escalates():
    """Not just exceptions: a provider that answers with nothing usable is a
    failure worth trying the next provider for."""
    orig_g, orig_o = lr._tool_call_groq, lr._tool_call_openrouter
    lr._tool_call_groq = lambda *a, **k: tc.ToolTurn.failed("empty", "groq")
    lr._tool_call_openrouter = lambda *a, **k: tc.normalise_openai_response(
        _resp(_msg("done")), provider="openrouter")
    try:
        with _EnvGuard(GEMINI_API_KEYS="", GEMINI_API_KEY="", OPENROUTER_API_KEY="k"):
            turn = lr.universal_tool_call([{"role": "user", "content": "x"}], TOOLS)
    finally:
        lr._tool_call_groq, lr._tool_call_openrouter = orig_g, orig_o
    assert turn.ok and turn.provider == "openrouter"


def test_all_providers_down_fails_honestly():
    orig = lr._tool_call_groq
    lr._tool_call_groq = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        with _EnvGuard(GEMINI_API_KEYS="", GEMINI_API_KEY="", OPENROUTER_API_KEY=""):
            turn = lr.universal_tool_call([{"role": "user", "content": "x"}], TOOLS)
    finally:
        lr._tool_call_groq = orig
    assert turn.ok is False
    assert "boom" in turn.error and not turn.tool_calls


def test_pinned_provider_skips_the_cascade():
    orig_g, orig_o = lr._tool_call_groq, lr._tool_call_openrouter
    lr._tool_call_groq = lambda *a, **k: (_ for _ in ()).throw(AssertionError("groq used"))
    lr._tool_call_openrouter = lambda *a, **k: tc.normalise_openai_response(
        _resp(_msg("ok")), provider="openrouter")
    try:
        turn = lr.universal_tool_call([{"role": "user", "content": "x"}], TOOLS,
                                      provider="openrouter")
    finally:
        lr._tool_call_groq, lr._tool_call_openrouter = orig_g, orig_o
    assert turn.ok and turn.provider == "openrouter"


def test_http_provider_posts_openai_shaped_tools():
    """Fake `requests` — assert what actually goes on the wire."""
    sent = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return _resp(_msg(None, [_call()]), model="free-model")

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, payload=json, headers=headers, timeout=timeout)
        return _Resp()

    orig = lr.requests.post
    lr.requests.post = fake_post
    try:
        turn = lr._openai_tool_http("https://x/y", "KEY", "free-model",
                                    [{"role": "user", "content": "open chrome"}],
                                    TOOLS, "auto", 0.2, 512, 30, "openrouter")
    finally:
        lr.requests.post = orig
    assert sent["payload"]["tools"] == TOOLS
    assert sent["payload"]["tool_choice"] == "auto"
    assert sent["payload"]["stream"] is False
    assert sent["headers"]["Authorization"] == "Bearer KEY"
    assert turn.ok and turn.provider == "openrouter" and turn.model == "free-model"


def test_openrouter_walks_its_tool_model_list():
    attempts = []

    def fake_http(url, key, model, *a, **k):
        attempts.append(model)
        if len(attempts) < 2:
            raise RuntimeError("model refuses tools")
        return tc.normalise_openai_response(_resp(_msg("ok"), model=model),
                                            provider="openrouter", model=model)

    orig = lr._openai_tool_http
    lr._openai_tool_http = fake_http
    try:
        with _EnvGuard(OPENROUTER_API_KEY="k"):
            turn = lr._tool_call_openrouter([{"role": "user", "content": "x"}],
                                            TOOLS, "auto", 0.2, 512, 30)
    finally:
        lr._openai_tool_http = orig
    assert len(attempts) == 2 and attempts[0] != attempts[1]
    assert turn.ok


def test_gemini_rotates_keys_on_failure():
    used = []

    def fake_http(url, key, model, *a, **k):
        used.append(key)
        if key == "bad":
            raise RuntimeError("quota exhausted")
        return tc.normalise_openai_response(_resp(_msg("ok")), provider="gemini")

    orig = lr._openai_tool_http
    lr._openai_tool_http = fake_http
    try:
        with _EnvGuard(GEMINI_API_KEYS="bad,good", GEMINI_API_KEY=""):
            turn = lr._tool_call_gemini([{"role": "user", "content": "x"}],
                                        TOOLS, "auto", 0.2, 512, 30)
    finally:
        lr._openai_tool_http = orig
    assert used == ["bad", "good"] and turn.ok


def test_groq_tool_model_is_separate_from_the_chat_model():
    """The 8B instant model can't hold a tool loop; making the agent smarter must
    not make every cheap classification turn expensive."""
    assert lr.GROQ_TOOL_MODEL != lr.GROQ_MODEL
    sent = {}

    def fake_rotation(fn):
        class _C:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kw):
                        sent.update(kw)
                        return _resp(_msg(None, [_call()]))
        return fn(_C)

    orig = lr.run_with_key_rotation
    lr.run_with_key_rotation = fake_rotation
    try:
        turn = lr._tool_call_groq([{"role": "user", "content": "x"}], TOOLS,
                                  "auto", 0.2, 512, 30)
    finally:
        lr.run_with_key_rotation = orig
    assert sent["model"] == lr.GROQ_TOOL_MODEL
    assert sent["tools"] == TOOLS and sent["tool_choice"] == "auto"
    assert turn.ok and turn.provider == "groq"


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
