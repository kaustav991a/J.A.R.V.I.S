import { app, BrowserWindow, ipcMain, screen } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const isDev = !app.isPackaged;

// ── Window references ─────────────────────────────────────────────────────
let notchWindow = null;
let sidecarWindow = null;

// ── Shared preload path ───────────────────────────────────────────────────
const PRELOAD = path.join(__dirname, 'preload.js');
const ICON    = path.join(__dirname, '..', 'public', 'favicon.svg');

/**
 * Resolve the URL/file path for a given hash route.
 *   dev  → http://localhost:5173/#/route
 *   prod → dist/index.html  (loaded with hash)
 */
function loadRoute(win, route) {
  if (isDev) {
    win.loadURL(`http://localhost:5173/#/${route}`);
  } else {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'), {
      hash: `/${route}`,
    });
  }
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
// App lifecycle
// ═══════════════════════════════════════════════════════════════════════════
app.whenReady().then(() => {
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
