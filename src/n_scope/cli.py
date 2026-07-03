from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .app import NScopeApp
from .config import SORT_MODES, AppConfig, ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="n-scope",
        description="Pretty TUI for surveying git repositories.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="root paths to scan (config roots or current directory by default)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=None,
        help="max recursion depth",
    )
    parser.add_argument(
        "--sort",
        choices=sorted(SORT_MODES),
        default=None,
        help="initial repository sort",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help="Textual theme name",
    )
    return parser


def resolve_options(
    args: argparse.Namespace,
    config: AppConfig,
    *,
    cwd: Path | None = None,
) -> AppConfig:
    raw_roots = tuple(Path(path) for path in args.paths) or config.roots
    if not raw_roots:
        raw_roots = (cwd or Path.cwd(),)

    roots: list[Path] = []
    seen: set[Path] = set()
    for raw_root in raw_roots:
        root = raw_root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"not a directory: {root}")
        if root not in seen:
            seen.add(root)
            roots.append(root)

    depth = args.depth if args.depth is not None else config.depth
    if depth < 0:
        raise ValueError("--depth must be zero or greater")

    return replace(
        config,
        roots=tuple(roots),
        depth=depth,
        sort=args.sort or config.sort,
        theme=args.theme or config.theme,
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        options = resolve_options(args, load_config())
        app = NScopeApp(
            roots=options.roots,
            max_depth=options.depth,
            sort_mode=options.sort,
            theme=options.theme,
        )
    except (ConfigError, ValueError) as error:
        parser.error(str(error))
    app.run()
