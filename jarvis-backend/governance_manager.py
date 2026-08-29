"""
governance_manager.py — J.A.R.V.I.S. Phase 6 Governance Engine
===============================================================

Intercepts every action payload before it reaches the ActionEngine.
Enforces the risk-tiered permission model defined in governance.json.

Risk tiers:
  AUTO    → Execute immediately, pass-through with no friction.
  CONFIRM → Suspend execution; serialise the payload into a pending
             slot and return a PENDING_CONFIRMATION signal so
             brain.py / main.py can ask the user for approval.
  BLOCK   → Reject immediately with BLOCKED; the payload is discarded.

FAIL-SAFE: Any action_type NOT present in the ruleset defaults to BLOCK.
           J.A.R.V.I.S. should never execute unknown commands silently.

Thread-safety:
  _pending_confirmation is keyed by a UUID so concurrent requests from
  different surfaces (voice + backdoor) don't clobber each other.
  A single-slot convenience API is also exposed (used by the
  request-response WebSocket / backdoor flow where only one action can
  be awaiting approval at a time).
"""

import json
import uuid
import time
import os
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------

class GovernanceTier(str, Enum):
    AUTO    = "AUTO"
    CONFIRM = "CONFIRM"
    BLOCK   = "BLOCK"


# ---------------------------------------------------------------------------
# Decision signals returned to the caller
# ---------------------------------------------------------------------------

class GovernanceSignal(str, Enum):
    PASS                = "PASS"                # AUTO tier — proceed immediately
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"  # CONFIRM tier — awaiting user
    BLOCKED             = "BLOCKED"             # BLOCK tier — rejected


# ---------------------------------------------------------------------------
# Result dataclass (plain dict for zero-dependency portability)
# ---------------------------------------------------------------------------

def _make_result(
    signal: GovernanceSignal,
    tier: GovernanceTier,
    action_type: str,
    confirmation_id: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    return {
        "signal":           signal.value,
        "tier":             tier.value,
        "action_type":      action_type,
        "confirmation_id":  confirmation_id,
        "reason":           reason,
    }


# ---------------------------------------------------------------------------
# GovernanceManager
# ---------------------------------------------------------------------------

class GovernanceManager:
    """
    Lightweight interceptor.  Instantiate once at module level — the same
    singleton is shared between action_engine.py and main.py so pending
    confirmations are visible to both.
    """

    _RULESET_PATH = os.path.join(os.path.dirname(__file__), "governance.json")

    def __init__(self) -> None:
        self._rules: dict[str, str] = {}
        self._load_ruleset()

        # pending_slot: at most ONE action awaiting confirmation at a time
        # (matches the single-threaded request/response model of the WebSocket flow).
        # Structure: { "id": str, "payload": dict, "expires_at": float }
        self._pending_slot: Optional[dict] = None

        # Full concurrent registry (keyed by confirmation_id) for future
        # multi-channel support.
        self._pending_registry: dict[str, dict] = {}

        # Confirmation timeout — auto-expire stale approvals after 90 s.
        self._CONFIRM_TTL_SECS = 90.0

    # -----------------------------------------------------------------------
    # Ruleset loading
    # -----------------------------------------------------------------------

    def _load_ruleset(self) -> None:
        """Parse governance.json, skipping comment/separator keys."""
        try:
            with open(self._RULESET_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            rules_raw: dict = raw.get("rules", {})
            self._rules = {
                k.lower(): v.upper()
                for k, v in rules_raw.items()
                if not k.startswith("===")     # skip human-readable separator keys
            }
            print(
                f"[GOVERNANCE] Ruleset loaded - {len(self._rules)} action types indexed.",
                flush=True,
            )
        except FileNotFoundError:
            print(
                f"[GOVERNANCE] [WARN] governance.json not found at {self._RULESET_PATH}. "
                "Defaulting ALL actions to BLOCK.",
                flush=True,
            )
            self._rules = {}
        except Exception as exc:
            print(f"[GOVERNANCE] [WARN] Failed to load ruleset: {exc}. Defaulting to BLOCK.", flush=True)
            self._rules = {}

    def reload_ruleset(self) -> None:
        """Hot-reload the ruleset at runtime (useful after editing governance.json)."""
        self._load_ruleset()

    # -----------------------------------------------------------------------
    # Core intercept
    # -----------------------------------------------------------------------

    def check(self, payload: dict) -> dict:
        """
        Main entry point.  Call this BEFORE dispatching to ActionEngine.execute().

        Args:
            payload: The raw action dict, e.g. {"action_type": "workspace_write", "target": "..."}

        Returns:
            A governance result dict with keys:
              signal           — PASS | PENDING_CONFIRMATION | BLOCKED
              tier             — AUTO | CONFIRM | BLOCK
              action_type      — echoed back
              confirmation_id  — UUID string (only when PENDING_CONFIRMATION)
              reason           — human-readable explanation
        """
        action_type: str = str(payload.get("action_type", "")).strip().lower()

        # --- Validate payload has an action_type ---
        if not action_type:
            print("[GOVERNANCE] [BLOCKED] Payload has no action_type.", flush=True)
            return _make_result(
                GovernanceSignal.BLOCKED,
                GovernanceTier.BLOCK,
                action_type="<missing>",
                reason="Payload is missing the required 'action_type' field.",
            )

        # --- Resolve tier (fail-safe: default BLOCK) ---
        tier_str: str = self._rules.get(action_type, "BLOCK")
        try:
            tier = GovernanceTier(tier_str)
        except ValueError:
            tier = GovernanceTier.BLOCK

        print(
            # ASCII arrow on purpose: this is the safety spine, and it must not
            # be able to die on its own log line where stdout is cp1252.
            f"[GOVERNANCE] action='{action_type}' -> tier={tier.value}",
            flush=True,
        )

        # --- AUTO: pass straight through ---
        if tier == GovernanceTier.AUTO:
            return _make_result(
                GovernanceSignal.PASS,
                GovernanceTier.AUTO,
                action_type,
                reason="Action is pre-authorised (AUTO tier).",
            )

        # --- BLOCK: reject immediately ---
        if tier == GovernanceTier.BLOCK:
            reason = (
                f"Action '{action_type}' is classified as HIGH-RISK and is permanently blocked "
                "by governance policy."
            ) if action_type in self._rules else (
                f"Action '{action_type}' is not recognised in the governance ruleset. "
                "Defaulting to BLOCK (fail-safe policy)."
            )
            print(f"[GOVERNANCE] [BLOCKED] {reason}", flush=True)
            return _make_result(
                GovernanceSignal.BLOCKED,
                GovernanceTier.BLOCK,
                action_type,
                reason=reason,
            )

        # --- CONFIRM: serialise and pend ---
        cid = uuid.uuid4().hex
        entry = {
            "id":         cid,
            "payload":    payload,
            "expires_at": time.monotonic() + self._CONFIRM_TTL_SECS,
        }
        self._pending_slot     = entry
        self._pending_registry[cid] = entry

        print(
            f"[GOVERNANCE] [PENDING] PENDING_CONFIRMATION id={cid} action='{action_type}'",
            flush=True,
        )
        return _make_result(
            GovernanceSignal.PENDING_CONFIRMATION,
            GovernanceTier.CONFIRM,
            action_type,
            confirmation_id=cid,
            reason=(
                f"Action '{action_type}' requires explicit user authorisation "
                "(CONFIRM tier). Awaiting approval."
            ),
        )

    # -----------------------------------------------------------------------
    # Confirmation resolution
    # -----------------------------------------------------------------------

    def get_pending_payload(self, confirmation_id: Optional[str] = None) -> Optional[dict]:
        """
        Retrieve the payload for the currently pending confirmation.

        If confirmation_id is None, returns the single-slot payload (the most
        recently pended action).  Otherwise looks up by ID.

        Returns None if nothing is pending or the slot has expired.
        """
        self._expire_stale()

        if confirmation_id is None:
            if self._pending_slot is None:
                return None
            return self._pending_slot["payload"]

        entry = self._pending_registry.get(confirmation_id)
        return entry["payload"] if entry else None

    def has_pending(self) -> bool:
        """True if at least one action is awaiting confirmation."""
        self._expire_stale()
        return self._pending_slot is not None

    def pending_id(self) -> Optional[str]:
        """The id of the single pending slot, or None.

        Exists so a caller that stages a confirmation can PIN the id it is
        responsible for, instead of later approving "whatever is pending" — see
        the desk-approval path in main.py. `check()` always mints an id, but the
        sentinel it travels in (`GOVERNANCE_CONFIRM:<atype>:<cid>`) is parsed by
        string split at the far end, so a caller that lost the id needs a way to
        ask for it rather than falling back to the slot.
        """
        self._expire_stale()
        return self._pending_slot["id"] if self._pending_slot else None

    def consume_pending(self, confirmation_id: Optional[str] = None) -> Optional[dict]:
        """
        Retrieve AND remove the pending payload (call this when user approves).

        If confirmation_id is None, consumes the single-slot entry.
        Returns the payload dict, or None if nothing to consume.
        """
        self._expire_stale()

        if confirmation_id is None:
            if self._pending_slot is None:
                return None
            payload = self._pending_slot["payload"]
            cid = self._pending_slot["id"]
            self._pending_slot = None
            self._pending_registry.pop(cid, None)
            print(f"[GOVERNANCE] [OK] Confirmation consumed (id={cid}).", flush=True)
            return payload

        entry = self._pending_registry.pop(confirmation_id, None)
        if entry is None:
            return None
        if self._pending_slot and self._pending_slot["id"] == confirmation_id:
            self._pending_slot = None
        print(f"[GOVERNANCE] [OK] Confirmation consumed (id={confirmation_id}).", flush=True)
        return entry["payload"]

    def cancel_pending(self, confirmation_id: Optional[str] = None) -> bool:
        """
        Discard the pending payload (call this when user declines).

        Returns True if something was cancelled, False if nothing was pending.
        """
        self._expire_stale()

        if confirmation_id is None:
            if self._pending_slot is None:
                return False
            cid = self._pending_slot["id"]
            self._pending_slot = None
            self._pending_registry.pop(cid, None)
            print(f"[GOVERNANCE] [CANCELLED] Pending action cancelled (id={cid}).", flush=True)
            return True

        entry = self._pending_registry.pop(confirmation_id, None)
        if entry is None:
            return False
        if self._pending_slot and self._pending_slot["id"] == confirmation_id:
            self._pending_slot = None
        print(f"[GOVERNANCE] [CANCELLED] Pending action cancelled (id={confirmation_id}).", flush=True)
        return True

    # -----------------------------------------------------------------------
    # Helper: expire stale entries
    # -----------------------------------------------------------------------

    def _expire_stale(self) -> None:
        now = time.monotonic()
        # Expire single slot
        if self._pending_slot and self._pending_slot["expires_at"] < now:
            print(
                f"[GOVERNANCE] [EXPIRED] Pending confirmation expired (id={self._pending_slot['id']}).",
                flush=True,
            )
            self._pending_registry.pop(self._pending_slot["id"], None)
            self._pending_slot = None

        # Expire registry entries
        expired = [cid for cid, e in self._pending_registry.items() if e["expires_at"] < now]
        for cid in expired:
            self._pending_registry.pop(cid, None)

    # -----------------------------------------------------------------------
    # Introspection (for debug / regression endpoints)
    # -----------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return a snapshot of current governance state (for /api/governance/status)."""
        self._expire_stale()
        pending_info = None
        if self._pending_slot:
            entry = self._pending_slot
            pending_info = {
                "id":          entry["id"],
                "action_type": entry["payload"].get("action_type"),
                "expires_in":  round(max(0, entry["expires_at"] - time.monotonic()), 1),
            }
        return {
            "rules_loaded":   len(self._rules),
            "has_pending":    self._pending_slot is not None,
            "pending_action": pending_info,
        }

    def get_tier(self, action_type: str) -> str:
        """Return the tier string for a given action_type (for introspection)."""
        return self._rules.get(action_type.lower(), "BLOCK")

    def is_known(self, action_type: str) -> bool:
        """Whether a rule was actually WRITTEN for this action.

        `get_tier` cannot answer this: it returns "BLOCK" both for an action ruled
        high-risk and for one nobody has ever heard of, which is right for the
        decision and wrong for the explanation. F-74b - the desk told the operator
        `get_calendar` was "classified as high-risk" when governance.json has no
        such entry and `check()` had already said so in its own reason. A refusal
        that misstates its grounds sends him looking for a rule that is not there.
        """
        return str(action_type or "").strip().lower() in self._rules


# ---------------------------------------------------------------------------
# Module-level singleton — import this from everywhere
# ---------------------------------------------------------------------------

governance_manager = GovernanceManager()
