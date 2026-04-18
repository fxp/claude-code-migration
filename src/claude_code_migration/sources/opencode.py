"""OpenCode → IR.

Reads:
- ~/.config/opencode/opencode.json       (global config with model + mcp + provider)
- ~/.config/opencode/skills/*/SKILL.md   (global skills)
- ~/.config/opencode/agents/*.md         (global custom agents)
- <project>/opencode.json                (project config)
- <project>/.opencode/agents/*.md        (project agents)
- <project>/.opencode/skills/*/SKILL.md  (project skills)
- <project>/AGENTS.md                    (project context)
- <project>/.opencode/projects/*/AGENTS.md (per-project memory)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..canonical import (
    CanonicalData, McpEndpoint, Agent, Skill, Project, Document,
)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
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
          global_dir: str | Path | None = None,
          **kwargs: Any) -> CanonicalData:
    proj = Path(project_dir).resolve() if project_dir else None
    home_cfg = Path(global_dir).expanduser() if global_dir else \
        Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "opencode"

    ir = CanonicalData(
        source_platform="opencode",
        source_project_dir=str(proj) if proj else None,
    )

    for cfg_path, scope in (
        (home_cfg / "opencode.json", "global"),
        (proj / "opencode.json" if proj else None, "project"),
    ):
        if cfg_path is None or not cfg_path.exists():
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        # MCP servers
        for name, mcp in (cfg.get("mcp") or {}).items():
            if not isinstance(mcp, dict):
                continue
            t = mcp.get("type") or ("remote" if mcp.get("url") else "local")
            if t == "remote":
                ep = McpEndpoint(
                    name=name, transport="http", url=mcp.get("url"),
                    headers=dict(mcp.get("headers") or {}), scope=scope,
                )
            else:
                cmd = mcp.get("command") or []
                if isinstance(cmd, list):
                    command, args = (cmd[0] if cmd else None), list(cmd[1:])
                else:
                    command, args = cmd, []
                ep = McpEndpoint(
                    name=name, transport="stdio", command=command, args=args,
                    env=dict(mcp.get("environment") or {}), scope=scope,
                )
            ir.mcp_endpoints.append(ep)

        # Preserve provider config + model setting
        ir.settings.setdefault(f"opencode_{scope}", {})
        for k in ("$schema", "model", "provider", "default_agent", "instructions"):
            if k in cfg:
                ir.settings[f"opencode_{scope}"][k] = cfg[k]

    # Skills — home + project
    for skills_root, plugin_owner in (
        (home_cfg / "skills", ""),
        (proj / ".opencode" / "skills" if proj else None, ""),
    ):
        if skills_root is None or not skills_root.is_dir():
            continue
        for sdir in skills_root.iterdir():
            if not sdir.is_dir():
                continue
            skill_md = sdir / "SKILL.md"
            if not skill_md.exists():
                continue
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            fm, body = _parse_frontmatter(text)
            ir.skills.append(Skill(
                name=fm.get("name") or sdir.name,
                description=fm.get("description", ""),
                body=body,
                frontmatter=fm,
                source_platform="opencode",
                source_plugin=plugin_owner,
            ))

    # Agents — home + project
    for agents_root in (home_cfg / "agents",
                        proj / ".opencode" / "agents" if proj else None):
        if agents_root is None or not agents_root.is_dir():
            continue
        for f in agents_root.glob("*.md"):
            text = f.read_text(encoding="utf-8", errors="replace")
            fm, body = _parse_frontmatter(text)
            ir.agents.append(Agent(
                name=f.stem,
                description=fm.get("description", ""),
                model=fm.get("model"),
                mode=fm.get("mode", "subagent"),
                instructions=body,
                source_platform="opencode",
            ))

    # Project context: AGENTS.md
    if proj and (proj / "AGENTS.md").exists():
        ir.projects.append(Project(
            name=proj.name,
            slug=proj.name.lower().replace(" ", "-"),
            context=(proj / "AGENTS.md").read_text(encoding="utf-8", errors="replace"),
        ))

    # .opencode/projects/<slug>/AGENTS.md — per-project memory (from migrated Cowork)
    if proj and (proj / ".opencode" / "projects").is_dir():
        for pdir in (proj / ".opencode" / "projects").iterdir():
            if not pdir.is_dir():
                continue
            ag = pdir / "AGENTS.md"
            if not ag.exists():
                continue
            docs: list[Document] = []
            docs_dir = pdir / "docs"
            if docs_dir.is_dir():
                for f in docs_dir.glob("*"):
                    if f.is_file():
                        docs.append(Document(
                            filename=f.name,
                            content=f.read_text(encoding="utf-8", errors="replace"),
                        ))
            ir.projects.append(Project(
                name=pdir.name,
                slug=pdir.name,
                context=ag.read_text(encoding="utf-8", errors="replace"),
                docs=docs,
            ))

    return ir
