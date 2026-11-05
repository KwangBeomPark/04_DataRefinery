"""Data Refinery desktop application launch runner.

Use this entrypoint to test-run the Data Refinery GUI application during development.
Supports direct execution in VS Code (Play button), terminal, or debugger.
"""

# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.data_refinery import main

if __name__ == "__main__":
    main()
