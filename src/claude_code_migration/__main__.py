"""CLI: claude-code-migration (alias: ccm)"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .scanner import scan_claude_code, save_scan
from .cowork import parse_cowork_zip
from .secrets import scan_secrets
from .adapters import ADAPTERS, get_adapter
from .hub import NeuDriveHub, push_scan_to_hub


def cmd_scan(args: argparse.Namespace) -> int:
    proj = Path(args.project).resolve() if args.project else None
    scan = scan_claude_code(project_dir=proj, include_sessions=not args.no_sessions)
    d = scan.to_dict()

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        save_scan(scan, out_path)
        print(f"✅ scan → {out_path}")
    else:
        # Summary to stdout
        print("=== Claude Code Scan Summary ===")
        print(f"Timestamp: {d['timestamp']}")
        print(f"Claude home: {d['claude_home']}")
        print(f"Project: {d['project_dir']}")
        print(f"CLAUDE.md: {'yes' if d.get('claude_md') else 'no'}")
        print(f"~/.claude/CLAUDE.md: {'yes' if d.get('home_claude_md') else 'no'}")
        print(f"Memory files: {len(d.get('memory') or [])}")
        print(f"Agent memory: {len(d.get('agent_memory') or [])}")
        print(f"Sessions: {len(d.get('sessions') or [])}")
        print(f"Agents: {len(d.get('agents') or [])}")
        print(f"Skills (global): {len(d.get('skills_global') or [])}")
        print(f"Skills (project): {len(d.get('skills_project') or [])}")
        print(f"MCP servers (global): {len(d.get('mcp_servers_global') or {})}")
        print(f"MCP servers (project): {len(d.get('mcp_servers_project') or {})}")
        print(f"Rules: {len(d.get('rules') or [])}")
        print(f"History entries: {d.get('history_count', 0)}")
        secrets = scan_secrets(d)
        print(f"Secrets detected: {len(secrets)}")
        for s in secrets[:5]:
            print(f"  ⚠️  {s.source} [{s.kind}] sha256:{s.sha256_prefix} → ${s.suggested_env_var}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    proj = Path(args.project).resolve() if args.project else None
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Scan
    scan = scan_claude_code(project_dir=proj, include_sessions=not args.no_sessions)
    scan_d = scan.to_dict()
    save_scan(scan, out_dir / "scan.json")

    # Optional cowork ZIP
    cowork_d = None
    if args.cowork_zip:
        cowork = parse_cowork_zip(args.cowork_zip)
        cowork_d = cowork.to_dict()
        (out_dir / "cowork.json").write_text(
            json.dumps(cowork_d, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # Secret report
    secrets = scan_secrets(scan_d)

    # Apply each target
    targets = [t.strip() for t in args.target.split(",") if t.strip()]
    results = []
    for t in targets:
        if t not in ADAPTERS:
            print(f"❌ Unknown target: {t}. Available: {', '.join(ADAPTERS)}", file=sys.stderr)
            return 2
        adapter = get_adapter(t)
        tgt_out = out_dir / f"{t}-target"
        tgt_out.mkdir(exist_ok=True)
        # Safety: by default, DO NOT write into the real project directory.
        # Instead, stage project-root files (like .cursor/rules/, .windsurfrules)
        # inside tgt_out/<project-basename>/. Use --in-place to actually write
        # into `proj`.
        if args.in_place and proj:
            project_root = proj
        elif proj:
            project_root = tgt_out / proj.name
            project_root.mkdir(exist_ok=True)
        else:
            project_root = None
        try:
            r = adapter.apply(scan_d, tgt_out, project_dir=project_root, cowork_export=cowork_d)
            results.append((t, r))
        except Exception as e:
            print(f"❌ {t} adapter failed: {e}", file=sys.stderr)
            raise

    # Report
    print(f"\n═══ Migration Report ═══")
    print(f"Output: {out_dir}")
    print(f"Scan: {out_dir / 'scan.json'}")
    print(f"Secrets detected: {len(secrets)}")
    for t, r in results:
        print(f"\n▸ {t}")
        print(f"  Files written: {len(r.files_written)}")
        print(f"  Env vars needed: {', '.join(r.env_vars_needed.keys()) or '(none)'}")
        for w in r.warnings:
            print(f"  ⚠️  {w}")
        if r.post_install_hint:
            for line in r.post_install_hint.splitlines():
                print(f"  {line}")
    return 0


def cmd_push_hub(args: argparse.Namespace) -> int:
    # Load scan
    scan_path = Path(args.scan)
    if not scan_path.exists():
        print(f"❌ scan.json not found: {scan_path}", file=sys.stderr)
        return 2
    scan_d = json.loads(scan_path.read_text())
    # Optional cowork
    cowork_d = None
    if args.cowork_json:
        cowork_d = json.loads(Path(args.cowork_json).read_text())

    token = args.token or os.environ.get("NEUDRIVE_TOKEN")
    if not token:
        print("❌ --token or NEUDRIVE_TOKEN required", file=sys.stderr)
        return 2

    with NeuDriveHub(base_url=args.api_base, token=token) as hub:
        try:
            who = hub.whoami()
            print(f"✅ Authenticated as: {who}")
        except Exception as e:
            print(f"❌ Auth failed: {e}", file=sys.stderr)
            return 2
        stats = push_scan_to_hub(scan_d, hub, cowork_export=cowork_d)
        print(f"\n═══ Hub Push Report ═══")
        for k, v in stats.items():
            print(f"  {k}: {v}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="claude-code-migration",
        description="Migrate Claude Code/Chat/Cowork to Hermes/OpenCode/Cursor/Windsurf/neuDrive",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # scan
    sp = sub.add_parser("scan", help="Scan local Claude Code data")
    sp.add_argument("--project", help="Project dir (default: cwd)")
    sp.add_argument("--out", help="Write scan.json to path")
    sp.add_argument("--no-sessions", action="store_true", help="Skip JSONL session enumeration")
    sp.set_defaults(func=cmd_scan)

    # migrate
    mp = sub.add_parser("migrate", help="Run migration to one or more targets")
    mp.add_argument("--target", required=True,
                    help=f"Target(s), comma-separated. Options: {', '.join(ADAPTERS)}")
    mp.add_argument("--project", help="Project dir (default: cwd)")
    mp.add_argument("--out", default="./ccm-output", help="Output dir")
    mp.add_argument("--cowork-zip", help="Optional Claude.ai/Cowork export ZIP")
    mp.add_argument("--no-sessions", action="store_true")
    mp.add_argument("--in-place", action="store_true",
                    help="Write project-root files (AGENTS.md, .cursor/rules/, .windsurfrules) "
                         "into the real project dir instead of staging under out-dir. "
                         "⚠️  MODIFIES YOUR PROJECT — use on a clean git branch.")
    mp.set_defaults(func=cmd_migrate)

    # push-hub
    hp = sub.add_parser("push-hub", help="Push a scan.json to neuDrive Hub")
    hp.add_argument("--scan", required=True, help="Path to scan.json")
    hp.add_argument("--cowork-json", help="Optional cowork.json from migrate")
    hp.add_argument("--api-base", default="https://www.neudrive.ai")
    hp.add_argument("--token", help="neuDrive token (or NEUDRIVE_TOKEN env)")
    hp.set_defaults(func=cmd_push_hub)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
