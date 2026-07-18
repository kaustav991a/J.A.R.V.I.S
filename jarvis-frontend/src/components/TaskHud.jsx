import React, { useEffect, useState, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import "./TaskHud.scss";
import { API_BASE } from "../api";

const HUD_EASE = [0.16, 1, 0.3, 1];
const API = `${API_BASE}/api/tasks`;

const STATUS_META = {
  running: { label: "RUNNING", cls: "is-running" },
  pending: { label: "QUEUED", cls: "is-pending" },
  needs_confirmation: { label: "NEEDS OK", cls: "is-confirm" },
  done: { label: "DONE", cls: "is-done" },
  failed: { label: "FAILED", cls: "is-failed" },
  cancelled: { label: "CANCELLED", cls: "is-cancelled" },
};

// Display priority: active work on top, finished below.
const ORDER = { running: 0, pending: 1, needs_confirmation: 2, failed: 3, done: 4, cancelled: 5 };

// Only in-flight tasks can be cancelled.
const CANCELLABLE = new Set(["running", "pending", "needs_confirmation"]);

/**
 * AUTONOMY QUEUE — live view of the Overnight Worker's background tasks.
 * Polls /api/tasks while open (and refetches immediately on a WS task event).
 */
export default function TaskHud({ open, onClose, refreshSignal = 0 }) {
  const [tasks, setTasks] = useState([]);
  const [error, setError] = useState(false);
  const abortRef = useRef(null);

  const fetchTasks = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await fetch(`${API}?limit=30`, { signal: ctrl.signal });
      if (!res.ok) throw new Error("bad status");
      const data = await res.json();
      setTasks(Array.isArray(data.tasks) ? data.tasks : []);
      setError(false);
    } catch (e) {
      if (e.name !== "AbortError") setError(true);
    }
  }, []);

  // Poll while open.
  useEffect(() => {
    if (!open) return;
    fetchTasks();
    const id = setInterval(fetchTasks, 4000);
    return () => {
      clearInterval(id);
      if (abortRef.current) abortRef.current.abort();
    };
  }, [open, fetchTasks]);

  // Refetch the instant a task event arrives over the WebSocket.
  useEffect(() => {
    if (open) fetchTasks();
  }, [refreshSignal, open, fetchTasks]);

  // Manual override: kill a background task immediately.
  const cancelTask = useCallback(async (id) => {
    // Optimistic update so the row goes struck-through grey instantly.
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, status: "cancelled" } : t)),
    );
    try {
      await fetch(`${API}/${id}/cancel`, { method: "POST" });
    } catch (e) {
      /* worker may already have finished it; the next poll reconciles truth */
    } finally {
      fetchTasks();
    }
  }, [fetchTasks]);

  const sorted = [...tasks].sort(
    (a, b) => (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9),
  );
  const active = tasks.filter(
    (t) => t.status === "running" || t.status === "pending",
  ).length;

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          key="task-hud"
          className="task-hud"
          initial={{ opacity: 0, x: -40 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -40 }}
          transition={{ duration: 0.45, ease: HUD_EASE }}
        >
          <div className="task-hud__header">
            <span className="task-hud__title">
              <span className="task-hud__dot" aria-hidden />
              AUTONOMY QUEUE
            </span>
            <span className="task-hud__count">{active} ACTIVE</span>
            <button
              type="button"
              className="task-hud__close"
              onClick={onClose}
              title="Hide queue"
              aria-label="Hide queue"
            >
              ✕
            </button>
          </div>

          <div className="task-hud__body">
            {error ? (
              <div className="task-hud__empty">Queue offline</div>
            ) : sorted.length === 0 ? (
              <div className="task-hud__empty">No background tasks</div>
            ) : (
              sorted.map((t) => {
                const meta =
                  STATUS_META[t.status] || {
                    label: (t.status || "").toUpperCase(),
                    cls: "",
                  };
                return (
                  <div key={t.id} className={`task-row ${meta.cls}`}>
                    <span className="task-row__pip" aria-hidden />
                    <span className="task-row__title" title={t.title}>
                      {t.title}
                    </span>
                    <span className="task-row__status">{meta.label}</span>
                    {CANCELLABLE.has(t.status) && (
                      <button
                        type="button"
                        className="task-row__cancel"
                        onClick={() => cancelTask(t.id)}
                        title="Cancel this task"
                        aria-label={`Cancel ${t.title}`}
                      >
                        ✕
                      </button>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
