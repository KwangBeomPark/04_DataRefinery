"""CLI wrapper for synthetic sample data generator for Data Refinery DWH pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Ensure repository root is on sys.path when script is executed directly
repo_root = str(Path(__file__).resolve().parent.parent)
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from src.sample_data import generate_sample_data  # noqa: E402

__all__ = ["generate_sample_data"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic sample data for DWH pipeline.")
    parser.add_argument("--base-dir", default=".", help="Base directory of the repository")
    parser.add_argument("--inject-unmapped", action="store_true", help="Inject unmapped customer/product codes to test fail-fast")
    args = parser.parse_args()

    generate_sample_data(base_dir=args.base_dir, inject_unmapped=args.inject_unmapped)
