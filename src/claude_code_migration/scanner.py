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
    plugins_installed: dict[str, Any] | None = None
    history_count: int = 0
    worktreeinclude: list[str] = field(default_factory=list)

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
) -> ClaudeScan:
    """Scan Claude Code data on disk.

    Args:
        project_dir: Optional project to focus on. If None, scans globally.
        include_sessions: Whether to enumerate JSONL session files.
        include_agent_memory: Whether to scan agent-memory dirs.
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

    # ~/.claude/CLAUDE.md
    scan.home_claude_md = _read_safe(claude_home / "CLAUDE.md")

    # Global settings
    scan.settings_global = _load_json_safe(claude_home / "settings.json")
    scan.settings_local = _load_json_safe(claude_home / "settings.local.json")

    # Global loop.md
    scan.loop_md_global = _read_safe(claude_home / "loop.md")

    # Global plans / todos (counts only — can be large)
    plans_dir = claude_home / "plans"
    if plans_dir.is_dir():
        for f in plans_dir.glob("*.md"):
            scan.plans.append({"name": f.name, "path": str(f), "size": str(f.stat().st_size)})
    todos_dir = claude_home / "todos"
    if todos_dir.is_dir():
        for f in todos_dir.glob("*.json"):
            try:
                d = json.loads(f.read_text())
                if d:  # non-empty
                    scan.todos.append({"path": str(f), "count": len(d) if isinstance(d, list) else 1})
            except Exception:
                pass

    # Plugins list
    plugins_file = claude_home / "plugins" / "installed_plugins.json"
    if plugins_file.exists():
        scan.plugins_installed = _load_json_safe(plugins_file)

    # history.jsonl (count only)
    hist = claude_home / "history.jsonl"
    if hist.exists():
        scan.history_count = sum(1 for _ in hist.open(encoding="utf-8", errors="replace"))

    # Global skills
    global_skills_dir = claude_home / "skills"
    if global_skills_dir.is_dir():
        for sub in global_skills_dir.iterdir():
            if sub.is_dir():
                skill = _scan_skill_dir(sub, sub.name)
                if skill:
                    scan.skills_global.append(skill)

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
        # CLAUDE.md variants
        scan.claude_md = _read_safe(proj / "CLAUDE.md")
        scan.claude_local_md = _read_safe(proj / "CLAUDE.local.md")
        scan.review_md = _read_safe(proj / "REVIEW.md")

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

        # Sessions (JSONL files)
        if include_sessions:
            encoded = _encoded_project_key(proj)
            proj_global_dir = claude_home / "projects" / encoded
            if proj_global_dir.is_dir():
                for jsonl in proj_global_dir.glob("*.jsonl"):
                    line_count = sum(1 for _ in jsonl.open(encoding="utf-8", errors="replace"))
                    scan.sessions.append(Session(
                        uuid=jsonl.stem,
                        path=str(jsonl),
                        size_bytes=jsonl.stat().st_size,
                        line_count=line_count,
                    ))

    return scan


def save_scan(scan: ClaudeScan, out_path: str | Path) -> None:
    """Serialize scan to JSON."""
    Path(out_path).write_text(
        json.dumps(scan.to_dict(), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
