"""Claude.ai Chat / Cowork ZIP export → IR."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..canonical import (CanonicalData, Conversation, Message, Attachment,
                         Artifact, Project, Document)
from ..cowork import parse_cowork_zip


def parse(zip_path: str | Path, **kwargs: Any) -> CanonicalData:
    ce = parse_cowork_zip(zip_path)
    d = ce.to_dict()

    ir = CanonicalData(
        source_platform=d.get("source", "claude-chat"),
        generated_at="",
    )

    # Projects
    for p in d.get("projects") or []:
        slug = (p.get("name") or "project").lower().replace(" ", "-")
        docs = [
            Document(filename=x.get("filename", ""), content=x.get("content", ""))
            for x in (p.get("docs") or [])
        ]
        ir.projects.append(Project(
            uuid=p.get("uuid"),
            name=p.get("name", ""),
            slug=slug,
            description=p.get("description", ""),
            prompt_template=p.get("prompt_template", ""),
            docs=docs,
            is_shared=bool(p.get("is_shared", False)),
            created_at=p.get("created_at", ""),
        ))

    # Conversations
    for c in d.get("conversations") or []:
        msgs = [
            Message(
                uuid=m.get("uuid", ""),
                role=m.get("sender", "user"),
                content=m.get("text", ""),
                timestamp=m.get("timestamp", ""),
                thinking=m.get("thinking", ""),
                attachments=[
                    Attachment(
                        filename=a.get("filename", ""),
                        content=a.get("content", ""),
                        url=a.get("url", ""),
                    )
                    for a in (m.get("attachments") or [])
                ],
            )
            for m in (c.get("messages") or [])
        ]
        arts = [
            Artifact(
                id=a.get("id", ""),
                title=a.get("title", ""),
                mime_type=a.get("type", ""),
                extension=a.get("extension", "txt"),
                final_content=a.get("final_content", ""),
                version_count=int(a.get("version_count", 1)),
            )
            for a in (c.get("artifacts") or [])
        ]
        ir.conversations.append(Conversation(
            uuid=c.get("uuid", ""),
            title=c.get("name", ""),
            messages=msgs,
            artifacts=arts,
            created_at=c.get("created_at", ""),
            updated_at=c.get("updated_at", ""),
            project_uuid=c.get("project_uuid"),
            model=c.get("model"),
            source_platform=d.get("source", "claude-chat"),
        ))

    return ir
