"""claude-code-migration — migrate Claude (Code/Chat/Cowork) to any Agent framework.

Public Python API (everything else is an internal detail that may change):

    from claude_code_migration import scan_claude_code, save_scan
    from claude_code_migration import parse_cowork_zip
    from claude_code_migration import scan_secrets, redact
    from claude_code_migration import WorkspaceDossier  # = CanonicalData

For the CLI, install the package and run `ccm --help`.
"""

__version__ = "1.0.0"

from .canonical import CanonicalData, WorkspaceDossier
from .cowork import parse_cowork_zip
from .redactor import redact
from .scanner import scan_claude_code, save_scan
from .secrets import scan_secrets

__all__ = [
    "__version__",
    # Scanner + save
    "scan_claude_code", "save_scan",
    # Cowork / Chat ZIP parser
    "parse_cowork_zip",
    # Canonical data type (WorkspaceDossier is the user-facing alias)
    "CanonicalData", "WorkspaceDossier",
    # Security
    "scan_secrets", "redact",
]
