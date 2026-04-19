"""Tests for the non-redactor hardening fixes (HIGH / MEDIUM / LOW).

Covers:
  H2  ZIP bomb guard in parse_cowork_zip (size + compression-ratio cap)
  M8  Path traversal rejection in NeuDriveHub.write_file
  M9  Strict basename match in cowork ZIP (no endswith prefix tricks)
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from claude_code_migration.cowork import parse_cowork_zip, ZipBombError
from claude_code_migration.hub import NeuDriveHub


# ── H2 + M9 · ZIP hardening ─────────────────────────────────────────

def _make_zip(tmp_path: Path, entries: dict[str, bytes],
              compression: int = zipfile.ZIP_DEFLATED) -> Path:
    zp = tmp_path / "test.zip"
    with zipfile.ZipFile(zp, "w", compression=compression) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return zp


def test_normal_zip_parses_fine(tmp_path):
    payload = json.dumps([{"uuid": "abc", "name": "t", "chat_messages": []}]).encode()
    zp = _make_zip(tmp_path, {"conversations.json": payload,
                              "projects.json": b"[]",
                              "users.json": b"[]"})
    export = parse_cowork_zip(zp)
    assert len(export.conversations) == 1


def test_zip_entry_exceeding_size_cap_is_rejected(tmp_path):
    # 600 MB uncompressed, highly compressible zeros — exceeds the 500 MB cap
    huge = b"\x00" * (600 * 1024 * 1024)
    zp = _make_zip(tmp_path, {"conversations.json": huge})
    with pytest.raises(ZipBombError, match="uncompressed size"):
        parse_cowork_zip(zp)


def test_zip_with_crazy_compression_ratio_is_rejected(tmp_path):
    # 100 MB of zeros compresses to ~100 KB → ratio ~1000× which exceeds cap.
    # Use a size below the per-entry byte cap so we specifically exercise the
    # ratio check rather than the byte cap.
    payload = b"\x00" * (50 * 1024 * 1024)
    zp = _make_zip(tmp_path, {"conversations.json": payload})
    with pytest.raises(ZipBombError, match="compression ratio"):
        parse_cowork_zip(zp)


def test_zip_with_traversal_name_is_rejected(tmp_path):
    zp = _make_zip(tmp_path, {"../conversations.json": b"[]"})
    with pytest.raises(ZipBombError, match="Unsafe entry name"):
        parse_cowork_zip(zp)


def test_zip_with_absolute_name_is_rejected(tmp_path):
    # ZipFile stores names verbatim; a crafted ZIP could have leading /.
    # Write one via raw ZipInfo to bypass the auto-strip.
    zp = tmp_path / "abs.zip"
    with zipfile.ZipFile(zp, "w") as z:
        info = zipfile.ZipInfo("/conversations.json")
        z.writestr(info, b"[]")
    with pytest.raises(ZipBombError, match="Unsafe entry name"):
        parse_cowork_zip(zp)


def test_zip_endswith_attack_does_not_match(tmp_path):
    # Old code used endswith("conversations.json") which would match
    # "evilconversations.json". New code matches exact basename, so the
    # attacker's file is ignored and the parse just returns empty.
    bad = b"[{\"uuid\": \"evil\", \"name\": \"hijacked\", \"chat_messages\": []}]"
    zp = _make_zip(tmp_path, {"subdir/evilconversations.json": bad})
    export = parse_cowork_zip(zp)
    assert export.conversations == []


# ── M8 · Hub path traversal ─────────────────────────────────────────

class _FakeClient:
    """Minimal stand-in for httpx.Client that records the URL of each PUT."""
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, **_):
        self.calls.append((method, path))
        resp = type("R", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"ok": True, "data": {}},
        })()
        return resp

    def close(self):
        pass


def _make_hub_with_fake_client() -> NeuDriveHub:
    hub = NeuDriveHub(token="ndt_" + "0" * 40)
    hub._client = _FakeClient()
    return hub


def test_write_file_rejects_dotdot_traversal():
    hub = _make_hub_with_fake_client()
    with pytest.raises(ValueError, match="traversal"):
        hub.write_file("/memory/../etc/passwd", "oops")


def test_write_file_rejects_single_dot_segment():
    hub = _make_hub_with_fake_client()
    with pytest.raises(ValueError, match="traversal"):
        hub.write_file("/memory/./x.md", "content")


def test_write_file_rejects_null_byte():
    hub = _make_hub_with_fake_client()
    with pytest.raises(ValueError, match="NUL"):
        hub.write_file("/memory/\x00name", "content")


def test_write_file_accepts_legit_canonical_path():
    hub = _make_hub_with_fake_client()
    hub.write_file("/conversations/claude-chat/abc12345/conversation.md", "body")
    # First call is our PUT; verify path made it through unmodified (minus
    # leading-slash normalization).
    method, url = hub._client.calls[-1]
    assert method == "PUT"
    assert url == "/agent/tree/conversations/claude-chat/abc12345/conversation.md"
