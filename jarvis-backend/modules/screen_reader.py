import os
import base64
from io import BytesIO

# Configuration toggle. "auto" (Phase 5 default) = the router's free-vision
# cascade: Gemini flash first (big quality upgrade over CPU llava), local llava
# as the offline fallback. "ollama"/"groq" force a single provider.
VLM_PROVIDER = os.getenv("JARVIS_VLM_PROVIDER", "auto").lower()
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")

_SCREEN_PROMPT = (
    "Analyze this screenshot of my computer screen. Describe the active "
    "applications, any visible text, the overall context, and what I am "
    "currently doing. Be concise, highly descriptive, and focus on the most "
    "important elements."
)

# ── F-61: the operating system already knows what is open ─────────────────────
# Live-gate session 4, row 12.1. With ollama finally up, the desk said:
#
#   "A code editor (VS Code) displaying a Python script handling JSON, a Chrome
#    window with a Google Sheets document, and a terminal window are open, Sir."
#
# I captured the same screen and looked at it: a VS Code-family editor showing a
# MARKDOWN file, a terminal panel, and no Chrome window on screen at all. Two of
# four claims invented, delivered with the same confidence as the two that were
# right, and nothing marked the answer as uncertain.
#
# `llava` on a CPU box at JPEG-75 is going to guess. The fix is not a better
# model — it is to stop asking the model for facts the machine can simply look
# up. The window list is authoritative, costs nothing, and it reaches BOTH models:
# the VLM is told not to name anything absent from it, and the reasoning model
# that writes the spoken sentence gets the same list plus an explicit note about
# any application the description mentioned that is not actually open.
#
# Same principle as F-09's "absence reaches the model as absence": a fact the
# system holds must be handed over, not inferred.
_GROUND_TRUTH_RULE = (
    "\n\nGROUND TRUTH — the operating system reports these windows as open right "
    "now. This list is authoritative and complete:\n{windows}\n"
    "Rules you must follow: describe only what is legible in the image; you may "
    "NOT name an application, document, website or file that is absent from that "
    "list; if you cannot identify something, say that you cannot rather than "
    "guessing a likely name."
)

#: Applications and sites specific enough that naming one wrongly is a false
#: statement rather than a vague one. Checked against the real window titles.
_VERIFIABLE_NAMES = (
    "google sheets", "google docs", "google slides", "excel", "word",
    "powerpoint", "outlook", "gmail", "slack", "discord", "teams", "zoom",
    "photoshop", "illustrator", "premiere", "after effects", "figma",
    "chrome", "firefox", "edge", "safari", "notepad", "spotify", "youtube",
    "whatsapp", "telegram", "netflix", "photopea", "blender", "unity",
)


def _open_window_titles(limit: int = 14) -> list:
    """Titles of the real top-level windows, straight from the OS.

    Best-effort by design: an empty list simply means the rule below is omitted,
    never that the screen read fails.
    """
    titles = []
    try:
        import pygetwindow as gw          # ships with pyautogui on Windows
        titles = [t.strip() for t in gw.getAllTitles() if t and t.strip()]
    except Exception:                      # noqa: BLE001
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def _cb(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value.strip():
                        found.append(buf.value.strip())
                return True

            user32.EnumWindows(_cb, 0)
            titles = found
        except Exception:                  # noqa: BLE001
            titles = []
    out, seen = [], set()
    for t in titles:
        key = t.lower()
        if key in seen or len(t) < 2:
            continue
        seen.add(key)
        out.append(t)
    return out[:limit]


def _unverified_names(description: str, titles: list) -> list:
    """Specific applications the description named that are NOT actually open."""
    low_desc = (description or "").lower()
    joined = " | ".join(titles).lower()
    return [n for n in _VERIFIABLE_NAMES if n in low_desc and n not in joined]

def _call_groq_vision(img_b64: str, prompt: str = _SCREEN_PROMPT) -> str:
    """Sends the base64 image to Groq's vision model.

    The id used to be the literal `llama-3.2-90b-vision-preview`, which Groq has
    retired — so this leg answered 404 and nobody noticed, because it only runs
    after Gemini has already failed. `test_single_source.py` found it by counting
    hardcoded model ids. It resolves through `groq_vision_model()` now, one place,
    shared with the cloud gateway's `GROQ_VISION_MODEL`.

    The one live vision id on this account streams a `<think>` block inside its
    content, so the result is stripped before it is returned as data — otherwise
    the monologue lands in the SCREEN CONTENTS block and the answering model reads
    it as observation.
    """
    from modules.groq_key_manager import groq_vision_model, run_with_key_rotation
    from modules import reasoning_guard

    def _api_call(client):
        response = client.chat.completions.create(
            model=groq_vision_model(),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            # F-61: the caller's prompt, not a third hardcoded
                            # copy of it. This leg ignored its own parameter, so
                            # the grounding rule would have reached llava and the
                            # router and silently skipped Groq.
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url", 
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                        }
                    ]
                }
            ],
            max_tokens=1024,
            temperature=0.2
        )
        return reasoning_guard.strip_reasoning(
            response.choices[0].message.content or "")

    return run_with_key_rotation(_api_call)

def _call_ollama_vision(img_b64: str, prompt: str = _SCREEN_PROMPT) -> str:
    """Sends the base64 image to a local Ollama Vision model (e.g., llava).

    F-61: the prompt is a PARAMETER now. It used to be a hardcoded copy of
    `_SCREEN_PROMPT`, so the grounding rule reached the router's cascade and the
    Groq leg while the local llava leg — the one that actually answers on this
    CPU box — kept asking the old unanchored question. Root cause #4 in one file.
    """
    import requests

    url = "http://localhost:11434/api/generate"
    payload = {
        "model": OLLAMA_VISION_MODEL,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False
    }

    # Tier 3.2: the same budget the router's leg consults, through the same
    # function -- this entry point is the one used when JARVIS_VLM_PROVIDER=ollama
    # forces the local model, so it cannot rely on the cascade having asked. A
    # tight load gets a longer deadline and a short keep_alive; it is never
    # refused, because here there is no cloud provider behind it at all.
    from modules import ram_budget
    deadline = ram_budget.apply(OLLAMA_VISION_MODEL, payload, 120.0,
                                "screen reader")

    response = requests.post(url, json=payload, timeout=deadline)
    response.raise_for_status()
    return response.json().get("response", "")

def read_active_screen() -> str:
    """Captures the primary monitor and extracts context using a Vision-Language Model (VLM)."""
    try:
        import pyautogui
        
        # 1. Capture screenshot
        screenshot = pyautogui.screenshot()
        
        # 2. Compress and convert to base64
        buffered = BytesIO()
        screenshot.save(buffered, format="JPEG", quality=75) # Optimize size for fast API transfer
        img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        print(f"[SCREEN READER] Uploading screenshot to {VLM_PROVIDER.upper()} Vision Model...")

        # F-61: hand the model the window list rather than letting it invent one.
        titles = _open_window_titles()
        prompt = _SCREEN_PROMPT
        if titles:
            prompt += _GROUND_TRUTH_RULE.format(
                windows="\n".join(f"  - {t}" for t in titles))
            print(f"[SCREEN READER] grounding on {len(titles)} open window(s) "
                  f"reported by the OS.", flush=True)

        # 3. Route to the configured VLM provider
        if VLM_PROVIDER == "ollama":
            description = _call_ollama_vision(img_b64, prompt)
        elif VLM_PROVIDER == "groq":
            description = _call_groq_vision(img_b64, prompt)
        else:  # "auto" — Phase 5 cascade: Gemini flash → local llava
            from modules.llm_router import universal_vision_call
            description = universal_vision_call(prompt, img_b64)

        if not description or not description.strip():
            return "No description could be generated from the screen."

        # F-61, second half: the reasoning model that writes the spoken sentence
        # gets the same authoritative list, and an explicit note about anything
        # the description named that is not actually open. It is a NOTE rather
        # than surgery on the text because the window list cannot confirm what IS
        # legible in the image — only what is not running at all.
        if titles:
            description += ("\n\nWINDOWS OPEN (operating system, authoritative):\n"
                            + "\n".join(f"  - {t}" for t in titles))
            bogus = _unverified_names(description, titles)
            if bogus:
                print(f"[SCREEN READER] the description named {bogus}, which the OS "
                      f"does not have open — flagged as unverified.", flush=True)
                description += (
                    "\n\nUNVERIFIED: the description above mentions "
                    + ", ".join(sorted(bogus))
                    + " — none of these are among the open windows. Do not repeat "
                      "them as fact; say instead that you could not identify that "
                      "part of the screen.")
        return description

    except ImportError as e:
        if "pyautogui" in str(e):
            return "Screen reading offline: pyautogui is not installed."
        raise e
    except Exception as e:
        return f"Screen reading VLM offline: {e}"
