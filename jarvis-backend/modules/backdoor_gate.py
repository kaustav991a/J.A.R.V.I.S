r"""backdoor_gate.py — the dev backdoor is a biometric bypass, so gate it.

`POST /api/backdoor` (the HUD command line, the regression driver, every
`test:` hook) used to dispatch commands with **no face scan at all**: typing
"wake up" into the HUD went straight to the morning briefing while the system
was still locked. That is the whole optical-biometrics layer bypassed by one
loopback POST — convenient for testing, but a discoverable accident rather than
a decision.

So the bypass is now conscious and OFF by default:

    JARVIS_ALLOW_BACKDOOR unset / "0"   the backdoor needs the SAME auth as any
                                        other path: it only dispatches once the
                                        session is already authenticated
                                        (`SYSTEM_ONLINE`, i.e. a real face/voice
                                        auth passed and JARVIS is not asleep).
                                        Locked  ->  HTTP 403, nothing runs.

    JARVIS_ALLOW_BACKDOOR=1             today's behaviour: dispatch regardless
                                        of auth state. Needed by the harnesses
                                        that drive real commands over HTTP
                                        (run_phase1_regression.py,
                                        test_ui_bridge_e2e.py) and by any manual
                                        live gate run from the HUD terminal.

What this does NOT touch: risk tiers and governance. `tier_allows` /
`governance_manager.check` still rule on every action from every surface —
this gate only decides whether the *test bypass of authentication* is open.
An authenticated backdoor command is exactly as constrained as a spoken one.

Pure decision logic (no FastAPI, no globals, env read only through an injected
mapping) so test_backdoor_gate.py can assert both directions without a server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_FLAG = "JARVIS_ALLOW_BACKDOOR"

# Same truthiness the rest of the repo uses ("1"), plus the spellings a human
# actually types into a .env file at 2am.
_TRUE = frozenset({"1", "true", "yes", "on"})

# Reasons are stable strings — they are logged and asserted on, not shown to the
# user, so they never change wording for cosmetic reasons.
REASON_FLAGGED = "flagged_bypass"          # allowed: flag on, auth skipped
REASON_AUTHENTICATED = "authenticated"     # allowed: session already authed
REASON_LOCKED = "locked"                   # refused: no auth, no flag

REFUSAL_MESSAGE = (
    "Biometric authorisation required, Sir. The command line does not bypass "
    "the optical sensors — say the wake word and complete the face scan."
)


def flag_enabled(env=None) -> bool:
    """Is the test bypass switched on? Default **off**.

    Read per request (not once at import) so the flag is honestly whatever the
    process env says, and so a harness can pass its own mapping.
    """
    src = os.environ if env is None else env
    return str(src.get(ENV_FLAG, "0")).strip().lower() in _TRUE


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    status: int = 200
    message: str = ""

    def as_payload(self) -> dict:
        """Body for a refused request. `status: "refused"` keeps it distinct
        from the endpoint's own `{"status": "success"}`."""
        return {
            "status": "refused",
            "reason": self.reason,
            "message": self.message,
            "flag": ENV_FLAG,
        }


def decide(command_text: str = "", *, enabled: bool, system_online: bool) -> Decision:
    """Should this backdoor command dispatch?

    `command_text` is accepted (and logged by the caller) but deliberately does
    NOT influence the answer: a per-command allowlist would just be a second,
    softer bypass — "test:" hooks reach the same dispatcher as everything else.
    """
    if enabled:
        return Decision(True, REASON_FLAGGED)
    if system_online:
        return Decision(True, REASON_AUTHENTICATED)
    return Decision(False, REASON_LOCKED, status=403, message=REFUSAL_MESSAGE)
