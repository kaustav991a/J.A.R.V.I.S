"""
action_router.py — Semantic (RAG) action selection
===================================================
Token-efficiency layer for J.A.R.V.I.S.'s MODE-2 planner.

Instead of stuffing the entire ~5.4k-token action catalogue into every action
turn's system prompt (which blew past Groq's free-tier 6k TPM limit), this module
**retrieves only the handful of actions semantically relevant to the user's
request** and assembles a compact, per-turn catalogue.

Design (mirrors personal_rag.py — privacy-first, fully local):
- Vector store : local ChromaDB (persisted on disk; nothing leaves the machine).
- Embeddings   : local HuggingFace `all-MiniLM-L6-v2` — NO cloud embeddings.
- Lazy init    : the model + index build only on first use, so import never blocks boot.

Output per turn ≈ header + tiny always-on ESSENTIALS + top-K retrieved actions +
the routing-rule block for whichever domains those actions belong to. Typically
~700–1,100 tokens vs ~5,400 for the full list — and it stays flat as more actions
are added (retrieval count is fixed), unlike keyword-section gating which grows.

If Chroma / the embedding model is unavailable, `build_catalogue()` returns None so
the caller can fall back to brain.build_action_catalogue() (keyword gating). No turn
ever loses action capability.
"""

from __future__ import annotations

import os

_PERSIST_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "action_chroma_db")
)
_COLLECTION_NAME = "action_catalogue"
_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "all-MiniLM-L6-v2")
_TOP_K = int(os.getenv("JARVIS_ACTION_TOPK", "12"))

# =============================================================================
# ACTION TABLE — the single source of truth for semantic routing.
# Each entry:  (action_type, domain, prompt_line, triggers)
#   prompt_line : exactly what is injected into the system prompt when selected.
#   triggers    : extra natural-language phrases embedded ALONGSIDE the line to
#                 improve recall (never shown to the model).
# Domains: core | pcop | code | comms | tv
# =============================================================================
ACTIONS = [
    # ---- CORE (HUD, search, system, memory, calendar, health) ----
    ("hud_open_widget", "core",
     '- "hud_open_widget": show a HUD panel / full-screen stage. target="vitals"|"mail"|"calendar"|"calculator"|"notepad"|"browser"|"camera"|"map". "camera"=live optical feed ("show me what you see","open your eyes"); "map"=any map/location request.',
     "open the camera feed, show me what you see, open your eyes, optical feed, open the map, show a map, map of, where I stay, location, navigation, directions, show calendar panel, show mail panel, show vitals panel, open notepad, open calculator, open browser panel"),
    ("hud_close_widget", "core",
     '- "hud_close_widget": dismiss that panel/stage. Same targets (incl. "camera","map").',
     "close the camera feed, close your eyes, hide the map, close the panel, dismiss the widget, hide the calendar"),
    ("close_display", "core",
     '- "close_display": close/dismiss the search panel. target="search_panel".',
     "close the search panel, dismiss search results, close the display, stop the embedded video"),
    ("render_chart", "core",
     '- "render_chart": visualise numeric data on the HUD. target={"title":"...","type":"bar"|"line"|"pie","data":[{"label":"Mon","value":12}]}.',
     "chart this, graph this, plot, visualise the numbers, show a bar chart, pie chart, line graph"),
    ("system_status", "core",
     '- "system_status": CPU/RAM/disk diagnostics. target="hardware".',
     "cpu usage, ram, memory usage, disk space, system health, hardware diagnostics, how is the system"),
    ("get_telemetry", "core",
     '- "get_telemetry": full live system snapshot. target="snapshot".',
     "full telemetry, system snapshot, all sensors, live stats"),
    ("os_control", "core",
     '- "os_control": target="mute"|"unmute"|"volume_up"|"volume_down"|"next_track"|"prev_track"|"play_pause"|"lock_screen".',
     "mute, unmute, volume up, volume down, next track, previous track, pause, lock the screen, lock the pc"),
    ("tavily_search", "core",
     '- "tavily_search": FAST AI lookup — PREFER for quick facts, definitions, current events, prices, "what is/who is/when is/latest". target=query. AUTO.',
     "what is, who is, when is, look up, search the web, latest news, current price, define, quick fact"),
    ("web_search", "core",
     '- "web_search": deeper/multi-result research. target=query.',
     "research, deep search, find articles, multiple sources"),
    ("web_search_image", "core",
     '- "web_search_image": ONLY for "show picture"/"what does X look like". target=query.',
     "show me a picture of, what does it look like, find an image of, show photo"),
    ("play_music", "core",
     '- "play_music": target="genre/song on platform" (this PC / HUD embed — NOT the TV).',
     "play music, play a song, put on some, play on youtube, play on spotify, play jazz, play lofi"),
    ("memory_recall", "core",
     '- "memory_recall": retrieve stored facts. target=query.',
     "what do you remember, recall, do you know my, what did I tell you about"),
    ("remember_fact", "core",
     '- "remember_fact": target="Category: fact details".',
     "remember that, note that, store this fact, keep in mind"),
    ("search_documents", "core",
     '- "search_documents": semantic search over the user\'s OWN indexed notes/documents. target=query.',
     "what did I write about, find my notes on, search my documents, my files about"),
    ("morning_briefing", "core",
     '- "morning_briefing": health+calendar+email digest. target="". AUTO.',
     "morning briefing, daily update, how does my day look, what's on today, my agenda, daily summary"),
    ("check_vitals", "core",
     '- "check_vitals": health metrics (voice/readout only). target="vitals".',
     "how am I doing health, my steps, heart rate, my vitals, fitness readout"),
    ("check_calendar", "core",
     '- "check_calendar": today\'s events. target="today". | "create_event": target=event description. | "clear_schedule": target="today".',
     "what's on my calendar, my schedule today, any meetings, add an event, schedule a meeting, clear my schedule"),
    ("find_file", "core",
     '- "find_file": target=filename/query.',
     "find a file, locate the file, where is my file, search for a document named"),
    ("create_note", "core",
     '- "create_note": target="Title: Content".',
     "make a note, jot down, create a note titled"),
    ("read_screen", "core",
     '- "read_screen": OCR the screen. target="screen".',
     "read my screen, what's on screen, ocr the screen, what does the screen say"),
    ("open_sticky_note", "core",
     '- "open_sticky_note"/"close_sticky_note": target="note".',
     "open a sticky note, close sticky note, scratch pad"),
    ("open_calculator", "core",
     '- "open_calculator"/"close_calculator": target="calculator". | "open_browser"/"close_browser": target="browser".',
     "open the calculator, close calculator, open the browser, close browser"),
    ("sleep_protocol", "core",
     '- "sleep_protocol": target="sleep".',
     "go to sleep, stand down, power down, sleep mode, shut yourself down"),
    ("enable_focus_mode", "core",
     '- "enable_focus_mode"/"disable_focus_mode": target="focus". CRITICAL: "disable"→disable_focus_mode, NEVER enable.',
     "turn on focus mode, enable do not disturb, turn off focus mode, disable focus"),

    # ---- PC-OPS (apps, OS, automation, web browsing) ----
    ("native_app_launcher", "pcop",
     '- "native_app_launcher": open/launch an app. target=app name. | "close_app": close an app. target=app name.',
     "open spotify, launch chrome, start notepad, open vs code, run the app, close the app, quit the app"),
    ("ghost_type", "pcop",
     '- "ghost_type": inject text into active app. target="text_to_type|^s" (^s=save). Content ONLY — never the filename. | "ghost_save_file": OS-level save. target="directory|filename".',
     "type this for me, write this in notepad, dictate text, save the file to desktop"),
    ("gui_action", "pcop",
     '- "gui_action": single input. target="keyboard_type"|"keyboard_press"|"mouse_scroll". | "agentic_gui_task": LAST RESORT for complex visual-only tasks.',
     "press a key, scroll the mouse, click around, do it in the GUI"),
    ("run_terminal_command", "pcop",
     '- "run_terminal_command": OS shell op. target="verb: argument". Verbs: list_directory, create_folder, move_file, copy_file, delete_file, list_processes, kill_process, network_info, ping, lock, sleep.',
     "run a terminal command, list the directory, create a folder, delete a file, move file, kill a process, ping, network info"),
    ("os_macro", "pcop",
     '- "os_macro": named OS macro. target="deep_work"|"shallow_work"|"diagnostic"|"entertainment" (deep_work URL: "deep_work:http://localhost:5173"). AUTO.',
     "deep work mode, lock me in, code mode, work mode, end work mode, exit deep work, run diagnostics, open task manager, entertainment mode, movie time"),
    ("run_autopilot", "pcop",
     '- "run_autopilot": overnight Figma→code build. target="<figma_file_key>" (or "key|out_dir"); ask for the key if not given. AUTO.',
     "run autopilot, build the figma design, generate code from figma, overnight build"),
    ("web_browse", "pcop",
     '- "web_browse": navigate to URL. target=url. | "web_click": target=element_id. | "web_type": target="element_id|text". | "web_scroll": "up"/"down". | "web_back": "". | "web_close": "".',
     "browse to a website, go to a url, open this site, click that button, fill the form, scroll the page, go back"),

    # ---- CODE (workspace files + git) ----
    ("workspace_write", "code",
     '- "workspace_write": create/overwrite a project file. target="filepath|file_content". | "workspace_read": read a project file. target=filepath.',
     "write a python file, create a script, generate code, save code to, make a program, write a function, create a class, read the file, open the source file"),
    ("workspace_patch", "code",
     '- "workspace_patch": surgical line edit. target="filepath|exact_search_string|replacement_string". exact_search_string MUST be the LITERAL current text char-for-char (from a prior result) — never a placeholder.',
     "edit the file, change this line, patch the code, replace text in the file, modify the function"),
    ("self_improve", "code",
     '- "self_improve": propose a change to your OWN codebase on a branch, run tests, open a PR (never merges). target="what to improve". CONFIRM.',
     "improve yourself, fix your own code, refactor your codebase, add a feature to yourself, upgrade your own"),
    ("github_status", "code",
     '- "github_status": git status. target="" or repo path. AUTO. | "github_log": last N commits. target="N". AUTO. | "github_diff": diff --stat. AUTO.',
     "git status, what branch, check the repo, git log, recent commits, commit history, what changed, show the diff"),
    ("github_commit", "code",
     '- "github_commit": stage all + commit. target="message". CONFIRM. | "github_push": push to origin. target="". CONFIRM.',
     "commit my changes, git commit, push to github, push to origin"),

    # ---- COMMS (email + telegram) ----
    ("gmail_read_unread", "comms",
     '- "gmail_read_unread": PRIMARY for new/unread mail. target="" (top 5) or "N". AUTO. | "gmail_read": search-based fetch. target=Gmail query or "query|N". AUTO.',
     "check my email, any new emails, unread messages, what's in my inbox, read my mail, find email from, search email about"),
    ("gmail_send", "comms",
     '- "gmail_send": send a NEW email. target="to@email.com | Subject | Body" or JSON. CONFIRM. | "gmail_reply": reply in a thread. target="thread_id | body". CONFIRM.',
     "send an email, email someone, compose a mail, write an email to, reply to that email, respond to the thread"),
    ("check_email", "comms",
     '- "check_email"/"read_email"/"search_email"/"send_email": legacy email equivalents (prefer the gmail_* actions).',
     "legacy email, basic inbox check"),
    ("telegram_send_file", "comms",
     '- "telegram_send_file": send a file to the operator\'s phone. target=filepath or {"path","caption"}.',
     "send me the file, text me the document, deliver the report to my phone, telegram me"),

    # ---- TV ----
    ("tv_power", "tv",
     '- "tv_power": toggle TV on/off via ADB. target="". AUTO. | "tv_volume": target="up"|"down"|"mute" or "up|5". AUTO.',
     "turn on the tv, turn off the television, tv power, tv volume up, tv volume down, mute the tv"),
    ("tv_play_media", "tv",
     '- "tv_play_media": play/search media on TV. target="App: content" if app named THIS message, else bare "content". AUTO. | "tv_launch_app": open a TV app. target=app name. | "tv_search": "App: query".',
     "play on the tv, watch on television, put on netflix, open youtube on tv, play on the big screen, search on tv"),
]

# Routing-rule blocks appended when ANY action from that domain is retrieved.
DOMAIN_RULES = {
    "pcop": (
        "PC-OPS RULES: deep work/lock me in/code mode → os_macro target=\"deep_work\"; "
        "exit/end/unlock/shallow work → \"shallow_work\"; diagnostics/task manager → \"diagnostic\"; "
        "entertainment/movie time → \"entertainment\". NEVER native_app_launcher for these macros. "
        "Addressing JARVIS by name is NOT deep-work intent. NEVER the native_app/ghost chain for code/files with extensions — use workspace_*."
    ),
    "code": (
        "CODE RULES: ANY filename with an extension (.py/.js/.jsx/.ts/.json/.html/.css/.md/.txt/.exe…) OR any "
        "write/create/generate code/script/program/function/class — EVEN \"save to desktop\" — → workspace_* ONLY, "
        "NEVER the Notepad chain. status/log/diff = AUTO; commit/push = CONFIRM. NEVER raw git via run_terminal_command. "
        "workspace_patch search-string must be LITERAL file text, char-for-char."
    ),
    "comms": (
        "EMAIL RULES: \"check/any new/unread\" → gmail_read_unread; \"find/search email about X\" → gmail_read; "
        "\"send/email X saying Y\" → gmail_send (ask recipient if missing; fill to+subject+body); \"reply to thread\" → gmail_reply (needs thread_id). "
        "OUTGOING VOICE: subjects/bodies are real mail FROM the user TO the recipient — first-person to them, normal greeting/sign-off, "
        "NEVER address the user (\"Sir\") or use assistant-to-user wording in the mail."
    ),
    "tv": (
        "TV RULES (only when TV/television/big screen named): \"turn on/off TV\" → tv_power; \"TV volume up/down/mute\" → tv_volume; "
        "\"open <app> on TV\" (no media) → tv_launch_app; \"play/watch/search <content> on TV\" → tv_play_media "
        "(bare target if app not named; NEVER guess an app from memory). NEVER tv_cast."
    ),
}

# Always-on header + the few essentials retrieval must never miss.
_ESSENTIALS = (
    "Available Actions for JSON Output (a focused subset relevant to this request):\n"
    "ALWAYS-AVAILABLE: hud_open_widget / hud_close_widget (targets incl. \"camera\",\"map\"), close_display, morning_briefing.\n"
    "CHAINING: multiple distinct tasks → all actions in one JSON array. EXCEPT briefings: never chain "
    "health+calendar+email; use morning_briefing alone.\n"
    "Example: {\"actions\": [{\"action_type\": \"hud_open_widget\", \"target\": \"map\"}]}\n"
)

_collection = None
_index_ready = False


def _ensure():
    """Lazily build the persistent collection with a local HF embedding function."""
    global _collection
    if _collection is not None:
        return _collection
    import chromadb
    from chromadb.utils import embedding_functions

    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=_EMBED_MODEL)
    client = chromadb.PersistentClient(path=_PERSIST_DIR)
    _collection = client.get_or_create_collection(name=_COLLECTION_NAME, embedding_function=embed_fn)
    return _collection


def _index(col) -> None:
    """(Re)embed the ACTION table. Rebuilt whenever the action count changes so edits
    to ACTIONS take effect automatically. Cheap — only ~45 short documents."""
    global _index_ready
    if _index_ready:
        return
    try:
        if col.count() == len(ACTIONS):
            _index_ready = True
            return
        # Count drift (first run or ACTIONS edited) → rebuild from scratch.
        existing = col.get().get("ids", [])
        if existing:
            col.delete(ids=existing)
        col.upsert(
            ids=[a[0] for a in ACTIONS],
            documents=[f"{a[0]}. {a[2]} triggers: {a[3]}" for a in ACTIONS],
            metadatas=[{"action_type": a[0], "domain": a[1], "line": a[2]} for a in ACTIONS],
        )
        _index_ready = True
        print(f"[ACTION_ROUTER] Indexed {len(ACTIONS)} actions into '{_COLLECTION_NAME}'.", flush=True)
    except Exception as e:
        print(f"[ACTION_ROUTER] Index build failed: {e}", flush=True)


def build_catalogue(intent: str, user_text: str, k: int = _TOP_K) -> str | None:
    """Return a compact, semantically-retrieved action catalogue for this turn, or
    None if the vector store / embedding model is unavailable (caller falls back to
    keyword gating). Always includes ESSENTIALS so common HUD control is never lost."""
    query = (user_text or "").strip()
    if not query:
        return None
    try:
        col = _ensure()
        _index(col)
        res = col.query(query_texts=[query], n_results=min(k, len(ACTIONS)))
    except Exception as e:
        print(f"[ACTION_ROUTER] retrieval unavailable, falling back: {e}", flush=True)
        return None

    metas = (res.get("metadatas") or [[]])[0]
    if not metas:
        return None

    lines, domains, seen = [], [], set()
    for m in metas:
        line = m.get("line")
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
        d = m.get("domain")
        if d and d not in domains:
            domains.append(d)

    parts = [_ESSENTIALS, "\n".join(lines)]
    for d in domains:
        if d in DOMAIN_RULES:
            parts.append(DOMAIN_RULES[d])
    return "\n".join(parts)
