"""Tests for the 2026 CLAUDE.md discovery spec coverage.

Reference: https://code.claude.com/docs/en/memory

The scanner must discover CLAUDE.md files from multiple locations:
  - ./CLAUDE.md                    (existing)
  - ./CLAUDE.local.md              (existing)
  - ./.claude/CLAUDE.md            (2026 alternate project location)
  - ancestor dirs (walks up)       (2026 concatenation semantics)
  - subdir CLAUDE.md (lazy-load)   (archival value)
  - @import references (≤ 5 hops)  (spec: recursive expansion)
  - managed policy (OS-specific)   (enterprise feature)
"""
from __future__ import annotations

from pathlib import Path

from claude_code_migration.scanner import (
    _expand_claude_md_imports,
    _walk_ancestor_claude_mds,
    _walk_subdir_claude_mds,
    scan_claude_code,
)


def test_scans_dot_claude_claude_md(tmp_path: Path) -> None:
    """`./.claude/CLAUDE.md` is a valid project-level location per 2026 spec."""
    proj = tmp_path / "myproj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "CLAUDE.md").write_text("Alt project-level instructions\n")

    s = scan_claude_code(project_dir=proj, include_sessions=False,
                          include_env_reproduction=False)
    assert s.project_claude_md_dotclaude == "Alt project-level instructions\n"


def test_walks_ancestor_claude_mds(tmp_path: Path) -> None:
    """Claude Code concatenates CLAUDE.md files from every ancestor directory."""
    grandparent = tmp_path / "org"
    parent = grandparent / "team"
    proj = parent / "repo"
    proj.mkdir(parents=True)
    (grandparent / "CLAUDE.md").write_text("ORG-WIDE\n")
    (parent / "CLAUDE.md").write_text("TEAM\n")
    (parent / "CLAUDE.local.md").write_text("TEAM-LOCAL\n")

    found = _walk_ancestor_claude_mds(proj)
    # We should see both team CLAUDE.md + CLAUDE.local.md + org CLAUDE.md;
    # order: farthest ancestor first.
    contents = [m.content for m in found]
    paths = [m.path for m in found]
    assert any("ORG-WIDE" in c for c in contents)
    assert any("TEAM\n" == c for c in contents)
    assert any("TEAM-LOCAL" in c for c in contents)
    # Reverse-ordering sanity: team dir files appear after the org file
    org_idx = next(i for i, p in enumerate(paths) if "org/CLAUDE.md" in p)
    team_idx = next(i for i, p in enumerate(paths) if "team/CLAUDE.md" in p)
    assert org_idx < team_idx


def test_enumerates_subdir_claude_mds(tmp_path: Path) -> None:
    """Subdirectory CLAUDE.md files get enumerated (lazy-loaded by Claude Code)."""
    proj = tmp_path / "myproj"
    (proj / "src" / "api").mkdir(parents=True)
    (proj / "src" / "CLAUDE.md").write_text("src rules\n")
    (proj / "src" / "api" / "CLAUDE.md").write_text("api rules\n")
    # Skip dirs we explicitly ignore
    (proj / "node_modules" / "foo").mkdir(parents=True)
    (proj / "node_modules" / "foo" / "CLAUDE.md").write_text("should be skipped\n")

    found = _walk_subdir_claude_mds(proj)
    rels = [m.file for m in found]
    assert any(r.endswith("src/CLAUDE.md") for r in rels)
    assert any(r.endswith("src/api/CLAUDE.md") for r in rels)
    assert not any("node_modules" in r for r in rels)


def test_expands_at_import_references(tmp_path: Path) -> None:
    """`@path/to/file` imports expand recursively up to 5 hops."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    main = proj / "CLAUDE.md"
    docs = proj / "docs"
    docs.mkdir()
    main.write_text("See @docs/git.md for workflow\n")
    (docs / "git.md").write_text("# Git workflow\nThen see @deep/nested.md\n")
    (docs / "deep").mkdir()
    (docs / "deep" / "nested.md").write_text("Deep file\n")

    imports = _expand_claude_md_imports([(main, main.read_text())])
    paths = [Path(m.path).name for m in imports]
    assert "git.md" in paths
    assert "nested.md" in paths


def test_at_import_respects_depth_limit(tmp_path: Path) -> None:
    """Cycle / deep chain: recursion stops at 5 hops."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    # Build a 10-deep chain a.md → b.md → ... → j.md
    chain = list("abcdefghij")
    for cur, nxt in zip(chain, chain[1:]):
        (proj / f"{cur}.md").write_text(f"See @{nxt}.md\n")
    (proj / "j.md").write_text("end\n")

    seed = (proj / "a.md", (proj / "a.md").read_text())
    imports = _expand_claude_md_imports([seed], depth=5)
    names = sorted(Path(m.path).name for m in imports)
    # Starting from a.md at hop 0, b/c/d/e/f can be discovered (5 hops).
    # g/h/i/j should NOT appear.
    assert "b.md" in names
    assert "f.md" in names
    assert "g.md" not in names
    assert "j.md" not in names


def test_at_import_skips_fenced_code_blocks(tmp_path: Path) -> None:
    """`@foo` inside a fenced code block is NOT treated as an import."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    main = proj / "CLAUDE.md"
    main.write_text(
        "Real: @real.md\n\n"
        "```\n"
        "echo @fake.md\n"
        "```\n"
    )
    (proj / "real.md").write_text("real content\n")
    (proj / "fake.md").write_text("should NOT be imported\n")

    imports = _expand_claude_md_imports([(main, main.read_text())])
    names = [Path(m.path).name for m in imports]
    assert "real.md" in names
    assert "fake.md" not in names


def test_scan_wires_claude_md_tree_to_dossier(tmp_path: Path) -> None:
    """End-to-end: scanning a project populates all the new fields."""
    proj = tmp_path / "myproj"
    (proj / ".claude").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("root rules\nSee @helper.md\n")
    (proj / ".claude" / "CLAUDE.md").write_text("dot-claude rules\n")
    (proj / "helper.md").write_text("helper content\n")
    (proj / "sub").mkdir()
    (proj / "sub" / "CLAUDE.md").write_text("sub rules\n")

    s = scan_claude_code(project_dir=proj, include_sessions=False,
                          include_env_reproduction=False)
    assert s.project_claude_md_dotclaude == "dot-claude rules\n"
    assert any("sub" in m.file for m in s.subdir_claude_mds)
    assert any(Path(m.path).name == "helper.md" for m in s.claude_md_imports)


def test_adapter_archive_dumps_claude_md_tree(tmp_path: Path) -> None:
    """Adapter _archive/claude-md-tree/ preserves every discovered location."""
    from claude_code_migration.adapters.base import write_archive

    scan = {
        "claude_md_tree": {
            "project_dotclaude": "alt root content\n",
            "ancestors": [{"file": "ancestor:X", "path": "/tmp/org/CLAUDE.md",
                           "content": "org\n", "type": "ancestor-claude-md",
                           "frontmatter": {}}],
            "subdirs": [{"file": "subdir:src/CLAUDE.md", "path": "/tmp/p/src/CLAUDE.md",
                          "content": "src\n", "type": "subdir-claude-md",
                          "frontmatter": {}}],
            "imports": [{"file": "@import:docs/x.md", "path": "/tmp/p/docs/x.md",
                          "content": "imp\n", "type": "claude-md-import",
                          "frontmatter": {}}],
            "managed_policy": {"path": "/etc/claude-code/CLAUDE.md",
                                "content": "ENTERPRISE POLICY\n"},
        }
    }
    write_archive(tmp_path, scan)
    tree_dir = tmp_path / "_archive" / "claude-md-tree"
    assert tree_dir.is_dir()
    assert (tree_dir / "project_dotclaude.md").read_text() == "alt root content\n"
    assert (tree_dir / "managed_policy.md").read_text() == "ENTERPRISE POLICY\n"
    idx = (tree_dir / "INDEX.md").read_text()
    assert "project_dotclaude.md" in idx
    assert "managed_policy.md" in idx
