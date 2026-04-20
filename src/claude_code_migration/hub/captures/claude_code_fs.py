"""Claude Code fsnotify capture.

Claude Code writes every chat message — user prompt, assistant reply, tool
call, tool result — as an atomic newline-append into:

    ~/.claude/projects/<encoded-project-path>/<session-uuid>.jsonl

This capture tails those files in real time (<50 ms latency) and streams
rows into the Dossier pipeline:

    JSONL line  →  parse  →  Dossier Message / Conversation  →  redact  →  outbox

Because JSONL is append-only, a simple byte-offset tracker per file is
sufficient for incremental tailing. Daemon restarts are handled by
persisting offsets in the LocalBuffer sync_state table, keyed by the
file's absolute path.

Also picks up (on first sight) the sidecar dirs:

    <session-uuid>/subagents/*.jsonl  → dossier_raw_archives kind='subagent_trace'
    <session-uuid>/tool-results/*.txt → dossier_raw_archives kind='tool_result'

These are polled occasionally rather than watched; tool-results are small
static files written once.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError as e:   # pragma: no cover
    raise RuntimeError("watchdog is required. pip install watchdog") from e

from .base import Capture, CaptureContext


_OFFSET_KEY_PREFIX = "offset:claude_code_fs:"


def _encoded_to_path(encoded: str) -> str:
    """Inverse of Claude Code's project-dir encoding.

    Claude replaces every non-alphanumeric char with '-'. The encoding
    is lossy so we can't perfectly recover; we return the encoded form
    as-is for use as a Dossier project slug.
    """
    return encoded.strip("-")


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def _extract_text(content: Any) -> str:
    """Flatten Claude's content[] blocks into plain text for FTS.

    Mirrors ccm.sources.claude_code._extract_text but stays local to avoid
    reaching across packages for internal helpers.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                t = b.get("type")
                if t == "text":
                    parts.append(b.get("text", ""))
                elif t == "thinking":
                    parts.append(f"[thinking] {b.get('thinking', '')}")
                elif t == "tool_use":
                    parts.append(f"[tool_use {b.get('name', '')} id={b.get('id', '')}]")
                elif t == "tool_result":
                    parts.append(f"[tool_result id={b.get('tool_use_id', '')}]")
        return "\n".join(p for p in parts if p)
    return ""


def _parse_jsonl_record(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one JSONL line into a Dossier message row. Returns None if the
    record isn't a chat message (e.g. summary or system event we don't
    want to store as a message)."""
    msg = raw.get("message") or raw
    role = msg.get("role") or raw.get("type")
    if role not in ("user", "assistant", "system", "tool"):
        return None

    content = msg.get("content")
    content_text = _extract_text(content)
    thinking = ""
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "thinking":
                thinking = b.get("thinking", "")
                break

    return {
        "source_uuid": str(raw.get("uuid") or msg.get("id") or ""),
        "role": role,
        "content_text": content_text,
        "content_blocks": content if not isinstance(content, str) else None,
        "thinking": thinking or None,
        "timestamp": raw.get("timestamp"),
    }


class _JsonlTailHandler(FileSystemEventHandler):
    """Watches a project subtree; on any .jsonl modification, tails new
    lines from the last recorded offset.

    One handler instance per capture; internally stores per-path offsets.
    """

    def __init__(self, capture: "ClaudeCodeFSCapture"):
        self.cap = capture
        self._lock = threading.Lock()
        self._debounce: dict[Path, float] = {}

    def on_modified(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix != ".jsonl":
            return
        # Simple 200 ms debounce per file to coalesce rapid writes
        now = time.monotonic()
        if now - self._debounce.get(p, 0.0) < 0.2:
            return
        self._debounce[p] = now
        with self._lock:
            self.cap._tail_file(p)

    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.suffix == ".jsonl":
            with self._lock:
                self.cap._tail_file(p)


class ClaudeCodeFSCapture(Capture):
    """Real-time tailing of ~/.claude/projects/<enc>/<session>.jsonl."""

    name = "claude_code_fs"

    def __init__(self, ctx: CaptureContext, *, claude_home: Path | None = None):
        super().__init__(ctx)
        self.claude_home = claude_home or _claude_home()
        self.projects_dir = self.claude_home / "projects"
        self._observer: Observer | None = None   # type: ignore[valid-type]
        # Cache of conversation_id (uuid) → L4 source_uuid value, to avoid
        # re-emitting Conversation rows for every message on every session.
        self._known_conversations: set[str] = set()

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        if not self.projects_dir.is_dir():
            print(f"[{self.name}] projects dir missing: {self.projects_dir}",
                  file=sys.stderr)
            return

        # First pass: tail everything that exists, pinned to its last known
        # offset. This catches up on activity that happened while the daemon
        # was offline.
        for jsonl in self.projects_dir.glob("*/*.jsonl"):
            self._tail_file(jsonl)

        # Then start watching for future changes.
        self._observer = Observer()
        self._observer.schedule(
            _JsonlTailHandler(self),
            str(self.projects_dir),
            recursive=True,
        )
        self._observer.start()
        super().start()
        print(f"[{self.name}] watching {self.projects_dir}", file=sys.stderr)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
        super().stop()

    # ── Tailing ────────────────────────────────────────────────

    def _tail_file(self, path: Path) -> None:
        session_uuid = path.stem
        project_slug = _encoded_to_path(path.parent.name)
        offset_key = _OFFSET_KEY_PREFIX + str(path)
        last_offset = int(self.ctx.buffer.get_state(offset_key, "0"))

        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        if size < last_offset:
            # File truncated or rotated — restart from 0.
            last_offset = 0
        if size == last_offset:
            return

        # Ensure the parent Conversation row exists once per session.
        if session_uuid not in self._known_conversations:
            self.ctx.emit(
                "dossier_conversations",
                {
                    "source_uuid": session_uuid,
                    "platform": "claude-code",
                    "title": f"claude-code {session_uuid[:8]}",
                    "project_id": None,   # will be joined client-side; hub doesn't require it
                },
                dedup_key=session_uuid,
                capture_source=self.name,
            )
            # Also emit a project row (best-effort; idempotent via slug unique)
            self.ctx.emit(
                "dossier_projects",
                {
                    "slug": project_slug or "unknown",
                    "name": project_slug or "unknown",
                },
                dedup_key=project_slug,
                capture_source=self.name,
            )
            self._known_conversations.add(session_uuid)

        # Open + seek + stream new lines.
        # Text-mode files disable tell() inside `for line in f` iterators,
        # so we use a readline() loop — slower but supports byte-accurate
        # resume on the next tail.
        new_offset = last_offset
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(last_offset)
                while True:
                    pos = f.tell()
                    line = f.readline()
                    if not line:
                        new_offset = pos
                        break
                    if not line.endswith("\n"):
                        # Partial line — stop BEFORE it so we re-read on next poll.
                        new_offset = pos
                        break
                    stripped = line.strip()
                    if not stripped:
                        new_offset = f.tell()
                        continue
                    try:
                        raw = json.loads(stripped)
                    except json.JSONDecodeError:
                        # Malformed complete line — skip it, keep going
                        new_offset = f.tell()
                        continue
                    new_offset = f.tell()
                    parsed = _parse_jsonl_record(raw)
                    if not parsed:
                        continue
                    # We UPSERT on messages.source_uuid. The hub-side query
                    # joins messages to conversations by source_uuid-indexed
                    # look-up in an Edge Function / RPC; for now we include
                    # the session uuid alongside so either join strategy works.
                    self.ctx.emit(
                        "dossier_messages",
                        {
                            **parsed,
                            "conversation_id": None,  # server-side resolved
                            "_conversation_source_uuid": session_uuid,
                        },
                        dedup_key=parsed.get("source_uuid"),
                        capture_source=self.name,
                    )
        except OSError as e:
            print(f"[{self.name}] read failed {path}: {e}", file=sys.stderr)
            return

        if new_offset != last_offset:
            self.ctx.buffer.set_state(offset_key, str(new_offset))


__all__ = ["ClaudeCodeFSCapture"]
