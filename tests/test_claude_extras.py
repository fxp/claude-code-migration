"""Tests for the 2026 Claude Code surface area beyond CLAUDE.md.

Covers:
- ~/.claude/commands/ + <proj>/.claude/commands/ (custom slash commands)
- ~/.claude/themes/
- ~/.claude/keybindings.json
- Plugin bin/ executables (W14 spec)
- Plugin-bundled commands + agents

References:
- https://code.claude.com/docs/en/commands (slash commands)
- https://code.claude.com/docs/en/claude-directory
- https://code.claude.com/docs/en/whats-new/2026-w14 (plugin bin/)
"""
from __future__ import annotations

import json
from pathlib import Path

from claude_code_migration.scanner import (
    _scan_commands_dir,
    _scan_themes_dir,
    scan_claude_code,
)


def test_commands_dir_recursive_with_namespacing(tmp_path: Path) -> None:
    """Subdirectories namespace commands: foo/bar.md → 'foo:bar'."""
    base = tmp_path / "commands"
    (base / "frontend").mkdir(parents=True)
    (base / "frontend" / "test.md").write_text(
        "---\ndescription: Run frontend tests\nallowed-tools: Bash, Edit\n"
        "argument-hint: [target file]\n---\nbody here\n"
    )
    (base / "deploy.md").write_text("---\ndescription: Deploy\n---\nshippy\n")

    cmds = _scan_commands_dir(base)
    by_name = {c.name: c for c in cmds}
    assert "deploy" in by_name
    assert "frontend:test" in by_name
    front = by_name["frontend:test"]
    assert front.description == "Run frontend tests"
    # allowed-tools comma-separated string parsed into a list
    assert "Bash" in front.allowed_tools
    assert "Edit" in front.allowed_tools
    assert front.argument_hint == "[target file]"
    assert "body here" in front.body


def test_commands_dir_with_prefix(tmp_path: Path) -> None:
    """Plugin-bundled commands get prefixed with plugin name."""
    base = tmp_path / "cmds"
    base.mkdir()
    (base / "ship.md").write_text("ship body\n")
    cmds = _scan_commands_dir(base, prefix="my-plugin")
    assert cmds[0].name == "my-plugin:ship"


def test_themes_dir_captures_files_verbatim(tmp_path: Path) -> None:
    """Themes are read as raw content; format unspecified by spec."""
    base = tmp_path / "themes"
    base.mkdir()
    (base / "midnight.json").write_text('{"bg": "#000"}')
    (base / "subdir").mkdir()
    (base / "subdir" / "high-contrast.md").write_text("# theme notes")

    themes = _scan_themes_dir(base)
    files = sorted(t.file for t in themes)
    # rglob — both root and nested files captured
    assert "midnight.json" in files
    assert any(f.endswith("high-contrast.md") for f in files)
    midnight = next(t for t in themes if t.file == "midnight.json")
    assert midnight.content == '{"bg": "#000"}'


def test_scan_picks_up_global_commands_themes_keybindings(tmp_path: Path,
                                                           monkeypatch) -> None:
    """End-to-end: scan_claude_code reads everything from a fake CLAUDE_CONFIG_DIR."""
    fake_home = tmp_path / "claude-home"
    (fake_home / "commands" / "ns").mkdir(parents=True)
    (fake_home / "commands" / "ns" / "ping.md").write_text("ping body\n")
    (fake_home / "themes").mkdir()
    (fake_home / "themes" / "neon.json").write_text('{"k":"v"}')
    (fake_home / "keybindings.json").write_text('{"submit": "ctrl+enter"}')

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home))
    s = scan_claude_code(include_sessions=False, include_env_reproduction=False)
    assert any(c.name == "ns:ping" for c in s.commands_global)
    assert any(t.file == "neon.json" for t in s.themes)
    assert s.keybindings == {"submit": "ctrl+enter"}


def test_scan_picks_up_project_commands(tmp_path: Path) -> None:
    """Project-level slash commands at <proj>/.claude/commands/."""
    proj = tmp_path / "proj"
    (proj / ".claude" / "commands").mkdir(parents=True)
    (proj / ".claude" / "commands" / "release.md").write_text("release body\n")

    s = scan_claude_code(project_dir=proj, include_sessions=False,
                         include_env_reproduction=False)
    names = [c.name for c in s.commands_project]
    assert "release" in names


def test_plugin_bin_and_commands_and_agents(tmp_path: Path, monkeypatch) -> None:
    """W14 plugin bin/ + bundled commands + bundled agents are captured."""
    fake_home = tmp_path / "claude-home"
    plugins_dir = fake_home / "plugins"
    plugins_dir.mkdir(parents=True)

    install_path = plugins_dir / "cache" / "official" / "myplug" / "1.0"
    install_path.mkdir(parents=True)
    (install_path / ".claude-plugin").mkdir()
    (install_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "myplug", "version": "1.0"})
    )
    (install_path / "bin").mkdir()
    (install_path / "bin" / "myplug-cli").write_text("#!/bin/sh\necho hi\n")
    (install_path / "bin" / "myplug-helper").write_text("#!/bin/sh\necho ok\n")
    (install_path / "commands").mkdir()
    (install_path / "commands" / "do-thing.md").write_text("doit\n")
    (install_path / "agents").mkdir()
    (install_path / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: PR reviewer\n---\nReview PRs.\n"
    )

    # Minimal installed_plugins.json so _scan_plugins picks it up
    (plugins_dir / "installed_plugins.json").write_text(json.dumps({
        "plugins": {
            "myplug@official": [
                {"version": "1.0", "installPath": str(install_path),
                 "scope": "user", "installedAt": "2026-04-20T00:00:00Z",
                 "lastUpdated": "2026-04-20T00:00:00Z"}
            ]
        }
    }))
    (plugins_dir / "known_marketplaces.json").write_text(json.dumps({
        "official": {"source": {"source": "github", "repo": "x/y"},
                     "installLocation": "", "lastUpdated": ""}
    }))

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home))
    s = scan_claude_code(include_sessions=False, include_env_reproduction=False)
    plug = next(p for p in s.plugins if p.id == "myplug@official")
    assert sorted(plug.bin_files) == ["bin/myplug-cli", "bin/myplug-helper"]
    assert "myplug:do-thing" in plug.command_names
    assert any(name.startswith("myplug:reviewer") for name in plug.agent_names)
    # Plugin-bundled command body lives in scan.plugins_commands
    cmd_names = [c.name for c in s.plugins_commands]
    assert "myplug:do-thing" in cmd_names
    # Plugin-bundled agent appears in the global agents list, prefixed
    agent_names = [a.name for a in s.agents]
    assert any("myplug:" in n for n in agent_names)


def test_adapter_archive_dumps_extras(tmp_path: Path) -> None:
    """Adapter _archive/claude-extras/ preserves commands, themes, keybindings, bin info."""
    from claude_code_migration.adapters.base import write_archive
    scan = {
        "claude_extras": {
            "commands_global": [{
                "name": "ns:ping", "path": "/x/ping.md", "body": "ping\n",
                "frontmatter": {"description": "Ping"},
                "description": "Ping", "allowed_tools": [], "argument_hint": "",
            }],
            "themes": [{"file": "neon.json", "path": "/x/neon.json",
                         "content": '{"k":"v"}'}],
            "keybindings": {"submit": "ctrl+enter"},
            "plugins_with_bin": [{
                "id": "myplug@official",
                "bin_files": ["bin/myplug-cli"],
                "install_path": "/x/myplug",
            }],
        }
    }
    write_archive(tmp_path, scan)
    ex = tmp_path / "_archive" / "claude-extras"
    assert ex.is_dir()
    assert (ex / "commands_global" / "ns__ping.md").is_file()
    assert (ex / "themes" / "neon.json").read_text() == '{"k":"v"}'
    assert json.loads((ex / "keybindings.json").read_text())["submit"] == "ctrl+enter"
    idx = (ex / "INDEX.md").read_text()
    assert "ns:ping" in idx
    assert "neon.json" in idx
    assert "myplug@official" in idx
