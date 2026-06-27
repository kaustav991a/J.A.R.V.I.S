"""
task_queue.py — Durable Task/Goal Queue (Roadmap §1.1: Continuous Autonomous Agency)
====================================================================================

A persistent SQLite-backed queue of goals J.A.R.V.I.S. should pursue on his own
(the "Overnight Worker Loop"). Tasks survive restarts, so a goal queued at night
is still there in the morning.

Each task carries a list of action payloads (the same {"action_type","target"}
shape the brain emits). The OvernightWorker (modules/worker_loop.py) drains this
queue, executes each task, and writes the result back here.

PURE SYNCHRONOUS SQLite — every function is blocking and MUST be called from async
code via `asyncio.to_thread(...)` so the event loop is never stalled (the
non-blocking pattern used across the backend).
"""

import os
import json
import sqlite3
import uuid
import datetime

_DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jarvis_tasks.db")
)

# --- Status lifecycle -------------------------------------------------------
PENDING            = "pending"
RUNNING            = "running"
DONE               = "done"
FAILED             = "failed"
CANCELLED          = "cancelled"
NEEDS_CONFIRMATION = "needs_confirmation"

# Statuses that represent a finished task whose outcome may need surfacing.
_FINISHED = (DONE, FAILED, NEEDS_CONFIRMATION)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the tasks table if it doesn't exist. Idempotent; safe at import."""
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                actions     TEXT NOT NULL,          -- JSON-encoded list of action payloads
                status      TEXT NOT NULL,
                result      TEXT,
                attempts    INTEGER NOT NULL DEFAULT 0,
                user        TEXT NOT NULL DEFAULT 'KAUSTAV',
                reported    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT,
                started_at  TEXT,
                finished_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["actions"] = json.loads(d.get("actions") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["actions"] = []
    return d


def enqueue(title: str, actions: list[dict], user: str = "KAUSTAV") -> str:
    """
    Add a new goal to the queue. `actions` is a list of action payloads, e.g.
    [{"action_type": "web_search", "target": "AI papers this week"}].
    Returns the new task id.
    """
    tid = uuid.uuid4().hex[:12]
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO tasks (id, title, actions, status, user, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                tid,
                str(title)[:300],
                json.dumps(actions or []),
                PENDING,
                user,
                datetime.datetime.now().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"[TASK_QUEUE] Enqueued task {tid}: {title}", flush=True)
    return tid


def claim_next_pending() -> dict | None:
    """
    Atomically claim the oldest pending task and mark it RUNNING.
    Returns the task dict (with `actions` decoded) or None if the queue is empty.
    BEGIN IMMEDIATE guards against a second worker grabbing the same row.
    """
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at ASC LIMIT 1",
            (PENDING,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        tid = row["id"]
        conn.execute(
            "UPDATE tasks SET status = ?, started_at = ?, attempts = attempts + 1 WHERE id = ?",
            (RUNNING, datetime.datetime.now().isoformat(), tid),
        )
        conn.commit()
        task = _row_to_dict(row)
        task["status"] = RUNNING
        task["attempts"] = (row["attempts"] or 0) + 1
        return task
    finally:
        conn.close()


def _finish(tid: str, status: str, result: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE tasks SET status = ?, result = ?, finished_at = ?, reported = 0 WHERE id = ?",
            (status, (result or "")[:4000], datetime.datetime.now().isoformat(), tid),
        )
        conn.commit()
    finally:
        conn.close()


def mark_done(tid: str, result: str) -> None:
    _finish(tid, DONE, result)


def mark_failed(tid: str, error: str) -> None:
    _finish(tid, FAILED, error)


def mark_needs_confirmation(tid: str, note: str) -> None:
    _finish(tid, NEEDS_CONFIRMATION, note)


def get_unreported_finished(user: str | None = None) -> list[dict]:
    """Finished tasks (done/failed/needs_confirmation) not yet surfaced to the user."""
    conn = _connect()
    try:
        q = (
            f"SELECT * FROM tasks WHERE reported = 0 AND status IN "
            f"({','.join('?' * len(_FINISHED))})"
        )
        params: list = list(_FINISHED)
        if user is not None:
            q += " AND user = ?"
            params.append(user)
        q += " ORDER BY finished_at ASC"
        rows = conn.execute(q, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def mark_reported(tid: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE tasks SET reported = 1 WHERE id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()


def list_tasks(status: str | None = None, limit: int = 50) -> list[dict]:
    conn = _connect()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def cancel(tid: str) -> bool:
    """
    Cancel an in-flight task. Returns True if it was cancelled.
    Covers pending, running, and needs_confirmation (matches the HUD's cancel control) —
    a task awaiting authorisation can be dismissed too.
    """
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE tasks SET status = ?, finished_at = ? WHERE id = ? AND status IN (?, ?, ?)",
            (CANCELLED, datetime.datetime.now().isoformat(), tid, PENDING, RUNNING, NEEDS_CONFIRMATION),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def status_counts() -> dict:
    """Return {status: count} across the whole queue (for HUD + voice summaries)."""
    conn = _connect()
    try:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}
    finally:
        conn.close()


def spoken_status_report() -> str:
    """
    Build a natural, J.A.R.V.I.S.-style spoken summary of the live background queue.
    Reads RUNNING + PENDING tasks (and a nod to recent completions) from jarvis_tasks.db.
    """
    conn = _connect()
    try:
        running = conn.execute(
            "SELECT title FROM tasks WHERE status = ? ORDER BY started_at ASC", (RUNNING,)
        ).fetchall()
        pending = conn.execute(
            "SELECT title FROM tasks WHERE status = ? ORDER BY created_at ASC", (PENDING,)
        ).fetchall()
        done_today = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status = ?", (DONE,)
        ).fetchone()["n"]
    finally:
        conn.close()

    if not running and not pending:
        if done_today:
            return f"Nothing in progress at the moment, Sir. I've completed {done_today} task{'s' if done_today != 1 else ''} so far."
        return "Nothing in the queue right now, Sir. Standing by for orders."

    parts: list[str] = []
    if running:
        first = running[0]["title"]
        if len(running) == 1:
            parts.append(f"I'm currently working on {first}")
        else:
            parts.append(f"I'm currently working on {first}, with {len(running) - 1} other task{'s' if len(running) - 1 != 1 else ''} running")
    if pending:
        n = len(pending)
        # Name the next one for a touch of specificity, like the real J.A.R.V.I.S.
        nxt = pending[0]["title"]
        if n == 1:
            parts.append(f"and I have one more queued: {nxt}")
        else:
            parts.append(f"and I have {n} more queued, starting with {nxt}")

    return ", ".join(parts).strip() + ", Sir."


def requeue_stuck_running() -> int:
    """
    On startup, any task left RUNNING (the server died mid-execution) is reset to
    PENDING so it gets retried. Returns the number of tasks recovered.
    """
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE tasks SET status = ? WHERE status = ?", (PENDING, RUNNING)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# Initialise the table immediately when the module is imported.
init_db()
