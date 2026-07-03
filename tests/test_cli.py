from __future__ import annotations

from pathlib import Path

import pytest

from n_scope.cli import build_parser, resolve_options
from n_scope.config import AppConfig


def test_resolve_options_uses_current_directory_by_default(tmp_path: Path) -> None:
    args = build_parser().parse_args([])

    options = resolve_options(args, AppConfig(), cwd=tmp_path)

    assert options.roots == (tmp_path.resolve(),)
    assert options.depth == 3
    assert options.sort == "status"
    assert options.theme == "textual-dark"


def test_resolve_options_uses_config_when_cli_is_omitted(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    config = AppConfig(
        roots=(first, second),
        depth=5,
        sort="name",
        theme="nord",
    )

    options = resolve_options(build_parser().parse_args([]), config)

    assert options.roots == (first.resolve(), second.resolve())
    assert options.depth == 5
    assert options.sort == "name"
    assert options.theme == "nord"


def test_cli_values_override_config_and_deduplicate_roots(tmp_path: Path) -> None:
    configured = tmp_path / "configured"
    cli_root = tmp_path / "cli"
    configured.mkdir()
    cli_root.mkdir()
    config = AppConfig(roots=(configured,), depth=5, sort="status", theme="nord")
    args = build_parser().parse_args(
        [
            str(cli_root),
            str(cli_root),
            "--depth",
            "1",
            "--sort",
            "name",
            "--theme",
            "dracula",
        ]
    )

    options = resolve_options(args, config)

    assert options.roots == (cli_root.resolve(),)
    assert options.depth == 1
    assert options.sort == "name"
    assert options.theme == "dracula"


def test_resolve_options_rejects_missing_root(tmp_path: Path) -> None:
    args = build_parser().parse_args([str(tmp_path / "missing")])

    with pytest.raises(ValueError, match="not a directory"):
        resolve_options(args, AppConfig())


def test_resolve_options_rejects_negative_depth(tmp_path: Path) -> None:
    args = build_parser().parse_args([str(tmp_path), "--depth", "-1"])

    with pytest.raises(ValueError, match="zero or greater"):
        resolve_options(args, AppConfig())
