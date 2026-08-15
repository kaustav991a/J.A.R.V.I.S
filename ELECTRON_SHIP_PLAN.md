# Ship desk JARVIS as an Electron `.exe`

> Written 2026-08-15. Target: a runnable desktop build **today**.
> Supersedes step 7 of `RESUME.md`'s road-to-exe table. Delete when the `.exe` ships.

## The one decision, and why it was taken this way

**The `.exe` ships the HUD. The Python backend stays a venv process, started by a
launcher.**

Bundling the backend into the installer means PyInstaller over `torch`,
`mediapipe`, `chromadb`, `vosk` and the model files. That is multi-gigabyte, it
puts the `protobuf 6.33.6` pin under a freezer that resolves imports differently,
and it is a multi-day job on its own. It is not a thing that happens today, and
attempting it today is how today produces nothing.

What ships today is the shell the user actually sees, plus a launcher that brings
the backend up with it. The backend can be frozen later without changing any of
the below.

## The origin problem, solved by not having it

`RESUME.md` flags this and it is the one real trap in packaging:

> ⚠️ **The Electron step must revisit this**: the packaged app has a different
> origin, and the tempting fix is a permissive one.

The desk API is unauthenticated on the reasoning that only local processes reach
it, and CORS is an explicit four-entry origin list. A packaged renderer loaded
from `file://` sends `Origin: null`, so every `fetch` in the HUD fails — and the
tempting fix is to add `null` or `*` to that list, which hands the origin check
away for good.

**So the packaged renderer is not loaded from `file://` at all.** The backend
serves the built HUD at `/hud`, and Electron loads
`http://127.0.0.1:8000/hud/#/notch`. Renderer origin and API origin become the
same string, CORS needs no new entry, and there is no `file://` document in the
process — which is the exact surface pre-Electron findings 10 and 13 were about.

Cost: the window has to wait for the backend. That is a retry loop, not a design
problem, and the backend has to be up for JARVIS to do anything anyway.

## Phases

| | What | Whose hands |
|---|---|---|
| **A** | Packaging — the build produces an `.exe` | keyboard |
| **B** | Launch — one thing to double-click | keyboard, verified on his screen |
| **C** | Shell hardening — CSP, single instance, clean quit | keyboard |
| **D** | Smoke gate — it actually runs | his desk |

### A — packaging

- **A1** `main.py` serves `jarvis-frontend/dist` at `/hud`. Absent build = a plain
  404 with a message, never a crash at import.
- **A2** `vite.config.js` gets `base: './'` so the bundle resolves under a subpath.
- **A3** `electron/main.js` prod path loads the served URL instead of `loadFile`,
  behind a wait-for-backend retry with a visible failure after N attempts.
- **A4** `package.json` gains `main`, the `electron` + `electron-builder` dev
  dependencies, the build scripts, and a `build` block (win `nsis` + `portable`,
  `asar`, icon). None of this was ever committed — the June `.exe` in `release/`
  was built from a local package.json that no longer exists.
- **A5** a real `.ico`, and `electron/main.js`'s `ICON` pointed at it (`favicon.svg`
  is not a Windows icon format).
- **A6** build, and confirm the artefacts.

### B — launch

- **B1** `start_jarvis.ps1` — activate venv, start `main.py`, wait on `/health`,
  launch the shell, and stop the backend when the shell exits.
- **B2** the shell itself tolerates a backend that is already up (B1 run twice must
  not start two backends).

### C — shell hardening

- **C1** a CSP on the renderer. `contextIsolation`, `sandbox` and
  `nodeIntegration: false` are already correct in `main.js`.
- **C2** single-instance lock — two notches on one screen is a support call.
- **C3** clean quit. The notch is `skipTaskbar`, so a stranded notch window has no
  taskbar entry to close it from.

### D — smoke gate (his desk)

1. Both windows appear; the notch is centred at the top edge, the sidecar docks right.
2. The WebSocket connects — the HUD shows live telemetry, not the disconnected state.
3. One spoken turn, one action executed, one action confirmed.
4. Quit leaves no orphaned window and no orphaned `python.exe`.

## Not in scope today, deliberately

- Freezing the backend into the installer (see the decision above).
- Code signing. Unsigned means SmartScreen warns on first run; that is expected and
  is a purchase, not a build step.
- Auto-update.
- The §7 live-gate rows. They are about JARVIS's behaviour, not its packaging, and
  they do not become more or less true inside an `.exe`.
