"""hub-agent daemon — the long-running process.

Owns:

  • LocalBuffer (L4 SQLite)
  • Redactor (state across all captures)
  • DrainWorker (async: outbox → HubClient)
  • MirrorSync (async: HubClient realtime → mirror tables)
  • N × Capture (one per source: Claude Code fsnotify, Claude Desktop
    proxy, etc.)

Life cycle:

  daemon = HubDaemon(config)
  daemon.start()
  # ... hub-agent runs forever ...
  daemon.stop()      # graceful: flushes outbox best-effort, stops captures

Configuration is a dict for now; we'll stabilize a YAML schema later.
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .buffer import LocalBuffer
from .captures import Capture, CaptureContext, ClaudeCodeFSCapture
from .drain import DrainWorker
from .mirror import MirrorSync
from .redact import Redactor
from .supabase_client import DryRunClient, HubClient, InMemoryClient, SupabaseClient


@dataclass
class HubConfig:
    buffer_path: Path = field(
        default_factory=lambda: Path("~/.dossier-hub/buffer.db").expanduser()
    )
    # Which captures to start. The CLI translates flags into this list.
    enabled_captures: list[str] = field(
        default_factory=lambda: ["claude_code_fs"]
    )
    # Backend: 'supabase' | 'in-memory' | 'dry-run'
    backend: str = "in-memory"
    # If backend='supabase', read SUPABASE_URL + SUPABASE_SERVICE_KEY from env.
    # Also set whether to enable realtime mirror sync.
    enable_mirror: bool = True
    enable_drain: bool = True


class HubDaemon:
    def __init__(self, config: HubConfig | None = None):
        self.config = config or HubConfig()
        self.buffer = LocalBuffer(self.config.buffer_path)
        self.redactor = Redactor()
        self.client: HubClient = self._build_client()
        self.drain = DrainWorker(self.buffer, self.client) if self.config.enable_drain else None
        self.mirror = MirrorSync(self.buffer, self.client) if self.config.enable_mirror else None
        self.captures: list[Capture] = []
        self._stop_evt = threading.Event()

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self) -> None:
        # Captures
        ctx = CaptureContext(
            buffer=self.buffer,
            redactor=self.redactor,
            source_platform="claude-code",   # default; captures can override per-row
        )
        for name in self.config.enabled_captures:
            cap = self._build_capture(name, ctx)
            if cap:
                cap.start()
                self.captures.append(cap)

        # Background workers
        if self.drain:
            self.drain.start()
            print(f"[daemon] drain worker started", file=sys.stderr)
        if self.mirror:
            try:
                self.mirror.subscribe()
                print(f"[daemon] realtime mirror subscribed", file=sys.stderr)
            except Exception as e:
                print(f"[daemon] realtime subscribe failed: {e}", file=sys.stderr)

        print(
            f"[daemon] started · buffer={self.config.buffer_path} · "
            f"backend={self.config.backend} · captures={[c.name for c in self.captures]}",
            file=sys.stderr,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        for cap in self.captures:
            try:
                cap.stop()
            except Exception as e:
                print(f"[daemon] stop({cap.name}) failed: {e}", file=sys.stderr)
        if self.drain:
            self.drain.stop(timeout=timeout)
        try:
            self.client.close()
        except Exception:
            pass
        self.buffer.close()
        print("[daemon] stopped", file=sys.stderr)

    def run_forever(self) -> None:
        """Block the current thread until SIGINT / SIGTERM."""
        def _handler(sig, frame):
            print(f"[daemon] signal {sig}", file=sys.stderr)
            self._stop_evt.set()
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        while not self._stop_evt.is_set():
            time.sleep(0.5)
        self.stop()

    # ── Introspection ──────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "buffer_path": str(self.config.buffer_path),
            "backend": self.config.backend,
            "captures": [c.name for c in self.captures if c.running],
            "outbox": self.buffer.outbox_size(),
            "dead_letter": self.buffer.dead_letter_count(),
            "drain": self.drain.snapshot() if self.drain else None,
            "mirror_stats": self.mirror.stats if self.mirror else None,
        }

    # ── Internals ──────────────────────────────────────────────

    def _build_client(self) -> HubClient:
        b = self.config.backend
        if b == "supabase":
            return SupabaseClient.from_env()
        if b == "dry-run":
            return DryRunClient()
        return InMemoryClient()

    def _build_capture(self, name: str, ctx: CaptureContext) -> Capture | None:
        if name == "claude_code_fs":
            return ClaudeCodeFSCapture(ctx)
        # Stubs for upcoming captures — CLI will reject unknown ones.
        print(f"[daemon] unknown capture: {name!r} (skipping)", file=sys.stderr)
        return None


__all__ = ["HubDaemon", "HubConfig"]
