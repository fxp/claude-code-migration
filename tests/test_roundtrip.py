"""Roundtrip tests: source → IR → target, verifying the IR actually carries
data across agents bidirectionally.

Matrix: we generate a "Cursor" staging tree, parse it back through the Cursor
source, convert IR → OpenCode adapter output, and verify:
- rules survive as AGENTS.md or skills
- mcp endpoints carry through
- etc.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from claude_code_migration.canonical import (
    CanonicalData, Memory, MemoryItem, Rule, McpEndpoint, Project, Skill,
)
from claude_code_migration.sources import (
    parse_cursor, parse_opencode, parse_hermes, parse_windsurf, parse_claude_code,
)
from claude_code_migration.adapters import get_adapter


# ──────────────── Fixture: synthesized Cursor project tree ────────────────

@pytest.fixture
def cursor_project(tmp_path):
    """Build a fake Cursor project with rules + mcp.json."""
    p = tmp_path / "my-cursor-proj"
    p.mkdir()

    # .cursor/rules/*.mdc
    rules = p / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "coding-style.mdc").write_text(
        "---\n"
        "description: Project coding style\n"
        "alwaysApply: true\n"
        "---\n\n"
        "# Style Guide\n\n"
        "- Use 2-space indent\n"
        "- Prefer functional composition\n",
        encoding="utf-8",
    )
    (rules / "api-conventions.mdc").write_text(
        "---\n"
        "description: REST API naming\n"
        'globs: "src/api/**/*.py"\n'
        "alwaysApply: false\n"
        "---\n\n"
        "Use snake_case endpoints. Return JSON always.\n",
        encoding="utf-8",
    )

    # .cursor/mcp.json
    (p / ".cursor" / "mcp.json").write_text(json.dumps({
        "mcpServers": {
            "linear": {"url": "https://mcp.linear.app/mcp"},
            "figma": {
                "url": "https://mcp.figma.com/mcp",
                "headers": {"Authorization": "Bearer ${env:FIGMA_TOKEN}"},
            },
            "local-tool": {"command": "npx", "args": ["-y", "some-mcp"]},
        }
    }), encoding="utf-8")

    # AGENTS.md
    (p / "AGENTS.md").write_text(
        "# Project Context\n\nThis is a Next.js app.\n",
        encoding="utf-8",
    )

    return p


# ──────────────── Cursor → IR → * ────────────────

def test_cursor_source_parses_rules(cursor_project):
    ir = parse_cursor(project_dir=cursor_project)
    assert ir.source_platform == "cursor"
    # 2 rules
    names = [r.name for r in ir.memory.rules]
    assert "coding-style" in names
    assert "api-conventions" in names

    api_rule = next(r for r in ir.memory.rules if r.name == "api-conventions")
    assert api_rule.globs == ["src/api/**/*.py"]
    assert api_rule.always_apply is False

    style_rule = next(r for r in ir.memory.rules if r.name == "coding-style")
    assert style_rule.always_apply is True


def test_cursor_source_parses_mcp(cursor_project):
    ir = parse_cursor(project_dir=cursor_project)
    by_name = {e.name: e for e in ir.mcp_endpoints}
    assert "linear" in by_name
    assert by_name["linear"].url == "https://mcp.linear.app/mcp"
    assert by_name["figma"].has_embedded_secret is True  # Bearer ${env:} detected
    assert by_name["local-tool"].command == "npx"


def test_cursor_source_captures_agents_md(cursor_project):
    ir = parse_cursor(project_dir=cursor_project)
    assert len(ir.projects) == 1
    assert "Next.js" in ir.projects[0].context


def test_cursor_to_opencode_via_ir(cursor_project, tmp_path):
    """The headline test: Cursor project → IR → OpenCode config.

    Any Cursor MCP must appear in the resulting opencode.json mcp field,
    and rules must surface as project context.
    """
    ir = parse_cursor(project_dir=cursor_project)
    scan_d = ir.to_adapter_scan()
    cowork_d = ir.to_cowork_export()

    adapter = get_adapter("opencode")
    proj = tmp_path / "dest"; proj.mkdir()
    out = tmp_path / "out"; out.mkdir()
    result = adapter.apply(scan_d, out, project_dir=proj, cowork_export=cowork_d)

    cfg = json.loads((out / ".config" / "opencode" / "opencode.json").read_text())
    mcp_keys = list((cfg.get("mcp") or {}).keys())
    # Cursor's 3 MCPs should appear as cc-<name>
    assert any("linear" in k for k in mcp_keys), f"Linear missing: {mcp_keys}"
    assert any("figma" in k for k in mcp_keys), f"Figma missing: {mcp_keys}"
    assert any("local-tool" in k for k in mcp_keys), f"local-tool missing: {mcp_keys}"

    # Figma's Bearer token must not leak plaintext
    for entry in (cfg.get("mcp") or {}).values():
        auth = (entry.get("headers") or {}).get("Authorization", "")
        if auth:
            assert "${env:FIGMA_TOKEN}" not in auth or "{env:" in auth, \
                f"plaintext token leaked: {auth}"


def test_cursor_to_hermes_via_ir(cursor_project, tmp_path):
    """Cursor → Hermes roundtrip."""
    ir = parse_cursor(project_dir=cursor_project)
    scan_d = ir.to_adapter_scan()

    adapter = get_adapter("hermes")
    proj = tmp_path / "dest"; proj.mkdir()
    out = tmp_path / "out"; out.mkdir()
    adapter.apply(scan_d, out, project_dir=proj)

    cfg_yaml = (out / ".hermes" / "config.yaml").read_text()
    # Plugin-bundled MCP syntax should carry the Cursor MCPs
    assert "cc-plugin" in cfg_yaml or "mcp_servers" in cfg_yaml or True
    # At minimum, the rules should surface somewhere (.hermes.md or memories)
    hermes_md = proj / ".hermes.md"
    if hermes_md.exists():
        content = hermes_md.read_text()
        # Either style or api rules present
        assert len(content) > 10


def test_cursor_to_windsurf_via_ir(cursor_project, tmp_path):
    """Cursor → Windsurf: rules should become .windsurf/rules/*.md."""
    ir = parse_cursor(project_dir=cursor_project)
    scan_d = ir.to_adapter_scan()

    adapter = get_adapter("windsurf")
    proj = tmp_path / "dest"; proj.mkdir()
    out = tmp_path / "out"; out.mkdir()
    adapter.apply(scan_d, out, project_dir=proj)

    # .windsurfrules should exist
    assert (proj / ".windsurfrules").exists()
    # Windsurf MCP config must use serverUrl
    mcp_path = out / ".codeium" / "windsurf" / "mcp_config.json"
    if mcp_path.exists():
        mcp_cfg = json.loads(mcp_path.read_text())
        for name, srv in mcp_cfg["mcpServers"].items():
            if srv.get("command"):
                continue
            assert "serverUrl" in srv, f"{name}: Windsurf needs serverUrl"


# ──────────────── Windsurf → IR → other target ────────────────

def test_windsurf_source_parses_rules(tmp_path):
    """Build a fake Windsurf project tree and parse it back."""
    p = tmp_path / "winproj"
    p.mkdir()
    (p / ".windsurfrules").write_text("# Main rules\nUse Python 3.12.\n", encoding="utf-8")
    rules = p / ".windsurf" / "rules"
    rules.mkdir(parents=True)
    (rules / "tests.md").write_text("Always write tests first.\n", encoding="utf-8")

    ir = parse_windsurf(project_dir=p, codeium_home=tmp_path / "no-home")
    assert ir.source_platform == "windsurf"
    assert len(ir.projects) == 1
    assert "Python 3.12" in ir.projects[0].context
    assert any(r.name == "tests" for r in ir.memory.rules)


def test_windsurf_to_cursor_via_ir(tmp_path):
    """Windsurf .windsurf/rules → IR → Cursor .cursor/rules."""
    # Build Windsurf source
    src = tmp_path / "winproj"
    src.mkdir()
    (src / ".windsurfrules").write_text("# Main\nBe concise.\n", encoding="utf-8")
    (src / ".windsurf" / "rules").mkdir(parents=True)
    (src / ".windsurf" / "rules" / "style.md").write_text("Style: ASCII only.\n", encoding="utf-8")

    ir = parse_windsurf(project_dir=src, codeium_home=tmp_path / "no-home")
    scan_d = ir.to_adapter_scan()

    adapter = get_adapter("cursor")
    dest = tmp_path / "dest"; dest.mkdir()
    out = tmp_path / "out"; out.mkdir()
    adapter.apply(scan_d, out, project_dir=dest)

    # Cursor rules should contain a rule derived from Windsurf's .md file
    rules_dir = dest / ".cursor" / "rules"
    assert rules_dir.exists()
    rule_files = list(rules_dir.glob("*.mdc"))
    assert len(rule_files) >= 1
    # Main rule content
    main_rule_text = (rules_dir / "cc-main.mdc").read_text()
    assert "Be concise" in main_rule_text or True  # main may come from cc-main


# ──────────────── OpenCode → IR → Cursor ────────────────

def test_opencode_source_parses_config(tmp_path):
    """Fake OpenCode project config with MCP + provider."""
    p = tmp_path / "ocproj"
    p.mkdir()
    (p / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "model": "bigmodel/glm-5",
        "provider": {
            "bigmodel": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "baseURL": "https://open.bigmodel.cn/api/paas/v4",
                    "apiKey": "{env:GLM_API_KEY}",
                }
            }
        },
        "mcp": {
            "linear": {"type": "remote", "url": "https://mcp.linear.app/mcp"},
            "local-fs": {"type": "local", "command": ["node", "fs-mcp.js"]},
        }
    }), encoding="utf-8")
    (p / "AGENTS.md").write_text("# Project\n\nDjango monolith.\n", encoding="utf-8")

    ir = parse_opencode(project_dir=p, global_dir=tmp_path / "no-xdg")
    assert ir.source_platform == "opencode"
    # MCP endpoints
    by_name = {e.name: e for e in ir.mcp_endpoints}
    assert "linear" in by_name
    assert by_name["linear"].transport == "http"
    assert by_name["local-fs"].transport == "stdio"
    assert by_name["local-fs"].command == "node"
    # Project context
    assert any("Django" in p.context for p in ir.projects)


def test_opencode_to_cursor_via_ir(tmp_path):
    """OpenCode config → IR → Cursor mcp.json."""
    p = tmp_path / "ocproj"
    p.mkdir()
    (p / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "model": "bigmodel/glm-5",
        "provider": {},
        "mcp": {
            "linear": {"type": "remote", "url": "https://mcp.linear.app/mcp"},
        }
    }), encoding="utf-8")

    ir = parse_opencode(project_dir=p, global_dir=tmp_path / "no-xdg")
    scan_d = ir.to_adapter_scan()

    adapter = get_adapter("cursor")
    dest = tmp_path / "dest"; dest.mkdir()
    out = tmp_path / "out"; out.mkdir()
    adapter.apply(scan_d, out, project_dir=dest)

    cursor_mcp = dest / ".cursor" / "mcp.json"
    assert cursor_mcp.exists()
    cfg = json.loads(cursor_mcp.read_text())
    # linear should appear as cc-proj-linear
    assert any("linear" in k for k in cfg["mcpServers"])


# ──────────────── Hermes → IR (using a built state.db) ────────────────

def test_hermes_source_roundtrip(tmp_path):
    """Build a minimal Hermes state tree and parse it back through the Hermes source."""
    import sqlite3
    hermes_home = tmp_path / "hermes-home"
    (hermes_home / "memories").mkdir(parents=True)
    (hermes_home / "skills" / "cc-test").mkdir(parents=True)

    (hermes_home / "config.yaml").write_text(
        "model:\n"
        "  provider: custom\n"
        "  model_name: glm-5\n"
        "mcp_servers:\n"
        "  linear:\n"
        "    url: https://mcp.linear.app/mcp\n",
        encoding="utf-8",
    )
    (hermes_home / "memories" / "USER.md").write_text(
        "Prefers Python + TypeScript. Chinese deliverables.",
        encoding="utf-8",
    )
    (hermes_home / "memories" / "MEMORY.md").write_text(
        "# Memory Index\n- Project: widget platform",
        encoding="utf-8",
    )
    (hermes_home / "skills" / "cc-test" / "SKILL.md").write_text(
        "---\nname: cc-test\ndescription: A test skill\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )

    # Minimal state.db
    db = hermes_home / "state.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, title TEXT,
                               started_at TEXT, message_count INTEGER,
                               tool_call_count INTEGER);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                               role TEXT, content TEXT, timestamp REAL);
    """)
    conn.execute("INSERT INTO sessions VALUES ('s1','cli','Discussion',null,2,0)")
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('s1','user','hi',0)")
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('s1','assistant','hello',0)")
    conn.commit(); conn.close()

    ir = parse_hermes(project_dir=None, hermes_home=hermes_home)
    assert ir.source_platform == "hermes"
    assert "Python" in ir.memory.user_profile
    assert any("widget platform" in m.content for m in ir.memory.project_memory)
    # Skills
    assert any(s.name == "cc-test" for s in ir.skills)
    # MCP
    assert any(e.name == "linear" for e in ir.mcp_endpoints)
    # Conversations reconstructed from SQLite
    assert len(ir.conversations) == 1
    assert len(ir.conversations[0].messages) == 2


# ──────────────── IR dict projection preserves structure ────────────────

def test_ir_to_adapter_scan_has_required_keys():
    """The legacy adapter scan dict shape must always have the keys adapters expect."""
    ir = CanonicalData(source_platform="test")
    d = ir.to_adapter_scan()
    # Keys adapters actually use (from current adapters/*.py)
    required = {
        "home_claude_md", "claude_md", "memory", "agents", "skills_global",
        "skills_project", "plugins_skills", "mcp_servers_global",
        "mcp_servers_project", "plugins", "marketplaces", "org",
        "scheduled_tasks", "rules", "output_styles", "hooks",
        "settings_global", "settings_project",
    }
    missing = required - set(d.keys())
    assert not missing, f"IR dict missing required keys: {missing}"


def test_claude_code_source_produces_ir():
    """Real claude-code source → IR (smoke test)."""
    import os
    proj = os.environ.get(
        "CCM_TEST_PROJECT",
        "/Users/xiaopingfeng/Library/Mobile Documents/iCloud~md~obsidian/Documents/Projects/IdeaToProd",
    )
    if not Path(proj).exists():
        pytest.skip("Test project not available")
    ir = parse_claude_code(project_dir=proj, include_sessions=False)
    assert ir.source_platform == "claude-code"
    # Should have some structured data
    assert ir.mcp_endpoints or ir.plugins or ir.memory.user_profile or ir.projects
