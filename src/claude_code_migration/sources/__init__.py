"""Sources: platform → CanonicalData IR.

Each source module exposes `parse(...)` that returns a CanonicalData instance.
Add a new source by dropping a module in here with a parse() function.
"""
from .claude_code import parse as parse_claude_code
from .claude_chat_zip import parse as parse_claude_chat_zip
from .cursor import parse as parse_cursor
from .opencode import parse as parse_opencode
from .hermes import parse as parse_hermes
from .windsurf import parse as parse_windsurf


SOURCES = {
    "claude-code": parse_claude_code,       # ~/.claude/ local scan
    "claude-chat": parse_claude_chat_zip,   # Claude.ai ZIP export
    "claude-cowork": parse_claude_chat_zip, # Same ZIP format, workspace_id detected
    "cursor": parse_cursor,                 # .cursor/rules + .cursor/mcp.json
    "opencode": parse_opencode,             # ~/.config/opencode/ or ./opencode.json
    "hermes": parse_hermes,                 # ~/.hermes/ (SQLite + memories/ + skills/)
    "windsurf": parse_windsurf,             # ~/.codeium/windsurf/ + .windsurfrules
}


def get_source(name: str):
    if name not in SOURCES:
        raise ValueError(f"Unknown source '{name}'. Available: {', '.join(SOURCES)}")
    return SOURCES[name]


__all__ = [
    "SOURCES", "get_source",
    "parse_claude_code", "parse_claude_chat_zip",
    "parse_cursor", "parse_opencode", "parse_hermes", "parse_windsurf",
]
