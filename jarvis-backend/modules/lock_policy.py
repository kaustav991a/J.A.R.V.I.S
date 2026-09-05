"""Whether JARVIS is permitted to lock this machine at all.

`JARVIS_NEVER_LOCK=1` is an owner-set interlock, not a preference. It exists
because on 2026-08-30 he was away from home, the desk soft-locked itself, and
neither Telegram nor the lock screen would let him back in - he shut the machine
off at the case. The individual defects behind that are fixed (F-83..F-86), but
those fixes are not yet proved live, and until they are he has asked that nothing
JARVIS does can lock the session while he is out.

It is deliberately checked at EVERY path that can lock, rather than at one
convenient chokepoint, because the incident happened precisely when a capability
existed at one layer and the layer above it could not reach it. Three paths can
lock this machine:

    os_agent.lock_workstation()        - the explicit action
    gesture_daemon._lock()             - the biometric soft lock
    gesture_daemon._monitor_power(off) - screen off, which reads as locked

Windows' OWN screensaver lock is untouched and should stay on: that is his
security, it is not JARVIS acting, and it does not lock anyone out of a session
they can sign back into.
"""
import os as _os


def never_lock() -> bool:
    return (_os.getenv("JARVIS_NEVER_LOCK", "") or "").strip().lower() in (
        "1", "true", "yes", "on")
