"""Cowork / Claude.ai ZIP export parser.

Accepts the official Settings → Privacy → Export data ZIP and produces
a normalized dict suitable for adapters.

Supports the 2026 schema (content[] structured, Artifacts as tool_use
grouped by input.id, files_v2 signed-URL attachments).
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class ParsedMessage:
    uuid: str
    sender: str  # "human" | "assistant"
    timestamp: str
    text: str
    attachments: list[dict[str, str]] = field(default_factory=list)
    thinking: str = ""


@dataclass
class ParsedArtifact:
    id: str
    title: str
    type: str
    extension: str
    final_content: str
    version_count: int


@dataclass
class ParsedConversation:
    uuid: str
    name: str
    created_at: str
    updated_at: str
    project_uuid: str | None
    workspace_id: str | None
    model: str | None
    messages: list[ParsedMessage]
    artifacts: list[ParsedArtifact]


@dataclass
class ParsedProject:
    uuid: str
    name: str
    description: str
    prompt_template: str
    created_at: str
    is_shared: bool
    docs: list[dict[str, Any]]


@dataclass
class CoworkExport:
    source: str  # "chat" | "cowork"
    users: list[dict[str, Any]]
    projects: list[ParsedProject]
    conversations: list[ParsedConversation]
    workspace_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_MIME_TO_EXT = {
    "text/markdown": "md",
    "text/html": "html",
    "text/plain": "txt",
    "application/vnd.ant.code": "txt",
    "application/vnd.ant.react": "tsx",
    "application/vnd.ant.mermaid": "mmd",
    "image/svg+xml": "svg",
}


def _artifact_extension(mime: str | None) -> str:
    if not mime:
        return "txt"
    return _MIME_TO_EXT.get(mime, "txt")


def _parse_content_items(content: Any, artifact_store: dict[str, ParsedArtifact]) -> tuple[str, str]:
    """Return (text_md, thinking)."""
    if isinstance(content, str):
        return content, ""
    if not isinstance(content, list):
        return "", ""
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "text":
            text_parts.append(item.get("text") or "")
        elif t == "thinking":
            thinking_parts.append(item.get("thinking") or "")
        elif t == "voice_note":
            text_parts.append(f"🎙️ {item.get('title','')}\n\n{item.get('text','')}")
        elif t == "tool_use":
            name = item.get("name", "")
            inp = item.get("input") or {}
            if name == "artifacts":
                aid = inp.get("id") or f"anon-{id(item)}"
                title = inp.get("title") or aid
                mime = inp.get("type") or "text/plain"
                content_str = inp.get("content") or ""
                if aid in artifact_store:
                    artifact_store[aid].version_count += 1
                    # Later versions replace earlier content for create/rewrite/update
                    if content_str:
                        artifact_store[aid].final_content = content_str
                else:
                    artifact_store[aid] = ParsedArtifact(
                        id=aid, title=title, type=mime,
                        extension=_artifact_extension(mime),
                        final_content=content_str, version_count=1,
                    )
                cmd = inp.get("command", "create")
                text_parts.append(f"📎 Artifact[{cmd}]: `{title}` → /artifacts/{aid}.{_artifact_extension(mime)}")
            else:
                text_parts.append(f"🔧 {name}(...)")
        elif t == "tool_result":
            items = item.get("content") or []
            body = "".join((x.get("text", "") if isinstance(x, dict) else "") for x in items)
            text_parts.append(f"📤 {item.get('name','')} → {body[:300]}")
    return "\n\n".join(p for p in text_parts if p), "\n\n".join(thinking_parts)


def parse_cowork_zip(zip_path: str | Path) -> CoworkExport:
    """Parse a Claude.ai/Cowork export ZIP and return structured data."""
    zp = Path(zip_path)
    if not zp.exists():
        raise FileNotFoundError(str(zp))

    with zipfile.ZipFile(zp) as z:
        names = set(z.namelist())

        def _load(name: str) -> Any:
            matches = [n for n in names if n.endswith(name)]
            if not matches:
                return None
            with z.open(matches[0]) as f:
                return json.loads(f.read())

        conversations_raw = _load("conversations.json") or []
        projects_raw = _load("projects.json") or []
        users_raw = _load("users.json") or []

    # Detect Cowork vs Chat via workspace_id presence
    workspace_ids: set[str] = set()
    for c in conversations_raw:
        ws = c.get("workspace_id")
        if ws:
            workspace_ids.add(ws)

    source = "cowork" if workspace_ids else "chat"

    # Parse projects
    projects: list[ParsedProject] = []
    for p in projects_raw:
        projects.append(ParsedProject(
            uuid=p.get("uuid", ""),
            name=p.get("name", ""),
            description=p.get("description", "") or "",
            prompt_template=p.get("prompt_template", "") or "",
            created_at=p.get("created_at", "") or "",
            is_shared=bool(p.get("is_shared", False)),
            docs=list(p.get("docs") or []),
        ))

    # Parse conversations
    conversations: list[ParsedConversation] = []
    for c in conversations_raw:
        messages: list[ParsedMessage] = []
        artifact_store: dict[str, ParsedArtifact] = {}
        for m in c.get("chat_messages") or []:
            content = m.get("content") or m.get("text") or ""
            text_md, thinking = _parse_content_items(content, artifact_store)
            if not text_md and isinstance(m.get("text"), str):
                text_md = m["text"]  # fallback
            atts: list[dict[str, str]] = []
            for a in m.get("attachments") or []:
                if a.get("extracted_content"):
                    atts.append({
                        "filename": a.get("file_name", "attachment"),
                        "content": a["extracted_content"][:4000],
                    })
            for f in (m.get("files_v2") or m.get("files") or []):
                if f.get("file_name"):
                    atts.append({
                        "filename": f["file_name"],
                        "url": f.get("preview_url", ""),  # signed URL, expires
                    })
            messages.append(ParsedMessage(
                uuid=m.get("uuid", ""),
                sender=m.get("sender", ""),
                timestamp=m.get("created_at", "") or "",
                text=text_md,
                attachments=atts,
                thinking=thinking,
            ))

        conversations.append(ParsedConversation(
            uuid=c.get("uuid", ""),
            name=c.get("name", "untitled"),
            created_at=c.get("created_at", "") or "",
            updated_at=c.get("updated_at", "") or "",
            project_uuid=c.get("project_uuid"),
            workspace_id=c.get("workspace_id"),
            model=c.get("model"),
            messages=messages,
            artifacts=list(artifact_store.values()),
        ))

    return CoworkExport(
        source=source,
        users=users_raw if isinstance(users_raw, list) else [users_raw],
        projects=projects,
        conversations=conversations,
        workspace_ids=sorted(workspace_ids),
    )


def safe_filename(s: str, max_len: int = 80) -> str:
    return (re.sub(r"[^\w\-\u4e00-\u9fa5]+", "-", s)[:max_len].strip("-") or "untitled")
