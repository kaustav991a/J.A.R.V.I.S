"""
lock_overlay.py — J.A.R.V.I.S soft-lock screen (G3), standalone subprocess
==========================================================================

Fullscreen, always-on-top black overlay spanning the whole virtual desktop
(all monitors). Spawned by gesture_daemon when the owner leaves the desk,
terminated when his face comes back. A separate PROCESS (not a thread)
because tkinter refuses to run outside the main thread of the FastAPI app.

Soft lock = deterrent, not Windows security: it swallows casual clicks and
keys by holding fullscreen focus, and the daemon usually powers the monitor
off on top of it. Someone determined can get past it — for real security the
voice command "lock the screen" still calls LockWorkStation.

Exits when: parent kills it (unlock), stdin reaches EOF (parent died), or a
JARVIS_UNLOCK_CODE typed blind + Enter matches (escape hatch if the camera
dies while locked; unset = disabled).
"""

import os
import sys
import threading
import tkinter as tk

CYAN = "#00ffcc"
BG = "#020507"


def virtual_screen():
    """(x, y, w, h) of the full multi-monitor virtual desktop."""
    if sys.platform != "win32":
        return 0, 0, 1920, 1080
    import ctypes

    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))


def main() -> int:
    root = tk.Tk()
    x, y, w, h = virtual_screen()
    root.overrideredirect(True)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    try:
        root.configure(cursor="none")
    except Exception:
        pass

    box = tk.Frame(root, bg=BG)
    box.place(relx=0.5, rely=0.5, anchor="center")
    tk.Label(box, text="J.A.R.V.I.S", fg=CYAN, bg=BG,
             font=("Segoe UI", 34, "bold")).pack()
    pulse = tk.Label(box, text="● DESK SECURED — SOFT LOCK", fg=CYAN, bg=BG,
                     font=("Consolas", 13))
    pulse.pack(pady=(14, 4))

    # F-25. This used to be one line naming ONLY the camera — which is the exit
    # that cannot work, because a camera that has stopped recognising anyone is
    # usually why the lock armed. The owner stood in front of this, faced the
    # camera, got nothing, found keys and clicks swallowed, and got out by
    # closing VS Code.
    #
    # The module docstring already knew better ("escape hatch if the camera dies
    # while locked"). That knowledge never reached the person standing here. It
    # does now: every exit that exists is on the screen, and the spoken one is
    # named FIRST because voice is never blocked — only keys and clicks are.
    code = os.getenv("JARVIS_UNLOCK_CODE", "")
    # The admin override opens this screen too.
    #
    # 2026-08-30: locked out while away from home, he typed
    # JARVIS_ADMIN_OVERRIDE_CODE at this overlay. Nothing happened - it only ever
    # compared against JARVIS_UNLOCK_CODE, and every other key is swallowed, so
    # there was no feedback either. He powered the machine off at the case.
    #
    # An override that does not work on the one screen you cannot walk away from
    # is not an override. Both codes are accepted here; neither is displayed.
    admin_code = (os.getenv("JARVIS_ADMIN_OVERRIDE_CODE", "") or "").strip()
    accepted = [c for c in (code, admin_code) if c]
    exits = [
        'say  "auto lock off"           — voice is never blocked',
        "face the camera                — if it can still see",
    ]
    if accepted:
        exits.append("type the unlock code + Enter   — blind, nothing is shown")
        if admin_code:
            exits.append("...the admin override code works here too")
    else:
        # Said plainly rather than omitted. An exit the owner believes he has and
        # does not is worse than a missing line.
        exits.append("unlock code: NOT SET (JARVIS_UNLOCK_CODE)")
    tk.Label(box, text="biometric watch active — ways out:",
             fg="#3a7f74", bg=BG, font=("Consolas", 10)).pack(pady=(8, 2))
    for line in exits:
        tk.Label(box, text=line, fg="#3a7f74", bg=BG,
                 font=("Consolas", 10), justify="left").pack(anchor="w")

    # blind-typed escape hatch (camera died while locked)
    typed = []

    def on_key(ev):
        if not accepted:
            return "break"
        if ev.keysym == "Return":
            tail = "".join(typed[-64:])
            if any(tail.endswith(c) for c in accepted):
                root.destroy()
            typed.clear()
        elif len(ev.char) == 1:
            typed.append(ev.char)
        return "break"  # swallow everything

    root.bind("<Key>", on_key)
    root.bind("<Button>", lambda e: "break")

    tick = {"on": True}

    def heartbeat():
        # reassert topmost+focus and pulse the dot
        try:
            root.lift()
            root.attributes("-topmost", True)
            root.focus_force()
        except Exception:
            pass
        tick["on"] = not tick["on"]
        pulse.configure(fg=CYAN if tick["on"] else "#0a4d44")
        root.after(600, heartbeat)

    def watch_parent():
        try:
            sys.stdin.buffer.read()  # EOF when the parent process dies
        except Exception:
            pass
        try:
            root.after(0, root.destroy)
        except Exception:
            pass

    threading.Thread(target=watch_parent, daemon=True).start()
    root.after(100, heartbeat)
    root.focus_force()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
