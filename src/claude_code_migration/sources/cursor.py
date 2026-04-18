"""Cursor → IR.

Reads:
- <project>/.cursor/rules/*.mdc       (project rules)
- <project>/.cursor/mcp.json          (project MCP)
- ~/.cursor/mcp.json                  (global MCP)
- <project>/AGENTS.md                 (optional Cursor reads it)
- <project>/.cursorrules              (legacy)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..canonical import (
    CanonicalData, McpEndpoint, Rule, Project,
)


def _parse_mdc(text: str) -> tuple[dict[str, Any], str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    meta_raw, body = m.group(1), m.group(2)
    meta: dict[str, Any] = {}
    for line in meta_raw.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v in ("true", "false"):
            meta[k] = v == "true"
        elif v.startswith("[") and v.endswith("]"):
            meta[k] = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
        else:
            meta[k] = v
    return meta, body


def _convert_mcp_entry(name: str, srv: dict[str, Any], scope: str) -> McpEndpoint:
    return McpEndpoint(
        name=name,
        transport=srv.get("type") or ("http" if srv.get("url") else "stdio"),
        url=srv.get("url"),
        command=srv.get("command"),
        args=list(srv.get("args") or []),
        env=dict(srv.get("env") or {}),
        headers=dict(srv.get("headers") or {}),
        scope=scope,
        has_embedded_secret=any(
            "${env:" in str(v) or "auth" in k.lower() or "token" in k.lower()
            for k, v in (srv.get("headers") or {}).items()
        ),
    )


def parse(project_dir: str | Path | None = None,
          global_dir: str | Path | None = None,
          **kwargs: Any) -> CanonicalData:
    proj = Path(project_dir).resolve() if project_dir else None
    home = Path(global_dir).expanduser() if global_dir else Path.home() / ".cursor"

    ir = CanonicalData(
        source_platform="cursor",
        source_project_dir=str(proj) if proj else None,
    )

    # .cursor/rules/*.mdc
    if proj and (proj / ".cursor" / "rules").is_dir():
        for mdc in (proj / ".cursor" / "rules").glob("*.mdc"):
            text = mdc.read_text(encoding="utf-8", errors="replace")
            fm, body = _parse_mdc(text)
            globs_raw = fm.get("globs", "")
            if isinstance(globs_raw, str):
                globs = [g.strip() for g in globs_raw.split(",") if g.strip()]
            elif isinstance(globs_raw, list):
                globs = globs_raw
            else:
                globs = []
            ir.memory.rules.append(Rule(
                name=mdc.stem,
                description=str(fm.get("description", "")),
                content=body,
                globs=globs,
                always_apply=bool(fm.get("alwaysApply", False)),
                frontmatter=fm,
            ))

    # Legacy .cursorrules
    if proj and (proj / ".cursorrules").exists():
        content = (proj / ".cursorrules").read_text(encoding="utf-8", errors="replace")
        ir.memory.rules.append(Rule(
            name="cursorrules-legacy",
            description="Legacy .cursorrules file",
            content=content,
            always_apply=True,
        ))

    # Project MCP (.cursor/mcp.json)
    if proj and (proj / ".cursor" / "mcp.json").exists():
        try:
            d = json.loads((proj / ".cursor" / "mcp.json").read_text())
            for name, srv in (d.get("mcpServers") or {}).items():
                if isinstance(srv, dict):
                    ir.mcp_endpoints.append(_convert_mcp_entry(name, srv, "project"))
        except Exception:
            pass

    # Global MCP (~/.cursor/mcp.json)
    if (home / "mcp.json").exists():
        try:
            d = json.loads((home / "mcp.json").read_text())
            for name, srv in (d.get("mcpServers") or {}).items():
                if isinstance(srv, dict):
                    ir.mcp_endpoints.append(_convert_mcp_entry(name, srv, "global"))
        except Exception:
            pass

    # AGENTS.md → primary project context
    if proj:
        agents_md = proj / "AGENTS.md"
        if agents_md.exists():
            ir.projects.append(Project(
                name=proj.name,
                slug=proj.name.lower().replace(" ", "-"),
                context=agents_md.read_text(encoding="utf-8", errors="replace"),
            ))

    return ir
