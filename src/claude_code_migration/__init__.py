"""claude-code-migration — migrate Claude (Code/Chat/Cowork) to any Agent framework."""

__version__ = "0.1.0"

from .scanner import scan_claude_code, save_scan
from .cowork import parse_cowork_zip
from .secrets import scan_secrets

__all__ = ["scan_claude_code", "save_scan", "parse_cowork_zip", "scan_secrets", "__version__"]
