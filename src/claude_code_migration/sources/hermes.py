"""Hermes Agent → IR.

Reads:
- ~/.hermes/config.yaml                  (provider + model + MCP)
- ~/.hermes/memories/USER.md             (user profile)
- ~/.hermes/memories/MEMORY.md           (project memory)
- ~/.hermes/memories/projects/*/context.md  (per-project memory — Cowork import)
- ~/.hermes/skills/*/SKILL.md            (skills, typically cc-*)
- ~/.hermes/state.db                     (SQLite FTS5 conversation archive)
- ~/.hermes/cron/*.md                    (scheduled-tasks preserved)
- ~/.hermes/SOUL.md                      (persona — treated as output style)
- <project>/.hermes.md                   (highest-priority project context)
- <project>/CLAUDE.md                    (Hermes reads this natively too)
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from ..canonical import (
    CanonicalData, Memory, MemoryItem, Project, Skill, McpEndpoint,
    Conversation, Message, ScheduledTask,
)


def _parse_yaml_ish(text: str) -> dict[str, Any]:
    """Minimal YAML parser sufficient for Hermes config.yaml top-level keys."""
    result: dict[str, Any] = {}
    stack = [result]
    indent_stack = [-1]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        while indent_stack and indent <= indent_stack[-1]:
            stack.pop(); indent_stack.pop()
        if stripped.endswith(":"):
            key = stripped[:-1].strip().strip('"').strip("'")
            new: dict[str, Any] = {}
            stack[-1][key] = new
            stack.append(new); indent_stack.append(indent)
        elif ":" in stripped:
            k, v = stripped.split(":", 1)
            k = k.strip().strip('"').strip("'")
            v = v.strip().strip('"').strip("'")
            stack[-1][k] = v
    return result


def _parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    meta_raw, body = m.group(1), m.group(2)
    meta: dict[str, Any] = {}
    for line in meta_raw.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def parse(project_dir: str | Path | None = None,
          hermes_home: str | Path | None = None,
          **kwargs: Any) -> CanonicalData:
    proj = Path(project_dir).resolve() if project_dir else None
    home = Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"

    ir = CanonicalData(
        source_platform="hermes",
        source_project_dir=str(proj) if proj else None,
    )

    # config.yaml — MCP + provider settings
    cfg_path = home / "config.yaml"
    if cfg_path.exists():
        cfg = _parse_yaml_ish(cfg_path.read_text(encoding="utf-8", errors="replace"))
        ir.settings["hermes_config"] = cfg
        # MCP servers
        for name, srv in (cfg.get("mcp_servers") or {}).items():
            if not isinstance(srv, dict):
                continue
            ir.mcp_endpoints.append(McpEndpoint(
                name=name,
                transport=srv.get("transport") or ("http" if srv.get("url") else "stdio"),
                url=srv.get("url"),
                command=srv.get("command"),
                scope="global",
            ))

    # Memories
    mem_dir = home / "memories"
    if (mem_dir / "USER.md").exists():
        ir.memory.user_profile = (mem_dir / "USER.md").read_text(encoding="utf-8", errors="replace")
    if (mem_dir / "MEMORY.md").exists():
        ir.memory.project_memory.append(MemoryItem(
            name="MEMORY.md",
            content=(mem_dir / "MEMORY.md").read_text(encoding="utf-8", errors="replace"),
            type="project",
        ))

    # Per-project memories → Projects
    projs_dir = mem_dir / "projects"
    if projs_dir.is_dir():
        for pd in projs_dir.iterdir():
            if pd.is_dir() and (pd / "context.md").exists():
                ir.projects.append(Project(
                    name=pd.name,
                    slug=pd.name,
                    context=(pd / "context.md").read_text(encoding="utf-8", errors="replace"),
                ))

    # SOUL.md → output style
    if (home / "SOUL.md").exists():
        ir.memory.output_styles.append(MemoryItem(
            name="SOUL.md",
            content=(home / "SOUL.md").read_text(encoding="utf-8", errors="replace"),
            type="output-style",
        ))

    # Project root context
    if proj:
        for candidate, priority in ((".hermes.md", 1), ("CLAUDE.md", 3), ("AGENTS.md", 2)):
            p = proj / candidate
            if p.exists():
                # Attach to first project, or create one
                if ir.projects:
                    if not ir.projects[0].context:
                        ir.projects[0].context = p.read_text(encoding="utf-8", errors="replace")
                else:
                    ir.projects.append(Project(
                        name=proj.name,
                        slug=proj.name.lower().replace(" ", "-"),
                        context=p.read_text(encoding="utf-8", errors="replace"),
                    ))
                break

    # Skills
    skills_dir = home / "skills"
    if skills_dir.is_dir():
        for sd in skills_dir.iterdir():
            if not sd.is_dir():
                continue
            smd = sd / "SKILL.md"
            if not smd.exists():
                continue
            fm, body = _parse_skill_md(smd.read_text(encoding="utf-8", errors="replace"))
            ir.skills.append(Skill(
                name=fm.get("name") or sd.name,
                description=fm.get("description", ""),
                body=body,
                frontmatter=fm,
                source_platform="hermes",
            ))

    # Scheduled tasks (preserved in cron/)
    cron_dir = home / "cron"
    if cron_dir.is_dir():
        for f in cron_dir.glob("*.md"):
            text = f.read_text(encoding="utf-8", errors="replace")
            ir.scheduled_tasks.append(ScheduledTask(
                name=f.stem,
                prompt=text,
                schedule="manual",
            ))

    # Conversations from state.db
    db = home / "state.db"
    if db.exists():
        try:
            conn = sqlite3.connect(str(db))
            sessions = conn.execute(
                "SELECT id, title, started_at, message_count FROM sessions"
            ).fetchall()
            for sid, title, started_at, mc in sessions:
                msgs = conn.execute(
                    "SELECT role, content, timestamp FROM messages WHERE session_id=? ORDER BY id",
                    (sid,)
                ).fetchall()
                ir.conversations.append(Conversation(
                    uuid=sid,
                    title=title or sid,
                    created_at=started_at or "",
                    source_platform="hermes",
                    messages=[
                        Message(uuid=f"{sid}-{i}", role=r, content=c or "",
                                timestamp=str(ts) if ts else "")
                        for i, (r, c, ts) in enumerate(msgs)
                    ],
                ))
            conn.close()
        except Exception:
            pass

    return ir
