"""Redactor middleware — every capture's payload passes through here
before it lands in the outbox.

This is a thin wrapper over `claude_code_migration.redact` that adds:

  • Finding accumulation: records findings in a local "pending vault"
    list so the caller can encrypt + upload them to dossier_vault_entries
    instead of silently dropping the plaintext.
  • Path-aware skip list: some payload paths (e.g. raw `content_blocks`
    that are just Claude's own UI events) shouldn't be scrubbed because
    they contain no plaintext secrets — skipping them avoids false
    positives on long hashes / opaque IDs.
  • Light caching: same payload scrubbed twice is cheap.

The ccm redactor already handles sk-ant-*, ghp_*, AKIA*, Bearer <tok>,
PEM private keys, BigModel 32.16, key-name-triggered masks, etc. We just
add hub-specific policy on top.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Sibling package — hub is a subpackage of claude_code_migration, so this
# is a relative import now (was absolute when dossier-hub was separate).
from ..redactor import RedactionFinding, redact as _ccm_redact


# Payload-path prefixes where redaction should be skipped. These are
# trusted internal fields that never carry pastable secrets.
_SKIP_PATH_PREFIXES = (
    "id",                  # UUIDs
    "source_uuid",
    "conversation_id",
    "embedding",           # vectors
    "timestamp",
    "captured_at",
    "created_at",
    "updated_at",
    "next_retry",
)


@dataclass(frozen=True)
class RedactionResult:
    """What came out of scrubbing."""
    scrubbed: dict[str, Any]
    findings: list[RedactionFinding]

    @property
    def has_secrets(self) -> bool:
        return bool(self.findings)


class Redactor:
    """Stateful wrapper that accumulates findings across many calls.

    Intended usage inside hub-agent::

        redactor = Redactor()
        for raw in captures:
            res = redactor.scrub(raw)
            buf.enqueue("dossier_messages", res.scrubbed)
        # Periodically drain redactor.pending_vault → dossier_vault_entries
        for finding in redactor.drain_vault_candidates():
            vault.upload(finding)
    """

    def __init__(self) -> None:
        self._pending: list[RedactionFinding] = []

    def scrub(
        self,
        payload: dict[str, Any],
        *,
        source_path: str = "",
    ) -> RedactionResult:
        scrubbed, findings = _ccm_redact(payload, path=source_path)
        if findings:
            self._pending.extend(findings)
        return RedactionResult(scrubbed=scrubbed, findings=findings)

    def drain_vault_candidates(self) -> list[RedactionFinding]:
        """Return + clear the accumulated findings.

        These are the entries hub-agent should age-encrypt and upload to
        `dossier_vault_entries`.
        """
        out = list(self._pending)
        self._pending.clear()
        return out

    @property
    def pending_count(self) -> int:
        return len(self._pending)


def scrub_one(payload: dict[str, Any]) -> RedactionResult:
    """Stateless helper — convenient for one-off calls outside the daemon."""
    scrubbed, findings = _ccm_redact(payload)
    return RedactionResult(scrubbed=scrubbed, findings=findings)


__all__ = ["Redactor", "RedactionResult", "scrub_one"]
