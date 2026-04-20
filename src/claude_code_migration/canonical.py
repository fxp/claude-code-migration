"""Canonical Intermediate Representation (IR).

Every Agent product has its own data shapes. Rather than maintaining N×M
adapter pairs (N sources × M targets), we canonicalize everything into
this IR and do N+M transforms:

    source → IR:     sources/claude_code.py, sources/cursor.py,
                     sources/opencode.py, sources/hermes.py, sources/windsurf.py,
                     sources/claude_chat.py (ZIP), sources/claude_cowork.py (ZIP)

    IR → target:     adapters/hermes.py, adapters/opencode.py,
                     adapters/cursor.py, adapters/windsurf.py

This means "Cursor → Hermes" or "OpenCode → Windsurf" or even the inverse
"Hermes → Claude Code" all work without writing new pair-specific code.

The IR is *the union* of all agent-data concepts we've encountered:

    Identity          ← oauthAccount (Claude) / no analog (Cursor/Hermes)
    Memory            ← ~/.claude/memory, CLAUDE.md, AGENTS.md, .cursor/rules,
                        ~/.hermes/memories, .windsurfrules, project_memory
    Project           ← Claude Projects, Cowork workspaces, local code projects
    Conversation      ← conversations.json, ~/.claude/projects/*.jsonl, state.db
    Session           ← session files (distinct from Conversation: Session
                        focuses on tool-call trace)
    Skill             ← SKILL.md files across all platforms
    Agent             ← .claude/agents, .opencode/agents, Claude Code sub-agents
    McpEndpoint       ← ~/.claude.json mcpServers, .mcp.json, opencode.json mcp,
                        .cursor/mcp.json, .codeium/windsurf/mcp_config.json
    Plugin            ← ~/.claude/plugins (Cowork feature)
    Hook              ← settings.json hooks
    ScheduledTask     ← ~/.claude/scheduled-tasks
    Rule              ← path-scoped rules (.cursor/rules, .claude/rules)

Extra fields preserved in `raw_archive` so parsing is lossless even for
platform-specific concepts we don't canonicalize.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


IR_VERSION = "1.0"


@dataclass
class Identity:
    """User identity + org affiliation."""
    account_uuid: str | None = None
    email: str | None = None
    display_name: str | None = None
    org_uuid: str | None = None
    org_name: str | None = None
    org_role: str | None = None           # admin | owner | member | None
    workspace_role: str | None = None     # Cowork workspace role
    billing_type: str | None = None       # apple_subscription | team_plan | enterprise_plan
    is_cowork: bool = False


@dataclass
class Document:
    """A knowledge-base document (e.g. Claude Project doc)."""
    filename: str
    content: str
    mime_type: str = "text/markdown"


@dataclass
class Project:
    """A named context/container — Claude Projects, Cowork workspace projects,
    or simply a code project with a CLAUDE.md / AGENTS.md."""
    name: str
    slug: str                              # url-safe lowercase
    description: str = ""
    context: str = ""                      # Primary prompt (CLAUDE.md / AGENTS.md body)
    prompt_template: str = ""              # Custom instructions (Claude Projects)
    docs: list[Document] = field(default_factory=list)
    is_shared: bool = False
    workspace_id: str | None = None        # Cowork
    created_at: str = ""
    uuid: str | None = None                # original source uuid


@dataclass
class Attachment:
    filename: str
    content: str = ""
    url: str = ""                          # may expire (signed URL from Chat export)


@dataclass
class Message:
    uuid: str
    role: str                              # user | assistant | system | tool
    content: str                           # markdown body
    timestamp: str = ""
    thinking: str = ""                     # hidden reasoning (Claude Chat)
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class Artifact:
    """Claude.ai Artifact — self-contained code/doc block produced in conversation."""
    id: str
    title: str
    mime_type: str                         # text/markdown | application/vnd.ant.code | ...
    extension: str                         # derived: md | txt | tsx | ...
    final_content: str                     # latest revision
    version_count: int = 1


@dataclass
class Conversation:
    """A chat thread — from claude.ai or Claude Code sessions."""
    uuid: str
    title: str
    messages: list[Message] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    project_uuid: str | None = None
    model: str | None = None
    source_platform: str = ""              # claude-chat | claude-cowork | claude-code | hermes | ...


@dataclass
class Rule:
    """Path-scoped rule — e.g. Cursor .mdc with globs, Copilot applyTo."""
    name: str
    description: str = ""
    content: str = ""
    globs: list[str] = field(default_factory=list)
    always_apply: bool = False
    frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryItem:
    """A single memory entry (user profile, project context, feedback rule)."""
    name: str
    content: str
    type: str = ""                         # user | project | feedback | scratch | index
    origin_session_id: str | None = None
    frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass
class Memory:
    """Aggregate memory layer across all scopes."""
    user_profile: str = ""                 # home CLAUDE.md or equivalent
    project_memory: list[MemoryItem] = field(default_factory=list)
    scratch: list[MemoryItem] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    output_styles: list[MemoryItem] = field(default_factory=list)
    agent_memory: list[MemoryItem] = field(default_factory=list)


@dataclass
class Skill:
    """A reusable capability — SKILL.md + bundled resources."""
    name: str                              # canonical, lowercase-hyphenated
    description: str = ""
    body: str = ""                         # SKILL.md body (sans frontmatter)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)  # paths to scripts/references/etc.
    source_platform: str = ""
    source_plugin: str = ""                # if bundled by a plugin (Cowork)


@dataclass
class Agent:
    """Custom agent — Claude Code sub-agent, OpenCode custom agent."""
    name: str
    description: str = ""
    model: str | None = None
    color: str | None = None
    instructions: str = ""                 # agent body / system prompt
    tools: list[str] = field(default_factory=list)
    mode: str = "subagent"                 # subagent | primary | all
    source_platform: str = ""


@dataclass
class McpEndpoint:
    """A Model Context Protocol server configuration."""
    name: str
    transport: str                         # http | sse | stdio
    url: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    scope: str = "global"                  # global | project | plugin
    plugin_owner: str = ""                 # if scope == plugin
    has_embedded_secret: bool = False


@dataclass
class Plugin:
    """A Cowork plugin install record + its bundled resources."""
    id: str                                # "figma@claude-plugins-official"
    name: str
    marketplace: str
    version: str = ""
    install_path: str = ""
    scope: str = "user"                    # user | project | local
    installed_at: str = ""
    git_commit_sha: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    bundled_mcp: list[str] = field(default_factory=list)      # mcp names (full defs in ir.mcp_endpoints)
    bundled_skills: list[str] = field(default_factory=list)   # skill names


@dataclass
class Marketplace:
    name: str
    source_type: str = ""                  # github | url | git-subdir | npm | path
    source_spec: dict[str, Any] = field(default_factory=dict)
    install_location: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass
class Hook:
    """Automation hook — fires on agent events."""
    event: str                             # PostToolUse | SessionStart | PreToolUse | ...
    matcher: str = ""                      # tool-name pattern (PostToolUse-specific)
    type: str = "command"                  # command | http | prompt | agent
    command: str = ""
    timeout_seconds: int = 30


@dataclass
class ScheduledTask:
    name: str
    schedule: str = "manual"               # cron expression or "manual"
    prompt: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass
class CanonicalData:
    """The middle layer. Every source → this. This → every target."""
    version: str = IR_VERSION
    source_platform: str = ""              # platform this was parsed FROM
    source_project_dir: str | None = None  # original project path
    generated_at: str = ""

    identity: Identity | None = None
    memory: Memory = field(default_factory=Memory)
    projects: list[Project] = field(default_factory=list)
    conversations: list[Conversation] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)
    mcp_endpoints: list[McpEndpoint] = field(default_factory=list)
    plugins: list[Plugin] = field(default_factory=list)
    marketplaces: list[Marketplace] = field(default_factory=list)
    hooks: list[Hook] = field(default_factory=list)
    scheduled_tasks: list[ScheduledTask] = field(default_factory=list)

    # Platform-specific settings we don't canonicalize (kept for round-trip)
    settings: dict[str, Any] = field(default_factory=dict)

    # Everything else we couldn't map — lossless preservation
    raw_archive: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_adapter_scan(self) -> dict[str, Any]:
        """Project the IR back to the legacy 'scan_dict' shape that existing
        adapters (adapters/hermes.py, opencode.py, cursor.py, windsurf.py)
        already consume. This lets us plug sources into all 4 target writers
        without changing the adapters.
        """
        scan: dict[str, Any] = {
            "timestamp": self.generated_at,
            "project_dir": self.source_project_dir,
            "claude_md": "",
            "home_claude_md": self.memory.user_profile or None,
            "review_md": None,
            "claude_local_md": None,
            "memory": [asdict(m) for m in self.memory.project_memory + self.memory.scratch],
            "agent_memory": [asdict(m) for m in self.memory.agent_memory],
            "rules": [
                {
                    "file": r.name,
                    "content": r.content,
                    "frontmatter": r.frontmatter,
                    "paths": ",".join(r.globs),
                }
                for r in self.memory.rules
            ],
            "output_styles": [asdict(m) for m in self.memory.output_styles],
            "sessions": [],
            "agents": [asdict(a) for a in self.agents],
            "skills_global": [asdict(s) for s in self.skills if s.source_plugin == ""],
            "plugins_skills": [asdict(s) for s in self.skills if s.source_plugin != ""],
            "skills_project": [],
            "mcp_servers_global": {
                e.name: {
                    "name": e.name,
                    "transport": e.transport,
                    "url": e.url,
                    "command": e.command,
                    "args": e.args,
                    "env": e.env,
                    "headers": e.headers,
                    "has_embedded_secret": e.has_embedded_secret,
                }
                for e in self.mcp_endpoints if e.scope == "global"
            },
            "mcp_servers_project": {
                e.name: {
                    "name": e.name,
                    "transport": e.transport,
                    "url": e.url,
                    "command": e.command,
                    "args": e.args,
                    "env": e.env,
                    "headers": e.headers,
                    "has_embedded_secret": e.has_embedded_secret,
                }
                for e in self.mcp_endpoints if e.scope == "project"
            },
            "plugins": [asdict(p) | {"plugin_name": p.name,
                                     "mcp_servers": self._plugin_mcp_dict(p.id)}
                        for p in self.plugins],
            "marketplaces": [asdict(m) for m in self.marketplaces],
            "org": asdict(self.identity) if self.identity else None,
            "scheduled_tasks": [asdict(s) for s in self.scheduled_tasks],
            "hooks": {
                h.event: [{"type": h.type, "command": h.command, "timeout": h.timeout_seconds}]
                for h in self.hooks
            } if self.hooks else {},
            "settings_global": self.settings.get("global") or {},
            "settings_local": self.settings.get("local") or {},
            "settings_project": self.settings.get("project") or {},
            "settings_project_local": self.settings.get("project_local") or {},
            "launch_json": self.settings.get("launch_json"),
            "plans": [],
            "todos": [],
            "plugins_installed": self.settings.get("plugins_installed"),
            "history_count": self.settings.get("history_count") or 0,
            "worktreeinclude": self.settings.get("worktreeinclude") or [],
            # CLAUDE.md discovery tree (2026 spec expansion — alt project loc,
            # ancestors, subdirs, @imports, managed policy). Adapters archive
            # this verbatim rather than flattening, since Claude Code's
            # concatenation depends on runtime cwd.
            "claude_md_tree": (self.raw_archive or {}).get("claude_md_tree") or {},
        }
        # First Project's context → main CLAUDE.md
        if self.projects:
            scan["claude_md"] = self.projects[0].context
        return scan

    def _plugin_mcp_dict(self, plugin_id: str) -> dict[str, Any]:
        """Helper: lookup plugin-scoped MCP endpoints by plugin id."""
        return {
            e.name: {
                "name": e.name,
                "transport": e.transport,
                "url": e.url,
                "command": e.command,
                "args": e.args,
                "env": e.env,
                "headers": e.headers,
                "has_embedded_secret": e.has_embedded_secret,
            }
            for e in self.mcp_endpoints
            if e.scope == "plugin" and e.plugin_owner == plugin_id
        }

    def to_cowork_export(self) -> dict[str, Any] | None:
        """Project back to the cowork_export shape that adapters expect
        (so Projects + conversations flow through)."""
        if not self.projects and not self.conversations:
            return None
        return {
            "source": self.source_platform,
            "users": [],
            "projects": [
                {
                    "uuid": p.uuid or p.slug,
                    "name": p.name,
                    "description": p.description,
                    "prompt_template": p.prompt_template,
                    "created_at": p.created_at,
                    "is_shared": p.is_shared,
                    "docs": [asdict(d) for d in p.docs],
                }
                for p in self.projects
            ],
            "conversations": [
                {
                    "uuid": c.uuid,
                    "name": c.title,
                    "created_at": c.created_at,
                    "updated_at": c.updated_at,
                    "project_uuid": c.project_uuid,
                    "workspace_id": None,
                    "model": c.model,
                    "messages": [
                        {
                            "uuid": m.uuid, "sender": m.role, "timestamp": m.timestamp,
                            "text": m.content, "thinking": m.thinking,
                            "attachments": [asdict(a) for a in m.attachments],
                        }
                        for m in c.messages
                    ],
                    "artifacts": [asdict(a) for a in c.artifacts],
                }
                for c in self.conversations
            ],
            "workspace_ids": [],
        }


# User-facing alias. `CanonicalData` is the internal / compiler-speak name;
# `WorkspaceDossier` is what the product calls it in docs and CLI. They are the
# same class — `isinstance(x, WorkspaceDossier)` works because it's just a
# module-level rebinding, not a subclass.
WorkspaceDossier = CanonicalData


__all__ = [
    "IR_VERSION", "CanonicalData", "WorkspaceDossier",
    "Identity", "Memory", "MemoryItem", "Rule",
    "Project", "Document",
    "Conversation", "Message", "Artifact", "Attachment",
    "Skill", "Agent",
    "McpEndpoint", "Plugin", "Marketplace",
    "Hook", "ScheduledTask",
]
