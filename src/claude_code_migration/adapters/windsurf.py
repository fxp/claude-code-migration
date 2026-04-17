"""Windsurf (Codeium) adapter.

Paths:
- .windsurfrules (project, legacy single file)
- .windsurf/rules/*.md (project, recommended)
- ~/.codeium/windsurf/memories/global_rules.md (global)
- ~/.codeium/windsurf/mcp_config.json (MCP, uses serverUrl not url)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Adapter, MigrationResult, ensure_dir, build_universal_agents_md


class WindsurfAdapter(Adapter):
    name = "windsurf"

    def apply(self, scan, out_dir, project_dir=None, cowork_export=None):
        r = MigrationResult(target=self.name)
        out_dir = ensure_dir(Path(out_dir))
        target_root = project_dir if project_dir else out_dir

        # 1. .windsurfrules (project root)
        main_body = scan.get("claude_md") or build_universal_agents_md(scan)
        (target_root / ".windsurfrules").write_text(main_body, encoding="utf-8")
        r.files_written.append(str(target_root / ".windsurfrules"))

        # 2. .windsurf/rules/*.md
        rules_dir = ensure_dir(target_root / ".windsurf" / "rules")
        for m in (scan.get("memory") or []):
            if m.get("type") in ("project", "feedback"):
                name = (m.get("file") or "mem").replace(".md", "")
                (rules_dir / f"cc-{name}.md").write_text(m.get("content") or "", encoding="utf-8")
                r.files_written.append(str(rules_dir / f"cc-{name}.md"))

        # 3. Global rules → ~/.codeium/windsurf/memories/global_rules.md
        if scan.get("home_claude_md"):
            global_dir = ensure_dir(out_dir / ".codeium" / "windsurf" / "memories")
            gr_path = global_dir / "global_rules.md"
            gr_path.write_text(scan["home_claude_md"], encoding="utf-8")
            r.files_written.append(str(gr_path))

        # 4. MCP config (Windsurf uses serverUrl for remote)
        servers: dict[str, Any] = {}
        for name, srv in (scan.get("mcp_servers_global") or {}).items():
            if srv.get("url"):
                cfg: dict[str, Any] = {
                    "serverUrl": srv["url"],  # Windsurf-specific
                    "headers": {},
                }
                for k, v in (srv.get("headers") or {}).items():
                    if "auth" in k.lower() or "token" in k.lower():
                        env_var = f"CC_MCP_{name.upper().replace('-', '_')}_TOKEN"
                        cfg["headers"][k] = "Bearer ${env:" + env_var + "}"
                        r.env_vars_needed[env_var] = f"From {name} mcpServer"
                    else:
                        cfg["headers"][k] = v
                if not cfg["headers"]:
                    del cfg["headers"]
            else:
                cfg = {
                    "command": srv.get("command") or "npx",
                    "args": list(srv.get("args") or []),
                }
                if srv.get("env"):
                    cfg["env"] = {k: "${env:" + k + "}" for k in srv["env"]}
            servers[f"cc-{name}"] = cfg

        if servers:
            mcp_dir = ensure_dir(out_dir / ".codeium" / "windsurf")
            mcp_path = mcp_dir / "mcp_config.json"
            mcp_path.write_text(json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False), encoding="utf-8")
            r.files_written.append(str(mcp_path))

        # 5. Agents → become manual-invoke rules
        for a in (scan.get("agents") or []):
            name = (a.get("name") or "agent").lower().replace(" ", "-")
            body = f"# {a.get('name')}\n\n{a.get('instructions') or ''}"
            (rules_dir / f"cc-agent-{name}.md").write_text(body, encoding="utf-8")
            r.files_written.append(str(rules_dir / f"cc-agent-{name}.md"))

        if cowork_export:
            r.warnings.append("Windsurf has no session-resume feature; Cowork conversations not imported (use Hermes target)")

        r.post_install_hint = (
            "Windsurf setup:\n"
            "  Copy out_dir/.codeium/ → ~/.codeium/\n"
            "  .windsurfrules + .windsurf/rules/ are auto-loaded from project root\n"
            "  Set env vars: " + (", ".join(r.env_vars_needed.keys()) or "(none)") + "\n"
            "  Restart Windsurf"
        )
        return r
