from __future__ import annotations

import argparse
from pathlib import Path

from .app import NScopeApp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="n-scope",
        description="Pretty TUI for surveying git repositories.",
    )
    parser.add_argument("path", nargs="?", default=".", help="root path to scan")
    parser.add_argument(
        "--depth", type=int, default=3,
        help="max recursion depth (default: 3)",
    )
    args = parser.parse_args()

    if args.depth < 0:
        parser.error("--depth must be zero or greater")

    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    NScopeApp(root=root, max_depth=args.depth).run()
