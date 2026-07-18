// Central backend origin. One place instead of hard-coded localhost:8000 /
// 127.0.0.1:8000 scattered across the HUD + widgets. Override at build/run time
// with VITE_API_BASE (e.g. "192.168.1.5:8000") to drive JARVIS from another host.
export const API_HOST = import.meta.env.VITE_API_BASE || "127.0.0.1:8000";
export const API_BASE = `http://${API_HOST}`;
export const WS_BASE = `ws://${API_HOST}`;
