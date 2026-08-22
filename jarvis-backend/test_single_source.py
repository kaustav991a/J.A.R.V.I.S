r"""test_single_source.py — one implementation per surface, enforced.

Run: venv\Scripts\python.exe test_single_source.py

WHY THIS EXISTS
---------------
Root cause #4 in this project's own numbering: *a class fixed one site at a time
stays open*. It is not an occasional slip, it is the single most productive bug
generator in the repo. Live-gate session 4 raised 16 findings and **8 of them were
this one habit**:

  F-46  a decommissioned Groq model id, hardcoded in FIVE files
  F-49  the cloud gateway had stripped reasoning since August; the desk had no
        guard at all
  F-51  F-22 fixed the relative form of a path and left the absolute form open
  F-58  `agent_tools` grew `offset`/`limit`; the reader underneath still refused
        the whole file before either could apply
  F-62  two stranger-debounces, opposite conclusions, and the twitchy one owned
        the phone
  F-63  a flag set and cleared in one branch only
  F-61  and while fixing it, TWO more inside a single file: one leg held a
        hardcoded copy of the prompt, the other took a `prompt` parameter and
        sent its own string anyway

Every one of those was findable by asking "how many places implement this?" and
counting. That question is cheap, mechanical, and nobody was asking it. So the
suite asks it now.

WHAT THIS IS NOT
----------------
Not a style checker and not a duplicate-code detector. Each pin below names a
surface where a SECOND implementation caused, or would cause, a specific observed
failure — and says which one. A pin without a failure behind it is noise, and
noise is how a harness earns the right to be ignored.

Its first run found a live defect: `clean_response` was guarded at two of its
three sites, and the unguarded one was the voice loop — the door he actually
uses — so the HUD rendered a model's monologue that the speaker had already
stripped from the audio, and episodic memory stored it.

HOW TO ADD A PIN
----------------
Add it when you fix a root-cause-#4 bug, and make it assert the shape you just
made true. If a pin starts failing because the code legitimately grew a second
site, the fix is to route both through one place — not to raise the count.
"""

import ast
import pathlib
import re
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


def _src(rel: str) -> str:
    return (HERE / rel).read_text(encoding="utf-8", errors="replace")


def _py_sources(skip_tests=True):
    """Every production .py under the backend, as (relpath, text)."""
    skip_dirs = {"venv", "__pycache__", "node_modules", ".git", "captures",
                 "metrics", "evals"}
    for p in sorted(HERE.rglob("*.py")):
        if any(part in skip_dirs for part in p.parts):
            continue
        if skip_tests and p.name.startswith("test_"):
            continue
        yield p.relative_to(HERE).as_posix(), p.read_text(encoding="utf-8",
                                                          errors="replace")


# ── 1. Spoken output: one funnel, one guard ─────────────────────────────────

def test_synthesis_cannot_be_reached_around_the_guard():
    """`speak_text` guards, so nothing may call the synthesisers directly.

    F-49: the desk spoke a model's monologue aloud. The guard went into
    `speak_text` precisely because all 120-odd call sites in main.py funnel
    through it — but that only holds while the two synthesisers stay private to
    it.
    """
    callers = []
    for rel, src in _py_sources():
        if rel == "speaker.py":
            continue
        if re.search(r"\b_speak_(cloud|local)\s*\(", src):
            callers.append(rel)
    check(not callers,
          f"only speaker.py reaches the synthesisers ({callers})")

    sp = _src("speaker.py")
    tree = ast.parse(sp)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "speak_text"), None)
    check(fn is not None, "speak_text exists")
    body = ast.get_source_segment(sp, fn) or ""
    check("guard_spoken" in body, "and it guards before it speaks")
    for name in ("_speak_local", "_speak_cloud"):
        check(f"{name}(" in body, f"and it is the caller of {name}")


def test_every_llm_reply_is_guarded_before_it_is_shown_or_stored():
    """All THREE `clean_response` sites, not two of them.

    This is the pin that found F-66. `reasoning_guard` was added at the remote
    door and the backdoor door; the voice loop — the microphone, the door he
    actually uses — was missed. `speak_text` still stripped the monologue from
    the audio, so the symptom was a HUD frame and an episodic-memory row that
    disagreed with what was said aloud.
    """
    src = _src("main.py")
    tree = ast.parse(src)
    assigns = [n for n in ast.walk(tree)
               if isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "clean_response"
                       for t in n.targets)
               and isinstance(n.value, ast.BoolOp)]   # the `preamble or strip_fences(...)` shape
    check(len(assigns) >= 3,
          f"every dispatch path computes a reply ({len(assigns)} found)")

    guarded = src.count("reasoning_guard.guard_spoken")
    check(guarded >= len(assigns),
          f"and every one of them is guarded ({guarded} guards for "
          f"{len(assigns)} computations)")

    # Each computation must be followed by a guard before anything consumes it.
    lines = src.splitlines()
    for node in assigns:
        window = "\n".join(lines[node.lineno - 1: node.lineno + 12])
        check("guard_spoken" in window,
              f"the reply computed at main.py:{node.lineno} is guarded within "
              f"12 lines")


# ── 2. Model ids: one place, one resolver ──────────────────────────────────

def test_no_module_hardcodes_a_groq_model_id():
    """F-46: five files held `llama-3.1-8b-instant` when Groq retired it.

    `test_model_ids.py` pins that no RETIRED id appears. This pins the shape that
    made the retirement expensive: a model id as a literal argument, anywhere but
    the one module that owns it.
    """
    offenders = []
    for rel, src in _py_sources():
        if rel.endswith("groq_key_manager.py") or rel.endswith("llm_router.py"):
            continue
        for m in re.finditer(r'model\s*=\s*["\']([^"\']+)["\']', src):
            ident = m.group(1)
            if ("/" in ident or "llama" in ident or "gpt-oss" in ident
                    or "qwen" in ident or "gemini" in ident or "gemma" in ident):
                line = src[:m.start()].count("\n") + 1
                offenders.append(f"{rel}:{line} -> {ident}")
    check(not offenders,
          f"no module hardcodes a provider model id ({offenders})")

    from modules import groq_key_manager as gkm
    check(callable(getattr(gkm, "groq_model", None)),
          "the one resolver exists")


# ── 3. Prompts: written once ────────────────────────────────────────────────

def test_a_prompt_exists_in_exactly_one_place():
    """F-61's two extra bugs. The screen prompt was written three times: the
    module constant, a hardcoded copy inside the ollama leg, and a third copy
    inside the Groq leg that ignored its own `prompt` parameter. A grounding rule
    added to the constant reached none of them."""
    sr = _src("modules/screen_reader.py")
    check(sr.count("Analyze this screenshot") == 1,
          f"the screen prompt appears once ({sr.count('Analyze this screenshot')})")

    # Both legs must take the caller's prompt and use it.
    tree = ast.parse(sr)
    for name in ("_call_ollama_vision", "_call_groq_vision"):
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == name), None)
        check(fn is not None, f"{name} exists")
        args = [a.arg for a in fn.args.args]
        check("prompt" in args, f"{name} takes the prompt")
        body = ast.get_source_segment(sr, fn) or ""
        check(re.search(r'["\']?\bprompt\b["\']?\s*[,:)]|:\s*prompt\b|=\s*prompt\b',
                        body) is not None,
              f"{name} passes it on rather than substituting its own")


# ── 4. Security alerts: one door ────────────────────────────────────────────

def test_the_stranger_alert_has_one_implementation_and_one_guard():
    """F-62: two debounces disagreed and the twitchy one owned the phone. The
    grace check must live in the alert itself, so a third call site inherits it."""
    src = _src("gesture_daemon.py")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "_stranger_alert"), None)
    check(fn is not None, "there is exactly one _stranger_alert")
    body = ast.get_source_segment(src, fn) or ""
    check("ALERT_OWNER_GRACE_S" in body,
          "and the owner-grace check is inside it, not at the call sites")

    # Nothing else may WRITE a stranger snapshot. Matching on the filename it
    # writes, not on the word "stranger" — the first version of this pin flagged
    # action_engine.py for the phrase "Stranger Things" in a Netflix deep-link
    # comment, and telegram_bot.py for merely DEFINING the transport. A pin that
    # cries wolf is worse than no pin.
    writers = []
    for rel, s in _py_sources():
        if rel == "gesture_daemon.py":
            continue
        if 'f"stranger_' in s or "'stranger_" in s or '"stranger_' in s:
            writers.append(rel)
    check(not writers, f"only the daemon writes a stranger snapshot ({writers})")


# ── 5. Path containment: one resolver ──────────────────────────────────────

def test_workspace_containment_is_decided_in_one_function():
    """F-22 and F-51 were the same guard, fixed twice, six months apart. Every
    workspace path must be judged by `_resolve_within_roots` — a second
    containment check is a second place to get it wrong."""
    from modules import workspace_agent as wa

    check(callable(getattr(wa.WorkspaceAgent, "_resolve_within_roots", None)),
          "the one resolver exists")

    offenders = []
    for rel, src in _py_sources():
        if rel.endswith("workspace_agent.py"):
            continue
        if "WORKSPACE_ROOTS" in src and "relative_to" in src:
            offenders.append(rel)
    check(not offenders,
          f"nothing else re-implements root containment ({offenders})")


# ── 6. Config precedence: the inventory is deliberate ──────────────────────

def test_the_dotenv_override_inventory_is_pinned():
    """F-65, and F-39 before it, are the same rule in opposite directions.

    `load_dotenv(override=True)` means `.env` WINS over the environment. F-39:
    an empty key in `.env` erased what the operator set on the command line.
    F-65: `.env` restored `TAVILY_API_KEY` after the operator cleared it, so a
    gate row that says "temporarily unset it" could never be performed and two
    attempts at that row silently ran against a live key.

    The count is pinned rather than forbidden — the behaviour is deliberate. What
    must not happen is a NINTH module quietly acquiring it, because every one of
    them is a place where an operator's action can be undone in silence.
    """
    callers = sorted(rel for rel, src in _py_sources()
                     if "load_dotenv(override=True)" in src)
    expected = {
        "brain.py", "cloud_gateway.py", "main.py", "memory.py",
        "memory_manager.py", "modules/groq_key_manager.py",
        "modules/human_gui_agent.py", "sensors.py",
    }
    check(set(callers) == expected,
          f"the override inventory is unchanged\n      expected: "
          f"{sorted(expected)}\n      found:    {callers}")


# ── 7. Confirmation vocabulary: one helper, three doors ────────────────────

def test_the_confirmation_words_live_in_one_place():
    """F-40 and F-42 were open at all three governance doors at once, because
    each door had its own word list. `test_confirm_path.py` pins the behaviour;
    this pins that there is still only one list to change."""
    src = _src("main.py")
    check(src.count("def _read_confirmation_answer") == 1,
          "one confirmation reader")
    doors = src.count("_read_confirmation_answer(")
    check(doors >= 4,
          f"and every door calls it ({doors - 1} call sites + the definition)")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 66)
    print("Single source of truth — root cause #4, asked mechanically")
    print("=" * 66)
    for t in TESTS:
        t()
    print("-" * 66)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
