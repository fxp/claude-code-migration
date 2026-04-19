"""Panic Backup — one-command emergency capture of everything a Claude account
ban would otherwise destroy.

Why this exists separate from `ccm export`:

    ccm export       → Workspace Dossier aimed at migrating to another agent.
                       Redacted, vendor-neutral, designed to be useful.
    ccm panic-backup → Defensive dump. Grab everything NOW before Anthropic
                       decides to shadow-ban your account. Includes OAuth
                       tokens, plugin state, and raw files that `ccm export`
                       deliberately strips for safety.

The output follows neuDrive's canonical path layout
(/identity, /memory, /projects, /skills, /conversations, /roles) so the same
archive can also be `neu sync import`ed to a neuDrive Hub.

What's included (categorized by loss severity if you get banned):

  Tier 1 — cloud-only, unrecoverable after ban:
    • claude.ai chat / projects / artifacts via ZIP (requires user to have
      triggered Settings → Privacy → Export first; see RESTORE.md)
    • NOT captured live because Anthropic's chat-history API isn't public
      and screen-scraping claude.ai requires a browser session this tool
      can't assume. We surface the manual step in RESTORE.md instead.

  Tier 2 — local but tied to Anthropic OAuth (MCPs, plugins stop working):
    • ~/.claude.json oauthAccount block (access_token, refresh_token, org
      memberships) — WITHOUT the redactor, chmod 0o600
    • ~/.claude/plugins/ full tree — plugin install state with OAuth tokens
    • MCP Bearer tokens in headers — WITHOUT the redactor (this is a backup,
      not a share)
    • ~/.claude/mcp-needs-auth-cache.json — which MCPs will need re-auth

  Tier 3 — fully local (scanner already covers these):
    • Memory, skills, agents, hooks, rules, scheduled tasks, session JSONL
      bodies + subagents + tool-results, shell snapshots, file history,
      history.jsonl, plans, todos — everything scan_claude_code() produces.

The archive is a `.tar.gz` at user-only permissions (0o600). A RESTORE.md
is written inside explaining how to recover on a new machine / new account.
"""
from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cowork import parse_cowork_zip
from .scanner import scan_claude_code


# ── Bundle layout (mirrors neuDrive canonical paths) ──────────────────

_IDENTITY     = "identity/profile.json"
_PROFILE_DIR  = "memory/profile"
_SCRATCH_DIR  = "memory/scratch"
_PROJECTS_DIR = "projects"
_SKILLS_DIR   = "skills"
_CONVOS_DIR   = "conversations"
_ROLES_DIR    = "roles"                 # Claude Code custom agents
_VAULT_DIR    = "credentials"           # Tier-2 secrets, DO NOT COMMIT
_EXTRAS_DIR   = "claude-code-extras"    # Tier-3 raw files (shell snapshots etc.)


@dataclass
class PanicBackupResult:
    """Receipt: what was captured, where it landed."""
    archive_path: Path
    size_bytes: int
    files_written: int
    tier1_sources: list[str] = field(default_factory=list)
    tier2_secrets_included: bool = False
    tier3_local_types: int = 0
    warnings: list[str] = field(default_factory=list)


# ── Public API ────────────────────────────────────────────────────────


def panic_backup(
    out_path: Path | str,
    project_dir: Path | str | None = None,
    include_credentials: bool = True,
    cowork_zip: Path | str | None = None,
) -> PanicBackupResult:
    """Capture everything a Claude ban would otherwise destroy.

    Args:
        out_path: Where to write the `.tar.gz`. Parent dirs are created.
            File ends up 0o600 (user-only) because it can contain OAuth tokens.
        project_dir: Optional project to include as a `/projects/<name>/`
            entry. If None, no project-specific capture — just ~/.claude/.
        include_credentials: If True (default), include Tier-2 secrets
            (OAuth tokens, plugin install state, MCP Bearer tokens) WITHOUT
            redacting. Pass False for a "safe to share" archive.
        cowork_zip: Optional path to a Claude.ai / Cowork official export
            ZIP. If present, conversations are unpacked into
            `/conversations/claude-chat/<uuid>/conversation.md`.

    Returns:
        PanicBackupResult describing what landed in the archive.
    """
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result = PanicBackupResult(
        archive_path=out_path,
        size_bytes=0,
        files_written=0,
    )

    # Stage everything in a temp dir, then tar+gzip at the end.
    with tempfile.TemporaryDirectory(prefix="ccm-panic-") as staging_str:
        staging = Path(staging_str)

        # Tier 3 — full scan via the existing scanner
        scan = scan_claude_code(
            project_dir=project_dir,
            include_sessions=True,
            include_env_reproduction=True,
            max_session_body_mb=256,  # panic-backup is allowed to be big
        )
        scan_d = scan.to_dict()
        result.tier3_local_types = _count_nonempty(scan_d)
        _stage_tier3(staging, scan_d, result)

        # Tier 2 — credentials (OAuth + plugin state + raw MCP tokens)
        if include_credentials:
            _stage_tier2(staging, result)
            result.tier2_secrets_included = True
        else:
            result.warnings.append(
                "include_credentials=False; OAuth tokens NOT included. "
                "Archive is safe to share but useless for restoring MCP / Claude Code auth."
            )

        # Tier 1 — Claude.ai cloud export, if user provided a ZIP
        if cowork_zip:
            _stage_tier1_from_zip(staging, Path(cowork_zip), result)
        else:
            result.warnings.append(
                "No --cowork-zip passed; claude.ai cloud chat/projects NOT in archive. "
                "Trigger Settings → Privacy → Export data on claude.ai and re-run with --cowork-zip."
            )

        # Manifest + restore guide
        _write_manifest(staging, scan_d, result)
        _write_restore_md(staging, result)

        # Pack
        result.files_written = sum(1 for _ in staging.rglob("*") if _.is_file())
        with tarfile.open(out_path, "w:gz") as tar:
            for f in sorted(staging.rglob("*")):
                if f.is_file():
                    tar.add(f, arcname=str(f.relative_to(staging)))

    try:
        os.chmod(out_path, 0o600)
    except OSError:
        result.warnings.append(
            f"could not chmod 0o600 on {out_path}; set it manually before sharing a working directory."
        )
    result.size_bytes = out_path.stat().st_size
    return result


# ── Stage 1: Tier-3 local data (from scanner) ─────────────────────────


def _stage_tier3(staging: Path, scan_d: dict[str, Any], result: PanicBackupResult) -> None:
    """Map scanner output into neuDrive canonical paths."""

    # /identity/profile.json
    org = scan_d.get("org")
    if org:
        identity = {
            "source": "claude-code",
            "captured_at": scan_d.get("timestamp"),
            "account_uuid": org.get("account_uuid"),
            "organization_uuid": org.get("organization_uuid"),
            "organization_name": org.get("organization_name"),
            "organization_role": org.get("organization_role"),
            "workspace_role": org.get("workspace_role"),
            "billing_type": org.get("billing_type"),
            "email_address": org.get("email_address"),
            "display_name": org.get("display_name"),
            "is_cowork": org.get("organization_role") not in (None, "", "None"),
        }
        _write_json(staging / _IDENTITY, identity)

    # /memory/profile/principles.md ← ~/.claude/CLAUDE.md
    if scan_d.get("home_claude_md"):
        _write_text(
            staging / _PROFILE_DIR / "principles.md",
            scan_d["home_claude_md"],
        )

    # /memory/profile/preferences.md ← user-type memory
    prefs: list[str] = []
    for m in scan_d.get("memory") or []:
        if m.get("type") == "user":
            prefs.append(m.get("content", ""))
    if prefs:
        _write_text(staging / _PROFILE_DIR / "preferences.md", "\n\n".join(prefs))

    # /memory/profile/rules/<name>.md
    for r in scan_d.get("rules") or []:
        name = r.get("file", "rule").replace(".md", "")
        _write_text(
            staging / _PROFILE_DIR / "rules" / f"{_safe_slug(name)}.md",
            r.get("content", ""),
        )

    # /memory/scratch/<date>/<slug>.md ← project + feedback memory + agent memory
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for m in scan_d.get("memory") or []:
        if m.get("type") in ("project", "feedback", "scratch"):
            slug = (m.get("file") or "memory").replace(".md", "")
            _write_text(
                staging / _SCRATCH_DIR / today / f"cc-{_safe_slug(slug)}.md",
                m.get("content", ""),
            )
    for m in scan_d.get("agent_memory") or []:
        slug = (m.get("file") or "agent-memory").replace(".md", "").replace("/", "-")
        _write_text(
            staging / _SCRATCH_DIR / today / f"agent-{_safe_slug(slug)}.md",
            m.get("content", ""),
        )

    # /projects/<name>/
    if scan_d.get("project_dir"):
        pname = _safe_slug(Path(scan_d["project_dir"]).name)
        pdir = staging / _PROJECTS_DIR / pname
        if scan_d.get("claude_md"):
            _write_text(pdir / "context.md", scan_d["claude_md"])
        if scan_d.get("claude_local_md"):
            _write_text(pdir / "context.local.md", scan_d["claude_local_md"])
        if scan_d.get("review_md"):
            _write_text(pdir / "REVIEW.md", scan_d["review_md"])
        # todos → log.jsonl
        log_lines: list[str] = []
        for t in scan_d.get("todos") or []:
            for item in t.get("items") or []:
                log_lines.append(json.dumps({
                    "source": t.get("path"),
                    "item": item,
                }, ensure_ascii=False))
        if log_lines:
            (pdir / "log.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (pdir / "log.jsonl").write_text("\n".join(log_lines), encoding="utf-8")
        # plans
        for p in scan_d.get("plans") or []:
            name = Path(p.get("name", "plan")).stem
            _write_text(pdir / "plans" / f"{_safe_slug(name)}.md", p.get("content", ""))
        # mcp + hooks + settings → project state bundle
        state = {
            "project_state": scan_d.get("project_state") or {},
            "hooks": scan_d.get("hooks") or {},
            "settings_project": scan_d.get("settings_project") or {},
            "mcp_servers_project": scan_d.get("mcp_servers_project") or {},
            "launch_json": scan_d.get("launch_json"),
            "worktreeinclude": scan_d.get("worktreeinclude") or [],
        }
        _write_json(pdir / "claude-code-state.json", state)

    # /skills/<name>/SKILL.md
    for s in (scan_d.get("skills_global") or []) + (scan_d.get("skills_project") or []):
        name = _safe_slug(s.get("name", "skill"))
        sdir = staging / _SKILLS_DIR / name
        _write_text(sdir / "SKILL.md", s.get("body", ""))
        if s.get("frontmatter"):
            _write_json(sdir / "frontmatter.json", s["frontmatter"])

    # /skills/<plugin-ns>:<name>/SKILL.md  (bundled by plugins)
    for s in scan_d.get("plugins_skills") or []:
        name = _safe_slug(s.get("name", "plugin-skill"))
        _write_text(staging / _SKILLS_DIR / name / "SKILL.md", s.get("body", ""))

    # /roles/<name>/SKILL.md ← custom agents
    for a in scan_d.get("agents") or []:
        name = _safe_slug(a.get("name", "agent"))
        rdir = staging / _ROLES_DIR / name
        header = []
        if a.get("description"):
            header.append(f"description: {a['description']}")
        if a.get("model"):
            header.append(f"model: {a['model']}")
        if a.get("color"):
            header.append(f"color: {a['color']}")
        body = ""
        if header:
            body = "---\n" + "\n".join(header) + "\n---\n\n"
        body += a.get("instructions", "")
        _write_text(rdir / "SKILL.md", body)

    # /conversations/claude-code/<uuid>/conversation.{json,md}
    for sess in scan_d.get("sessions") or []:
        uuid = sess.get("uuid", "unknown")
        cdir = staging / _CONVOS_DIR / "claude-code" / uuid
        # Full raw JSONL (one record per message)
        if sess.get("messages"):
            (cdir).mkdir(parents=True, exist_ok=True)
            (cdir / "conversation.json").write_text(
                "\n".join(json.dumps(m, ensure_ascii=False) for m in sess["messages"]),
                encoding="utf-8",
            )
            # Readable markdown transcript (best-effort rendering)
            (cdir / "conversation.md").write_text(
                _render_session_md(sess),
                encoding="utf-8",
            )
        # Sub-agents + tool-results
        for sub in sess.get("subagents") or []:
            sid = sub.get("id", "agent")
            (cdir / "subagents" / sid).mkdir(parents=True, exist_ok=True)
            (cdir / "subagents" / sid / "meta.json").write_text(
                json.dumps(sub.get("meta") or {}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            if sub.get("messages"):
                (cdir / "subagents" / sid / "transcript.jsonl").write_text(
                    "\n".join(json.dumps(m, ensure_ascii=False) for m in sub["messages"]),
                    encoding="utf-8",
                )
        for tid, body in (sess.get("tool_results") or {}).items():
            _write_text(cdir / "tool-results" / f"{tid}.txt", body)

    # /conversations/claude-code/index.json
    sess_idx = [
        {"uuid": s["uuid"], "size_bytes": s["size_bytes"], "line_count": s["line_count"]}
        for s in (scan_d.get("sessions") or [])
    ]
    if sess_idx:
        _write_json(staging / _CONVOS_DIR / "claude-code" / "index.json", sess_idx)

    # /claude-code-extras — raw Tier-3 files that don't fit neuDrive schema
    extras = staging / _EXTRAS_DIR
    # Shell snapshots
    for i, snap in enumerate(scan_d.get("shell_snapshots") or []):
        (extras / "shell-snapshots").mkdir(parents=True, exist_ok=True)
        src_name = Path(snap.get("path", f"snap-{i}")).name
        _write_text(extras / "shell-snapshots" / src_name, snap.get("content", ""))
    # Session env
    for senv in scan_d.get("session_envs") or []:
        sdir = extras / "session-env" / senv.get("session_uuid", "unknown")
        for fname, content in (senv.get("files") or {}).items():
            _write_text(sdir / fname, content)
    # File history
    for fh in scan_d.get("file_history") or []:
        _write_text(
            extras / "file-history" / fh.get("session_uuid", "unknown") / fh.get("file_id", "entry"),
            fh.get("content", ""),
        )
    # History (prompt input log)
    if scan_d.get("history"):
        _write_json(extras / "history.json", scan_d["history"])
    # Full raw scan.json for paranoia
    _write_json(extras / "raw-scan.json", scan_d)


# ── Stage 2: Tier-2 credentials ────────────────────────────────────────


def _stage_tier2(staging: Path, result: PanicBackupResult) -> None:
    """Capture OAuth tokens + plugin state + raw MCP Bearer tokens.

    These live under /credentials/ with a big DANGER README. Nothing here is
    redacted — the whole point is to have the raw material for re-auth.
    """
    vault = staging / _VAULT_DIR

    danger = (
        "# ⚠️  /credentials/ contains plaintext OAuth tokens and Bearer keys.\n\n"
        "Treat this directory exactly like a password manager export:\n"
        "- Never commit it to git.\n"
        "- Never share the archive publicly.\n"
        "- File perms on the parent archive are 0o600; keep them that way.\n"
        "- After restoring on a new machine, shred this directory.\n"
    )
    _write_text(vault / "README.md", danger)

    # ~/.claude.json oauthAccount + whole file (small, high-value)
    home_claude_json = Path.home() / ".claude.json"
    if home_claude_json.exists():
        try:
            raw = json.loads(home_claude_json.read_text(encoding="utf-8"))
            # Pull out high-signal subtrees
            focused = {
                "oauthAccount": raw.get("oauthAccount"),
                "mcpServers": raw.get("mcpServers"),
                "customApiKeyResponses": raw.get("customApiKeyResponses"),
                "userID": raw.get("userID"),
                "githubRepoPaths": raw.get("githubRepoPaths"),
            }
            _write_json(vault / "claude-oauth.json", focused)
            # Full copy too
            _write_json(vault / "dot-claude-json-full.json", raw)
        except Exception as e:
            result.warnings.append(f"~/.claude.json read failed: {e}")

    # ~/.claude/plugins/ — copy whole tree (plugin OAuth state lives here)
    plugins_dir = Path.home() / ".claude" / "plugins"
    if plugins_dir.is_dir():
        dest = vault / "plugins"
        try:
            shutil.copytree(plugins_dir, dest, dirs_exist_ok=True, ignore_dangling_symlinks=True)
        except Exception as e:
            result.warnings.append(f"copy ~/.claude/plugins/ failed: {e}")

    # ~/.claude/mcp-needs-auth-cache.json — tells you which MCPs need re-auth
    auth_cache = Path.home() / ".claude" / "mcp-needs-auth-cache.json"
    if auth_cache.exists():
        _write_text(vault / "mcp-needs-auth-cache.json", auth_cache.read_text())

    # settings.local.json — often has embedded session tokens
    for sname in ("settings.local.json", "settings.json"):
        src = Path.home() / ".claude" / sname
        if src.exists():
            _write_text(vault / sname, src.read_text())


# ── Stage 3: Tier-1 cloud data ────────────────────────────────────────


def _stage_tier1_from_zip(staging: Path, zip_path: Path, result: PanicBackupResult) -> None:
    """Unpack a user-supplied Claude.ai ZIP into neuDrive conversation paths."""
    try:
        export = parse_cowork_zip(zip_path)
    except Exception as e:
        result.warnings.append(f"cowork zip parse failed ({zip_path}): {e}")
        return

    result.tier1_sources.append(f"zip:{zip_path}")
    platform = "claude-cowork" if export.workspace_ids else "claude-chat"
    base = staging / _CONVOS_DIR / platform

    # Raw export as first-class
    _write_json(staging / _EXTRAS_DIR / "claude-ai-export-raw.json", export.to_dict())

    # Each conversation → canonical dir
    for conv in export.conversations:
        key = (conv.uuid or "conv")[:8] or "conv"
        cdir = base / key
        # JSON view
        _write_json(cdir / "conversation.json", {
            "uuid": conv.uuid,
            "name": conv.name,
            "project_uuid": conv.project_uuid,
            "created_at": conv.created_at,
            "model": conv.model,
            "messages": [
                {"uuid": m.uuid, "sender": m.sender, "timestamp": m.timestamp,
                 "text": m.text, "thinking": m.thinking,
                 "attachments": m.attachments}
                for m in conv.messages
            ],
        })
        # Markdown view
        md_lines = [f"# {conv.name or conv.uuid}\n"]
        if conv.created_at:
            md_lines.append(f"_Created: {conv.created_at}_")
        if conv.model:
            md_lines.append(f"_Model: {conv.model}_\n")
        for m in conv.messages:
            md_lines.append(f"\n## {m.sender} — {m.timestamp}\n")
            if m.thinking:
                md_lines.append(f"> [thinking]\n> {m.thinking}\n")
            md_lines.append(m.text or "")
        _write_text(cdir / "conversation.md", "\n".join(md_lines))

    # Projects → /projects/<name>/context.md
    for p in export.projects:
        pname = _safe_slug(p.name or p.uuid)
        pdir = staging / _PROJECTS_DIR / pname
        context_parts = []
        if p.description:
            context_parts.append(f"# {p.name}\n\n{p.description}\n")
        if p.prompt_template:
            context_parts.append(f"## Custom instructions\n\n{p.prompt_template}\n")
        if p.docs:
            context_parts.append("## Documents")
            for doc in p.docs:
                if isinstance(doc, dict):
                    fname = doc.get("filename", "doc")
                    content = doc.get("content", "")
                    context_parts.append(f"### {fname}\n\n{content}\n")
        _write_text(pdir / "context.md", "\n\n".join(context_parts))


# ── Manifest + restore guide ──────────────────────────────────────────


def _write_manifest(staging: Path, scan_d: dict[str, Any], result: PanicBackupResult) -> None:
    manifest = {
        "schema": "neudrive-canonical+claude-code-extras",
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generated_by": "claude-code-migration ccm panic-backup",
        "tier3_types_captured": result.tier3_local_types,
        "tier2_credentials_included": result.tier2_secrets_included,
        "tier1_cloud_sources": result.tier1_sources,
        "canonical_paths": {
            "identity":        _IDENTITY,
            "profile_dir":     _PROFILE_DIR,
            "scratch_dir":     _SCRATCH_DIR,
            "projects_dir":    _PROJECTS_DIR,
            "skills_dir":      _SKILLS_DIR,
            "conversations":   _CONVOS_DIR,
            "roles":           _ROLES_DIR,
            "credentials":     _VAULT_DIR,
            "claude_extras":   _EXTRAS_DIR,
        },
        "warnings": list(result.warnings),
    }
    _write_json(staging / "manifest.json", manifest)


_RESTORE_MD = """# Claude Panic-Backup · Restore Guide

Generated: {ts}

This archive contains everything on your machine that a Claude account ban
would silently destroy. The tree follows neuDrive's canonical path convention
so the same archive can also be imported into a neuDrive Hub if you run one.

## Contents

| Path | What it holds |
|------|---------------|
| `/identity/profile.json` | Your `oauthAccount` block — account UUID, org UUIDs, billing type |
| `/memory/profile/*.md` | `~/.claude/CLAUDE.md` (principles), user preferences, rules |
| `/memory/scratch/<date>/*.md` | Project / feedback / agent memory snapshots |
| `/projects/<name>/` | Project CLAUDE.md + hooks + MCP config + plans + todos |
| `/skills/<name>/SKILL.md` | Every ~/.claude/skills/ entry, including plugin-bundled skills |
| `/roles/<name>/SKILL.md` | Every Claude Code sub-agent (~/.claude/agents/) |
| `/conversations/claude-code/<uuid>/` | Full session JSONL + subagents + tool-results |
| `/conversations/claude-chat/<uuid>/` | Each chat / project from a claude.ai ZIP (if --cowork-zip was passed) |
| `/credentials/` | ⚠️  RAW OAuth tokens + plugin state + MCP Bearer keys. Treat as a password file. |
| `/claude-code-extras/` | Shell snapshots, session env, file history, prompt history, raw-scan.json |
| `manifest.json` | What was captured + warnings |

## How to restore

### Option A — New Claude Code account

1. Install the Claude Code CLI on the new machine.
2. Sign in with a fresh Anthropic account.
3. `mkdir -p ~/.claude && tar -xzf panic-backup.tar.gz -C /tmp/restore`
4. Selectively copy back:
   - `cp /tmp/restore/memory/profile/principles.md ~/.claude/CLAUDE.md`
   - `cp -r /tmp/restore/skills/* ~/.claude/skills/`
   - `cp -r /tmp/restore/roles/* ~/.claude/agents/`
   - For MCP servers with OAuth: re-auth each one in the new account; do not
     paste old tokens from `/credentials/` unless you know the server accepts
     cross-account reuse.
5. For each project you care about, copy `/projects/<name>/context.md` into
   the new repo's CLAUDE.md.

### Option B — Migrate to a different agent

Use `ccm apply` against this archive's scanner-produced parts:

```bash
ccm apply --dossier <(jq '.' claude-code-extras/raw-scan.json) --target hermes
```

…but the `ccm export` command gives you a cleaner, redacted Workspace Dossier
that's better suited for this. Panic-backup is for defensive storage, not for
feeding adapters.

### Option C — Upload to neuDrive Hub

The archive already follows neuDrive's canonical paths. Assuming you have a
hub URL + token:

```bash
neu sync import panic-backup.tar.gz \\
  --api-base https://www.neudrive.ai \\
  --token "$NEUDRIVE_TOKEN"
```

Once on neuDrive, any connected agent (Cursor / Codex / Gemini / 飞书…) can
read your identity, memory, and skills via MCP.

## claude.ai cloud data (Tier 1)

This tool can only capture cloud data you've already pulled down yourself via
the official Settings → Privacy → Export data flow. If you haven't triggered
that, do so NOW — the email with your ZIP usually takes minutes to hours, and
if your account gets banned mid-wait, the export may never arrive.

After the ZIP arrives, re-run:

```bash
ccm panic-backup --out new-backup.tar.gz --cowork-zip ~/Downloads/anthropic-data-export.zip
```

## Security

- The archive is chmod 0o600 (user-only). Keep it that way.
- `/credentials/` contains plaintext OAuth tokens. Never commit. Never share.
- After successful restore, shred the `/credentials/` directory from the
  extracted tree:
  ```bash
  find /tmp/restore/credentials -type f -exec shred -u {{}} +
  rm -rf /tmp/restore/credentials
  ```

## Warnings from this capture

{warnings_block}
"""


def _write_restore_md(staging: Path, result: PanicBackupResult) -> None:
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    warnings_block = (
        "\n".join(f"- {w}" for w in result.warnings) if result.warnings
        else "_(none — complete capture)_"
    )
    body = _RESTORE_MD.format(ts=ts, warnings_block=warnings_block)
    _write_text(staging / "RESTORE.md", body)


# ── Small I/O helpers ─────────────────────────────────────────────────


def _safe_slug(s: str) -> str:
    import re
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s).strip()).strip("-._").lower()
    return out or "entry"


def _write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text or "", encoding="utf-8")


def _write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _count_nonempty(scan_d: dict[str, Any]) -> int:
    """Rough count of distinct data-type categories that had at least one record."""
    count = 0
    for k, v in scan_d.items():
        if isinstance(v, list) and v:
            count += 1
        elif isinstance(v, dict) and v:
            count += 1
        elif isinstance(v, str) and v.strip():
            count += 1
    return count


def _render_session_md(sess: dict[str, Any]) -> str:
    """Best-effort markdown transcript from a session JSONL dict.

    The session JSONL uses Claude Code's internal shape: each line has a
    `message` field with `role` + `content` (either a string or a list of
    content blocks). We pull just what's readable — unreadable blocks get a
    sigil instead of crashing.
    """
    out = [f"# Session {sess.get('uuid', '')}\n"]
    for rec in sess.get("messages") or []:
        m = rec.get("message") or rec
        role = m.get("role") or rec.get("type") or "?"
        ts = rec.get("timestamp") or ""
        out.append(f"\n## {role} — {ts}\n")
        content = m.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                t = block.get("type")
                if t == "text":
                    out.append(block.get("text", ""))
                elif t == "thinking":
                    out.append(f"> [thinking]\n> {block.get('thinking','')}")
                elif t == "tool_use":
                    out.append(f"`[tool_use {block.get('name','')}]`")
                elif t == "tool_result":
                    out.append(f"`[tool_result {block.get('tool_use_id','')}]`")
    return "\n".join(out)


__all__ = ["panic_backup", "PanicBackupResult"]
