"""Windsurf → IR."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..canonical import (CanonicalData, McpEndpoint, Rule, Project, MemoryItem)


def parse(project_dir: str | Path | None = None,
          codeium_home: str | Path | None = None,
          **kwargs: Any) -> CanonicalData:
    proj = Path(project_dir).resolve() if project_dir else None
    home = Path(codeium_home).expanduser() if codeium_home else \
        Path.home() / ".codeium" / "windsurf"

    ir = CanonicalData(
        source_platform="windsurf",
        source_project_dir=str(proj) if proj else None,
    )

    # Project .windsurfrules
    if proj and (proj / ".windsurfrules").exists():
        content = (proj / ".windsurfrules").read_text(encoding="utf-8", errors="replace")
        ir.projects.append(Project(
            name=proj.name,
            slug=proj.name.lower().replace(" ", "-"),
            context=content,
        ))

    # Project .windsurf/rules/*.md
    rules_dir = (proj / ".windsurf" / "rules") if proj else None
    if rules_dir and rules_dir.is_dir():
        for f in rules_dir.glob("*.md"):
            content = f.read_text(encoding="utf-8", errors="replace")
            ir.memory.rules.append(Rule(name=f.stem, content=content))

    # Global rules: ~/.codeium/windsurf/memories/global_rules.md
    gr = home / "memories" / "global_rules.md"
    if gr.exists():
        ir.memory.user_profile = gr.read_text(encoding="utf-8", errors="replace")

    # MCP config
    mcp_path = home / "mcp_config.json"
    if mcp_path.exists():
        try:
            d = json.loads(mcp_path.read_text())
            for name, srv in (d.get("mcpServers") or {}).items():
                if not isinstance(srv, dict):
                    continue
                url = srv.get("serverUrl") or srv.get("url")
                cmd = srv.get("command")
                ir.mcp_endpoints.append(McpEndpoint(
                    name=name,
                    transport="http" if url else "stdio",
                    url=url, command=cmd,
                    args=list(srv.get("args") or []),
                    env=dict(srv.get("env") or {}),
                    headers=dict(srv.get("headers") or {}),
                    scope="global",
                ))
        except Exception:
            pass

    return ir
