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

    Merges every signal we have: CLAUDE.md + memory + rules + hooks + env vars +
    MCP inventory + agents + skills + launch configs. Falls back to a reasonable
    project synthesis when no explicit CLAUDE.md exists.
    """
    parts: list[str] = []
    parts.append("# Project Agent Instructions")
    if header_note:
        parts.append(f"> {header_note}\n")

    cm = scan.get("claude_md")
    if cm:
        parts.append("## Project Guidelines (from CLAUDE.md)\n\n" + cm.strip())

    # project + feedback memory
    mem_added = False
    for m in scan.get("memory") or []:
        mtype = m.get("type")
        if mtype in ("project", "feedback"):
            if not mem_added:
                parts.append("## Project Memory")
                mem_added = True
            parts.append(f"### {m.get('file', 'memory')}\n\n{m.get('content', '').strip()}")

    # rules
    rules = scan.get("rules") or []
    if rules:
        parts.append("## Rules")
        for r in rules:
            parts.append(f"### {r.get('file', 'rule')}\n\n{r.get('content', '').strip()}")

    # Agents inventory (Claude Code subagents)
    agents = scan.get("agents") or []
    if agents:
        parts.append("## Custom Agents Available")
        for a in agents[:10]:
            desc = (a.get('description') or '').split('\n')[0][:200]
            parts.append(f"- **{a.get('name')}**: {desc}")

    # Project settings / hooks / env / launch
    proj_settings = scan.get("settings_project") or {}
    sections: list[str] = []
    if proj_settings.get("hooks"):
        hook_events = list((proj_settings["hooks"] or {}).keys())
        sections.append(f"- **Hooks configured**: {', '.join(hook_events)}")
    if proj_settings.get("env"):
        env_vars = list(proj_settings["env"].keys())
        sections.append(f"- **Project env vars**: {', '.join(env_vars)}")
    if proj_settings.get("enableAllProjectMcpServers"):
        sections.append("- **enableAllProjectMcpServers: true**")
    launch = scan.get("launch_json")
    if launch and launch.get("configurations"):
        names = [c.get("name", "?") for c in launch["configurations"]]
        sections.append(f"- **Launch configs**: {', '.join(names)}")
    if sections:
        parts.append("## Project Setup\n\n" + "\n".join(sections))

    # MCP servers inventory
    mcps_global = scan.get("mcp_servers_global") or {}
    mcps_project = scan.get("mcp_servers_project") or {}
    if mcps_global or mcps_project:
        mcp_lines: list[str] = []
        for name, srv in mcps_global.items():
            url = srv.get("url") or f"stdio: {srv.get('command','')}"
            mcp_lines.append(f"- `{name}` (global): {url}")
        for name, srv in mcps_project.items():
            url = srv.get("url") or f"stdio: {srv.get('command','')}"
            mcp_lines.append(f"- `{name}` (project): {url}")
        parts.append("## Connected MCP Servers\n\n" + "\n".join(mcp_lines))

    # Permission summary (helps the agent understand what's allowed)
    perms = (proj_settings.get("permissions") or {}).get("allow") or []
    if perms:
        parts.append(f"## Allowed Tools ({len(perms)} rules)\n\n" +
                     "\n".join(f"- `{p}`" for p in perms[:15]) +
                     (f"\n- _(...{len(perms)-15} more)_" if len(perms) > 15 else ""))

    # Skills inventory
    skills_proj = scan.get("skills_project") or []
    if skills_proj:
        parts.append("## Project-Local Skills\n\n" +
                     "\n".join(f"- **{s.get('name')}**: {(s.get('description') or '')[:150]}"
                               for s in skills_proj))

    # Last resort: if we still have nothing substantial, add project metadata
    body = "\n\n".join(p for p in parts if p.strip())
    if len(body) < 200 and scan.get("project_dir"):
        parts.append(f"## Project Location\n\nMigrated from `{scan['project_dir']}` "
                     f"via claude-code-migration. No CLAUDE.md was present; this file was "
                     f"synthesized from available Claude Code signals.")
        body = "\n\n".join(p for p in parts if p.strip())

    return body + "\n"
