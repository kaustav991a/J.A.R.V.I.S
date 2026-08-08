r"""mcp_client.py — a stdio MCP client, with no SDK and no new dependency.

Roadmap §6.8.3 (Phase 3, reference §6.2). The reference builds this on the `mcp`
package. This does not, for one reason worth stating plainly: **this environment
is one to leave alone.** It carries a hard `protobuf==6.33.6` pin that several
subsystems depend on, and MCP's stdio transport is newline-delimited JSON-RPC
2.0 — a protocol small enough that importing a dependency tree to speak it costs
more risk than it removes. Roughly 150 lines, and nothing above `subprocess`.

WHAT THIS IS NOT
----------------
Not a full MCP implementation. It speaks exactly the four messages a tool bridge
needs — `initialize`, the `notifications/initialized` follow-up, `tools/list`
and `tools/call` — and ignores resources, prompts, sampling and server-initiated
requests. A server that needs those will not work here, and will say so rather
than half-working.

THE PARTS THAT ARE ABOUT SAFETY, NOT PROTOCOL
---------------------------------------------
* **The command is a list, never a string, and never goes through a shell.**
  A server entry is `["npx", "-y", "@modelcontextprotocol/server-filesystem",
  "F:\\work"]`. With `shell=True` a config file would be a remote code execution
  primitive dressed as configuration.
* **Every call has a timeout and every read is bounded.** A server that hangs
  must fail the tool call, not the agent run — and a server that streams forever
  must not fill memory. `MAX_LINE_BYTES` bounds a single message;
  `default_timeout` bounds a call.
* **stderr is drained on a thread.** A subprocess whose stderr pipe fills stops
  writing to stdout too, and the symptom is a hang with no error anywhere —
  which is a very long afternoon if you have not seen it before.
* **Shutdown is ordered and never blocks forever**: close stdin, wait briefly,
  terminate, wait again, kill. A dead client must not leave a process holding a
  filesystem root open.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["StdioMcpClient", "McpError", "McpTimeout", "PROTOCOL_VERSION",
           "MAX_LINE_BYTES"]

#: The version this client implements. Sent in `initialize`; a server that
#: disagrees is expected to negotiate down, and if it cannot, it should fail
#: loudly here rather than mid-task.
PROTOCOL_VERSION = "2024-11-05"

#: One JSON-RPC message may not exceed this. A server that sends more is
#: misbehaving, and the alternative to a cap is an unbounded read from a process
#: we do not control.
MAX_LINE_BYTES = 4_000_000

CLIENT_INFO = {"name": "jarvis", "version": "1.0"}


class McpError(RuntimeError):
    """The server answered, and the answer was an error."""


class McpTimeout(McpError):
    """The server did not answer in time. Deliberately a subclass: to a caller
    'no answer' and 'an error answer' both mean the tool did not run."""


@dataclass
class StdioMcpClient:
    """One MCP server subprocess, and the four messages we speak to it."""

    name: str
    command: list
    env: dict | None = None
    cwd: str | None = None
    #: Seconds any single request may take. A tool call that outlives this fails
    #: the CALL; the run continues and the model is told.
    default_timeout: float = 30.0
    #: The handshake is separate and shorter: a server that cannot say hello in
    #: this long is not going to serve a tool call in 30.
    startup_timeout: float = 20.0

    _proc: Any = field(default=None, init=False)
    _replies: Any = field(default=None, init=False)
    _reader: Any = field(default=None, init=False)
    _stderr: Any = field(default=None, init=False)
    _next_id: int = field(default=0, init=False)
    _lock: Any = field(default_factory=threading.Lock, init=False)
    #: Everything the server said on stderr, capped. Diagnosing "why did that
    #: server refuse" from an empty log is not diagnosing.
    stderr_tail: list = field(default_factory=list, init=False)

    # -- lifecycle ---------------------------------------------------------- #

    def start(self) -> None:
        """Spawn the server and complete the handshake."""
        if not isinstance(self.command, (list, tuple)) or not self.command:
            raise McpError(f"server '{self.name}': command must be a non-empty list")
        try:
            self._proc = subprocess.Popen(
                list(self.command),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd, env=self._child_env(),
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
                # No shell. See the module docstring.
                shell=False,
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"server '{self.name}' would not start: {exc}") from exc

        self._replies = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True,
                                        name=f"mcp-{self.name}-out")
        self._reader.start()
        self._stderr = threading.Thread(target=self._read_stderr, daemon=True,
                                        name=f"mcp-{self.name}-err")
        self._stderr.start()

        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }, timeout=self.startup_timeout)
        # A notification: no id, no reply expected. Servers that follow the spec
        # will not serve tools until they have seen it.
        self.notify("notifications/initialized", {})

    def _child_env(self) -> dict | None:
        if self.env is None:
            return None
        import os
        # The configured env is MERGED over the parent's, not substituted for
        # it: a bare env would strip PATH and SystemRoot, and the failure looks
        # like "the server is broken" rather than "it had no PATH".
        return {**os.environ, **{str(k): str(v) for k, v in self.env.items()}}

    def close(self) -> None:
        """Ordered shutdown. Never blocks for more than a couple of seconds."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        for step in (0.5, 1.0):
            try:
                proc.wait(timeout=step)
                return
            except subprocess.TimeoutExpired:
                proc.terminate() if step == 0.5 else proc.kill()
        try:
            proc.wait(timeout=1.0)
        except Exception:  # noqa: BLE001
            pass

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- the wire ----------------------------------------------------------- #

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if len(line) > MAX_LINE_BYTES:
                self._replies.put({"__oversize__": len(line)})
                continue
            line = line.strip()
            if not line:
                continue
            try:
                self._replies.put(json.loads(line))
            except json.JSONDecodeError:
                # Servers print banners to stdout more often than they should.
                # Not fatal: skip it rather than killing the session.
                print(f"[MCP:{self.name}] non-JSON on stdout: {line[:120]!r}",
                      flush=True)
        self._replies.put(None)     # EOF sentinel: the server is gone

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.rstrip()
            if not line:
                continue
            self.stderr_tail.append(line)
            del self.stderr_tail[:-40]

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params: dict | None = None,
                timeout: float | None = None) -> Any:
        """One JSON-RPC round trip. Raises `McpTimeout` or `McpError`."""
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method,
                    "params": params or {}})
        return self._await_reply(request_id, timeout or self.default_timeout,
                                 method)

    def _send(self, message: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpError(f"server '{self.name}' is not running")
        try:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"server '{self.name}' closed its input: {exc}") from exc

    def _await_reply(self, request_id: int, timeout: float, method: str) -> Any:
        """Wait for OUR id, holding anything else that arrives.

        Out-of-order replies are legal, and a server that emits a notification
        mid-call must not make the call look like a timeout.
        """
        deadline = time.monotonic() + timeout
        stash = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpTimeout(
                        f"server '{self.name}' did not answer '{method}' within "
                        f"{timeout:g}s")
                try:
                    message = self._replies.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    continue
                if message is None:
                    tail = "; ".join(self.stderr_tail[-3:])
                    raise McpError(
                        f"server '{self.name}' exited during '{method}'"
                        + (f" — it said: {tail}" if tail else ""))
                if message.get("__oversize__"):
                    raise McpError(
                        f"server '{self.name}' sent a {message['__oversize__']}-byte "
                        f"message, over the {MAX_LINE_BYTES}-byte limit")
                if message.get("id") != request_id:
                    stash.append(message)      # someone else's, or a notification
                    continue
                if "error" in message:
                    err = message["error"] or {}
                    raise McpError(
                        f"server '{self.name}' refused '{method}': "
                        f"{err.get('message', err)}")
                return message.get("result")
        finally:
            for held in stash:
                self._replies.put(held)

    # -- the two calls a bridge needs --------------------------------------- #

    def list_tools(self) -> list:
        """Every tool the server advertises, as raw dicts."""
        result = self.request("tools/list") or {}
        tools = result.get("tools")
        return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []

    def call_tool(self, tool: str, arguments: dict | None = None,
                  timeout: float | None = None) -> tuple[str, bool]:
        """Run one tool. Returns `(text, is_error)`; never raises for a tool-level
        failure, because a failing tool is something the MODEL must read."""
        try:
            result = self.request("tools/call",
                                  {"name": tool, "arguments": arguments or {}},
                                  timeout=timeout)
        except McpError as exc:
            return str(exc), True
        return flatten_content(result), bool((result or {}).get("isError"))


def flatten_content(result: Any) -> str:
    """Turn an MCP content array into text a model can read.

    Non-text blocks are DESCRIBED, not dropped and not dumped: an image block
    inlined as base64 would swallow the transcript, and dropping it silently
    would tell the model the server returned nothing.
    """
    if not isinstance(result, dict):
        return "(empty result)"
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return "(empty result)"
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text", "")))
        elif kind == "image":
            parts.append(f"[image: {block.get('mimeType', 'unknown type')}, "
                         f"{len(str(block.get('data') or ''))} base64 chars — "
                         f"you cannot see it]")
        elif kind == "resource":
            resource = block.get("resource") or {}
            parts.append(f"[resource: {resource.get('uri', '?')}]")
        else:
            parts.append(json.dumps(block, default=str)[:2000])
    return "\n".join(p for p in parts if p) or "(empty result)"
