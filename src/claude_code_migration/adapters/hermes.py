"""Hermes Agent adapter (Nous Research)."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from .base import (Adapter, MigrationResult, ensure_dir,
                   render_cowork_project_markdown,
                   safe_slug, write_archive)


class HermesAdapter(Adapter):
    name = "hermes"

    def apply(self, scan, out_dir, project_dir=None, cowork_export=None):
        r = MigrationResult(target=self.name)
        out_dir = ensure_dir(Path(out_dir))

        # ~/.hermes/ layout (under out_dir for testing)
        hermes_root = ensure_dir(out_dir / ".hermes")
        memories = ensure_dir(hermes_root / "memories")
        skills = ensure_dir(hermes_root / "skills")
        ensure_dir(hermes_root / "hooks")

        # 1. config.yaml — BigModel GLM-5 default
        config_yaml = (
            "# Migrated by claude-code-migration\n"
            'model:\n'
            '  provider: "custom"\n'
            '  model_name: "glm-5"\n'
            '\n'
            'custom_providers:\n'
            '  bigmodel:\n'
            '    base_url: "https://open.bigmodel.cn/api/paas/v4"\n'
            '    api_key: "${OPENAI_API_KEY}"\n'
            '\n'
            'terminal_backend: "local"\n'
            '\n'
            'memory:\n'
            '  max_memory_tokens: 800\n'
            '  max_user_profile_tokens: 500\n'
            '\n'
            'compression:\n'
            '  enable_compression: true\n'
            '  compression_threshold: 0.5\n'
        )
        # Append plugin-bundled MCP servers as "mcp_servers" section
        plugins = scan.get("plugins") or []
        plugin_mcp_lines: list[str] = []
        for p in plugins:
            for mname, msrv in (p.get("mcp_servers") or {}).items():
                if msrv.get("url"):
                    plugin_mcp_lines.append(f'  "cc-plugin-{p["plugin_name"]}-{mname}":')
                    plugin_mcp_lines.append(f'    url: "{msrv["url"]}"')
                    plugin_mcp_lines.append(f'    transport: "http"')
        if plugin_mcp_lines:
            config_yaml += '\n# Plugin-bundled MCP servers (migrated from Claude Code plugins)\nmcp_servers:\n' + \
                           '\n'.join(plugin_mcp_lines) + '\n'

        cfg_path = hermes_root / "config.yaml"
        cfg_path.write_text(config_yaml, encoding="utf-8")
        r.files_written.append(str(cfg_path))
        r.env_vars_needed["OPENAI_API_KEY"] = "BigModel/GLM API key from https://open.bigmodel.cn/"

        # Org metadata (Cowork) → identity comment in config
        org = scan.get("org")
        if org and org.get("organization_name"):
            r.warnings.append(
                f"Cowork org detected: {org['organization_name']} "
                f"(role: {org.get('organization_role', '?')}, billing: {org.get('billing_type', '?')}). "
                "Plugin inventory preserved."
            )

        # 2. memories/USER.md — from home CLAUDE.md or user profile memory
        user_content = scan.get("home_claude_md") or ""
        for m in (scan.get("memory") or []):
            if m.get("type") == "user":
                user_content = (user_content + "\n\n" + (m.get("content") or "")).strip()
        if user_content:
            user_content_trimmed = user_content[:1375]  # Hermes limit
            (memories / "USER.md").write_text(user_content_trimmed, encoding="utf-8")
            r.files_written.append(str(memories / "USER.md"))

        # 3. memories/MEMORY.md — index + top project memory
        mem_parts = []
        for m in (scan.get("memory") or []):
            if m.get("file") == "MEMORY.md":
                mem_parts.append(m.get("content", ""))
        for m in (scan.get("memory") or []):
            if m.get("type") == "project":
                mem_parts.append(m.get("content", ""))
        mem_text = "\n\n".join(mem_parts).strip()
        if mem_text:
            mem_text = mem_text[:2200]
            (memories / "MEMORY.md").write_text(mem_text, encoding="utf-8")
            r.files_written.append(str(memories / "MEMORY.md"))

        # 4. .hermes.md at project root (priority 1 context file)
        if project_dir:
            proj_mem_parts: list[str] = []
            for m in (scan.get("memory") or []):
                if m.get("type") in ("project", "feedback"):
                    proj_mem_parts.append(f"## {m.get('file')}\n\n{m.get('content')}")
            if proj_mem_parts:
                content = "# Project Memory\n\n" + "\n\n---\n\n".join(proj_mem_parts)
                content = content[:20000]
                (project_dir / ".hermes.md").write_text(content, encoding="utf-8")
                r.files_written.append(str(project_dir / ".hermes.md"))

        # 5. Skills → ~/.hermes/skills/cc-{name}/SKILL.md (Hermes format)
        # Merge global skills + plugin-bundled skills (Cowork plugin system)
        all_skills = list(scan.get("skills_global") or []) + list(scan.get("plugins_skills") or [])
        for skill in all_skills[:200]:
            sd = ensure_dir(skills / f"cc-{skill['name']}")
            fm = f"""---
name: cc-{skill['name']}
description: "{(skill.get('description') or '').replace(chr(10), ' ')[:400]}"
version: 1.0.0
metadata:
  hermes:
    tags: [migrated, claude-code]
    category: migrated
---

"""
            # Replace hardcoded Claude paths
            body = (skill.get("body") or "").replace(
                "~/.claude/skills/", "~/.hermes/skills/cc-"
            )
            (sd / "SKILL.md").write_text(fm + body, encoding="utf-8")
            r.files_written.append(str(sd / "SKILL.md"))

        # 6. Sessions → state.db SQLite FTS5
        sessions = scan.get("sessions") or []
        if sessions:
            db_path = hermes_root / "state.db"
            conn = sqlite3.connect(str(db_path))
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, source TEXT, title TEXT,
                    started_at TEXT, message_count INTEGER, tool_call_count INTEGER
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                    role TEXT, content TEXT, timestamp REAL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                    USING fts5(content, content=messages, content_rowid=id);
            """)
            imported = 0
            for s in sessions:
                cc_sid = s["uuid"]
                msgs: list[tuple[str, str, float]] = []
                first_ts = None
                first_msg = ""
                try:
                    with open(s["path"], encoding="utf-8", errors="replace") as f:
                        for line in f:
                            try:
                                entry = json.loads(line.strip())
                            except Exception:
                                continue
                            etype = entry.get("type")
                            if etype in ("user", "assistant"):
                                msg = entry.get("message") or {}
                                c = msg.get("content") if isinstance(msg, dict) else None
                                if isinstance(c, str) and c:
                                    ts = entry.get("timestamp", "")
                                    try:
                                        epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                                    except Exception:
                                        epoch = time.time()
                                    msgs.append((etype, c, epoch))
                                    if not first_ts:
                                        first_ts, first_msg = ts, c[:80]
                except Exception:
                    continue
                if not msgs:
                    continue
                try:
                    ts_obj = datetime.fromisoformat(first_ts.replace("Z", "+00:00")) if first_ts else datetime.now()
                except Exception:
                    ts_obj = datetime.now()
                hermes_sid = f"cc_{ts_obj.strftime('%Y%m%d_%H%M%S')}_{cc_sid[:8]}"
                conn.execute(
                    "INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?,?)",
                    (hermes_sid, "cli", f"[CC] {first_msg}", first_ts, len(msgs), 0),
                )
                for role, content, ts_ in msgs:
                    cur = conn.execute(
                        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                        (hermes_sid, role, content, ts_),
                    )
                    conn.execute(
                        "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
                        (cur.lastrowid, content),
                    )
                imported += 1
            conn.commit()
            conn.close()
            r.files_written.append(str(db_path))

        # 7a. Cowork Projects → ~/.hermes/memories/projects/<slug>/context.md
        if cowork_export and (cowork_export.get("projects") or []):
            proj_root = ensure_dir(hermes_root / "memories" / "projects")
            for cproj in cowork_export["projects"]:
                slug = safe_slug(cproj.get("name", "project"))
                proj_dir = ensure_dir(proj_root / slug)
                proj_dir.joinpath("context.md").write_text(
                    render_cowork_project_markdown(cproj, include_docs=True),
                    encoding="utf-8")
                r.files_written.append(str(proj_dir / "context.md"))

        # 7b. Scheduled tasks → ~/.hermes/cron/<name>.md (Hermes has cronjob tool)
        sched = scan.get("scheduled_tasks") or []
        if sched:
            cron_dir = ensure_dir(hermes_root / "cron")
            for st in sched:
                p = cron_dir / f"{safe_slug(st['name'])}.md"
                fm = st.get("frontmatter") or {}
                body = st.get("body") or ""
                # Hermes can't auto-trigger; dump as reference doc
                content = f"# Scheduled Task: {st['name']}\n\n"
                if fm:
                    content += "Frontmatter:\n```yaml\n"
                    for k, v in fm.items():
                        content += f"{k}: {v}\n"
                    content += "```\n\n"
                content += body
                p.write_text(content, encoding="utf-8")
                r.files_written.append(str(p))
            r.warnings.append(
                f"{len(sched)} scheduled tasks preserved in ~/.hermes/cron/ — "
                "manually re-create with Hermes `cronjob` tool"
            )

        # 8. Cowork conversations → state.db (if provided)
        if cowork_export:
            db_path = hermes_root / "state.db"
            # Ensure db exists
            if not db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY, source TEXT, title TEXT,
                        started_at TEXT, message_count INTEGER, tool_call_count INTEGER);
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
                        role TEXT, content TEXT, timestamp REAL);
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                        USING fts5(content, content=messages, content_rowid=id);
                """)
                conn.commit()
                conn.close()
            conn = sqlite3.connect(str(db_path))
            for conv in (cowork_export.get("conversations") or [])[:200]:
                hermes_sid = f"cowork_{conv['uuid'][:12]}"
                conn.execute(
                    "INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?,?)",
                    (hermes_sid, "cli", f"[Cowork] {conv['name'][:60]}", conv["created_at"],
                     len(conv.get("messages") or []), 0),
                )
                for m in conv.get("messages") or []:
                    try:
                        epoch = datetime.fromisoformat((m["timestamp"] or "").replace("Z", "+00:00")).timestamp()
                    except Exception:
                        epoch = time.time()
                    cur = conn.execute(
                        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                        (hermes_sid, m["sender"] or "user", m["text"] or "", epoch),
                    )
                    conn.execute(
                        "INSERT INTO messages_fts (rowid, content) VALUES (?, ?)",
                        (cur.lastrowid, m["text"] or ""),
                    )
            conn.commit()
            conn.close()

        # 9. Archive: unmigrateable raw data preserved in _archive/
        archive_files = write_archive(out_dir, scan, cowork_export,
                                      unmigratable_notes=[
                                          "Hermes does not support scheduled-task auto-trigger; see cron/ for preserved tasks",
                                      ] if (scan.get("scheduled_tasks") or []) else None)
        r.files_written.extend(archive_files)

        r.post_install_hint = (
            "Install Hermes: curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash\n"
            "  Copy out_dir/.hermes/ → ~/.hermes/\n"
            "  Set env: export OPENAI_API_KEY=<your-bigmodel-key>\n"
            "  Run: hermes"
        )
        return r
