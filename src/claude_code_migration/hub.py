"""neuDrive Hub HTTP client.

Thin wrapper around agi-bar/neuDrive public REST API (docs/reference.md).
We do NOT copy neuDrive code — we just call its documented endpoints.

Supported operations (v0.1):
- whoami / auth check
- update_profile(category, content)
- write_file(path, content)
- import_claude_memory(memories)
- import_skill(name, files)
- write_secret(scope, value)

See: https://github.com/agi-bar/neuDrive/blob/main/docs/reference.md
"""
from __future__ import annotations

import sys
import httpx
from dataclasses import dataclass
from typing import Any


DEFAULT_HOSTED_URL = "https://www.neudrive.ai"


@dataclass
class HubAuth:
    base_url: str = DEFAULT_HOSTED_URL
    token: str | None = None


class NeuDriveHub:
    """Minimal HTTP client for neuDrive Hub.

    Usage:
        hub = NeuDriveHub(base_url="https://www.neudrive.ai", token="ndt_...")
        hub.write_file("/memory/profile/principles.md", "...")
        hub.import_claude_memory([{"file": "a.md", "content": "..."}])
    """

    def __init__(self, base_url: str = DEFAULT_HOSTED_URL, token: str | None = None, timeout: float = 30.0):
        if not token:
            raise ValueError("neuDrive token required (format: ndt_<40 hex>)")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    # -- lifecycle --

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # -- internals --

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = self._client.request(method, path, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("ok") is True and "data" in data:
            return data["data"]
        return data

    # -- API --

    def whoami(self) -> dict[str, Any]:
        """Verify auth + return scope info."""
        return self._request("GET", "/agent/auth/whoami")

    def update_profile(self, category: str, content: str) -> None:
        """preferences / relationships / principles / output-style."""
        self._request("PUT", "/agent/memory/profile",
                      json={"category": category, "content": content})

    def write_file(self, path: str, content: str) -> None:
        """Write to virtual file tree (canonical paths per hubpath/).

        Client-side validates the path to prevent accidental/malicious
        traversal ('..' segments) or protocol injection; server-side should
        validate too, but defense-in-depth.
        """
        if not path.startswith("/"):
            path = "/" + path
        if "\0" in path:
            raise ValueError("Hub path contains NUL byte")
        # Reject traversal / dot segments. Defense in depth — the server
        # should also reject these, but we don't want to rely on it.
        segments = [s for s in path.split("/") if s]
        if any(seg in ("..", ".") for seg in segments):
            raise ValueError(f"Hub path contains traversal segment: {path!r}")
        self._request("PUT", f"/agent/tree/{'/'.join(segments)}", json={"content": content})

    def import_claude_memory(self, memories: list[dict[str, Any]]) -> dict[str, Any]:
        """Bulk-import Claude memory entries via dedicated endpoint."""
        return self._request("POST", "/agent/import/claude-memory",
                             json={"memories": memories})

    def import_skill(self, name: str, files: dict[str, str]) -> dict[str, Any]:
        """files: relative_path -> content."""
        return self._request("POST", "/agent/import/skill",
                             json={"name": name, "files": files})

    def write_secret(self, scope: str, value: str) -> None:
        """AES-256-GCM encrypted vault. Scope like 'claude/web-search/token'."""
        self._request("PUT", f"/agent/vault/{scope}", json={"data": value})

    def list_projects(self) -> list[dict[str, Any]]:
        return (self._request("GET", "/agent/projects").get("projects") or [])

    def create_project(self, name: str) -> dict[str, Any]:
        return self._request("POST", "/agent/projects", json={"name": name})

    def search(self, query: str, scope: str = "all") -> list[dict[str, Any]]:
        return (self._request("GET", "/agent/search",
                              params={"q": query, "scope": scope}).get("results") or [])


def push_scan_to_hub(
    scan: dict[str, Any],
    hub: NeuDriveHub,
    cowork_export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Push a scan (+ optional cowork) to a neuDrive Hub.

    Returns a stats dict. Does NOT overwrite files that already exist in hub
    unless explicitly forced (not yet implemented — future: pass overwrite=True).
    """
    stats: dict[str, int] = {
        "profile_entries": 0,
        "memory_files": 0,
        "skills_uploaded": 0,
        "conversations_uploaded": 0,
        "secrets_vaulted": 0,
    }

    # Profile from ~/.claude/CLAUDE.md
    if scan.get("home_claude_md"):
        hub.update_profile("principles", scan["home_claude_md"])
        stats["profile_entries"] += 1

    # user-typed memory file
    for m in (scan.get("memory") or []):
        if m.get("type") == "user":
            hub.update_profile("preferences", m.get("content", ""))
            stats["profile_entries"] += 1
            break

    # Memory → /memory/scratch/<date>/*.md
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    for m in (scan.get("memory") or []):
        if m.get("type") in ("project", "feedback"):
            slug = (m.get("file") or "mem").replace(".md", "")
            hub.write_file(f"/memory/scratch/{today}/cc-{slug}.md", m.get("content") or "")
            stats["memory_files"] += 1

    # Skills → import_skill
    errors: list[str] = []
    for skill in (scan.get("skills_global") or [])[:50]:
        files = {"SKILL.md": skill.get("body") or ""}
        try:
            hub.import_skill(f"cc-{skill['name']}", files)
            stats["skills_uploaded"] += 1
        except httpx.HTTPError as e:
            errors.append(f"skill {skill.get('name','?')!r}: {type(e).__name__} {e}")

    # Cowork conversations → /conversations/cowork/<uuid>/conversation.md
    if cowork_export:
        platform = cowork_export.get("source", "claude-chat")
        for conv in (cowork_export.get("conversations") or [])[:100]:
            uuid = conv["uuid"]
            path = f"/conversations/{platform}/{uuid[:8]}/conversation.md"
            lines = [f"# {conv['name']}\n"]
            for m in conv.get("messages") or []:
                lines.append(f"## {m['sender']} — {m['timestamp']}\n\n{m.get('text','')}")
            try:
                hub.write_file(path, "\n\n".join(lines))
                stats["conversations_uploaded"] += 1
            except httpx.HTTPError as e:
                errors.append(f"conversation {uuid[:8]}: {type(e).__name__} {e}")
            except ValueError as e:  # path traversal guard tripped
                errors.append(f"conversation {uuid[:8]}: invalid path — {e}")

    if errors:
        print(f"\n⚠️  {len(errors)} hub push error(s):", file=sys.stderr)
        for msg in errors[:10]:
            print(f"   · {msg}", file=sys.stderr)
        if len(errors) > 10:
            print(f"   · ... and {len(errors) - 10} more", file=sys.stderr)
        stats["errors"] = len(errors)

    return stats
