r"""
cursor_overlay.py — G5.3 gesture HUD overlay (cursor halo + edge toasts)
========================================================================

A standalone, always-on-top, CLICK-THROUGH tkinter subprocess spawned by
gesture_daemon. It gives hand-gesture control a *felt* presence: a coloured
ring that follows the cursor and reflects the live gesture state (move / grab /
scroll / clutch), plus brief edge toasts on the transitions that matter
("HAND READY", "JARVIS DRIVING", "YOU HAVE CONTROL", "UNAUTHORIZED").

Unlike lock_overlay.py — which *grabs* focus and swallows input to deter a
stranger — these windows are deliberately transparent to the mouse
(WS_EX_TRANSPARENT + WS_EX_NOACTIVATE): the gesture-driven cursor still clicks
the app underneath, and the overlay never steals focus. It only draws.

State arrives as newline-delimited JSON frames on stdin (one per daemon _hud
update — see GestureDaemon._feed_cursor_overlay). The cursor *position* is polled
locally via GetCursorPos so smoothness never depends on IPC rate.

BLAST-RADIUS DESIGN (2026-07-25, after a live incident: the whole desktop went
black and could not be closed). This used to be ONE window spanning the entire
virtual desktop, made invisible purely by a colour-key. Three properties
combined into a desk-killer: the fill is near-black, WS_EX_NOACTIVATE means
Alt+F4 can never target it, and keep_topmost() re-lifts it every second — so
when the colour-key silently failed the user was left with an opaque black
screen with no way to dismiss it. It only died when the parent backend was
killed and stdin hit EOF. Three independent guards now:

  1. NO FULLSCREEN WINDOW. Each element gets its own small window sized to what
     it draws (halo ~72px around the cursor, ripple ~128px at the action point,
     toast sized to its text). A transparency failure is now a small floating
     square, not a dead desktop.
  2. THE COLOUR-KEY IS VERIFIED, NOT ASSUMED. We call
     SetLayeredWindowAttributes ourselves *after* the exstyle change (per Win32
     docs, touching WS_EX_LAYERED post-creation can invalidate the layered
     attributes Tk set) and check the return. If any window can't confirm its
     key, the process refuses to run rather than sitting there opaque.
  3. DEADMAN. Exits if no state frame arrives for JARVIS_OVERLAY_DEADMAN_S
     (default 20s, ~10 missed daemon heartbeats), not just on stdin EOF. The
     daemon's _ensure_cursor_overlay respawns it, rate-limited, once frames flow
     again.

Exits on: stdin EOF (parent died), deadman expiry, or colour-key failure.

Windows-only (ctypes layered-window styles). No-op elsewhere.
"""

from __future__ import annotations

import json
import os
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
TOAST_BG = "#04211d"    # toast plate (opaque on purpose — it's the readable bit)

RING_R = 28             # halo outer radius (px)
DOT_R = 4               # centre dot radius (px)
TOAST_S = 1.9           # toast visible duration (s)
PULSE_S = 0.45          # click/grab action ripple duration (s)
PURPLE = "#b58cff"      # right-click ripple (distinct from grab/amber + denied/red)

# Window sizes — each just big enough for what it draws (guard #1). The halo box
# holds the ring at its widest stroke; the pulse box holds the ripple at full
# expansion (DOT_R + 2*RING_R) plus its stroke.
HALO_BOX = 2 * (RING_R + 8)                  # 72
PULSE_MAX_R = DOT_R + RING_R * 2.0           # 60.0 — mirrors _render_pulse
PULSE_BOX = int(2 * (PULSE_MAX_R + 6))       # 132
TOAST_PAD_X = 14
TOAST_PAD_Y = 8

# Guard #3: exit if the daemon stops talking. Its _hud re-sends every
# HUD_HEARTBEAT_S (2.0s) even when nothing changed, so 20s is ~10 missed beats.
DEADMAN_S = float(os.getenv("JARVIS_OVERLAY_DEADMAN_S", "20") or 0)

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
LWA_COLORKEY = 0x00000001
GA_ROOT = 2                      # GetAncestor: the real top-level window


# =========================================================================== #
# pure helpers — no Tk, no ctypes, no I/O (harnessed by test_cursor_overlay.py)
# =========================================================================== #

def colorref(hex_color: str) -> int:
    """"#RRGGBB" -> Win32 COLORREF (0x00BBGGRR). Byte order is the whole point:
    a red/blue swap here keys out the wrong colour and the window paints solid."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected #RRGGBB, got {hex_color!r}")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def geometry_str(w: int, h: int, x: int, y: int) -> str:
    """Tk geometry with an ALWAYS-explicit '+' before each offset.

    Tk reads a leading '-' as "measure from the far edge", so a left-hand
    monitor at x=-1920 must be written '+-1920', never '-1920'. f"{x:+d}" would
    emit the broken form.
    """
    return f"{w}x{h}+{x}+{y}"


def box_place(cx: int, cy: int, box: int,
              vx: int, vy: int, vw: int, vh: int) -> tuple[int, int, int, int]:
    """Place a `box`-sized window centred on (cx, cy), kept inside the virtual
    desktop. Returns (win_x, win_y, local_cx, local_cy).

    The window is clamped but the drawing is NOT: near a screen edge the window
    stops sliding and the ring moves within it, so the halo still sits exactly on
    the cursor instead of drifting off it.
    """
    half = box // 2
    wx = max(vx, min(cx - half, vx + vw - box))
    wy = max(vy, min(cy - half, vy + vh - box))
    return wx, wy, cx - wx, cy - wy


def halo_style(f: dict):
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


def toast_for(prev: dict | None, cur: dict):
    """(text, color) for a state TRANSITION worth announcing, else None.

    None on the first frame (no transition yet) and while locked — the lock
    screen speaks for itself.
    """
    if prev is None or cur.get("locked"):
        return None
    if cur.get("denied") and not prev.get("denied"):
        return "UNAUTHORIZED", RED
    if cur.get("suspended") and not prev.get("suspended"):
        return "JARVIS DRIVING", AMBER
    if prev.get("suspended") and not cur.get("suspended") and cur.get("engaged"):
        return "YOU HAVE CONTROL", CYAN
    if cur.get("engaged") and not prev.get("engaged"):
        return "HAND READY", CYAN
    if prev.get("engaged") and not cur.get("engaged") \
            and not cur.get("suspended") and not cur.get("locked"):
        return "CONTROL OFF", DIM
    return None


def deadman_expired(last_frame_t: float, now: float, timeout: float) -> bool:
    """True when the daemon has gone quiet long enough to warrant exiting.
    A timeout <= 0 disables the guard."""
    if timeout <= 0:
        return False
    return (now - last_frame_t) >= timeout


def pulse_radius(elapsed: float, duration: float = PULSE_S) -> tuple[float, int]:
    """(radius, stroke_width) of the action ripple `elapsed` seconds in."""
    prog = 0.0 if duration <= 0 else max(0.0, min(1.0, elapsed / duration))
    return DOT_R + prog * (RING_R * 2.0), max(1, int(4 * (1.0 - prog)))


# =========================================================================== #
# Win32 glue
# =========================================================================== #

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


def _exstyle_fns():
    import ctypes

    u = ctypes.windll.user32
    try:
        return u.GetWindowLongPtrW, u.SetWindowLongPtrW
    except AttributeError:  # 32-bit Python
        return u.GetWindowLongW, u.SetWindowLongW


def toplevel_hwnd(win) -> int:
    """The REAL top-level HWND behind a Tk widget.

    `winfo_id()` on a Toplevel returns Tk's INNER child window (class TkChild);
    the window Windows actually manages is its parent (class TkTopLevel). Styling
    the child is silently useless: WS_EX_TRANSPARENT and WS_EX_NOACTIVATE only
    affect hit-testing and activation on the top-level, and a colour-key applied
    to the child "succeeds" while keying nothing that matters. That mismatch is
    why clicks landed ON the halo instead of the app beneath — verified live:
    winfo_id -> TkChild, GetAncestor/GetParent/wm_frame all -> TkTopLevel.
    """
    import ctypes

    hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), GA_ROOT)
    if hwnd:
        return hwnd
    frame = win.wm_frame()            # Tk's own answer; hex string on Windows
    return int(frame, 16) if isinstance(frame, str) else int(frame)


def _make_click_through(hwnd: int) -> None:
    """OR the layered / click-through / no-activate bits into the exstyle."""
    get, setl = _exstyle_fns()
    ex = get(hwnd, GWL_EXSTYLE)
    ex |= (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    setl(hwnd, GWL_EXSTYLE, ex)


def _exstyle_missing(hwnd: int) -> list:
    """Which of the bits we require are NOT actually set on `hwnd`."""
    get, _ = _exstyle_fns()
    ex = get(hwnd, GWL_EXSTYLE)
    return [name for name, bit in (("TRANSPARENT", WS_EX_TRANSPARENT),
                                   ("NOACTIVATE", WS_EX_NOACTIVATE),
                                   ("LAYERED", WS_EX_LAYERED))
            if not ex & bit]


def _apply_colorkey(hwnd: int, hex_color: str) -> bool:
    """Guard #2: (re)apply the colour-key ourselves and REPORT whether it took.

    Must run AFTER _make_click_through: per Win32 docs, setting WS_EX_LAYERED
    with SetWindowLong invalidates any layered attributes already applied, so
    Tk's own -transparentcolor cannot be trusted to survive that call. This is
    the suspected cause of the black-desktop incident. False here means the
    window would paint SOLID, so main() refuses to run.
    """
    import ctypes
    from ctypes import wintypes

    fn = ctypes.windll.user32.SetLayeredWindowAttributes
    fn.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
    fn.restype = wintypes.BOOL
    return bool(fn(wintypes.HWND(hwnd), wintypes.COLORREF(colorref(hex_color)),
                   0, LWA_COLORKEY))


def _new_layer(root: tk.Tk, w: int, h: int) -> tuple[tk.Toplevel, tk.Canvas]:
    """A small, borderless, topmost, click-through, colour-keyed draw surface.

    Raises RuntimeError if the colour-key can't be confirmed — the caller turns
    that into a clean exit rather than an opaque window.
    """
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.geometry(geometry_str(w, h, 0, 0))
    win.configure(bg=BG)
    win.attributes("-topmost", True)
    try:
        win.attributes("-transparentcolor", BG)
    except tk.TclError:
        pass                      # our own colour-key below is the real guarantee
    canvas = tk.Canvas(win, width=w, height=h, bg=BG, highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    win.update_idletasks()        # force the HWND + Tk's own exstyle
    # NB: the TOP-LEVEL hwnd, not winfo_id() — see toplevel_hwnd().
    hwnd = toplevel_hwnd(win)
    _make_click_through(hwnd)
    if not _apply_colorkey(hwnd, BG):
        raise RuntimeError(f"SetLayeredWindowAttributes failed for hwnd {hwnd}")
    missing = _exstyle_missing(hwnd)
    if missing:
        # Read the bits BACK off the same window we keyed. Without this the guard
        # is theatre: styling the wrong hwnd still returns success from every call.
        raise RuntimeError(f"hwnd {hwnd} missing exstyle bits: {', '.join(missing)}")
    win.withdraw()
    return win, canvas


# =========================================================================== #
# overlay
# =========================================================================== #

class Overlay:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.vx, self.vy, self.vw, self.vh = virtual_screen()
        self._lock = threading.Lock()
        # latest gesture-state frame (single-writer: stdin thread)
        self._frame: dict = {"state": "idle", "engaged": False, "clutch": False,
                             "suspended": False, "denied": False, "pose": "none",
                             "locked": False}
        self._last_frame_t = time.monotonic()   # guard #3
        # toast state, owned by the render loop
        self._prev: dict | None = None
        self._toast_until = 0.0
        self._toast_shown = False
        # G6.2 action ripple (click/right/grab) — pulses at the cursor when the
        # daemon reports a fresh last_action_ts.
        self._seen_action_ts = 0.0
        self._pulse_start = 0.0
        self._pulse_until = 0.0
        self._pulse_color = CYAN

        # --- halo: HALO_BOX window that follows the cursor ---
        self.halo_win, self.halo_cv = _new_layer(root, HALO_BOX, HALO_BOX)
        self._halo_at: tuple[int, int] | None = None    # last geometry, to skip no-op moves
        self._halo_mapped = False
        self.ring = self.halo_cv.create_oval(0, 0, 0, 0, outline=CYAN, width=3,
                                             state="hidden")
        self.dot = self.halo_cv.create_oval(0, 0, 0, 0, fill=WHITE, outline="",
                                            state="hidden")

        # --- pulse: PULSE_BOX window pinned where the action happened ---
        self.pulse_win, self.pulse_cv = _new_layer(root, PULSE_BOX, PULSE_BOX)
        self._pulse_mapped = False
        self._pulse_local = (PULSE_BOX // 2, PULSE_BOX // 2)
        self.pulse = self.pulse_cv.create_oval(0, 0, 0, 0, outline=CYAN, width=3,
                                               state="hidden")

        # --- toast: resized to its text each time it shows ---
        self.toast_win, self.toast_cv = _new_layer(root, 200, 48)
        self.toast_tx = self.toast_cv.create_text(0, 0, text="", fill=CYAN,
                                                  font=("Consolas", 15, "bold"),
                                                  anchor="center")

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
                    self._last_frame_t = time.monotonic()

    # ---- deadman (guard #3) --------------------------------------------- #

    def watch_deadman(self) -> None:
        with self._lock:
            last = self._last_frame_t
        if deadman_expired(last, time.monotonic(), DEADMAN_S):
            print(f"[CURSOR_OVERLAY] no state frame in {DEADMAN_S:.0f}s — exiting "
                  f"(daemon will respawn me)", flush=True)
            # os._exit, not root.destroy: unlike the EOF path, the reader thread is
            # still BLOCKED in readline() on a live pipe here, and a normal
            # interpreter shutdown then dies with
            # "_enter_buffered_busy: could not acquire lock for <stdin>"
            # (0xC0000005). Nothing needs flushing — the windows are the OS's to
            # reclaim and the daemon respawns us.
            sys.stdout.flush()
            os._exit(0)
        self.root.after(1000, self.watch_deadman)

    # ---- toast ----------------------------------------------------------- #

    def _show_toast(self, text: str, color: str) -> None:
        self.toast_cv.itemconfig(self.toast_tx, text=text, fill=color)
        self.toast_cv.coords(self.toast_tx, 0, 0)      # measure at a known origin
        self.toast_win.update_idletasks()
        x0, y0, x1, y1 = self.toast_cv.bbox(self.toast_tx)
        w = int(x1 - x0) + 2 * TOAST_PAD_X
        h = int(y1 - y0) + 2 * TOAST_PAD_Y
        wx = self.vx + (self.vw - w) // 2
        wy = self.vy + int(self.vh * 0.86)
        self.toast_win.geometry(geometry_str(w, h, wx, wy))
        self.toast_cv.configure(width=w, height=h, bg=TOAST_BG)
        self.toast_cv.coords(self.toast_tx, w // 2, h // 2)
        if not self._toast_shown:
            self.toast_win.deiconify()
            self._toast_shown = True
        self._toast_until = time.monotonic() + TOAST_S

    def _hide_toast(self) -> None:
        if self._toast_shown:
            self.toast_win.withdraw()
            self._toast_shown = False
        self._toast_until = 0.0

    # ---- render loop ----------------------------------------------------- #

    def _detect_pulse(self, f: dict) -> None:
        """Arm an action ripple when the daemon reports a fresh last_action_ts."""
        ats = f.get("last_action_ts") or 0.0
        if ats <= self._seen_action_ts:
            return
        self._seen_action_ts = ats
        self._pulse_start = time.monotonic()
        self._pulse_until = self._pulse_start + PULSE_S
        self._pulse_color = _ACTION_COLOR.get(f.get("last_action"), CYAN)
        cx, cy = _cursor_pos()
        wx, wy, lx, ly = box_place(cx, cy, PULSE_BOX,
                                   self.vx, self.vy, self.vw, self.vh)
        self.pulse_win.geometry(geometry_str(PULSE_BOX, PULSE_BOX, wx, wy))
        self._pulse_local = (lx, ly)

    def _render_pulse(self) -> None:
        now = time.monotonic()
        if not self._pulse_until or now >= self._pulse_until:
            if self._pulse_mapped:
                self.pulse_cv.itemconfig(self.pulse, state="hidden")
                self.pulse_win.withdraw()
                self._pulse_mapped = False
            self._pulse_until = 0.0
            return
        r, w = pulse_radius(now - self._pulse_start)
        x, y = self._pulse_local
        self.pulse_cv.coords(self.pulse, x - r, y - r, x + r, y + r)
        self.pulse_cv.itemconfig(self.pulse, outline=self._pulse_color, width=w,
                                 state="normal")
        if not self._pulse_mapped:
            self.pulse_win.deiconify()
            self._pulse_mapped = True

    def _render_halo(self, f: dict) -> None:
        style = halo_style(f)
        if style is None:
            if self._halo_mapped:
                self.halo_cv.itemconfig(self.ring, state="hidden")
                self.halo_cv.itemconfig(self.dot, state="hidden")
                self.halo_win.withdraw()
                self._halo_mapped = False
            return
        color, width, dash = style
        cx, cy = _cursor_pos()
        wx, wy, lx, ly = box_place(cx, cy, HALO_BOX,
                                   self.vx, self.vy, self.vw, self.vh)
        if self._halo_at != (wx, wy):
            self.halo_win.geometry(geometry_str(HALO_BOX, HALO_BOX, wx, wy))
            self._halo_at = (wx, wy)
        self.halo_cv.coords(self.ring, lx - RING_R, ly - RING_R,
                            lx + RING_R, ly + RING_R)
        self.halo_cv.itemconfig(self.ring, outline=color, width=width,
                                dash=dash or (), state="normal")
        self.halo_cv.coords(self.dot, lx - DOT_R, ly - DOT_R, lx + DOT_R, ly + DOT_R)
        self.halo_cv.itemconfig(self.dot, state="normal")
        if not self._halo_mapped:
            self.halo_win.deiconify()
            self._halo_mapped = True

    def tick(self) -> None:
        with self._lock:
            f = dict(self._frame)

        prev, self._prev = self._prev, dict(f)
        if f.get("locked"):
            self._hide_toast()
        else:
            t = toast_for(prev, f)
            if t is not None:
                self._show_toast(*t)

        self._detect_pulse(f)
        self._render_halo(f)
        self._render_pulse()

        if self._toast_until and time.monotonic() >= self._toast_until:
            self._hide_toast()

        self.root.after(16, self.tick)       # ~60 fps

    def keep_topmost(self) -> None:
        for win in (self.halo_win, self.pulse_win, self.toast_win):
            try:
                win.attributes("-topmost", True)
                win.lift()               # NB: no focus_force — must not steal focus
            except Exception:
                pass
        self.root.after(1000, self.keep_topmost)


def main() -> int:
    if sys.platform != "win32":
        return 0
    root = tk.Tk()
    root.withdraw()                  # the Tk root itself never draws anything
    root.overrideredirect(True)
    root.geometry("1x1+0+0")
    try:
        ov = Overlay(root)
    except Exception as e:            # noqa: BLE001 — includes colour-key failure
        # Guard #2: refuse to run rather than leave opaque windows on screen.
        print(f"[CURSOR_OVERLAY] transparency unavailable, not drawing: {e}",
              flush=True)
        try:
            root.destroy()
        except Exception:
            pass
        return 1

    threading.Thread(target=ov.read_stdin, daemon=True).start()
    root.after(16, ov.tick)
    root.after(1000, ov.keep_topmost)
    root.after(1000, ov.watch_deadman)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
