const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisAPI', {
  // ── Window Controls ───────────────────────────────────────────────────
  minimize: () => ipcRenderer.send('window:minimize'),
  maximize: () => ipcRenderer.send('window:maximize'),
  close:    () => ipcRenderer.send('window:close'),

  // ── Cross-window messaging (Notch ↔ Sidecar) ─────────────────────────
  // Send a message to the OTHER window via the main process relay.
  sendToOther: (channel, ...args) => ipcRenderer.send('ipc:relay', channel, ...args),

  // Listen for messages relayed from the other window.
  onMessage: (channel, callback) => {
    const handler = (_event, ...args) => callback(...args);
    ipcRenderer.on(channel, handler);
    // Return an unsubscribe function for cleanup in useEffect().
    return () => ipcRenderer.removeListener(channel, handler);
  },

  // ── Environment info ──────────────────────────────────────────────────
  platform: process.platform,
  isElectron: true,
});
