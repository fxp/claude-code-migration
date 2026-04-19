"""Regression tests for dossier disk round-trip (write → load → apply).

Background: `_rehydrate_dossier` rebuilds dataclass instances from JSON
loaded off disk. If a nested list field is missed, downstream code that
calls `asdict()` on its items crashes with `TypeError: asdict() should
be called on dataclass instances`.

These tests pin down every nested-list field that needs rehydration.
"""
from __future__ import annotations

import json

import pytest

from claude_code_migration.canonical import (
    Artifact,
    Attachment,
    CanonicalData,
    Conversation,
    Document,
    Message,
    Project,
)
from claude_code_migration.__main__ import _rehydrate_dossier


def _roundtrip(d: CanonicalData) -> CanonicalData:
    """Serialize to JSON and rehydrate, the way `_write_dossier` + `_load_dossier` do."""
    return _rehydrate_dossier(json.loads(json.dumps(d.to_dict(), default=str)))


def test_message_attachments_rehydrate_to_dataclass():
    """Regression: Message.attachments was being left as raw dicts after load,
    causing `to_cowork_export()` to crash on `asdict(attachment)`."""
    d = CanonicalData(source_platform="claude-chat")
    d.conversations = [
        Conversation(
            uuid="c1",
            title="t",
            messages=[
                Message(
                    uuid="m1",
                    role="user",
                    content="see attached",
                    attachments=[Attachment(filename="x.png", url="https://x")],
                )
            ],
        )
    ]
    rehydrated = _roundtrip(d)
    att = rehydrated.conversations[0].messages[0].attachments[0]
    assert isinstance(att, Attachment), f"expected Attachment, got {type(att).__name__}"
    # And the export path that previously crashed must now succeed:
    out = rehydrated.to_cowork_export()
    assert out["conversations"][0]["messages"][0]["attachments"][0]["filename"] == "x.png"


def test_conversation_artifacts_rehydrate_to_dataclass():
    d = CanonicalData(source_platform="claude-chat")
    d.conversations = [
        Conversation(
            uuid="c1", title="t",
            artifacts=[Artifact(id="a1", title="hello.md",
                                mime_type="text/markdown", extension="md",
                                final_content="# hi")],
        )
    ]
    rehydrated = _roundtrip(d)
    art = rehydrated.conversations[0].artifacts[0]
    assert isinstance(art, Artifact)


def test_project_docs_rehydrate_to_dataclass():
    d = CanonicalData(source_platform="claude-cowork")
    d.projects = [Project(name="p", slug="p",
                          docs=[Document(filename="readme.md", content="# r")])]
    rehydrated = _roundtrip(d)
    doc = rehydrated.projects[0].docs[0]
    assert isinstance(doc, Document)
    # to_cowork_export does asdict(d) for d in p.docs — must not crash:
    out = rehydrated.to_cowork_export()
    assert out["projects"][0]["docs"][0]["filename"] == "readme.md"


def test_full_roundtrip_with_attachments_can_be_applied():
    """End-to-end: a chat-style dossier with attachments survives load and
    can produce the cowork-export shape that adapters consume."""
    d = CanonicalData(source_platform="claude-chat", source_project_dir=None)
    d.conversations = [
        Conversation(
            uuid="c1", title="t",
            messages=[
                Message(uuid=f"m{i}", role="user", content="hi",
                        attachments=[Attachment(filename=f"f{i}.txt", content="x")])
                for i in range(3)
            ],
            artifacts=[Artifact(id="a1", title="x", mime_type="text/plain",
                                extension="txt", final_content="x")],
        )
    ]
    rehydrated = _roundtrip(d)
    out = rehydrated.to_cowork_export()
    assert len(out["conversations"][0]["messages"]) == 3
    assert all(m["attachments"][0]["filename"].startswith("f")
               for m in out["conversations"][0]["messages"])
