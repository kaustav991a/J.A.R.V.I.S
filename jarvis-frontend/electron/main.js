import { app, BrowserWindow, dialog, ipcMain, screen, shell } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const isDev = !app.isPackaged;

// The backend the HUD talks to, and — in a packaged build — the backend that
// SERVES the HUD. Overridable for a non-default port.
const API_ORIGIN = process.env.JARVIS_API_ORIGIN || 'http://127.0.0.1:8000';

// ── Window references ─────────────────────────────────────────────────────
let notchWindow = null;
let sidecarWindow = null;

// ── Shared preload path ───────────────────────────────────────────────────
const PRELOAD = path.join(__dirname, 'preload.js');
const ICON    = path.join(__dirname, '..', 'build', 'icon.ico');

/**
 * Resolve the URL for a given hash route.
 *   dev  → http://localhost:5173/#/route         (Vite)
 *   prod → http://127.0.0.1:8000/hud/#/route     (served by the backend)
 *
 * The packaged build is deliberately NOT loaded off disk with `file://`. A
 * `file://` document has origin `null`, which the backend's four-entry CORS list
 * refuses — and widening that list is how an unauthenticated local API stops
 * being defensible. Serving the bundle from the API's own origin makes the
 * renderer same-origin with everything it calls, and leaves no `file://`
 * document in the process at all.
 */
function routeUrl(route) {
  return isDev
    ? `http://localhost:5173/#/${route}`
    : `${API_ORIGIN}/hud/#/${route}`;
}

function loadRoute(win, route) {
  win.loadURL(routeUrl(route));
}

/**
 * Wait for the backend to answer before opening any window.
 *
 * In a packaged build the backend serves the HUD itself, so opening a window
 * first means a blank frameless rectangle with no way to tell what went wrong —
 * and the notch is `skipTaskbar`, so it cannot even be closed from the taskbar.
 * Better to wait, then say so.
 */
async function waitForBackend({ attempts = 60, delayMs = 500 } = {}) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      const res = await fetch(`${API_ORIGIN}/health`, { method: 'GET' });
      if (res.ok) return true;
    } catch {
      // not up yet — the whole point of the loop
    }
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return false;
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. THE NOTCH — small pill, top-center, always on top
// ═══════════════════════════════════════════════════════════════════════════
function createNotchWindow() {
  const { width: screenW } = screen.getPrimaryDisplay().workAreaSize;
  const notchW = 340;
  const notchH = 72;

  notchWindow = new BrowserWindow({
    width: notchW,
    height: notchH,
    x: Math.round((screenW - notchW) / 2),
    y: 0,                        // Snap to very top edge
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
    icon: ICON,
    show: false,
  });

  notchWindow.once('ready-to-show', () => {
    notchWindow.show();
    if (isDev) notchWindow.webContents.openDevTools({ mode: 'detach' });
  });

  loadRoute(notchWindow, 'notch');

  notchWindow.on('closed', () => { notchWindow = null; });
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. THE SIDECAR — tall slim panel, docked to the right edge
// ═══════════════════════════════════════════════════════════════════════════
function createSidecarWindow() {
  const { width: screenW, height: screenH } = screen.getPrimaryDisplay().workAreaSize;
  const sideW = 370;
  const sideH = Math.min(820, screenH - 40);

  sidecarWindow = new BrowserWindow({
    width: sideW,
    height: sideH,
    x: screenW - sideW,          // Dock to right edge
    y: Math.round((screenH - sideH) / 2),
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    resizable: true,
    movable: true,
    alwaysOnTop: false,
    skipTaskbar: false,
    hasShadow: false,
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
    icon: ICON,
    show: false,
  });

  sidecarWindow.once('ready-to-show', () => {
    sidecarWindow.show();
    if (isDev) sidecarWindow.webContents.openDevTools({ mode: 'detach' });
  });

  loadRoute(sidecarWindow, 'sidecar');

  sidecarWindow.on('closed', () => { sidecarWindow = null; });
}

// ═══════════════════════════════════════════════════════════════════════════
// IPC — window controls scoped by sender
// ═══════════════════════════════════════════════════════════════════════════
function windowFromEvent(event) {
  return BrowserWindow.fromWebContents(event.sender);
}

ipcMain.on('window:minimize', (event) => {
  windowFromEvent(event)?.minimize();
});

ipcMain.on('window:maximize', (event) => {
  const win = windowFromEvent(event);
  if (!win) return;
  win.isMaximized() ? win.unmaximize() : win.maximize();
});

ipcMain.on('window:close', (event) => {
  windowFromEvent(event)?.close();
});

// Cross-window relay: one window can send a message to the other.
// Usage from renderer: jarvisAPI.sendToOther('event-name', payload)
ipcMain.on('ipc:relay', (event, channel, ...args) => {
  const sender = windowFromEvent(event);
  const target = sender === notchWindow ? sidecarWindow : notchWindow;
  if (target && !target.isDestroyed()) {
    target.webContents.send(channel, ...args);
  }
});

// ═══════════════════════════════════════════════════════════════════════════
// Navigation lockdown — the shell-level twin of the browser-tool findings
// ═══════════════════════════════════════════════════════════════════════════
// The HUD renders URLs that arrive over the WebSocket, which means URLs the
// MODEL chose, which since the tool layer means URLs an injected page can
// influence. `BrowserWidget` gates its own iframe, but a top-level navigation
// or a `window.open` bypasses that widget entirely — and inside a shell,
// `file:///…/.env` is a readable document.
//
// So the rule lives here too, once, for every window and every future one:
// the renderer may navigate only within the origin it was loaded from.
// Anything else is refused, and an ordinary web link is handed to the user's
// real browser instead — where it is a tab, not part of this process.
const ALLOWED_ORIGINS = new Set([API_ORIGIN, 'http://localhost:5173']);

function isAllowedOrigin(rawUrl) {
  try {
    return ALLOWED_ORIGINS.has(new URL(rawUrl).origin);
  } catch {
    return false;
  }
}

app.on('web-contents-created', (_event, contents) => {
  contents.on('will-navigate', (event, url) => {
    if (!isAllowedOrigin(url)) {
      event.preventDefault();
      console.warn(`[SHELL] blocked navigation to ${url}`);
    }
  });

  contents.setWindowOpenHandler(({ url }) => {
    // Never a second Electron window. An http(s) link goes to the real browser;
    // anything else (file:, data:, javascript:) is dropped without ceremony.
    if (/^https?:$/.test(safeProtocol(url))) shell.openExternal(url);
    else console.warn(`[SHELL] refused to open ${url}`);
    return { action: 'deny' };
  });

  contents.on('will-attach-webview', (event) => {
    event.preventDefault();  // no <webview> tags, ever
  });
});

function safeProtocol(rawUrl) {
  try {
    return new URL(rawUrl).protocol;
  } catch {
    return '';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// App lifecycle
// ═══════════════════════════════════════════════════════════════════════════

// One JARVIS per desktop. Both windows are frameless and the notch is
// `skipTaskbar`, so a second instance is two overlapping notches with no
// taskbar entry to tell them apart or close one from.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    for (const win of [notchWindow, sidecarWindow]) {
      if (win && !win.isDestroyed()) { win.show(); win.focus(); }
    }
  });

  app.whenReady().then(async () => {
    // A packaged build is served BY the backend, so there is nothing to show
    // until it answers. Waiting is not a nicety here — a window opened early is
    // a blank frameless rectangle the user cannot close.
    if (!isDev) {
      const up = await waitForBackend();
      if (!up) {
        dialog.showErrorBox(
          'J.A.R.V.I.S. backend not running',
          `No response from ${API_ORIGIN} after 30 seconds.\n\n` +
          'Start the backend first (start_jarvis.ps1 does both), then launch ' +
          'this again.',
        );
        app.quit();
        return;
      }
    }
    createNotchWindow();
    createSidecarWindow();
  });

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createNotchWindow();
      createSidecarWindow();
    }
  });
}
