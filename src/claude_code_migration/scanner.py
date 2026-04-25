"""Scanner — enumerates Claude Code data on local disk.

Produces a stable JSON-serializable snapshot that adapters consume.
Honors CLAUDE_CONFIG_DIR env var; autoMemoryDirectory setting is read from
~/.claude/settings.json if present.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def _encoded_project_key(project_dir: Path) -> str:
    """Claude Code's path-encoding for ~/.claude/projects/ directories.

    Claude Code replaces every non-alphanumeric char with '-' and keeps
    the leading '-'. Example: '/Users/foo/Mobile Documents' →
    '-Users-foo-Mobile-Documents'.
    """
    s = str(project_dir)
    return re.sub(r"[^A-Za-z0-9]+", "-", s)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML-ish frontmatter (---...---) prefix. Returns (meta, body)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
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
        elif re.match(r"^-?\d+$", v):
            meta[k] = int(v)
        elif v:
            meta[k] = v
    return meta, body


@dataclass
class MemoryFile:
    file: str
    path: str
    type: str | None
    content: str
    frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    uuid: str
    path: str
    size_bytes: int
    line_count: int
    messages: list[dict[str, Any]] = field(default_factory=list)  # parsed JSONL entries
    subagents: list[dict[str, Any]] = field(default_factory=list)  # from <session>/subagents/
    tool_results: dict[str, str] = field(default_factory=dict)     # toolu_id → payload


@dataclass
class ShellSnapshot:
    path: str
    size_bytes: int
    mtime: str
    content: str = ""


@dataclass
class SessionEnv:
    """One session-env bundle — ~/.claude/session-env/<uuid>/"""
    session_uuid: str
    path: str
    files: dict[str, str] = field(default_factory=dict)  # filename → content


@dataclass
class FileHistoryEntry:
    """~/.claude/file-history/<uuid>/<hash>@v<n> — Edit-tool file snapshots."""
    session_uuid: str
    file_id: str                 # <hash>@v<n>
    path: str
    size_bytes: int
    content: str = ""


@dataclass
class AgentDef:
    name: str
    path: str
    description: str
    model: str | None
    color: str | None
    instructions: str


@dataclass
class SkillDef:
    name: str
    path: str
    description: str
    frontmatter: dict[str, Any]
    body: str
    extras: list[str]  # relative paths to scripts/ references/ etc.


@dataclass
class CommandDef:
    """A custom slash command — `~/.claude/commands/<ns>/<name>.md` or
    project `.claude/commands/...`. Subdirectories namespace the command
    (e.g. `frontend/test.md` is invoked as `/frontend:test`).
    Per Claude Code 2026 spec: same mechanism as skills, kept for legacy.
    """
    name: str            # canonical slash name including namespace, e.g. "frontend:test"
    path: str
    body: str
    frontmatter: dict[str, Any] = field(default_factory=dict)
    # Common frontmatter fields surfaced for adapter convenience:
    description: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    argument_hint: str = ""


@dataclass
class ThemeFile:
    """A file inside `~/.claude/themes/` (custom user theme)."""
    file: str            # relative to themes/
    path: str
    content: str


@dataclass
class McpServer:
    name: str
    transport: str  # "http" | "stdio"
    url: str | None
    command: str | None
    args: list[str]
    env: dict[str, str]
    headers: dict[str, str]
    has_embedded_secret: bool


@dataclass
class PluginInstall:
    """A plugin installed via Claude Code plugin marketplace (Cowork feature).

    Each plugin CAN bundle its own MCP servers and skills. These live at
    ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/
    """
    id: str                              # e.g. "figma@claude-plugins-official"
    plugin_name: str                     # e.g. "figma"
    marketplace: str                     # e.g. "claude-plugins-official"
    version: str
    install_path: str
    scope: str                           # user | project | local
    installed_at: str
    last_updated: str
    git_commit_sha: str | None
    # Plugin definition from .claude-plugin/plugin.json
    manifest: dict[str, Any] = field(default_factory=dict)
    # Bundled MCP servers from <plugin>/.mcp.json
    mcp_servers: dict[str, McpServer] = field(default_factory=dict)
    # Bundled skills from <plugin>/skills/*/SKILL.md (name only, full body in skills_from_plugins)
    skill_names: list[str] = field(default_factory=list)
    # Plugin-bundled executables (W14 spec: `<plugin>/bin/*` joins PATH while plugin enabled)
    bin_files: list[str] = field(default_factory=list)
    # Plugin-bundled custom slash commands (`<plugin>/commands/**/*.md`)
    command_names: list[str] = field(default_factory=list)
    # Plugin-bundled custom subagents (`<plugin>/agents/*.md`)
    agent_names: list[str] = field(default_factory=list)


@dataclass
class Marketplace:
    """A plugin marketplace known to this Claude Code install (~/.claude/plugins/known_marketplaces.json)."""
    name: str                            # e.g. "claude-plugins-official"
    source_type: str                     # github | url | git-subdir | npm | path
    source_spec: dict[str, Any]          # repo / url / package depending on source_type
    install_location: str
    last_updated: str
    manifest: dict[str, Any] = field(default_factory=dict)  # marketplace.json contents


@dataclass
class OrgMetadata:
    """Claude Cowork org info from ~/.claude.json oauthAccount.

    When organizationRole is 'admin' or 'owner', and billingType is 'team_plan'
    or 'enterprise_plan', this is a Cowork account.
    """
    account_uuid: str | None = None
    organization_uuid: str | None = None
    organization_name: str | None = None
    organization_role: str | None = None       # admin | member | owner | None
    workspace_role: str | None = None          # Cowork workspace role
    billing_type: str | None = None            # apple_subscription | team_plan | enterprise_plan
    has_extra_usage_enabled: bool = False
    email_address: str | None = None
    display_name: str | None = None

    @property
    def is_cowork(self) -> bool:
        return bool(self.organization_role and self.organization_role != "None") or \
               (self.billing_type or "") in ("team_plan", "enterprise_plan")


@dataclass
class ClaudeScan:
    timestamp: str
    claude_home: str
    project_dir: str | None
    claude_md: str | None
    home_claude_md: str | None
    review_md: str | None
    claude_local_md: str | None
    memory: list[MemoryFile] = field(default_factory=list)
    agent_memory: list[MemoryFile] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    agents: list[AgentDef] = field(default_factory=list)
    skills_global: list[SkillDef] = field(default_factory=list)
    skills_project: list[SkillDef] = field(default_factory=list)
    rules: list[MemoryFile] = field(default_factory=list)
    output_styles: list[MemoryFile] = field(default_factory=list)
    loop_md_project: str | None = None
    loop_md_global: str | None = None
    mcp_servers_global: dict[str, McpServer] = field(default_factory=dict)
    mcp_servers_project: dict[str, McpServer] = field(default_factory=dict)
    settings_global: dict[str, Any] = field(default_factory=dict)
    settings_local: dict[str, Any] = field(default_factory=dict)
    settings_project: dict[str, Any] = field(default_factory=dict)
    settings_project_local: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, Any] = field(default_factory=dict)
    launch_json: dict[str, Any] | None = None
    plans: list[dict[str, str]] = field(default_factory=list)
    todos: list[dict[str, Any]] = field(default_factory=list)
    plugins_installed: dict[str, Any] | None = None  # raw installed_plugins.json (for reference)
    plugins: list[PluginInstall] = field(default_factory=list)  # structured inventory
    plugins_skills: list[SkillDef] = field(default_factory=list)  # skills bundled in installed plugins
    marketplaces: list[Marketplace] = field(default_factory=list)
    org: OrgMetadata | None = None
    scheduled_tasks: list[dict[str, Any]] = field(default_factory=list)  # ~/.claude/scheduled-tasks/
    history_count: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)  # parsed ~/.claude/history.jsonl entries
    worktreeinclude: list[str] = field(default_factory=list)
    # Per-project nested state from ~/.claude.json["projects"][path]
    project_state: dict[str, Any] = field(default_factory=dict)
    # Top-level ~/.claude.json keys we deliberately preserve (not settings.json)
    dot_claude_meta: dict[str, Any] = field(default_factory=dict)
    # Environment reproduction
    shell_snapshots: list[ShellSnapshot] = field(default_factory=list)
    session_envs: list[SessionEnv] = field(default_factory=list)
    file_history: list[FileHistoryEntry] = field(default_factory=list)
    mcp_needs_auth: dict[str, Any] = field(default_factory=dict)
    # ── CLAUDE.md discovery tree (per 2026 spec, code.claude.com/docs/en/memory) ──
    # Alternate project-level location: ./.claude/CLAUDE.md
    project_claude_md_dotclaude: str | None = None
    # Ancestor walk: CLAUDE.md / CLAUDE.local.md from each parent dir up to repo/fs root
    ancestor_claude_mds: list[MemoryFile] = field(default_factory=list)
    # Subdirectory CLAUDE.md files (lazy-loaded by Claude Code on file access)
    subdir_claude_mds: list[MemoryFile] = field(default_factory=list)
    # Files pulled in via @path imports (recursive, max depth 5, per spec)
    claude_md_imports: list[MemoryFile] = field(default_factory=list)
    # Enterprise managed-policy CLAUDE.md (OS-specific path, cannot be excluded)
    managed_claude_md: str | None = None
    managed_claude_md_path: str | None = None
    # Custom slash commands (`~/.claude/commands/` and `<proj>/.claude/commands/`)
    commands_global: list[CommandDef] = field(default_factory=list)
    commands_project: list[CommandDef] = field(default_factory=list)
    # Plugin-bundled commands — full bodies (PluginInstall.command_names is summary-only)
    plugins_commands: list[CommandDef] = field(default_factory=list)
    # User themes (`~/.claude/themes/`)
    themes: list[ThemeFile] = field(default_factory=list)
    # User keybindings (`~/.claude/keybindings.json`)
    keybindings: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        # Convert McpServer objects in dicts
        d = asdict(self)
        return d


def _read_safe(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────
# CLAUDE.md discovery (per 2026 spec, code.claude.com/docs/en/memory)
# ──────────────────────────────────────────────────────────────────────────

_IMPORT_RE = re.compile(r"(?:^|\s)@([^\s\n`'\"]+)")
_IMPORT_MAX_DEPTH = 5  # per spec


def _managed_claude_md_path() -> Path | None:
    """Return the managed-policy CLAUDE.md path for this OS, or None if the
    current platform has no such concept."""
    import platform, sys
    system = platform.system()
    if system == "Darwin":
        return Path("/Library/Application Support/ClaudeCode/CLAUDE.md")
    if system == "Windows" or sys.platform.startswith("win"):
        return Path(r"C:\Program Files\ClaudeCode\CLAUDE.md")
    # Linux + WSL (treat all other POSIX as Linux-style)
    return Path("/etc/claude-code/CLAUDE.md")


def _walk_ancestor_claude_mds(proj: Path) -> list[MemoryFile]:
    """Walk upward from ``proj`` collecting CLAUDE.md + CLAUDE.local.md at each
    ancestor directory. Matches Claude Code's concatenation semantics — every
    discovered file is loaded, more specific locations (closer to proj) listed
    last so they take precedence in downstream concatenation."""
    out: list[MemoryFile] = []
    cur = proj.parent
    seen: set[Path] = set()
    # Walk up until fs root; cap at 12 hops as a safety net
    for _ in range(12):
        if cur in seen or cur == cur.parent:
            break
        seen.add(cur)
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            f = cur / name
            if f.is_file():
                text = _read_safe(f) or ""
                meta, _ = _parse_frontmatter(text)
                out.append(MemoryFile(
                    file=f"ancestor:{f}",
                    path=str(f),
                    type=meta.get("type") or "ancestor-claude-md",
                    content=text,
                    frontmatter=meta,
                ))
        cur = cur.parent
    # Reverse: farthest ancestor first (matches load order semantically)
    return list(reversed(out))


def _walk_subdir_claude_mds(proj: Path, max_files: int = 200) -> list[MemoryFile]:
    """Enumerate CLAUDE.md files inside ``proj`` (lazy-loaded by Claude Code).
    Skips node_modules / .git / .venv etc. Capped at ``max_files`` so a
    pathological repo doesn't explode the scan."""
    out: list[MemoryFile] = []
    SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
                 ".next", ".nuxt", "dist", "build", "target", ".tox",
                 ".mypy_cache", ".pytest_cache", ".claude"}  # .claude handled separately
    for root, dirs, files in os.walk(proj):
        # in-place prune
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        root_p = Path(root)
        # Skip the project root itself (handled by scan.claude_md / claude_local_md)
        if root_p == proj:
            continue
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            if name in files:
                f = root_p / name
                text = _read_safe(f) or ""
                meta, _ = _parse_frontmatter(text)
                rel = f.relative_to(proj)
                out.append(MemoryFile(
                    file=f"subdir:{rel}",
                    path=str(f),
                    type=meta.get("type") or "subdir-claude-md",
                    content=text,
                    frontmatter=meta,
                ))
                if len(out) >= max_files:
                    return out
    return out


def _expand_claude_md_imports(seed_files: list[tuple[Path, str]],
                              depth: int = _IMPORT_MAX_DEPTH) -> list[MemoryFile]:
    """Recursively resolve ``@path`` import tokens in CLAUDE.md-style text.

    Per spec (code.claude.com/docs/en/memory):
      - Both relative and absolute paths are allowed.
      - Relative paths resolve against the file containing the import.
      - Imported files can import further, capped at 5 hops.
      - Imports inside code fences are NOT expanded; we match outside them.

    ``seed_files`` is a list of (path, content) tuples to start from.
    """
    out: list[MemoryFile] = []
    seen: set[Path] = set()
    queue: list[tuple[Path, str, int]] = [(p, c, 0) for p, c in seed_files]
    while queue:
        containing, text, hop = queue.pop(0)
        if hop >= depth:
            continue
        # Strip fenced code blocks so we don't match @foo inside triple-backticks
        stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        stripped = re.sub(r"`[^`\n]*`", "", stripped)  # inline code too
        for m in _IMPORT_RE.finditer(stripped):
            token = m.group(1).rstrip(".,;:)")  # trim punctuation
            # Skip e.g. email addresses ("@foo" in an email) and bare words
            # with no path separator + no extension — heuristic that matches
            # spec examples (`@README`, `@package.json`, `@docs/git-instructions.md`,
            # `@~/.claude/foo.md`).
            if "/" not in token and "." not in token and not token.startswith("~"):
                continue
            # Resolve path
            p_str = token
            if p_str.startswith("~"):
                target = Path(p_str).expanduser()
            elif p_str.startswith("/"):
                target = Path(p_str)
            else:
                target = (containing.parent / p_str).resolve()
            if target in seen or not target.is_file():
                continue
            seen.add(target)
            content = _read_safe(target) or ""
            meta, _ = _parse_frontmatter(content)
            out.append(MemoryFile(
                file=f"@import:{token}",
                path=str(target),
                type=meta.get("type") or "claude-md-import",
                content=content,
                frontmatter=meta,
            ))
            # Recurse
            queue.append((target, content, hop + 1))
    return out


def _load_json_safe(p: Path) -> dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_mcp_server(name: str, cfg: dict[str, Any]) -> McpServer:
    url = cfg.get("url")
    transport = cfg.get("type") or ("http" if url else "stdio")
    headers = dict(cfg.get("headers") or {})
    has_secret = False
    for k, v in headers.items():
        if "auth" in k.lower() or "bearer" in str(v).lower() or "token" in k.lower():
            has_secret = True
            break
    # Also detect command-level secrets
    env = dict(cfg.get("env") or {})
    for k, v in env.items():
        if any(x in k.lower() for x in ("key", "secret", "token", "password")) and v:
            has_secret = True
    return McpServer(
        name=name,
        transport=transport,
        url=url,
        command=cfg.get("command"),
        args=list(cfg.get("args") or []),
        env=env,
        headers=headers,
        has_embedded_secret=has_secret,
    )


def _scan_skill_dir(base: Path, name: str) -> SkillDef | None:
    # Accept SKILL.md or skill.md
    skill_md = base / "SKILL.md"
    if not skill_md.exists():
        skill_md = base / "skill.md"
    if not skill_md.exists():
        return None
    text = _read_safe(skill_md) or ""
    meta, body = _parse_frontmatter(text)
    extras: list[str] = []
    for sub in ("scripts", "references", "templates", "bin", "assets"):
        sub_dir = base / sub
        if sub_dir.is_dir():
            for f in sub_dir.rglob("*"):
                if f.is_file() and not f.name.startswith(".") and "node_modules" not in f.parts:
                    extras.append(str(f.relative_to(base)))
    return SkillDef(
        name=name,
        path=str(skill_md),
        description=str(meta.get("description", "")),
        frontmatter=meta,
        body=body,
        extras=extras,
    )


def _scan_commands_dir(base: Path, prefix: str = "") -> list[CommandDef]:
    """Recursively walk a `commands/` directory.

    Subdirectories namespace commands per Claude Code spec — `frontend/test.md`
    becomes `/frontend:test`. Frontmatter fields `description`, `allowed-tools`,
    and `argument-hint` are surfaced explicitly; the rest stays in `frontmatter`.
    """
    out: list[CommandDef] = []
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*.md")):
        if not f.is_file() or f.name.startswith("."):
            continue
        rel = f.relative_to(base)
        # Build namespaced name: foo/bar/baz.md → "foo:bar:baz"
        parts = list(rel.with_suffix("").parts)
        ns_name = ":".join(parts)
        if prefix:
            ns_name = f"{prefix}:{ns_name}"
        text = _read_safe(f) or ""
        meta, body = _parse_frontmatter(text)
        allowed = meta.get("allowed-tools") or meta.get("allowedTools") or []
        if isinstance(allowed, str):
            # Frontmatter scalar can be a comma-separated string
            allowed = [t.strip() for t in allowed.split(",") if t.strip()]
        out.append(CommandDef(
            name=ns_name,
            path=str(f),
            body=body,
            frontmatter=meta,
            description=str(meta.get("description", "")),
            allowed_tools=list(allowed),
            argument_hint=str(meta.get("argument-hint", "") or meta.get("argumentHint", "")),
        ))
    return out


def _scan_themes_dir(base: Path) -> list[ThemeFile]:
    """Read every file inside `~/.claude/themes/` (file format is unspecified
    in the spec; we capture it verbatim so a theme survives migration even if
    its format changes)."""
    out: list[ThemeFile] = []
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        out.append(ThemeFile(
            file=str(f.relative_to(base)),
            path=str(f),
            content=_read_safe(f) or "",
        ))
    return out


def _scan_memory_dir(directory: Path) -> list[MemoryFile]:
    out: list[MemoryFile] = []
    if not directory.is_dir():
        return out
    for f in sorted(directory.glob("*.md")):
        text = _read_safe(f) or ""
        meta, _ = _parse_frontmatter(text)
        out.append(MemoryFile(
            file=f.name,
            path=str(f),
            type=meta.get("type"),
            content=text,
            frontmatter=meta,
        ))
    return out


def scan_claude_code(
    project_dir: str | Path | None = None,
    include_sessions: bool = True,
    include_agent_memory: bool = True,
    include_session_bodies: bool = True,
    include_env_reproduction: bool = True,
    max_session_body_mb: int = 32,
) -> ClaudeScan:
    """Scan Claude Code data on disk.

    Args:
        project_dir: Optional project to focus on. If None, scans globally.
        include_sessions: Whether to enumerate JSONL session files.
        include_agent_memory: Whether to scan agent-memory dirs.
        include_session_bodies: Read JSONL message bodies + subagents/ + tool-results/.
            Setting False keeps legacy metadata-only behavior.
        include_env_reproduction: Scan shell-snapshots, session-env, file-history,
            mcp-needs-auth-cache so the migrated agent can reproduce the shell env.
        max_session_body_mb: Per-file size cap for session/history/snapshot bodies
            to avoid loading multi-GB artifacts.
    """
    from datetime import datetime, timezone

    claude_home = _claude_home()
    proj = Path(project_dir).resolve() if project_dir else None

    scan = ClaudeScan(
        timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        claude_home=str(claude_home),
        project_dir=str(proj) if proj else None,
        claude_md=None,
        home_claude_md=None,
        review_md=None,
        claude_local_md=None,
    )

    # ~/.claude.json (core state file)
    dot_claude_json = Path.home() / ".claude.json"
    if dot_claude_json.exists():
        d = _load_json_safe(dot_claude_json)
        for name, cfg in (d.get("mcpServers") or {}).items():
            if isinstance(cfg, dict):
                scan.mcp_servers_global[name] = _parse_mcp_server(name, cfg)

        # Top-level meta worth preserving (usage, tips, flags that affect UX).
        # Skip opaque/ephemeral caches and feature-flag cruft.
        _META_KEYS = (
            "skillUsage", "toolUsage", "clientDataCache",
            "customApiKeyResponses", "tipsHistory", "promptQueueUseCount",
            "githubRepoPaths", "feedbackSurveyState",
            "hasCompletedOnboarding", "hasSeenTasksHint", "hasSeenStashHint",
            "installMethod", "autoUpdates", "userID", "anonymousId",
            "firstStartTime", "claudeCodeFirstTokenDate",
        )
        scan.dot_claude_meta = {k: d[k] for k in _META_KEYS if k in d}

        # Per-project state: allowedTools, mcpContextUris, enabledMcpjsonServers,
        # disabledMcpjsonServers, hasTrustDialogAccepted, exampleFiles, lastSessionId,
        # lastCost / lastAPIDuration / token counters, etc.
        proj_state = d.get("projects") or {}
        if proj:
            # Exact path match first, then case-insensitive fallback
            key = str(proj)
            if key in proj_state:
                scan.project_state = dict(proj_state[key])
            else:
                for k in proj_state:
                    if k.lower() == key.lower():
                        scan.project_state = dict(proj_state[k])
                        break

        # oauthAccount → OrgMetadata (Cowork indicator)
        oauth = d.get("oauthAccount") or {}
        if oauth:
            scan.org = OrgMetadata(
                account_uuid=oauth.get("accountUuid"),
                organization_uuid=oauth.get("organizationUuid"),
                organization_name=oauth.get("organizationName"),
                organization_role=oauth.get("organizationRole"),
                workspace_role=oauth.get("workspaceRole"),
                billing_type=oauth.get("billingType"),
                has_extra_usage_enabled=bool(oauth.get("hasExtraUsageEnabled")),
                email_address=oauth.get("emailAddress"),
                display_name=oauth.get("displayName"),
            )

    # ~/.claude/CLAUDE.md
    scan.home_claude_md = _read_safe(claude_home / "CLAUDE.md")

    # Enterprise managed-policy CLAUDE.md — OS-specific, cannot be excluded
    # (per Claude Code 2026 spec §Deploy organization-wide CLAUDE.md)
    mp_path = _managed_claude_md_path()
    if mp_path and mp_path.is_file():
        scan.managed_claude_md_path = str(mp_path)
        scan.managed_claude_md = _read_safe(mp_path)

    # Global settings
    scan.settings_global = _load_json_safe(claude_home / "settings.json")
    scan.settings_local = _load_json_safe(claude_home / "settings.local.json")

    # Global loop.md
    scan.loop_md_global = _read_safe(claude_home / "loop.md")

    # Global plans — read full body (small markdown files)
    plans_dir = claude_home / "plans"
    if plans_dir.is_dir():
        for f in plans_dir.glob("*.md"):
            scan.plans.append({
                "name": f.name,
                "path": str(f),
                "size": str(f.stat().st_size),
                "content": _read_safe(f) or "",
            })
    # Global todos — keep full item list so migration preserves pending work
    todos_dir = claude_home / "todos"
    if todos_dir.is_dir():
        for f in todos_dir.glob("*.json"):
            try:
                items = json.loads(f.read_text())
                if items:
                    scan.todos.append({
                        "path": str(f),
                        "count": len(items) if isinstance(items, list) else 1,
                        "items": items,
                    })
            except (OSError, json.JSONDecodeError) as e:
                import sys
                print(f"⚠️  scanner: skipping unreadable todo {f.name} — {e}",
                      file=sys.stderr)

    # Plugin inventory (Cowork feature) — walk plugins/, marketplaces/, cache/
    plugins_dir = claude_home / "plugins"
    plugins_file = plugins_dir / "installed_plugins.json"
    if plugins_file.exists():
        scan.plugins_installed = _load_json_safe(plugins_file)
        _scan_plugins(plugins_dir, scan)

    # MCP OAuth-required cache — tells the target agent which plugins still need auth
    mcp_auth_cache = claude_home / "mcp-needs-auth-cache.json"
    if mcp_auth_cache.exists():
        scan.mcp_needs_auth = _load_json_safe(mcp_auth_cache)

    # Environment reproduction (shell snapshots, session-env, file-history)
    if include_env_reproduction:
        _scan_env_reproduction(claude_home, scan, max_session_body_mb)

    # Scheduled tasks — ~/.claude/scheduled-tasks/<name>/SKILL.md
    sched_dir = claude_home / "scheduled-tasks"
    if sched_dir.is_dir():
        for sub in sched_dir.iterdir():
            if sub.is_dir():
                skill_md = sub / "SKILL.md"
                if skill_md.exists():
                    text = _read_safe(skill_md) or ""
                    meta, body = _parse_frontmatter(text)
                    scan.scheduled_tasks.append({
                        "name": sub.name,
                        "path": str(skill_md),
                        "frontmatter": meta,
                        "body": body,
                    })

    # history.jsonl — full prompt history so migrated agent can seed command history
    hist = claude_home / "history.jsonl"
    if hist.exists():
        cap_bytes = max_session_body_mb * 1024 * 1024
        if hist.stat().st_size <= cap_bytes:
            with hist.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    scan.history_count += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        scan.history.append(json.loads(line))
                    except Exception:
                        scan.history.append({"raw": line})
        else:
            scan.history_count = sum(1 for _ in hist.open(encoding="utf-8", errors="replace"))

    # Global skills
    global_skills_dir = claude_home / "skills"
    if global_skills_dir.is_dir():
        for sub in global_skills_dir.iterdir():
            if sub.is_dir():
                skill = _scan_skill_dir(sub, sub.name)
                if skill:
                    scan.skills_global.append(skill)

    # Global custom slash commands (~/.claude/commands/**/*.md)
    scan.commands_global = _scan_commands_dir(claude_home / "commands")

    # Themes (~/.claude/themes/)
    scan.themes = _scan_themes_dir(claude_home / "themes")

    # Keybindings (~/.claude/keybindings.json)
    kb_file = claude_home / "keybindings.json"
    if kb_file.is_file():
        loaded = _load_json_safe(kb_file)
        if loaded:
            scan.keybindings = loaded

    # Global agents
    global_agents_dir = claude_home / "agents"
    if global_agents_dir.is_dir():
        for f in global_agents_dir.glob("*.md"):
            text = _read_safe(f) or ""
            meta, body = _parse_frontmatter(text)
            scan.agents.append(AgentDef(
                name=str(meta.get("name", f.stem)),
                path=str(f),
                description=str(meta.get("description", "")),
                model=meta.get("model"),
                color=meta.get("color"),
                instructions=body,
            ))

    # Global rules + output-styles + agent-memory
    scan.rules.extend(_scan_memory_dir(claude_home / "rules"))
    scan.output_styles.extend(_scan_memory_dir(claude_home / "output-styles"))
    if include_agent_memory:
        agent_mem_dir = claude_home / "agent-memory"
        if agent_mem_dir.is_dir():
            for sub in agent_mem_dir.iterdir():
                if sub.is_dir():
                    for mf in _scan_memory_dir(sub):
                        mf.file = f"{sub.name}/{mf.file}"
                        scan.agent_memory.append(mf)

    # Project-specific data
    if proj:
        # CLAUDE.md variants — per 2026 spec, both ./CLAUDE.md AND
        # ./.claude/CLAUDE.md are valid project locations
        scan.claude_md = _read_safe(proj / "CLAUDE.md")
        scan.claude_local_md = _read_safe(proj / "CLAUDE.local.md")
        scan.project_claude_md_dotclaude = _read_safe(proj / ".claude" / "CLAUDE.md")
        scan.review_md = _read_safe(proj / "REVIEW.md")

        # Ancestor walk: Claude Code loads CLAUDE.md from every parent dir
        scan.ancestor_claude_mds = _walk_ancestor_claude_mds(proj)
        # Subdirectory CLAUDE.md (lazy-loaded, but archival-worthy)
        scan.subdir_claude_mds = _walk_subdir_claude_mds(proj)

        # @import expansion — recursive, depth ≤ 5
        seed: list[tuple[Path, str]] = []
        if scan.claude_md:
            seed.append((proj / "CLAUDE.md", scan.claude_md))
        if scan.claude_local_md:
            seed.append((proj / "CLAUDE.local.md", scan.claude_local_md))
        if scan.project_claude_md_dotclaude:
            seed.append((proj / ".claude" / "CLAUDE.md", scan.project_claude_md_dotclaude))
        for mf in scan.ancestor_claude_mds:
            seed.append((Path(mf.path), mf.content))
        if seed:
            scan.claude_md_imports = _expand_claude_md_imports(seed)

        # .worktreeinclude
        wt = proj / ".worktreeinclude"
        if wt.exists():
            scan.worktreeinclude = [ln.strip() for ln in wt.read_text().splitlines() if ln.strip()]

        # Project settings
        scan.settings_project = _load_json_safe(proj / ".claude" / "settings.json")
        scan.settings_project_local = _load_json_safe(proj / ".claude" / "settings.local.json")
        scan.hooks = scan.settings_project.get("hooks") or {}
        scan.launch_json = _load_json_safe(proj / ".claude" / "launch.json") or None
        scan.loop_md_project = _read_safe(proj / ".claude" / "loop.md")

        # .mcp.json
        proj_mcp = proj / ".mcp.json"
        if proj_mcp.exists():
            d = _load_json_safe(proj_mcp)
            for name, cfg in (d.get("mcpServers") or {}).items():
                if isinstance(cfg, dict):
                    scan.mcp_servers_project[name] = _parse_mcp_server(name, cfg)

        # Project skills (.claude/skills, .agents/skills, .cursor/skills)
        for skill_root in [proj / ".claude" / "skills", proj / ".agents" / "skills", proj / ".cursor" / "skills"]:
            if skill_root.is_dir():
                for sub in skill_root.iterdir():
                    if sub.is_dir():
                        skill = _scan_skill_dir(sub, sub.name)
                        if skill:
                            scan.skills_project.append(skill)

        # Project custom slash commands (<proj>/.claude/commands/**/*.md)
        scan.commands_project = _scan_commands_dir(proj / ".claude" / "commands")

        # Project agents
        proj_agents = proj / ".claude" / "agents"
        if proj_agents.is_dir():
            for f in proj_agents.glob("*.md"):
                text = _read_safe(f) or ""
                meta, body = _parse_frontmatter(text)
                scan.agents.append(AgentDef(
                    name=str(meta.get("name", f.stem)),
                    path=str(f),
                    description=str(meta.get("description", "")),
                    model=meta.get("model"),
                    color=meta.get("color"),
                    instructions=body,
                ))

        # Project rules + output-styles + agent-memory
        scan.rules.extend(_scan_memory_dir(proj / ".claude" / "rules"))
        scan.output_styles.extend(_scan_memory_dir(proj / ".claude" / "output-styles"))
        if include_agent_memory:
            for sub_root in (proj / ".claude" / "agent-memory", proj / ".claude" / "agent-memory-local"):
                if sub_root.is_dir():
                    for sub in sub_root.iterdir():
                        if sub.is_dir():
                            for mf in _scan_memory_dir(sub):
                                mf.file = f"{sub.name}/{mf.file}"
                                scan.agent_memory.append(mf)

        # Auto-memory (CoWork-ish local memory)
        am_dir = proj / ".auto-memory"
        if am_dir.is_dir():
            for mf in _scan_memory_dir(am_dir):
                scan.agent_memory.append(mf)

        # Global projects/{encoded}/memory → scan.memory (the real project memory)
        # Resolve autoMemoryDirectory override first
        auto_mem_dir = scan.settings_global.get("autoMemoryDirectory") or \
                       scan.settings_project.get("autoMemoryDirectory")
        if auto_mem_dir:
            mem_root = Path(auto_mem_dir).expanduser()
        else:
            encoded = _encoded_project_key(proj)
            mem_root = claude_home / "projects" / encoded / "memory"
        if mem_root.is_dir():
            scan.memory.extend(_scan_memory_dir(mem_root))

        # Sessions (JSONL files) — body + subagents + tool-results when requested
        if include_sessions:
            encoded = _encoded_project_key(proj)
            proj_global_dir = claude_home / "projects" / encoded
            if proj_global_dir.is_dir():
                cap_bytes = max_session_body_mb * 1024 * 1024
                for jsonl in proj_global_dir.glob("*.jsonl"):
                    sess = _scan_session(jsonl, proj_global_dir,
                                        include_bodies=include_session_bodies,
                                        cap_bytes=cap_bytes)
                    scan.sessions.append(sess)

    return scan


def _scan_plugins(plugins_dir: Path, scan: ClaudeScan) -> None:
    """Walk ~/.claude/plugins/ and populate scan.plugins / scan.marketplaces /
    scan.plugins_skills. Each installed plugin may bundle its own MCP servers
    and skills — these are the "Cowork plugin system" data that's otherwise
    lost if you only look at installed_plugins.json.
    """
    # known_marketplaces.json
    km_file = plugins_dir / "known_marketplaces.json"
    km = _load_json_safe(km_file) if km_file.exists() else {}
    for mp_name, mp_cfg in km.items():
        if not isinstance(mp_cfg, dict):
            continue
        src = mp_cfg.get("source") or {}
        mp = Marketplace(
            name=mp_name,
            source_type=str(src.get("source", "unknown")),
            source_spec={k: v for k, v in src.items() if k != "source"},
            install_location=str(mp_cfg.get("installLocation", "")),
            last_updated=str(mp_cfg.get("lastUpdated", "")),
        )
        # Load marketplace.json if installed
        mp_manifest = Path(mp.install_location) / ".claude-plugin" / "marketplace.json"
        if mp_manifest.exists():
            mp.manifest = _load_json_safe(mp_manifest)
        scan.marketplaces.append(mp)

    # installed_plugins.json → PluginInstall records
    installed = scan.plugins_installed or {}
    for plugin_id, installs in (installed.get("plugins") or {}).items():
        if not isinstance(installs, list):
            continue
        # plugin_id is "<name>@<marketplace>"
        if "@" in plugin_id:
            plugin_name, marketplace = plugin_id.split("@", 1)
        else:
            plugin_name, marketplace = plugin_id, "unknown"
        for inst in installs:
            if not isinstance(inst, dict):
                continue
            install_path = Path(inst.get("installPath", ""))
            p = PluginInstall(
                id=plugin_id,
                plugin_name=plugin_name,
                marketplace=marketplace,
                version=str(inst.get("version", "unknown")),
                install_path=str(install_path),
                scope=str(inst.get("scope", "user")),
                installed_at=str(inst.get("installedAt", "")),
                last_updated=str(inst.get("lastUpdated", "")),
                git_commit_sha=inst.get("gitCommitSha"),
            )

            if install_path.is_dir():
                # plugin.json manifest (try .claude-plugin/ first, fall back to root)
                for cand in (
                    install_path / ".claude-plugin" / "plugin.json",
                    install_path / ".cursor-plugin" / "plugin.json",
                    install_path / "plugin.json",
                ):
                    if cand.exists():
                        p.manifest = _load_json_safe(cand)
                        break

                # Plugin-bundled MCP servers (from .mcp.json)
                mcp_file = install_path / ".mcp.json"
                if mcp_file.exists():
                    d = _load_json_safe(mcp_file)
                    for name, cfg in (d.get("mcpServers") or {}).items():
                        if isinstance(cfg, dict):
                            p.mcp_servers[name] = _parse_mcp_server(name, cfg)

                # Plugin-bundled skills
                skills_dir = install_path / "skills"
                if skills_dir.is_dir():
                    for sub in skills_dir.iterdir():
                        if sub.is_dir():
                            s = _scan_skill_dir(sub, f"{plugin_name}:{sub.name}")
                            if s:
                                p.skill_names.append(s.name)
                                scan.plugins_skills.append(s)

                # Plugin-bundled bin/ executables (W14: PATH-injected while plugin enabled)
                bin_dir = install_path / "bin"
                if bin_dir.is_dir():
                    for f in bin_dir.iterdir():
                        if f.is_file() and not f.name.startswith("."):
                            p.bin_files.append(str(f.relative_to(install_path)))

                # Plugin-bundled custom slash commands
                cmds = _scan_commands_dir(install_path / "commands", prefix=plugin_name)
                for c in cmds:
                    p.command_names.append(c.name)
                    scan.plugins_commands.append(c)

                # Plugin-bundled custom subagents
                pa_dir = install_path / "agents"
                if pa_dir.is_dir():
                    for f in pa_dir.glob("*.md"):
                        text = _read_safe(f) or ""
                        meta, body = _parse_frontmatter(text)
                        agent_name = f"{plugin_name}:{meta.get('name', f.stem)}"
                        scan.agents.append(AgentDef(
                            name=agent_name,
                            path=str(f),
                            description=str(meta.get("description", "")),
                            model=meta.get("model"),
                            color=meta.get("color"),
                            instructions=body,
                        ))
                        p.agent_names.append(agent_name)

            scan.plugins.append(p)


def _scan_session(jsonl: Path, proj_global_dir: Path,
                  include_bodies: bool, cap_bytes: int) -> Session:
    """Parse one session: JSONL body, sidecar subagents/ and tool-results/."""
    size = jsonl.stat().st_size
    line_count = 0
    messages: list[dict[str, Any]] = []
    if include_bodies and size <= cap_bytes:
        with jsonl.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line_count += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except Exception:
                    messages.append({"raw": line})
    else:
        line_count = sum(1 for _ in jsonl.open(encoding="utf-8", errors="replace"))

    sess = Session(
        uuid=jsonl.stem,
        path=str(jsonl),
        size_bytes=size,
        line_count=line_count,
        messages=messages,
    )

    if include_bodies:
        # Sidecar dir: projects/<enc>/<session-uuid>/ {subagents,tool-results}
        side = proj_global_dir / jsonl.stem
        if side.is_dir():
            subagents_dir = side / "subagents"
            if subagents_dir.is_dir():
                for meta_file in sorted(subagents_dir.glob("*.meta.json")):
                    agent_id = meta_file.name[:-len(".meta.json")]
                    jsonl_file = subagents_dir / f"{agent_id}.jsonl"
                    meta = _load_json_safe(meta_file)
                    agent_msgs: list[dict[str, Any]] = []
                    if jsonl_file.exists() and jsonl_file.stat().st_size <= cap_bytes:
                        with jsonl_file.open(encoding="utf-8", errors="replace") as fh:
                            for line in fh:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    agent_msgs.append(json.loads(line))
                                except Exception:
                                    agent_msgs.append({"raw": line})
                    sess.subagents.append({
                        "id": agent_id,
                        "meta": meta,
                        "messages": agent_msgs,
                    })

            tr_dir = side / "tool-results"
            if tr_dir.is_dir():
                for tr in tr_dir.glob("toolu_*.txt"):
                    if tr.stat().st_size > cap_bytes:
                        continue
                    sess.tool_results[tr.stem] = _read_safe(tr) or ""

    return sess


def _scan_env_reproduction(claude_home: Path, scan: ClaudeScan, max_mb: int) -> None:
    """Scan shell-snapshots/, session-env/, file-history/ so the migrated agent
    can reproduce the original Bash-tool environment (PATH, aliases, functions,
    per-session env vars, file undo history)."""
    from datetime import datetime, timezone

    cap_bytes = max_mb * 1024 * 1024

    snap_dir = claude_home / "shell-snapshots"
    if snap_dir.is_dir():
        for f in snap_dir.glob("snapshot-*.sh"):
            st = f.stat()
            content = ""
            if st.st_size <= cap_bytes:
                content = _read_safe(f) or ""
            scan.shell_snapshots.append(ShellSnapshot(
                path=str(f),
                size_bytes=st.st_size,
                mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                      .isoformat().replace("+00:00", "Z"),
                content=content,
            ))

    senv_dir = claude_home / "session-env"
    if senv_dir.is_dir():
        for sub in senv_dir.iterdir():
            if not sub.is_dir():
                continue
            entry = SessionEnv(session_uuid=sub.name, path=str(sub))
            for f in sub.iterdir():
                if f.is_file() and f.stat().st_size <= cap_bytes:
                    entry.files[f.name] = _read_safe(f) or ""
            scan.session_envs.append(entry)

    fh_dir = claude_home / "file-history"
    if fh_dir.is_dir():
        for sub in fh_dir.iterdir():
            if not sub.is_dir():
                continue
            for f in sub.iterdir():
                if not f.is_file():
                    continue
                sz = f.stat().st_size
                scan.file_history.append(FileHistoryEntry(
                    session_uuid=sub.name,
                    file_id=f.name,
                    path=str(f),
                    size_bytes=sz,
                    content=(_read_safe(f) or "") if sz <= cap_bytes else "",
                ))


def save_scan(scan: ClaudeScan, out_path: str | Path) -> None:
    """Serialize scan to JSON with plaintext secrets redacted.

    scan.json can contain MCP Bearer tokens, pasted API keys in history,
    env-var exports in shell snapshots, etc. We scrub them before disk
    write via the `redactor` module and chmod 0o600 the output.
    """
    # Local import avoids a circular dep (secrets.py → scanner.py not used,
    # but redactor.py is independent; keeping it local for symmetry).
    from .redactor import redact, to_manifest

    out_path = Path(out_path)
    redacted_dict, findings = redact(scan.to_dict())
    out_path.write_text(
        json.dumps(redacted_dict, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    try:
        os.chmod(out_path, 0o600)
    except OSError:
        pass
    if findings:
        manifest_path = out_path.parent / (out_path.stem + ".secrets-manifest.json")
        manifest_path.write_text(
            json.dumps(to_manifest(findings), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            os.chmod(manifest_path, 0o600)
        except OSError:
            pass
