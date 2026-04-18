"""Claude Code source → IR.

Wraps the existing rich scanner (scanner.py scans 47+ data types) and
projects its output into CanonicalData.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..canonical import (
    CanonicalData, Identity, Memory, MemoryItem, Rule,
    Skill, Agent, McpEndpoint, Plugin, Marketplace, Hook, ScheduledTask, Project,
)
from ..scanner import scan_claude_code


def parse(project_dir: str | Path | None = None,
          include_sessions: bool = False,
          **kwargs: Any) -> CanonicalData:
    """Scan local Claude Code data + wrap as IR."""
    scan = scan_claude_code(project_dir=project_dir, include_sessions=include_sessions, **kwargs)
    d = scan.to_dict()

    ir = CanonicalData(
        source_platform="claude-code",
        source_project_dir=d.get("project_dir"),
        generated_at=d.get("timestamp", ""),
    )

    # Identity
    if d.get("org"):
        o = d["org"]
        ir.identity = Identity(
            account_uuid=o.get("account_uuid"),
            email=o.get("email_address"),
            display_name=o.get("display_name"),
            org_uuid=o.get("organization_uuid"),
            org_name=o.get("organization_name"),
            org_role=o.get("organization_role"),
            workspace_role=o.get("workspace_role"),
            billing_type=o.get("billing_type"),
            is_cowork=bool(o.get("organization_role") and o["organization_role"] != "None"),
        )

    # Memory
    ir.memory.user_profile = d.get("home_claude_md") or ""
    for m in d.get("memory") or []:
        item = MemoryItem(
            name=m.get("file", ""),
            content=m.get("content", ""),
            type=m.get("type") or "",
            frontmatter=m.get("frontmatter") or {},
        )
        if item.type == "scratch":
            ir.memory.scratch.append(item)
        else:
            ir.memory.project_memory.append(item)

    for r in d.get("rules") or []:
        ir.memory.rules.append(Rule(
            name=r.get("file", ""),
            content=r.get("content", ""),
            frontmatter=r.get("frontmatter") or {},
        ))

    for os_ in d.get("output_styles") or []:
        ir.memory.output_styles.append(MemoryItem(
            name=os_.get("file", ""),
            content=os_.get("content", ""),
            type="output-style",
        ))

    for am in d.get("agent_memory") or []:
        ir.memory.agent_memory.append(MemoryItem(
            name=am.get("file", ""),
            content=am.get("content", ""),
            type=am.get("type") or "",
        ))

    # Project (current dir as single project)
    if d.get("project_dir"):
        slug = Path(d["project_dir"]).name.lower().replace(" ", "-")
        ir.projects.append(Project(
            name=Path(d["project_dir"]).name,
            slug=slug,
            context=d.get("claude_md") or "",
        ))

    # Skills (global + plugin-bundled)
    for s in d.get("skills_global") or []:
        ir.skills.append(Skill(
            name=s.get("name", ""),
            description=s.get("description", ""),
            body=s.get("body", ""),
            frontmatter=s.get("frontmatter") or {},
            extras=s.get("extras") or [],
            source_platform="claude-code",
        ))
    for s in d.get("plugins_skills") or []:
        # Plugin skills have "plugin:skill" naming convention
        plugin_owner = s.get("name", "").split(":")[0] if ":" in s.get("name", "") else ""
        ir.skills.append(Skill(
            name=s.get("name", ""),
            description=s.get("description", ""),
            body=s.get("body", ""),
            frontmatter=s.get("frontmatter") or {},
            extras=s.get("extras") or [],
            source_platform="claude-code",
            source_plugin=plugin_owner,
        ))

    # Agents
    for a in d.get("agents") or []:
        ir.agents.append(Agent(
            name=a.get("name", ""),
            description=a.get("description", ""),
            model=a.get("model"),
            color=a.get("color"),
            instructions=a.get("instructions", ""),
            source_platform="claude-code",
        ))

    # MCP endpoints (global + project + plugin-bundled)
    def _to_endpoint(name: str, srv: dict[str, Any], scope: str, plugin_owner: str = "") -> McpEndpoint:
        return McpEndpoint(
            name=name,
            transport=srv.get("transport") or ("http" if srv.get("url") else "stdio"),
            url=srv.get("url"),
            command=srv.get("command"),
            args=list(srv.get("args") or []),
            env=dict(srv.get("env") or {}),
            headers=dict(srv.get("headers") or {}),
            scope=scope,
            plugin_owner=plugin_owner,
            has_embedded_secret=bool(srv.get("has_embedded_secret")),
        )

    for name, srv in (d.get("mcp_servers_global") or {}).items():
        ir.mcp_endpoints.append(_to_endpoint(name, srv, "global"))
    for name, srv in (d.get("mcp_servers_project") or {}).items():
        ir.mcp_endpoints.append(_to_endpoint(name, srv, "project"))

    # Plugins + their bundled MCPs
    for p in d.get("plugins") or []:
        for mname, msrv in (p.get("mcp_servers") or {}).items():
            ir.mcp_endpoints.append(_to_endpoint(mname, msrv, "plugin", plugin_owner=p["id"]))
        ir.plugins.append(Plugin(
            id=p.get("id", ""),
            name=p.get("plugin_name", ""),
            marketplace=p.get("marketplace", ""),
            version=p.get("version", ""),
            install_path=p.get("install_path", ""),
            scope=p.get("scope", "user"),
            installed_at=p.get("installed_at", ""),
            git_commit_sha=p.get("git_commit_sha"),
            manifest=p.get("manifest") or {},
            bundled_mcp=list((p.get("mcp_servers") or {}).keys()),
            bundled_skills=list(p.get("skill_names") or []),
        ))
    for m in d.get("marketplaces") or []:
        ir.marketplaces.append(Marketplace(
            name=m.get("name", ""),
            source_type=m.get("source_type", ""),
            source_spec=m.get("source_spec") or {},
            install_location=m.get("install_location", ""),
            manifest=m.get("manifest") or {},
        ))

    # Hooks
    hooks_cfg = d.get("hooks") or {}
    for event, handlers in hooks_cfg.items():
        for h in (handlers or []):
            for inner in (h.get("hooks") or []):
                ir.hooks.append(Hook(
                    event=event,
                    matcher=h.get("matcher", ""),
                    type=inner.get("type", "command"),
                    command=inner.get("command", ""),
                    timeout_seconds=int(inner.get("timeout", 30)),
                ))

    # Scheduled tasks
    for st in d.get("scheduled_tasks") or []:
        fm = st.get("frontmatter") or {}
        ir.scheduled_tasks.append(ScheduledTask(
            name=st.get("name", ""),
            schedule=str(fm.get("schedule", "manual")),
            prompt=st.get("body", ""),
            frontmatter=fm,
        ))

    # Settings (preserve opaque parts)
    ir.settings = {
        "global": d.get("settings_global") or {},
        "local": d.get("settings_local") or {},
        "project": d.get("settings_project") or {},
        "project_local": d.get("settings_project_local") or {},
        "launch_json": d.get("launch_json"),
        "plugins_installed": d.get("plugins_installed"),
        "history_count": d.get("history_count", 0),
        "worktreeinclude": d.get("worktreeinclude") or [],
    }

    return ir
