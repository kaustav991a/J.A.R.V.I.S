"""Harness: NVIDIA NIM as a fourth cloud leg, and the two ways that could go wrong.

WHY THIS EXISTS
---------------
Added 2026-08-29 at Kaustav's request, after `build.nvidia.com` showed
`nvidia/nemotron-3-ultra-550b-a55b` on a **free** OpenAI-compatible endpoint —
561B MoE, 1M context, catalogued as agentic and tool-capable.

**The argument for it is the quota, not the speed.** The figure that prompted
this ("5x faster than Claude and ChatGPT") is from an Instagram post and appears
nowhere on NVIDIA's page, which makes no latency claim at all. What IS true and
useful: OpenRouter's free cap is shared across every `:free` id, and Gemini's is
20 requests a DAY per project (F-70). A fourth independent bucket is worth more
here than a fast model on a bucket that is already empty.

WHAT THIS PINS
--------------
Offline and deterministic. No request leaves the machine: `requests.post` is
replaced, and every assertion is about the payload we would have sent or the
order we would have tried.

  * **absent without a key.** A machine with no `NVIDIA_API_KEY` routes exactly
    as it did before this provider existed — chat chain and tool chain both;
  * **appended, never inserted.** It sits behind every leg that has been measured
    on real turns. Moving it up is `JARVIS_CLOUD_ORDER`, a deliberate act;
  * **thinking is OFF, and sent explicitly.** NVIDIA's own sample enables it and
    reads a `reasoning_content` stream. F-48 is what that costs (reasoning shares
    the answer's token budget — the desk spoke "It is", "System load is") and
    F-49 is worse (a model's private monologue read out loud in the room). The
    flag is sent either way rather than omitted, because the server-side default
    is ON and a default we do not control is how both findings happened;
  * **`reasoning_content` is discarded even so.** If a model ignores the flag,
    the monologue must not reach `content` and be spoken;
  * a JSON caller gets the same belt-and-braces instruction OpenRouter gets —
    prose parsed as "zero actions" is narrated as success;
  * the tool leg reuses `_openai_tool_http`, so this provider is a URL, a key and
    a model id rather than a fourth copy of one request.

**What this does NOT pin, and says so:** whether the model is any good. The
OpenRouter tool list in this same file is ordered by CORRECTNESS, NOT SPEED
because its fastest model invented an argument nobody said. The same measurement
is owed here, against real desk-shaped turns, before this leg moves up the chain
— and it needs a key, which is Kaustav's to generate.

Run standalone: `python test_nvidia_provider.py`
"""

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules import llm_router as r  # noqa: E402
from modules import tool_calls as tc  # noqa: E402

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


KEY = "harness-nvidia-key"


class _Resp:
    """Enough of a `requests` response for the non-streaming path."""

    status_code = 200

    def __init__(self, content="an answer"):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _capture(content="an answer"):
    """Replace requests.post and record what would have been sent."""
    sent: list = []
    real = r.requests.post

    def _post(url, json=None, headers=None, timeout=None, **kw):
        sent.append({"url": url, "payload": json, "headers": headers})
        return _Resp(content)

    r.requests.post = _post

    def restore():
        r.requests.post = real

    return sent, restore


def _with_key(fn, key=KEY):
    was = os.environ.get("NVIDIA_API_KEY")
    os.environ["NVIDIA_API_KEY"] = key
    try:
        return fn()
    finally:
        if was is None:
            os.environ.pop("NVIDIA_API_KEY", None)
        else:
            os.environ["NVIDIA_API_KEY"] = was


def _without_key(fn):
    was = os.environ.pop("NVIDIA_API_KEY", None)
    try:
        return fn()
    finally:
        if was is not None:
            os.environ["NVIDIA_API_KEY"] = was


# ── absent without a key ────────────────────────────────────────────────────

def test_a_machine_with_no_key_routes_exactly_as_before():
    chain = _without_key(lambda: r._route_order("standard", None))
    tools = _without_key(lambda: r._tool_route_order())
    check("nvidia" not in chain, f"the chat chain does not name it: {chain}")
    check("nvidia" not in tools, f"the tool chain does not name it: {tools}")
    check(_without_key(r._nvidia_configured) is False,
          "and the provider reports itself unconfigured")


def test_a_key_puts_it_LAST_rather_than_first():
    """Appended behind every measured leg. Moving it up is a deliberate act."""
    chain = _with_key(lambda: r._route_order("standard", None))
    cloud = [p for p in chain if p != "ollama"]
    check(cloud and cloud[-1] == "nvidia",
          f"it is the last cloud leg, not the first: {cloud}")
    tools = _with_key(lambda: r._tool_route_order())
    check(tools and tools[-1] == "nvidia", f"same for tools: {tools}")


def test_the_order_is_overridable_without_an_edit():
    was = os.environ.get("JARVIS_CLOUD_ORDER")
    os.environ["JARVIS_CLOUD_ORDER"] = "nvidia,groq"
    try:
        chain = _with_key(lambda: r._route_order("standard", None))
        check(chain[:1] == ["ollama"] or chain[0] == "nvidia",
              f"an explicit order is honoured: {chain}")
        check("nvidia" in chain and chain.index("nvidia") < chain.index("groq"),
              "...and puts it ahead of groq when asked")
    finally:
        if was is None:
            os.environ.pop("JARVIS_CLOUD_ORDER", None)
        else:
            os.environ["JARVIS_CLOUD_ORDER"] = was


def test_a_typo_cannot_silence_the_cloud():
    was = os.environ.get("JARVIS_CLOUD_ORDER")
    os.environ["JARVIS_CLOUD_ORDER"] = "nvidiaa,gemeni"
    try:
        chain = _cloud = r._cloud_chain()
        check(set(chain) == set(r._KNOWN_CLOUD),
              f"an all-typo order falls back to every known provider: {chain}")
    finally:
        if was is None:
            os.environ.pop("JARVIS_CLOUD_ORDER", None)
        else:
            os.environ["JARVIS_CLOUD_ORDER"] = was


# ── the thinking flag, which is F-48 and F-49 ───────────────────────────────

def test_thinking_is_off_and_sent_explicitly():
    """The server-side default is ON. Omitting the flag would be relying on a
    default we do not control, which is how both findings happened."""
    payload = r._nvidia_payload("m", [{"role": "user", "content": "hi"}],
                                0.6, 100, False, False)
    check("chat_template_kwargs" in payload,
          "the flag is present rather than left to the server")
    check(payload["chat_template_kwargs"] == {"enable_thinking": False},
          f"...and it is OFF: {payload['chat_template_kwargs']}")
    check(r.NVIDIA_THINKING is False, "the module default is off")


def test_thinking_can_be_turned_on_deliberately():
    was = r.NVIDIA_THINKING
    r.NVIDIA_THINKING = True
    try:
        payload = r._nvidia_payload("m", [], 0.6, 100, False, False)
        check(payload["chat_template_kwargs"] == {"enable_thinking": True},
              "JARVIS_NVIDIA_THINKING=1 turns it back on")
    finally:
        r.NVIDIA_THINKING = was


def test_a_reasoning_stream_never_reaches_the_answer():
    """Belt and braces for a model that ignores the flag: `reasoning_content` is
    read and discarded, so a monologue cannot be spoken."""
    src = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8")
    leg = src.split("def _call_nvidia_model(")[1].split("\n# ====")[0]
    check('delta.get("content")' in leg,
          "the stream reads `content` specifically")
    check("reasoning_content" in leg,
          "...and names the field it is deliberately dropping")


# ── the request itself ──────────────────────────────────────────────────────

def test_the_call_goes_to_nvidia_with_the_key_as_a_bearer():
    sent, restore = _capture()
    try:
        out = _with_key(lambda: r._call_nvidia_model(
            "nvidia/nemotron-3-ultra-550b-a55b",
            [{"role": "user", "content": "hi"}], 0.6, 100, False, False, 30))
    finally:
        restore()
    check(out == "an answer", f"the answer comes back: {out!r}")
    check(len(sent) == 1, "one request was made")
    check("integrate.api.nvidia.com" in sent[0]["url"], f"to NIM: {sent[0]['url']}")
    check(sent[0]["headers"]["Authorization"] == f"Bearer {KEY}",
          "with the key as a bearer token")
    check(sent[0]["payload"]["model"] == "nvidia/nemotron-3-ultra-550b-a55b",
          "and the model id it was asked for")


def test_a_json_caller_gets_the_instruction_as_well_as_the_flag():
    """`response_format` is honoured unevenly. Prose parsed as 'zero actions' is
    narrated as success, which is the failure this belt-and-braces exists for."""
    sent, restore = _capture('{"actions": []}')
    try:
        _with_key(lambda: r._call_nvidia_model(
            "m", [{"role": "user", "content": "do the thing"}],
            0.6, 100, False, True, 30))
    finally:
        restore()
    payload = sent[0]["payload"]
    check(payload.get("response_format") == {"type": "json_object"},
          "the format is requested")
    check("valid JSON" in payload["messages"][-1]["content"],
          "...and said in the prompt too")


def test_the_model_list_is_walked_until_one_answers():
    calls: list = []
    real = r._call_nvidia_model

    def _fake(model, *a, **k):
        calls.append(model)
        if model == r.NVIDIA_MODELS[0]:
            raise RuntimeError("429 busy")
        return "the tail answered"

    r._call_nvidia_model = _fake
    try:
        out = _with_key(lambda: r._call_nvidia(
            [{"role": "user", "content": "hi"}], 0.6, 100, False, False, 30))
    finally:
        r._call_nvidia_model = real
    check(out == "the tail answered", "a busy head falls through to the tail")
    check(calls == list(r.NVIDIA_MODELS), f"in order: {calls}")


def test_no_key_raises_rather_than_posting_anonymously():
    sent, restore = _capture()
    try:
        raised = False
        try:
            _without_key(lambda: r._call_nvidia_model(
                "m", [], 0.6, 100, False, False, 30))
        except RuntimeError:
            raised = True
    finally:
        restore()
    check(raised, "an unset key is an error, not an anonymous request")
    check(not sent, "and nothing was sent")


# ── the tool leg ────────────────────────────────────────────────────────────

def test_the_tool_leg_reuses_the_shared_openai_helper():
    """A fourth copy of the same request is how a fix comes to live in five files
    and drift apart — root cause #4, which this project has paid for twice."""
    src = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8")
    leg = src.split("def _tool_call_nvidia(")[1].split("\ndef ")[0]
    check("_openai_tool_http(" in leg, "the tool leg calls the shared helper")
    check("NVIDIA_TOOL_MODELS" in leg, "...and walks its own model list")
    check('provider="nvidia"' in leg, "...tagged as its own provider")


def test_the_provider_is_registered_where_the_dispatch_reads_it():
    check("nvidia" in tc.TOOL_PROVIDERS,
          f"TOOL_PROVIDERS names it: {tc.TOOL_PROVIDERS}")
    check(tc.TOOL_PROVIDERS[-1] == "nvidia",
          "...last, behind the measured ones")
    src = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8")
    check('elif name == "nvidia":' in src,
          "and the dispatch actually has a branch for it — a provider in the "
          "list with no branch fails as 'cannot serve tool calls'")


def test_the_chat_dispatch_has_its_branch_too():
    src = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8")
    body = src.split("def universal_llm_call(")[1].split("\ndef ")[0]
    check("_call_nvidia(" in body,
          "the chat dispatch calls it, so a routed turn is not silently dropped")
    check("_reset_cloud_breaker(name)" in body.split("_call_nvidia(")[1][:200],
          "...and a success closes its circuit breaker like every other leg")


def test_the_measurement_is_recorded_beside_the_list_it_decided():
    """The honest part. A provider added on a social-media claim has to carry the
    numbers that actually justified its position, where the next reader looks."""
    src = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8")
    note = src.split("# NVIDIA NIM (build.nvidia.com)")[1][:3500]
    check("4/4" in note and "3/4" in note,
          "the tool scores are written down")
    check("instagram" in note.lower(),
          "...and where the '5x faster' claim actually came from")
    check("500" in note,
          "...and that the free tier 500s intermittently, which is not a detail")
    check("CORRECTNESS, NOT SPEED" in note,
          "...and the rule that put the slower model first")


def test_every_configured_id_was_checked_against_the_live_catalogue():
    """The first draft of this list carried `nemotron-nano-9b-v2`, which is an
    OpenRouter id and does not exist on NIM. An id assumed to transfer between
    providers is exactly the rot ladder item 0.3 exists to catch."""
    for m in list(r.NVIDIA_MODELS) + list(r.NVIDIA_TOOL_MODELS):
        check(not m.endswith(":free"),
              f"{m} carries no OpenRouter ':free' suffix - NIM rejects those")
        check(m != "nvidia/nemotron-nano-9b-v2",
              "the id that does not exist on NIM is gone")
    src = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8")
    check("does not exist on NIM" in src,
          "and the mistake is recorded rather than quietly corrected")


# ── the boot preflight, because an unchecked id rots silently ──────────

def test_the_preflight_checks_these_ids_against_the_live_catalogue():
    """Ladder item 0.3 exists because three of four OpenRouter `:free` ids had
    been withdrawn and nothing said so - the paid base ids still exist, so a
    casual look says fine. A new provider whose ids nobody checks is that gap
    reopened."""
    from modules import boot_preflight as bp
    check("nvidia" in bp._CATALOGUE_URLS,
          "the preflight knows where NVIDIA's catalogue is")
    check("integrate.api.nvidia.com" in bp._CATALOGUE_URLS["nvidia"],
          f"...and it is the NIM one: {bp._CATALOGUE_URLS['nvidia']}")

    env = dict(os.environ)
    env["NVIDIA_API_KEY"] = KEY
    ids = [m for p, m, w in bp.configured_models(env) if p == "nvidia"]
    check(set(ids) == set(r.NVIDIA_MODELS) | set(r.NVIDIA_TOOL_MODELS),
          f"every configured id is checked, chat and tool: {ids}")

    env.pop("NVIDIA_API_KEY", None)
    quiet = [m for p, m, w in bp.configured_models(env) if p == "nvidia"]
    check(quiet == [],
          "...and nothing is checked when no key is set, since the router drops "
          "the provider anyway")


def test_the_preflight_authenticates_its_catalogue_request():
    src = (HERE / "modules" / "boot_preflight.py").read_text(encoding="utf-8")
    fetch = src.split("def _default_fetch(")[1].split("\ndef ")[0]
    check("integrate.api.nvidia.com" in fetch and "NVIDIA_API_KEY" in fetch,
          "the fetch sends the key - an unauthenticated catalogue read would "
          "either 401 or answer for somebody else's account")


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
