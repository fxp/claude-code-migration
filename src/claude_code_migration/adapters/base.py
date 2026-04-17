"""Adapter base class. Each target framework implements an Adapter."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MigrationResult:
    target: str
    files_written: list[str] = field(default_factory=list)
    env_vars_needed: dict[str, str] = field(default_factory=dict)  # var_name -> masked hint
    warnings: list[str] = field(default_factory=list)
    post_install_hint: str = ""


class Adapter(ABC):
    """Interface: take a scan (+ optional cowork export) → write target-native files."""

    name: str = "base"

    @abstractmethod
    def apply(
        self,
        scan: dict[str, Any],
        out_dir: Path,
        project_dir: Path | None = None,
        cowork_export: dict[str, Any] | None = None,
    ) -> MigrationResult:
        """Generate target-native files under out_dir.

        When project_dir is provided, files that belong at the project root
        (like AGENTS.md, .cursor/rules/) go there. Otherwise everything lives
        under out_dir for preview/testing.
        """
        ...


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_universal_agents_md(scan: dict[str, Any], header_note: str = "") -> str:
    """AGENTS.md content — universal across Cursor/Codex/Gemini/OpenCode.

    Merges CLAUDE.md + project memory + rules into a single file.
    """
    parts: list[str] = []
    parts.append("# Project Agent Instructions\n")
    if header_note:
        parts.append(f"> {header_note}\n")
    parts.append("")

    cm = scan.get("claude_md")
    if cm:
        parts.append("## Project Guidelines (from CLAUDE.md)\n")
        parts.append(cm.strip())

    # project + feedback memory
    for m in scan.get("memory") or []:
        mtype = m.get("type")
        if mtype in ("project", "feedback"):
            parts.append(f"\n## {m.get('file', 'memory')}")
            parts.append(m.get("content", "").strip())

    # rules
    for r in scan.get("rules") or []:
        parts.append(f"\n## Rule: {r.get('file', 'rule')}")
        parts.append(r.get("content", "").strip())

    return "\n\n".join(p for p in parts if p) + "\n"
