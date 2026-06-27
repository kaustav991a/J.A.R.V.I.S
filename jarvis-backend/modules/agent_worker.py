"""
agent_worker.py — Overnight Autopilot (Phase 3, LangGraph)
==========================================================

A cyclic, self-healing LangGraph StateGraph that turns a Figma design into
standards-compliant HTML/SCSS while you sleep.

Pipeline (nodes):
    parse_figma → retrieve_standards → generate_code → validate_syntax → save_files

Self-healing edge:
    If validate_syntax finds HTML/SCSS errors, loop back to generate_code with the
    error traceback appended to the prompt — up to MAX_RETRIES (3). Otherwise proceed
    to save_files and END.

Design notes:
- `generate_code` uses the Anthropic Claude API for the heavy lifting when
  ANTHROPIC_API_KEY is set; otherwise it falls back to the router's 'heavy' cloud path
  (Groq) and finally local Ollama — so the pipeline degrades gracefully.
- langgraph / anthropic are imported LAZILY inside functions, so importing this module
  never blocks J.A.R.V.I.S. startup or crashes the boot if the packages are absent.
- `launch_autopilot()` schedules the whole graph as a background asyncio task so it
  never blocks the main voice loop.
"""

import os
import re
import json
import asyncio
import traceback
from typing import TypedDict

MAX_RETRIES = 3
_ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")  # current, capable default


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
class AutopilotState(TypedDict, total=False):
    file_key: str
    out_dir: str
    token: str
    figma_tokens: dict
    standards: list
    code: dict           # {filename: content}
    errors: str          # validation traceback for the self-healing loop
    retries: int
    status: str


# ---------------------------------------------------------------------------
# Heavy-lift LLM (Claude preferred, graceful fallbacks)
# ---------------------------------------------------------------------------
def _generate_with_llm(prompt: str) -> str:
    # 1) Anthropic Claude (heavy lifting), if configured.
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=_ANTHROPIC_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(
                block.text for block in msg.content if getattr(block, "type", None) == "text"
            )
        except Exception as e:
            print(f"[AUTOPILOT] Anthropic call failed ({e}); falling back to router heavy path.", flush=True)

    # 2) Router 'heavy' path → cloud (Groq) → local Ollama fallback.
    from modules.llm_router import universal_llm_call
    return universal_llm_call(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=4096,
        stream=False,
        json_mode=False,
        timeout=120.0,
        complexity="heavy",
    )


def _build_prompt(state: AutopilotState) -> str:
    tokens = json.dumps(state.get("figma_tokens", {}), indent=2)[:6000]
    standards = "\n\n".join(state.get("standards", []))[:6000]
    error_block = ""
    if state.get("errors"):
        error_block = (
            "\n\n--- PREVIOUS ATTEMPT FAILED VALIDATION ---\n"
            f"{state['errors']}\n"
            "Fix these issues. Ensure every HTML tag and every SCSS brace is balanced.\n"
        )
    return f"""You are a senior front-end engineer. Convert the following Figma design tokens
into production HTML and SCSS that STRICTLY follow the team standards below.

--- TEAM STANDARDS (authoritative) ---
{standards}

--- FIGMA DESIGN TOKENS ---
{tokens}
{error_block}
Output ONLY a JSON object of the form:
{{"files": {{"index.html": "<!doctype html>...", "styles.scss": "..."}}}}
No prose, no markdown fences — just the JSON object."""


def _parse_code_response(raw: str) -> dict:
    """Extract {filename: content} from the LLM response (tolerant of fences/prose)."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            files = obj.get("files", obj)
            if isinstance(files, dict):
                return {k: str(v) for k, v in files.items()}
        except json.JSONDecodeError:
            pass
    # Fallback: dump the raw response so the run still produces an artifact to inspect.
    return {"generated_output.txt": raw}


# ---------------------------------------------------------------------------
# Syntax validation (heuristic, dependency-free)
# ---------------------------------------------------------------------------
def _validate_code(code: dict) -> str:
    """Return an error string if HTML/SCSS looks malformed, else ''."""
    problems: list[str] = []
    for fname, content in code.items():
        lower = fname.lower()
        if lower.endswith((".scss", ".css")):
            opens, closes = content.count("{"), content.count("}")
            if opens != closes:
                problems.append(f"{fname}: unbalanced braces ({opens} '{{' vs {closes} '}}').")
        if lower.endswith((".html", ".htm")):
            # crude tag balance over non-void, non-self-closing tags
            tags = re.findall(r"<\s*(/?)([a-zA-Z][\w-]*)([^>]*?)(/?)\s*>", content)
            void = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                    "link", "meta", "param", "source", "track", "wbr", "!doctype"}
            stack: list[str] = []
            for closing, tag, _attrs, selfclose in tags:
                t = tag.lower()
                if t in void or selfclose:
                    continue
                if not closing:
                    stack.append(t)
                else:
                    if stack and stack[-1] == t:
                        stack.pop()
                    else:
                        problems.append(f"{fname}: mismatched closing </{t}>.")
            if stack:
                problems.append(f"{fname}: {len(stack)} unclosed tag(s): {', '.join(stack[:5])}.")
    return "\n".join(problems)


# ---------------------------------------------------------------------------
# Graph nodes (async; offload blocking work to threads)
# ---------------------------------------------------------------------------
async def _node_parse_figma(state: AutopilotState) -> dict:
    from modules import figma_parser
    print("[AUTOPILOT] node: parse_figma", flush=True)
    tokens = await figma_parser.extract_from_figma(state["file_key"], state.get("token"))
    return {"figma_tokens": tokens, "status": "parsed"}


async def _node_retrieve_standards(state: AutopilotState) -> dict:
    from modules import rag_cortex
    print("[AUTOPILOT] node: retrieve_standards", flush=True)
    hits = await rag_cortex.aquery_standards(
        "BEM SCSS naming, auto-layout to flexbox, spacing tokens, typography scale", n_results=6
    )
    return {"standards": hits, "status": "standards_retrieved"}


async def _node_generate_code(state: AutopilotState) -> dict:
    print(f"[AUTOPILOT] node: generate_code (attempt {state.get('retries', 0) + 1})", flush=True)
    prompt = _build_prompt(state)
    raw = await asyncio.to_thread(_generate_with_llm, prompt)
    code = _parse_code_response(raw)
    return {"code": code, "status": "generated"}


async def _node_validate_syntax(state: AutopilotState) -> dict:
    print("[AUTOPILOT] node: validate_syntax", flush=True)
    errors = await asyncio.to_thread(_validate_code, state.get("code", {}))
    return {
        "errors": errors,
        "retries": state.get("retries", 0) + (1 if errors else 0),
        "status": "invalid" if errors else "valid",
    }


async def _node_save_files(state: AutopilotState) -> dict:
    out_dir = os.path.abspath(state.get("out_dir") or "autopilot_output")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for fname, content in (state.get("code") or {}).items():
        safe = os.path.basename(fname)  # never escape out_dir
        path = os.path.join(out_dir, safe)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(safe)
    print(f"[AUTOPILOT] node: save_files → {written} in {out_dir}", flush=True)
    return {"status": "saved"}


def _route_after_validate(state: AutopilotState) -> str:
    """Self-healing decision: retry generation on errors (bounded), else save."""
    if state.get("errors") and state.get("retries", 0) < MAX_RETRIES:
        print(f"[AUTOPILOT] validation failed → looping back (retry {state.get('retries')}/{MAX_RETRIES}).", flush=True)
        return "retry"
    return "done"


# ---------------------------------------------------------------------------
# Graph builder + runner
# ---------------------------------------------------------------------------
def _build_graph():
    from langgraph.graph import StateGraph, END

    g = StateGraph(AutopilotState)
    g.add_node("parse_figma", _node_parse_figma)
    g.add_node("retrieve_standards", _node_retrieve_standards)
    g.add_node("generate_code", _node_generate_code)
    g.add_node("validate_syntax", _node_validate_syntax)
    g.add_node("save_files", _node_save_files)

    g.set_entry_point("parse_figma")
    g.add_edge("parse_figma", "retrieve_standards")
    g.add_edge("retrieve_standards", "generate_code")
    g.add_edge("generate_code", "validate_syntax")
    g.add_conditional_edges(
        "validate_syntax",
        _route_after_validate,
        {"retry": "generate_code", "done": "save_files"},
    )
    g.add_edge("save_files", END)
    return g.compile()


async def run_autopilot_task(
    file_key: str,
    out_dir: str = "autopilot_output",
    token: str | None = None,
    broadcast=None,
    speak=None,
) -> dict:
    """
    Run the full Figma→code pipeline once. Returns the final state.
    Fully sandboxed: any failure is trapped and reported, never propagated to the loop.
    """
    async def _notify(payload):
        if broadcast:
            try:
                await broadcast(payload)
            except Exception:
                pass

    await _notify({"status": "autopilot_started", "file_key": file_key})
    try:
        graph = _build_graph()
        initial: AutopilotState = {
            "file_key": file_key, "out_dir": out_dir, "token": token or "",
            "retries": 0, "errors": "", "status": "init",
        }
        final = await graph.ainvoke(initial)
        ok = final.get("status") == "saved"
        await _notify({
            "status": "autopilot_done" if ok else "autopilot_failed",
            "file_key": file_key,
            "files": list((final.get("code") or {}).keys()),
            "retries": final.get("retries", 0),
        })
        if speak:
            try:
                await speak(
                    "Overnight build complete, Sir. The interface is ready for your review."
                    if ok else
                    "I attempted the overnight build, Sir, but couldn't fully validate the output."
                )
            except Exception:
                pass
        return final
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[AUTOPILOT] pipeline crashed: {e}\n{tb}", flush=True)
        await _notify({"status": "autopilot_failed", "file_key": file_key, "error": str(e)})
        return {"status": "crashed", "error": str(e)}


def launch_autopilot(file_key: str, out_dir: str = "autopilot_output",
                     token: str | None = None, broadcast=None, speak=None) -> "asyncio.Task":
    """Fire-and-forget: schedule the autopilot as a background task (never blocks the loop)."""
    return asyncio.create_task(
        run_autopilot_task(file_key, out_dir=out_dir, token=token, broadcast=broadcast, speak=speak)
    )
