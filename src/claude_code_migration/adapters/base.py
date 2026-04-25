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


def safe_slug(s: str, max_len: int = 64) -> str:
    import re
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fa5]+", "-", s or "")
    return slug.strip("-")[:max_len] or "untitled"


def render_cowork_project_markdown(proj: dict[str, Any], include_docs: bool = True) -> str:
    """Render a Claude Project / Cowork Project into self-contained markdown.

    Combines description + prompt_template (custom instructions) + docs
    into one file suitable as AGENTS.md / rule / memory file for any target.
    """
    parts: list[str] = []
    parts.append(f"# {proj.get('name', 'Project')}")
    if proj.get("is_shared"):
        parts.append("> 🌐 Shared Cowork project")
    if proj.get("description"):
        parts.append(f"\n{proj['description'].strip()}")
    pt = (proj.get("prompt_template") or "").strip()
    if pt:
        parts.append("\n## Custom Instructions (prompt_template)\n")
        parts.append(pt)
    docs = proj.get("docs") or []
    if include_docs and docs:
        parts.append(f"\n## Knowledge Base ({len(docs)} docs)\n")
        for d in docs:
            fname = d.get("filename") or "untitled"
            content = (d.get("content") or "").strip()
            if content:
                parts.append(f"### {fname}\n\n{content}")
            else:
                parts.append(f"### {fname}\n\n_(empty or binary)_")
    return "\n\n".join(parts) + "\n"


def write_archive(
    out_dir: Path,
    scan: dict[str, Any],
    cowork_export: dict[str, Any] | None = None,
    unmigratable_notes: list[str] | None = None,
) -> list[str]:
    """Write an `_archive/` containing raw artifacts that couldn't be mapped
    to target-native files — so users never lose data even for features a
    target doesn't support.

    Contents:
    - raw-cowork-export.json: full parsed cowork ZIP (if provided)
    - plugin-inventory.json: installed plugins + marketplaces
    - org-metadata.json: Cowork org info
    - secrets-manifest.json: SHA256 hashes of detected secrets (no values)
    - MIGRATION_NOTES.md: what was/wasn't migrated + recovery steps
    """
    import json
    from datetime import datetime, timezone

    archive = ensure_dir(out_dir / "_archive")
    written: list[str] = []

    # Raw cowork export
    if cowork_export:
        p = archive / "raw-cowork-export.json"
        p.write_text(json.dumps(cowork_export, indent=2, ensure_ascii=False, default=str),
                     encoding="utf-8")
        written.append(str(p))

    # Plugin inventory
    plugins_manifest = {
        "plugins": scan.get("plugins") or [],
        "marketplaces": scan.get("marketplaces") or [],
        "plugins_installed_raw": scan.get("plugins_installed"),
    }
    p = archive / "plugin-inventory.json"
    p.write_text(json.dumps(plugins_manifest, indent=2, ensure_ascii=False, default=str),
                 encoding="utf-8")
    written.append(str(p))

    # Org metadata
    if scan.get("org"):
        p = archive / "org-metadata.json"
        p.write_text(json.dumps(scan["org"], indent=2, ensure_ascii=False, default=str),
                     encoding="utf-8")
        written.append(str(p))

    # CLAUDE.md discovery tree — the 2026 spec loads CLAUDE.md from 5 different
    # locations (alt project dir, ancestors, subdirs, @imports, managed policy)
    # and the load order depends on cwd at runtime. We archive each bucket
    # verbatim under _archive/claude-md-tree/ so the user can audit which files
    # were picked up and re-map manually if the target agent has a different
    # discovery rule.
    tree = scan.get("claude_md_tree") or {}
    if tree:
        tree_dir = ensure_dir(archive / "claude-md-tree")
        tree_index: list[str] = [
            "# CLAUDE.md Discovery Tree",
            "",
            "Claude Code (per 2026 spec) loads CLAUDE.md files from multiple",
            "locations. Each bucket below was captured at export time; the load",
            "order depends on runtime cwd so we don't flatten them.",
            "",
        ]
        if tree.get("project_dotclaude"):
            p = tree_dir / "project_dotclaude.md"
            p.write_text(tree["project_dotclaude"], encoding="utf-8")
            written.append(str(p))
            tree_index.append("- `project_dotclaude.md` — content of `./.claude/CLAUDE.md`")
        for i, m in enumerate(tree.get("ancestors") or []):
            p = tree_dir / f"ancestor_{i:02d}_{Path(m['path']).parent.name or 'root'}.md"
            p.write_text(m.get("content", ""), encoding="utf-8")
            written.append(str(p))
            tree_index.append(f"- `{p.name}` — from `{m['path']}`")
        for m in tree.get("subdirs") or []:
            rel = m["file"].replace("subdir:", "")
            safe = rel.replace("/", "__")
            p = tree_dir / f"subdir_{safe}"
            p.write_text(m.get("content", ""), encoding="utf-8")
            written.append(str(p))
            tree_index.append(f"- `{p.name}` — lazy-loaded from `{rel}`")
        for m in tree.get("imports") or []:
            tok = m["file"].replace("@import:", "")
            safe = tok.replace("/", "__").lstrip(".~")
            p = tree_dir / f"import_{safe}"
            if not p.suffix:
                p = p.with_suffix(".md")
            p.write_text(m.get("content", ""), encoding="utf-8")
            written.append(str(p))
            tree_index.append(f"- `{p.name}` — via `@{tok}`")
        if tree.get("managed_policy"):
            mp = tree["managed_policy"]
            p = tree_dir / "managed_policy.md"
            p.write_text(mp.get("content", ""), encoding="utf-8")
            written.append(str(p))
            tree_index.append(f"- `managed_policy.md` — enterprise policy from `{mp.get('path')}`")
        idx = tree_dir / "INDEX.md"
        idx.write_text("\n".join(tree_index) + "\n", encoding="utf-8")
        written.append(str(idx))

    # Custom slash commands, themes, keybindings, plugin bin/ — Claude Code
    # surface area without 1:1 equivalents in most targets. Archive verbatim
    # so the user can hand-port the ones they actually use.
    extras = scan.get("claude_extras") or {}
    if extras:
        ex_dir = ensure_dir(archive / "claude-extras")
        ex_index: list[str] = [
            "# Claude Code Extras (2026 spec)",
            "",
            "Surface area beyond the canonical IR — typically with no direct",
            "equivalent in your target agent. Each bucket below is preserved",
            "verbatim so you can re-create it manually if needed.",
            "",
        ]
        # Slash commands — write each as a standalone .md so the user can
        # drop it into their target's command system if it has one.
        cmd_buckets = [
            ("commands_global", "global custom slash command"),
            ("commands_project", "project custom slash command"),
            ("plugins_commands", "plugin-bundled slash command"),
        ]
        for key, label in cmd_buckets:
            commands = extras.get(key) or []
            if not commands:
                continue
            sub = ensure_dir(ex_dir / key)
            ex_index.append(f"## {key} ({len(commands)} {label}{'s' if len(commands)!=1 else ''})")
            ex_index.append("")
            for c in commands:
                safe = c["name"].replace(":", "__").replace("/", "__")
                p = sub / f"{safe}.md"
                # Reconstruct frontmatter + body so command is self-contained
                fm_lines = []
                if c.get("frontmatter"):
                    fm_lines.append("---")
                    for k, v in c["frontmatter"].items():
                        fm_lines.append(f"{k}: {v}")
                    fm_lines.append("---")
                    fm_lines.append("")
                p.write_text("\n".join(fm_lines) + (c.get("body") or ""), encoding="utf-8")
                written.append(str(p))
                ex_index.append(f"- `{key}/{p.name}` — `/{c['name']}` (from `{c['path']}`)")
            ex_index.append("")
        # Themes — verbatim copy
        themes = extras.get("themes") or []
        if themes:
            t_sub = ensure_dir(ex_dir / "themes")
            ex_index.append(f"## themes ({len(themes)} files)")
            ex_index.append("")
            for t in themes:
                safe = t["file"].replace("/", "__")
                p = t_sub / safe
                p.write_text(t.get("content", ""), encoding="utf-8")
                written.append(str(p))
                ex_index.append(f"- `themes/{safe}` — from `{t['path']}`")
            ex_index.append("")
        # Keybindings — single JSON
        kb = extras.get("keybindings")
        if kb:
            p = ex_dir / "keybindings.json"
            p.write_text(json.dumps(kb, indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(str(p))
            ex_index.append("## keybindings")
            ex_index.append("")
            ex_index.append("- `keybindings.json` — full ~/.claude/keybindings.json (target-specific; "
                            "most agents have their own keybinding system)")
            ex_index.append("")
        # Plugins with bin/ — note the existence; files live under the
        # original install_path which may or may not be on the migrated
        # machine.
        bin_plugins = extras.get("plugins_with_bin") or []
        if bin_plugins:
            ex_index.append("## plugins with bin/ (W14 feature)")
            ex_index.append("")
            ex_index.append("These plugins ship PATH-injected executables. The Bash tool")
            ex_index.append("called them as bare commands; in your target agent you'll need")
            ex_index.append("to either install the plugin natively or copy the binaries onto")
            ex_index.append("your PATH manually.")
            ex_index.append("")
            for bp in bin_plugins:
                ex_index.append(f"- **{bp['id']}** — `bin/`: {', '.join(bp['bin_files'])}  "
                                f"(install path: `{bp['install_path']}`)")
            ex_index.append("")
        idx_p = ex_dir / "INDEX.md"
        idx_p.write_text("\n".join(ex_index) + "\n", encoding="utf-8")
        written.append(str(idx_p))

    # Secret manifest (SHA256 hashes only, no raw values — safe to commit)
    try:
        from ..secrets import scan_secrets
        findings = scan_secrets(scan)
        manifest = [
            {
                "source": f.source,
                "kind": f.kind,
                "sha256_prefix": f.sha256_prefix,
                "suggested_env_var": f.suggested_env_var,
            }
            for f in findings
        ]
        p = archive / "secrets-manifest.json"
        p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        written.append(str(p))
    except (OSError, KeyError, TypeError) as e:
        import sys
        print(f"⚠️  adapter: failed writing secrets-manifest.json — {e}", file=sys.stderr)

    # MIGRATION_NOTES.md
    notes_lines = [
        "# Migration Notes",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat().replace('+00:00','Z')} by claude-code-migration",
        "",
        "This `_archive/` holds raw artifacts that couldn't be losslessly",
        "mapped to your target framework's native format. Keep it alongside",
        "your target output.",
        "",
        "## What's in here",
        "",
        "- `raw-cowork-export.json` — full parsed Cowork/Chat ZIP contents",
        "- `plugin-inventory.json` — installed plugins + marketplace sources",
        "- `org-metadata.json` — Cowork organization info",
        "- `secrets-manifest.json` — SHA256 hashes of detected secrets",
        "  (NO plaintext values; safe to commit)",
        "",
        "## What CAN'T be migrated automatically",
        "",
        "These Cowork features have no equivalent in any target framework",
        "or require manual setup:",
        "",
        "- **Custom styles** (concise / explanatory / formal) — set in Claude.ai",
        "  Settings → Appearance → Style. Not exported by Anthropic.",
        "- **Connected apps** (Google Drive, Gmail, Slack, etc.) — each target",
        "  needs its own OAuth setup. In OpenCode use `opencode auth login`;",
        "  in Hermes use environment variables or vault.",
        "- **Cowork activity audit trail** — only accessible via your organization's",
        "  OpenTelemetry (OTel) collector (see",
        "  https://support.claude.com/en/articles/14477985).",
        "- **Cursor User Rules** — Cursor stores these in a settings DB, not files.",
        "  See `.migration/cursor-user-rules.md` for paste-ready content.",
        "- **Artifact version history** — Anthropic exports only the final content",
        "  of each artifact; prior revisions are lost.",
        "",
        "## What WAS migrated",
        "",
    ]

    if cowork_export:
        ce = cowork_export
        notes_lines.append(
            f"- **Cowork source**: {ce.get('source', 'chat')}, "
            f"{len(ce.get('conversations') or [])} conversations, "
            f"{len(ce.get('projects') or [])} projects, "
            f"{len(ce.get('workspace_ids') or [])} workspaces"
        )
    plugs = scan.get("plugins") or []
    if plugs:
        notes_lines.append(f"- **Plugins**: {len(plugs)} installed, see plugin-inventory.json")
    if scan.get("org"):
        notes_lines.append(f"- **Org**: {scan['org'].get('organization_name', '?')} "
                           f"(role={scan['org'].get('organization_role', '?')})")

    if unmigratable_notes:
        notes_lines.append("")
        notes_lines.append("## Target-specific notes")
        notes_lines.append("")
        for n in unmigratable_notes:
            notes_lines.append(f"- {n}")

    p = archive / "MIGRATION_NOTES.md"
    p.write_text("\n".join(notes_lines) + "\n", encoding="utf-8")
    written.append(str(p))

    return written


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

    # Cowork org metadata
    org = scan.get("org") or {}
    if org and (org.get("organization_name") or org.get("organization_role")):
        org_lines = []
        if org.get("organization_name"):
            org_lines.append(f"- **Organization**: {org['organization_name']}")
        if org.get("organization_role"):
            org_lines.append(f"- **Role**: {org['organization_role']}")
        if org.get("workspace_role") and org["workspace_role"] != "None":
            org_lines.append(f"- **Workspace role**: {org['workspace_role']}")
        if org.get("billing_type"):
            org_lines.append(f"- **Billing**: {org['billing_type']}")
        if org_lines:
            parts.append("## Cowork Organization\n\n" + "\n".join(org_lines))

    # Installed plugins (Cowork plugin system) — each plugin may bundle MCP + skills
    plugins = scan.get("plugins") or []
    if plugins:
        plug_lines = []
        for p in plugins:
            pid = p.get("id") or p.get("plugin_name")
            ver = p.get("version") or ""
            n_mcp = len(p.get("mcp_servers") or {})
            n_skills = len(p.get("skill_names") or [])
            bits = [f"v{ver}"] if ver and ver != "unknown" else []
            if n_mcp: bits.append(f"{n_mcp} MCP")
            if n_skills: bits.append(f"{n_skills} skills")
            suffix = f" ({', '.join(bits)})" if bits else ""
            plug_lines.append(f"- **{pid}**{suffix}")
        parts.append("## Installed Plugins (Cowork)\n\n" + "\n".join(plug_lines))

    # Last resort: if we still have nothing substantial, add project metadata
    body = "\n\n".join(p for p in parts if p.strip())
    if len(body) < 200 and scan.get("project_dir"):
        parts.append(f"## Project Location\n\nMigrated from `{scan['project_dir']}` "
                     f"via claude-code-migration. No CLAUDE.md was present; this file was "
                     f"synthesized from available Claude Code signals.")
        body = "\n\n".join(p for p in parts if p.strip())

    return body + "\n"
