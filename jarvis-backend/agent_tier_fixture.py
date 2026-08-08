"""Shared governance tiers for the agent harnesses — fake ruleset, one copy.

Not named `test_*`, so `run_harnesses.py` does not try to run it as a harness.

WHY THIS EXISTS
---------------
Every agent harness builds the real `ToolRegistry` against a FAKE tier lookup,
so no test reads `governance.json` (except the one that deliberately
cross-checks against it). Each harness used to carry its own copy of that map.
That was fine while the registry had ten tools; it stopped being fine the moment
§6.8.2 started adding tools in waves, because `ToolRegistry.register` refuses an
action governance does not know — an unknown type fails closed to BLOCK — so
**every new tool broke five harnesses in five identical ways.**

Adding a wave now means adding its action types HERE, once.

The values mirror `governance.json`. They are not read from it on purpose: a
harness that reads the shipped ruleset cannot prove how the registry behaves
when the ruleset says something else, and BLOCK-tier entries below are how the
"a blocked action can never become a tool" guarantee is exercised at all.
`test_agent_tools.test_the_real_registry_builds_against_governance_json` is the
one place the two are compared, and it is where a drift between this file and
the shipped ruleset shows up.
"""

from __future__ import annotations

#: action_type -> tier. Anything absent resolves to BLOCK via `tier_lookup`,
#: which is exactly how governance itself fails closed.
TIERS: dict[str, str] = {
    # -- the original curated set ----------------------------------------- #
    "tavily_search": "AUTO",
    "web_browse": "AUTO",
    "search_documents": "AUTO",
    "memory_recall": "AUTO",
    "workspace_read": "AUTO",
    "list_directory": "AUTO",
    "find_file": "AUTO",
    "system_status": "AUTO",
    "read_screen": "AUTO",
    "workspace_write": "CONFIRM",
    "workspace_patch": "CONFIRM",

    # -- wave 1 (§6.8.2): email + calendar --------------------------------- #
    "gmail_read_unread": "AUTO",
    "gmail_read": "AUTO",
    "search_email": "AUTO",
    "read_email": "AUTO",
    "check_calendar": "AUTO",
    "morning_briefing": "AUTO",
    "gmail_send": "CONFIRM",
    "gmail_reply": "CONFIRM",
    "create_event": "CONFIRM",
    "clear_schedule": "CONFIRM",

    # -- wave 2 (§6.8.2): television + music ------------------------------- #
    # All AUTO in the shipped ruleset: a keypress on a TV is undone by another
    # keypress. `tv_search` is listed although it is deliberately NOT
    # registered — the drift check compares this map against governance.json,
    # and an entry missing here would read as governance not knowing it.
    "tv_power": "AUTO",
    "tv_volume": "AUTO",
    "tv_control": "AUTO",
    "tv_launch_app": "AUTO",
    "tv_play_media": "AUTO",
    "tv_search": "AUTO",
    "tv_type": "AUTO",
    "play_music": "AUTO",

    # -- wave 3 (§6.8.2): apps, the desk display, the machine -------------- #
    # `launch_app` and the two focus-mode actions are listed but NOT registered
    # — see the comments in `agent_tools`. They are here because the drift check
    # reads this map as "what governance knows".
    "native_app_launcher": "AUTO",
    "launch_app": "AUTO",
    "close_app": "AUTO",
    "hud_open_widget": "AUTO",
    "hud_close_widget": "AUTO",
    "os_control": "AUTO",
    "os_macro": "AUTO",
    "open_link": "AUTO",
    "enable_focus_mode": "AUTO",
    "disable_focus_mode": "AUTO",

    # -- wave 4 (§6.8.2): git ---------------------------------------------- #
    # Reads AUTO, the two that change or publish history CONFIRM.
    "github_status": "AUTO",
    "github_diff": "AUTO",
    "github_log": "AUTO",
    "github_commit": "CONFIRM",
    "github_push": "CONFIRM",

    # -- wave 5 (§6.8.2): driving a real browser --------------------------- #
    # `web_search` is listed but NOT registered — it is `tavily_search` with a
    # DuckDuckGo fallback, and two spellings of one job make the model choose.
    "web_click": "AUTO",
    "web_type": "AUTO",
    "web_scroll": "AUTO",
    "web_back": "AUTO",
    "web_close": "AUTO",
    "web_search_image": "AUTO",
    "web_search": "AUTO",

    # -- wave 6 (§6.8.2): people, house, and the remainder ----------------- #
    # The trailing entries are listed but NOT registered — see the exclusion
    # comments in `agent_tools`.
    "message_partner": "CONFIRM",      # listed, deliberately NOT registered
    "partner_contact_status": "AUTO",
    "summarize_partner_chat": "AUTO",
    "telegram_send_file": "AUTO",
    "remember_fact": "AUTO",
    "check_vitals": "AUTO",
    "movie_protocol": "AUTO",
    "sleep_protocol": "AUTO",
    "create_note": "CONFIRM",
    "organize_downloads": "CONFIRM",
    "render_chart": "AUTO",
    "get_telemetry": "AUTO",
    "close_display": "AUTO",
    "self_improve": "CONFIRM",
    "run_autopilot": "AUTO",
    "ghost_type": "AUTO",
    "ghost_save_file": "CONFIRM",
    "gui_action": "AUTO",
    "agentic_gui_task": "AUTO",

    # -- present so the BLOCK guarantees can be exercised ------------------ #
    "delete_file": "BLOCK",
    "run_terminal_command": "BLOCK",
}


def tier_lookup(overrides: dict | None = None):
    """A `get_tier` callable for `build_default_registry`.

    `overrides` replaces or adds entries for one test — used to prove that
    re-tiering an action to BLOCK drops it from the registry.
    """
    table = dict(TIERS)
    if overrides:
        table.update(overrides)
    return lambda action_type: table.get(action_type, "BLOCK")
