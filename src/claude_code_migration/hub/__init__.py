"""Hub mode for claude-code-migration.

`ccm` on its own is a one-shot pipeline: scan → Workspace Dossier →
apply to another agent. The `hub` subpackage is the always-on sibling:
captures stream real-time into a Supabase-backed data store, MCP tools
let any agent read the data back with <1ms latency, and an offline-first
SQLite buffer keeps the whole thing working on an airplane.

Quick-start (from the CLI)::

    ccm hub init                        # create ~/.dossier-hub/ + buffer
    ccm hub serve --local-only          # daemon, no cloud
    ccm hub serve --remote              # requires SUPABASE_URL env vars
    ccm hub status                      # outbox size, dead-letter count

Public Python surface (may grow)::

    from claude_code_migration.hub import LocalBuffer   # L4 SQLite buffer
    from claude_code_migration.hub import Redactor      # capture middleware
    from claude_code_migration.hub.captures import Capture  # base class
    from claude_code_migration.hub.daemon import HubDaemon, HubConfig

See `docs/HUB_ARCHITECTURE.md` for the full layered design.

Imports require the `hub` extra: ``pip install 'claude-code-migration[hub]'``
(for watchdog/supabase/psycopg). The core `ccm` CLI (export/apply/migrate/
panic-backup/scan/push-hub) works without these extras.
"""
from __future__ import annotations

from .buffer import LocalBuffer
from .redact import Redactor

__all__ = ["LocalBuffer", "Redactor"]
