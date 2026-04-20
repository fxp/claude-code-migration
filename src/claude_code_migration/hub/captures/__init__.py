"""Capture implementations.

Each module implements one Capture subclass. Captures run inside the
hub-agent daemon; they produce Dossier-shaped dict rows and push them
into the L4 outbox via Capture.emit().

Public surface:

    from claude_code_migration.hub.captures import (
        Capture,                 # base class
        ClaudeCodeFSCapture,     # fsnotify ~/.claude/projects/*.jsonl
    )
"""
from __future__ import annotations

from .base import Capture, CaptureContext
from .claude_code_fs import ClaudeCodeFSCapture

__all__ = ["Capture", "CaptureContext", "ClaudeCodeFSCapture"]
