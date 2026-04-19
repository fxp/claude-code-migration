"""Tests for panic_backup.py — the emergency pre-ban dump.

We can't easily fixture an entire ~/.claude/ tree, so most of these
tests drive an isolated synthetic CLAUDE_CONFIG_DIR + project, confirm
the archive contains the expected neuDrive canonical paths, and verify
security properties (0o600 perms, credentials gating, RESTORE.md always
present).
"""
from __future__ import annotations

import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from claude_code_migration.panic_backup import panic_backup


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def synthetic_claude_home(tmp_path, monkeypatch):
    """Build a minimal ~/.claude/ layout at CLAUDE_CONFIG_DIR."""
    home = tmp_path / "fake-claude-home"
    home.mkdir()
    (home / "CLAUDE.md").write_text(
        "# User profile\nI prefer concise answers.\nAlways run tests.\n"
    )
    (home / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["WebFetch(domain:github.com)"]},
    }))
    # settings.local.json (tier 2)
    (home / "settings.local.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls)"]},
    }))
    # skills
    sk_dir = home / "skills" / "my-skill"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: demo\n---\nbody\n")
    # agents
    (home / "agents").mkdir()
    (home / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: reviews diffs\n---\n\nYou review code.\n"
    )
    # rules
    (home / "rules").mkdir()
    (home / "rules" / "style.md").write_text("---\ntype: rule\n---\nUse short lines.\n")
    # Pretend oauth — via a fake ~/.claude.json in tmp HOME
    home_json = tmp_path / ".claude.json"
    home_json.write_text(json.dumps({
        "userID": "fake-user-id",
        "oauthAccount": {
            "accountUuid": "fake-account-uuid",
            "organizationUuid": "fake-org-uuid",
            "organizationName": "Test Org",
            "organizationRole": "admin",
            "billingType": "team_plan",
            "emailAddress": "test@example.com",
            "displayName": "Test User",
        },
        "mcpServers": {
            "web-search": {
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer test-token-12345"},
            },
        },
    }))

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    return home


@pytest.fixture
def synthetic_project(tmp_path):
    proj = tmp_path / "my-project"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# Project\nIt builds widgets.\n")
    return proj


# ── Archive structure ────────────────────────────────────────────────

def test_archive_is_created_and_chmod_0600(synthetic_claude_home, tmp_path):
    out = tmp_path / "backup.tar.gz"
    r = panic_backup(out_path=out, include_credentials=False)
    assert out.exists()
    # chmod 0o600 — user rw, nothing else
    mode = stat.S_IMODE(out.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    assert r.size_bytes > 0
    assert r.files_written > 0


def test_archive_has_manifest_and_restore_md(synthetic_claude_home, tmp_path):
    out = tmp_path / "backup.tar.gz"
    panic_backup(out_path=out, include_credentials=False)
    names = _tar_names(out)
    assert "manifest.json" in names
    assert "RESTORE.md" in names


def test_archive_contains_neudrive_canonical_paths(synthetic_claude_home, tmp_path):
    out = tmp_path / "backup.tar.gz"
    panic_backup(out_path=out, include_credentials=False)
    names = _tar_names(out)

    # /identity/profile.json — from oauthAccount
    assert "identity/profile.json" in names
    # /memory/profile/principles.md ← ~/.claude/CLAUDE.md
    assert "memory/profile/principles.md" in names
    # /memory/profile/rules/style.md
    assert any(n.startswith("memory/profile/rules/") for n in names), \
        f"no rules dir in {names}"
    # /skills/my-skill/SKILL.md
    assert "skills/my-skill/SKILL.md" in names
    # /roles/reviewer/SKILL.md
    assert "roles/reviewer/SKILL.md" in names


def test_identity_profile_reflects_oauth_account(synthetic_claude_home, tmp_path):
    out = tmp_path / "backup.tar.gz"
    panic_backup(out_path=out, include_credentials=False)
    with tarfile.open(out) as t:
        f = t.extractfile("identity/profile.json")
        assert f is not None
        identity = json.loads(f.read())
    assert identity["account_uuid"] == "fake-account-uuid"
    assert identity["organization_name"] == "Test Org"
    assert identity["is_cowork"] is True


def test_project_context_lands_under_projects_slug(synthetic_claude_home,
                                                    synthetic_project, tmp_path):
    out = tmp_path / "backup.tar.gz"
    panic_backup(out_path=out, project_dir=synthetic_project,
                 include_credentials=False)
    names = _tar_names(out)
    assert any(n.startswith("projects/my-project/context.md") for n in names), \
        f"project context missing; got {names}"


# ── Tier-2 credentials gating ────────────────────────────────────────

def test_credentials_included_by_default_with_danger_readme(synthetic_claude_home,
                                                              tmp_path):
    out = tmp_path / "backup.tar.gz"
    r = panic_backup(out_path=out)   # include_credentials default = True
    assert r.tier2_secrets_included is True
    names = _tar_names(out)
    assert "credentials/README.md" in names
    # The actual oauth dump
    assert "credentials/claude-oauth.json" in names
    # Plaintext Bearer token survives intact here — this is the backup's job
    with tarfile.open(out) as t:
        f = t.extractfile("credentials/claude-oauth.json")
        dump = json.loads(f.read())
    assert dump["oauthAccount"]["accountUuid"] == "fake-account-uuid"
    auth = dump["mcpServers"]["web-search"]["headers"]["Authorization"]
    assert "test-token-12345" in auth, "bearer token should NOT be redacted in panic archive"


def test_redact_credentials_excludes_them(synthetic_claude_home, tmp_path):
    out = tmp_path / "backup.tar.gz"
    r = panic_backup(out_path=out, include_credentials=False)
    assert r.tier2_secrets_included is False
    names = _tar_names(out)
    assert not any(n.startswith("credentials/") for n in names), \
        f"credentials/ leaked: {[n for n in names if n.startswith('credentials/')]}"
    # Warning surfaces to the user
    assert any("include_credentials=False" in w for w in r.warnings)


def test_credentials_readme_warns_about_plaintext(synthetic_claude_home, tmp_path):
    out = tmp_path / "backup.tar.gz"
    panic_backup(out_path=out)
    with tarfile.open(out) as t:
        f = t.extractfile("credentials/README.md")
        body = f.read().decode("utf-8")
    # Danger language present
    assert "plaintext" in body.lower()
    assert "never commit" in body.lower()


# ── Tier-1 cloud ZIP handling ────────────────────────────────────────

def test_cowork_zip_conversations_unpack_into_canonical_paths(synthetic_claude_home,
                                                                tmp_path):
    # Make a minimal fake export ZIP
    convs = [{
        "uuid": "conv-uuid-0001",
        "name": "my chat",
        "created_at": "2026-01-01T00:00:00Z",
        "chat_messages": [
            {
                "uuid": "m1", "sender": "human", "created_at": "2026-01-01T00:00:00Z",
                "content": [{"type": "text", "text": "hi"}],
            },
            {
                "uuid": "m2", "sender": "assistant", "created_at": "2026-01-01T00:00:05Z",
                "content": [{"type": "text", "text": "hello"}],
            },
        ],
    }]
    zp = tmp_path / "export.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("conversations.json", json.dumps(convs))
        z.writestr("projects.json", "[]")
        z.writestr("users.json", "[]")

    out = tmp_path / "backup.tar.gz"
    r = panic_backup(out_path=out, include_credentials=False, cowork_zip=zp)
    assert any("zip:" in s for s in r.tier1_sources)
    names = _tar_names(out)
    # conversation landed at the canonical 8-char-uuid prefix path
    assert any(n.startswith("conversations/claude-chat/conv-uui/conversation.")
               for n in names), f"expected claude-chat conversation in {names}"


def test_missing_cowork_zip_emits_warning(synthetic_claude_home, tmp_path):
    out = tmp_path / "backup.tar.gz"
    r = panic_backup(out_path=out, include_credentials=False)
    assert any("claude.ai cloud" in w for w in r.warnings)


# ── Small helpers ────────────────────────────────────────────────────

def _tar_names(path: Path) -> list[str]:
    with tarfile.open(path, "r:gz") as t:
        return t.getnames()
