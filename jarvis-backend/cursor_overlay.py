r"""
cursor_overlay.py — G5.3 gesture HUD overlay (cursor halo + edge toasts)
========================================================================

A standalone, always-on-top, CLICK-THROUGH tkinter subprocess spawned by
gesture_daemon. It gives hand-gesture control a *felt* presence: a coloured
ring that follows the cursor and reflects the live gesture state (move / grab /
scroll / clutch), plus brief edge toasts on the transitions that matter
("HAND READY", "JARVIS DRIVING", "YOU HAVE CONTROL", "UNAUTHORIZED").

Unlike lock_overlay.py — which *grabs* focus and swallows input to deter a
stranger — this window is deliberately transparent to the mouse
(WS_EX_TRANSPARENT + WS_EX_NOACTIVATE): the gesture-driven cursor still clicks
the app underneath, and the overlay never steals focus. It only draws.

State arrives as newline-delimited JSON frames on stdin (one per daemon _hud
update — see GestureDaemon._feed_cursor_overlay). The cursor *position* is polled
locally via GetCursorPos so smoothness never depends on IPC rate. Exits on stdin
EOF (parent died), same contract as lock_overlay.

Windows-only (ctypes layered-window styles). No-op elsewhere.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import tkinter as tk

# --- palette -------------------------------------------------------------- #
BG = "#010203"          # transparent color-key: near-black, unlikely to collide
CYAN = "#00ffcc"        # move / active
AMBER = "#ffb020"       # grab (fist) / JARVIS driving
RED = "#ff3b3b"         # denied
DIM = "#2a5f57"         # clutch (frozen) / control off
WHITE = "#eafffb"       # cursor dot

RING_R = 28             # halo outer radius (px)
DOT_R = 4               # centre dot radius (px)
TOAST_S = 1.9           # toast visible duration (s)
PULSE_S = 0.45          # click/grab action ripple duration (s)
PURPLE = "#b58cff"      # right-click ripple (distinct from grab/amber + denied/red)

# G6.2: colour of the action ripple by the daemon's `last_action` label.
_ACTION_COLOR = {
    "click": CYAN, "double": CYAN, "right": PURPLE,
    "grab": AMBER, "drop": AMBER,
}

# --- Win32 extended-style bits ------------------------------------------- #
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020   # whole window is click-through
WS_EX_TOOLWINDOW = 0x00000080    # no taskbar entry
WS_EX_NOACTIVATE = 0x08000000    # never steals focus


def virtual_screen():
    """(x, y, w, h) of the full multi-monitor virtual desktop."""
    if sys.platform != "win32":
        return 0, 0, 1920, 1080
    import ctypes

    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))


def _cursor_pos():
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _make_click_through(hwnd: int) -> None:
    """OR the click-through / no-activate bits into the window's exstyle. Called
    after Tk has applied its own transparentcolor exstyle so we don't clobber it."""
    import ctypes

    u = ctypes.windll.user32
    try:
        get = u.GetWindowLongPtrW
        setl = u.SetWindowLongPtrW
    except AttributeError:  # 32-bit Python
        get = u.GetWindowLongW
        setl = u.SetWindowLongW
    ex = get(hwnd, GWL_EXSTYLE)
    ex |= (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    setl(hwnd, GWL_EXSTYLE, ex)


class Overlay:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.vx, self.vy, self.vw, self.vh = virtual_screen()
        self._lock = threading.Lock()
        # latest gesture-state frame (single-writer: stdin thread)
        self._frame: dict = {"state": "idle", "engaged": False, "clutch": False,
                             "suspended": False, "denied": False, "pose": "none",
                             "locked": False}
        # toast state, owned by the render loop
        self._prev: dict | None = None
        self._toast_until = 0.0
        # G6.2 action ripple (click/right/grab) — pulses at the cursor when the
        # daemon reports a fresh last_action_ts.
        self._seen_action_ts = 0.0
        self._pulse_start = 0.0
        self._pulse_until = 0.0
        self._pulse_xy = (0, 0)
        self._pulse_color = CYAN

        self.canvas = tk.Canvas(root, width=self.vw, height=self.vh, bg=BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        # persistent items — moved/reconfigured each frame, never recreated
        self.ring = self.canvas.create_oval(0, 0, 0, 0, outline=CYAN, width=3,
                                            state="hidden")
        self.dot = self.canvas.create_oval(0, 0, 0, 0, fill=WHITE, outline="",
                                           state="hidden")
        self.toast_bg = self.canvas.create_rectangle(0, 0, 0, 0, fill=BG,
                                                     outline="", state="hidden")
        self.toast_tx = self.canvas.create_text(0, 0, text="", fill=CYAN,
                                                font=("Consolas", 15, "bold"),
                                                anchor="center", state="hidden")
        # action ripple ring (expands + fades on a click/right-click/grab)
        self.pulse = self.canvas.create_oval(0, 0, 0, 0, outline=CYAN, width=3,
                                             state="hidden")

    # ---- stdin: newline-delimited JSON frames --------------------------- #

    def read_stdin(self) -> None:
        buf = sys.stdin.buffer
        while True:
            line = buf.readline()
            if not line:                       # EOF -> parent died
                try:
                    self.root.after(0, self.root.destroy)
                except Exception:
                    pass
                return
            try:
                frame = json.loads(line.decode("utf-8").strip())
            except Exception:
                continue
            if isinstance(frame, dict):
                with self._lock:
                    self._frame.update(frame)

    # ---- toast ----------------------------------------------------------- #

    def _show_toast(self, text: str, color: str) -> None:
        cx, cy = self.vw // 2, int(self.vh * 0.86)
        self.canvas.itemconfig(self.toast_tx, text=text, fill=color, state="normal")
        self.canvas.coords(self.toast_tx, cx, cy)
        x0, y0, x1, y1 = self.canvas.bbox(self.toast_tx)
        pad = 14
        self.canvas.coords(self.toast_bg, x0 - pad, y0 - 8, x1 + pad, y1 + 8)
        self.canvas.itemconfig(self.toast_bg, fill="#04211d", state="normal")
        self.canvas.tag_raise(self.toast_tx)
        self._toast_until = time.monotonic() + TOAST_S

    def _hide_toast(self) -> None:
        self.canvas.itemconfig(self.toast_tx, state="hidden")
        self.canvas.itemconfig(self.toast_bg, state="hidden")

    def _detect_toast(self, f: dict) -> None:
        p = self._prev
        self._prev = dict(f)
        if p is None:
            return
        if f.get("locked"):
            self._hide_toast()
            self._toast_until = 0.0
            return
        if f.get("denied") and not p.get("denied"):
            self._show_toast("UNAUTHORIZED", RED)
        elif f.get("suspended") and not p.get("suspended"):
            self._show_toast("JARVIS DRIVING", AMBER)
        elif p.get("suspended") and not f.get("suspended") and f.get("engaged"):
            self._show_toast("YOU HAVE CONTROL", CYAN)
        elif f.get("engaged") and not p.get("engaged"):
            self._show_toast("HAND READY", CYAN)
        elif p.get("engaged") and not f.get("engaged") \
                and not f.get("suspended") and not f.get("locked"):
            self._show_toast("CONTROL OFF", DIM)

    # ---- halo ------------------------------------------------------------ #

    def _halo_style(self, f: dict):
        """(color, width, dash|None) for the current pose, or None to hide."""
        show = (f.get("engaged") and f.get("state") == "active"
                and not f.get("locked") and not f.get("suspended"))
        if not show:
            return None
        if f.get("clutch"):
            return DIM, 2, (4, 4)          # frozen / repositioning
        pose = f.get("pose")
        if pose == "fist":
            return AMBER, 5, None          # grab
        if pose == "two_finger":
            return CYAN, 3, (2, 3)         # scroll
        return CYAN, 3, None               # palm / move

    # ---- render loop ----------------------------------------------------- #

    def _detect_pulse(self, f: dict) -> None:
        """Arm an action ripple when the daemon reports a fresh last_action_ts."""
        ats = f.get("last_action_ts") or 0.0
        if ats <= self._seen_action_ts:
            return
        self._seen_action_ts = ats
        self._pulse_start = time.monotonic()
        self._pulse_until = self._pulse_start + PULSE_S
        self._pulse_xy = _cursor_pos()
        self._pulse_color = _ACTION_COLOR.get(f.get("last_action"), CYAN)

    def _render_pulse(self) -> None:
        now = time.monotonic()
        if not self._pulse_until or now >= self._pulse_until:
            self.canvas.itemconfig(self.pulse, state="hidden")
            self._pulse_until = 0.0
            return
        prog = (now - self._pulse_start) / PULSE_S     # 0..1
        r = DOT_R + prog * (RING_R * 2.0)              # expands outward
        w = max(1, int(4 * (1.0 - prog)))             # fades by thinning
        cx, cy = self._pulse_xy
        x, y = cx - self.vx, cy - self.vy
        self.canvas.coords(self.pulse, x - r, y - r, x + r, y + r)
        self.canvas.itemconfig(self.pulse, outline=self._pulse_color, width=w,
                               state="normal")
        self.canvas.tag_raise(self.pulse)

    def tick(self) -> None:
        with self._lock:
            f = dict(self._frame)
        self._detect_toast(f)
        self._detect_pulse(f)

        style = self._halo_style(f)
        if style is None:
            self.canvas.itemconfig(self.ring, state="hidden")
            self.canvas.itemconfig(self.dot, state="hidden")
        else:
            color, width, dash = style
            cx, cy = _cursor_pos()
            x, y = cx - self.vx, cy - self.vy
            self.canvas.coords(self.ring, x - RING_R, y - RING_R,
                               x + RING_R, y + RING_R)
            self.canvas.itemconfig(self.ring, outline=color, width=width,
                                   dash=dash or (), state="normal")
            self.canvas.coords(self.dot, x - DOT_R, y - DOT_R, x + DOT_R, y + DOT_R)
            self.canvas.itemconfig(self.dot, state="normal")
            self.canvas.tag_raise(self.ring)
            self.canvas.tag_raise(self.dot)

        self._render_pulse()

        if self._toast_until and time.monotonic() >= self._toast_until:
            self._hide_toast()
            self._toast_until = 0.0

        self.root.after(16, self.tick)       # ~60 fps

    def keep_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()                 # NB: no focus_force — must not steal focus
        except Exception:
            pass
        self.root.after(1000, self.keep_topmost)


def main() -> int:
    if sys.platform != "win32":
        return 0
    root = tk.Tk()
    x, y, w, h = virtual_screen()
    root.overrideredirect(True)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.configure(bg=BG)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", BG)   # BG pixels become see-through

    ov = Overlay(root)
    root.update_idletasks()
    root.update()                              # force HWND + Tk's transparentcolor exstyle
    try:
        _make_click_through(root.winfo_id())
    except Exception as e:                      # noqa: BLE001
        print(f"[CURSOR_OVERLAY] click-through setup failed: {e}", flush=True)

    threading.Thread(target=ov.read_stdin, daemon=True).start()
    root.after(16, ov.tick)
    root.after(1000, ov.keep_topmost)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
