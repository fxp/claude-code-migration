"""End-to-end validation: migrate a real project to all 4 targets and
verify each target produces valid output files in the expected formats.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from claude_code_migration import scan_claude_code, scan_secrets
from claude_code_migration.adapters import get_adapter


PROJ = Path(os.environ.get(
    "CCM_TEST_PROJECT",
    "/Users/xiaopingfeng/Library/Mobile Documents/iCloud~md~obsidian/Documents/Projects/OpenClaw Course",
))


@pytest.fixture(scope="session")
def scan_dict():
    if not PROJ.exists():
        pytest.skip(f"Test project not available: {PROJ}")
    scan = scan_claude_code(project_dir=PROJ, include_sessions=True)
    return scan.to_dict()


def test_scan_finds_data(scan_dict):
    """Scanner discovers OpenClaw Course data correctly."""
    # Must find at least some structured data
    assert scan_dict["claude_home"]
    assert scan_dict["project_dir"] == str(PROJ)
    # OpenClaw has 5 memory files
    assert len(scan_dict.get("memory") or []) >= 3
    # Should have at least 1 session (jsonl)
    assert len(scan_dict.get("sessions") or []) >= 1
    # MCP servers global should have web-search-prime
    assert scan_dict.get("mcp_servers_global")


def test_secret_detection(scan_dict):
    """Embedded Bearer token in mcpServers headers should be detected."""
    findings = scan_secrets(scan_dict)
    # Must find at least the BigModel token in web-search-prime
    assert any("web-search-prime" in f.source for f in findings)
    for f in findings:
        assert f.sha256_prefix  # has a stable identifier
        assert f.suggested_env_var.startswith("CC_")


@pytest.mark.parametrize("target", ["hermes", "opencode", "cursor", "windsurf"])
def test_adapter_produces_output(scan_dict, target, tmp_path):
    """Each adapter should produce valid target-specific files."""
    adapter = get_adapter(target)
    # Use tmp for both project (isolated) and output
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = adapter.apply(scan_dict, out_dir, project_dir=proj_dir)
    assert result.target == target
    assert len(result.files_written) > 0

    # Target-specific validations
    if target == "hermes":
        cfg = out_dir / ".hermes" / "config.yaml"
        assert cfg.exists()
        text = cfg.read_text()
        assert "glm-5" in text
        assert "bigmodel" in text
        assert "https://open.bigmodel.cn/api/paas/v4" in text
        # SQLite DB should be present if sessions
        if scan_dict.get("sessions"):
            db = out_dir / ".hermes" / "state.db"
            assert db.exists(), "Hermes SQLite state.db missing"
            conn = sqlite3.connect(str(db))
            (n,) = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
            conn.close()
            assert n > 0, "No sessions imported into state.db"

    elif target == "opencode":
        cfg_path = out_dir / ".config" / "opencode" / "opencode.json"
        assert cfg_path.exists()
        cfg = json.loads(cfg_path.read_text())
        assert cfg["model"] == "bigmodel/glm-5"
        assert cfg["$schema"] == "https://opencode.ai/config.json"
        assert "bigmodel" in cfg["provider"]
        # MCP servers should be under "mcp" not "mcpServers"
        assert "mcp" in cfg
        # Skills directory has content
        skill_dir = out_dir / ".config" / "opencode" / "skills"
        if scan_dict.get("skills_global"):
            assert skill_dir.exists()
            assert list(skill_dir.glob("cc-*/SKILL.md"))

    elif target == "cursor":
        rules_dir = proj_dir / ".cursor" / "rules"
        assert rules_dir.exists()
        main_rule = rules_dir / "cc-main.mdc"
        assert main_rule.exists()
        text = main_rule.read_text()
        assert "alwaysApply: true" in text
        assert text.startswith("---\n")
        # MCP config uses ${env:VAR} interpolation, not plain Bearer
        mcp_path = proj_dir / ".cursor" / "mcp.json"
        if scan_dict.get("mcp_servers_global"):
            assert mcp_path.exists()
            mcp_cfg = json.loads(mcp_path.read_text())
            assert "mcpServers" in mcp_cfg
            for name, srv in mcp_cfg["mcpServers"].items():
                # Check that no plain Bearer tokens leaked
                auth = (srv.get("headers") or {}).get("Authorization", "")
                if auth:
                    assert "${env:" in auth, f"Cursor plain auth header: {auth}"

    elif target == "windsurf":
        wr = proj_dir / ".windsurfrules"
        assert wr.exists()
        # Windsurf MCP uses "serverUrl" not "url"
        mcp_path = out_dir / ".codeium" / "windsurf" / "mcp_config.json"
        if scan_dict.get("mcp_servers_global"):
            assert mcp_path.exists()
            cfg = json.loads(mcp_path.read_text())
            for name, srv in cfg["mcpServers"].items():
                if "url" in srv and "serverUrl" not in srv:
                    pytest.fail(f"Windsurf must use 'serverUrl' not 'url' for {name}")


def test_no_plaintext_bearer_tokens_in_outputs(scan_dict, tmp_path):
    """Critical safety check: no plaintext secret values in any generated file."""
    findings = scan_secrets(scan_dict)
    if not findings:
        pytest.skip("No secrets detected in this project")
    secret_values = {f.raw_value for f in findings if len(f.raw_value) >= 20}

    for target in ("hermes", "opencode", "cursor", "windsurf"):
        adapter = get_adapter(target)
        proj_dir = tmp_path / f"proj-{target}"
        proj_dir.mkdir()
        out_dir = tmp_path / f"out-{target}"
        out_dir.mkdir()
        adapter.apply(scan_dict, out_dir, project_dir=proj_dir)

        for file in list(out_dir.rglob("*")) + list(proj_dir.rglob("*")):
            if not file.is_file() or file.suffix == ".db":
                continue
            try:
                content = file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for secret in secret_values:
                assert secret not in content, f"{target}: plaintext secret leaked in {file}"
