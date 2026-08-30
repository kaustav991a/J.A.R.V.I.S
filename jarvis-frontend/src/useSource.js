import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "./api";

/**
 * One polling source for a HUD widget, with the three states kept apart.
 *
 * WHY THIS EXISTS
 * ---------------
 * The vitals, calendar and mail widgets each carried the same eight lines:
 *
 *     const [data, setData] = useState({ configured: false, ... });
 *     try { const res = await fetch(url); if (res.ok) setData(await res.json()); }
 *     catch (e) { }                                  // silent
 *     if (!data.configured) return <span>VITALS OFFLINE</span>;
 *
 * Three separate situations render identically as OFFLINE:
 *
 *   1. **Not asked yet.** The initial state asserts `configured: false`, so the
 *      widget declares the source offline *before it has ever made a request*.
 *      `/api/health/summary` takes about ten seconds - it calls Google Fit - and
 *      for all ten of them the HUD says VITALS OFFLINE while the vitals are fine.
 *      Measured on the desk: the panel read VITALS OFFLINE while the very same
 *      URL, fetched from the same page, returned `configured:true, steps:799`.
 *   2. **The request failed.** The catch is empty, so a network error is
 *      indistinguishable from a source that answered "not configured".
 *   3. **Genuinely unavailable**, which is the only one the word OFFLINE means.
 *
 * `loading` was tracked in all three widgets and used only to spin the refresh
 * icon - never to change the message. So the HUD stated something it did not
 * know, which is the same failure the backend spent two days removing from what
 * the desk SAYS. A screen is an assertion too.
 *
 * One hook rather than three fixes, because three copies of eight lines is how
 * this happened.
 *
 * Returns `{ data, phase, loading, refresh }` where phase is
 * `"loading" | "error" | "ready" | "unconfigured"`.
 */
export function useSource(path, { intervalMs = 60000, initial = {} } = {}) {
  const [data, setData] = useState(initial);
  const [phase, setPhase] = useState("loading");
  const [loading, setLoading] = useState(false);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}${path}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body = await res.json();
      if (!alive.current) return;
      setData(body);
      setPhase(body && body.configured === false ? "unconfigured" : "ready");
    } catch (e) {
      if (!alive.current) return;
      // Not silent. A widget that cannot say WHY it is empty sends the reader to
      // the backend log for a fault that is in the browser.
      console.warn(`[HUD] ${path} failed:`, e);
      setPhase("error");
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    refresh();
    const timer = setInterval(refresh, intervalMs);
    return () => {
      alive.current = false;
      clearInterval(timer);
    };
  }, [refresh, intervalMs]);

  return { data, phase, loading, refresh };
}
