"""Hub subcommand group for the ccm CLI.

Wired up from `claude_code_migration.__main__` via `add_hub_subparser(sub)`.
All verbs live under ``ccm hub <verb>``::

    ccm hub init           — create buffer + sample config
    ccm hub serve          — run the daemon in foreground
    ccm hub status         — buffer stats (outbox / dead-letter / sync watermark)
    ccm hub migrate        — apply sql/*.sql to your Supabase project
    ccm hub bootstrap      — first-run mirror pull Supabase → L4
    ccm hub drain-once     — flush outbox once, exit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .buffer import LocalBuffer
from .daemon import HubConfig, HubDaemon
from .drain import DrainWorker
from .redact import Redactor
from .supabase_client import DryRunClient, HubClient, InMemoryClient, SupabaseClient


def _resolve_backend(args: argparse.Namespace) -> str:
    if getattr(args, "dry_run", False):
        return "dry-run"
    if getattr(args, "local_only", False):
        return "in-memory"
    if getattr(args, "remote", False) or os.environ.get("SUPABASE_URL"):
        return "supabase"
    return "in-memory"


def _buffer_path(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "buffer", None) or "~/.dossier-hub/buffer.db").expanduser()


def _sql_dir() -> Path:
    """Locate the bundled sql/ directory. Works both for editable installs
    (repo layout) and wheel installs (package-data under hub/sql/)."""
    pkg_sql = Path(__file__).parent / "sql"
    if pkg_sql.is_dir():
        return pkg_sql
    # Repo layout fallback (probably never hit once sql is moved into hub/)
    repo_sql = Path(__file__).parent.parent.parent.parent / "sql"
    return repo_sql


# ── Handlers ─────────────────────────────────────────────────────────

def cmd_hub_init(args: argparse.Namespace) -> int:
    buffer_path = _buffer_path(args)
    buffer_path.parent.mkdir(parents=True, exist_ok=True)
    LocalBuffer(buffer_path).close()
    print(f"✅ buffer initialized: {buffer_path}")
    cfg = {
        "buffer_path": str(buffer_path),
        "enabled_captures": ["claude_code_fs"],
        "backend": "in-memory",
    }
    cfg_path = buffer_path.parent / "config.example.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))
    print(f"✅ sample config: {cfg_path}")
    print()
    print("Next step:")
    print("  ccm hub serve               # in-memory mode, no cloud")
    print("  ccm hub serve --remote      # requires SUPABASE_URL + SUPABASE_SERVICE_KEY env vars")
    return 0


def cmd_hub_serve(args: argparse.Namespace) -> int:
    config = HubConfig(
        buffer_path=_buffer_path(args),
        enabled_captures=args.captures.split(",") if args.captures else ["claude_code_fs"],
        backend=_resolve_backend(args),
        enable_mirror=not args.no_mirror,
        enable_drain=not args.no_drain,
    )
    daemon = HubDaemon(config)
    daemon.start()
    try:
        daemon.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()
    return 0


def cmd_hub_status(args: argparse.Namespace) -> int:
    buffer_path = _buffer_path(args)
    if not buffer_path.exists():
        print(f"❌ no buffer at {buffer_path}; run `ccm hub init` first", file=sys.stderr)
        return 2
    buf = LocalBuffer(buffer_path)
    try:
        from .. import __version__
    except ImportError:
        __version__ = "unknown"
    print(json.dumps({
        "version":     __version__,
        "buffer":      str(buffer_path),
        "outbox":      buf.outbox_size(),
        "dead_letter": buf.dead_letter_count(),
        "last_mirror_sync_epoch": buf.get_state("last_mirror_sync_epoch", "0"),
    }, indent=2))
    return 0


def cmd_hub_migrate(args: argparse.Namespace) -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("❌ SUPABASE_URL + SUPABASE_SERVICE_KEY must be set", file=sys.stderr)
        return 2
    try:
        import psycopg  # type: ignore
    except ImportError:
        print("❌ psycopg not installed. pip install 'claude-code-migration[hub]'",
              file=sys.stderr)
        return 2

    conn_str = os.environ.get("SUPABASE_DB_URL")
    if not conn_str:
        print(
            "❌ SUPABASE_DB_URL must be set (e.g. "
            "postgresql://postgres:<pw>@db.<proj>.supabase.co:5432/postgres)",
            file=sys.stderr,
        )
        print(
            "   Find it in Supabase Dashboard → Project Settings → Database → Connection string.",
            file=sys.stderr,
        )
        return 2

    sql_dir = _sql_dir()
    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        print(f"❌ no SQL files in {sql_dir}", file=sys.stderr)
        return 2

    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            for f in files:
                print(f"▶ applying {f.name}", file=sys.stderr)
                cur.execute(f.read_text())
        conn.commit()
    print(f"✅ migrated {len(files)} SQL file(s) to {url}")
    return 0


def cmd_hub_bootstrap(args: argparse.Namespace) -> int:
    buffer_path = _buffer_path(args)
    buf = LocalBuffer(buffer_path)

    backend = _resolve_backend(args)
    if backend != "supabase":
        print("❌ bootstrap requires --remote (SUPABASE_URL + SUPABASE_SERVICE_KEY)",
              file=sys.stderr)
        return 2
    client = SupabaseClient.from_env()

    from .mirror import MirrorSync
    mirror = MirrorSync(buf, client)
    mirror.bootstrap()
    print(json.dumps({
        "bootstrap_rows": mirror.stats["bootstrap_rows"],
        "last_mirror_sync_epoch": buf.get_state("last_mirror_sync_epoch", "0"),
    }, indent=2))
    return 0


def cmd_hub_drain_once(args: argparse.Namespace) -> int:
    buffer_path = _buffer_path(args)
    buf = LocalBuffer(buffer_path)

    backend = _resolve_backend(args)
    if backend == "supabase":
        client: HubClient = SupabaseClient.from_env()
    elif backend == "dry-run":
        client = DryRunClient()
    else:
        client = InMemoryClient()

    worker = DrainWorker(buf, client)
    budget = args.max_batches
    while budget > 0:
        batch = buf.peek_due(limit=50)
        if not batch:
            break
        for entry in batch:
            worker._try_one(entry)
        budget -= 1

    print(json.dumps(worker.snapshot(), indent=2))
    return 0


# ── Subparser registration ───────────────────────────────────────────

def add_hub_subparser(parent_sub: argparse._SubParsersAction) -> None:
    """Attach `ccm hub <verb>` to the top-level ccm subparser.

    Called from `claude_code_migration.__main__.main()` at argparse build time.
    """
    hub = parent_sub.add_parser(
        "hub",
        help="Always-on hub mode · daemon, captures, Supabase backend",
        description=(
            "Hub mode — continuous capture of your Workspace Dossier into a "
            "Supabase-backed data store. Captures stream real-time, an "
            "offline-first SQLite buffer keeps everything working without "
            "network, and MCP tools (coming soon) let any agent read the "
            "data back with <1ms latency."
        ),
    )
    hub.add_argument(
        "--buffer",
        help="Path to L4 buffer db (default ~/.dossier-hub/buffer.db)",
    )
    sub = hub.add_subparsers(dest="hub_cmd", required=True)

    # init
    ip = sub.add_parser("init", help="Create buffer + sample config")
    ip.set_defaults(func=cmd_hub_init)

    # serve
    sp = sub.add_parser("serve", help="Run the hub daemon in the foreground")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--local-only", action="store_true",
                   help="No cloud backend; outbox fills forever (good for debugging)")
    g.add_argument("--remote", action="store_true",
                   help="Connect to Supabase (SUPABASE_URL + SUPABASE_SERVICE_KEY env vars)")
    g.add_argument("--dry-run", action="store_true",
                   help="Log intended Supabase calls to stderr, don't send")
    sp.add_argument("--captures", help="Comma-separated capture names (default: claude_code_fs)")
    sp.add_argument("--no-mirror", action="store_true",
                    help="Disable realtime mirror sync (capture-only mode)")
    sp.add_argument("--no-drain", action="store_true",
                    help="Disable drain worker (buffer-only mode; outbox grows)")
    sp.set_defaults(func=cmd_hub_serve)

    # status
    stp = sub.add_parser("status", help="Print buffer stats")
    stp.set_defaults(func=cmd_hub_status)

    # migrate
    mp = sub.add_parser("migrate", help="Apply sql/*.sql to your Supabase project")
    mp.set_defaults(func=cmd_hub_migrate)

    # bootstrap
    bp = sub.add_parser("bootstrap", help="Pull everything from Supabase → L4 mirror")
    bp.add_argument("--remote", action="store_true", default=True)
    bp.set_defaults(func=cmd_hub_bootstrap)

    # drain-once
    dp = sub.add_parser("drain-once", help="Flush the outbox once and exit")
    g2 = dp.add_mutually_exclusive_group()
    g2.add_argument("--local-only", action="store_true")
    g2.add_argument("--remote", action="store_true")
    g2.add_argument("--dry-run", action="store_true")
    dp.add_argument("--max-batches", type=int, default=100)
    dp.set_defaults(func=cmd_hub_drain_once)


__all__ = ["add_hub_subparser"]
