from __future__ import annotations

from pathlib import Path

import pytest

from n_scope.config import AppConfig, ConfigError, load_config


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "missing.toml") == AppConfig()


def test_load_config_reads_supported_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '\n'.join(
            [
                'roots = ["~/projects", "~/work"]',
                "depth = 5",
                'sort = "name"',
                'theme = "nord"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.roots == (
        Path("~/projects").expanduser(),
        Path("~/work").expanduser(),
    )
    assert config.depth == 5
    assert config.sort == "name"
    assert config.theme == "nord"


@pytest.mark.parametrize(
    "content, message",
    [
        ('unknown = true\n', "unknown config key"),
        ('roots = "~/projects"\n', "roots must be a list"),
        ('roots = [""]\n', "roots must be a list"),
        ("depth = -1\n", "depth must be a non-negative integer"),
        ("depth = true\n", "depth must be a non-negative integer"),
        ('sort = "recent"\n', "sort must be one of"),
        ("sort = []\n", "sort must be one of"),
        ('theme = ""\n', "theme must be a non-empty string"),
    ],
)
def test_load_config_rejects_invalid_values(
    tmp_path: Path, content: str, message: str
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(config_path)


def test_load_config_wraps_toml_syntax_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("roots = [\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="could not read"):
        load_config(config_path)
