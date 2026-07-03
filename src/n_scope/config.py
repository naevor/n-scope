from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 in CI
    import tomli as tomllib

DEFAULT_DEPTH = 3
DEFAULT_SORT = "status"
DEFAULT_THEME = "textual-dark"
SORT_MODES = frozenset({"name", "status"})


class ConfigError(ValueError):
    """Raised when the n-scope configuration is invalid."""


@dataclass(frozen=True)
class AppConfig:
    roots: tuple[Path, ...] = ()
    depth: int = DEFAULT_DEPTH
    sort: str = DEFAULT_SORT
    theme: str = DEFAULT_THEME


def default_config_path() -> Path:
    return Path("~/.config/n-scope/config.toml").expanduser()


def _validate_config(data: dict[str, Any], path: Path) -> AppConfig:
    allowed = {"roots", "depth", "sort", "theme"}
    unknown = sorted(data.keys() - allowed)
    if unknown:
        names = ", ".join(unknown)
        raise ConfigError(f"{path}: unknown config key(s): {names}")

    raw_roots = data.get("roots", [])
    if not isinstance(raw_roots, list) or not all(
        isinstance(root, str) and root.strip() for root in raw_roots
    ):
        raise ConfigError(f"{path}: roots must be a list of non-empty paths")

    depth = data.get("depth", DEFAULT_DEPTH)
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
        raise ConfigError(f"{path}: depth must be a non-negative integer")

    sort = data.get("sort", DEFAULT_SORT)
    if not isinstance(sort, str) or sort not in SORT_MODES:
        choices = ", ".join(sorted(SORT_MODES))
        raise ConfigError(f"{path}: sort must be one of: {choices}")

    theme = data.get("theme", DEFAULT_THEME)
    if not isinstance(theme, str) or not theme.strip():
        raise ConfigError(f"{path}: theme must be a non-empty string")

    return AppConfig(
        roots=tuple(Path(root).expanduser() for root in raw_roots),
        depth=depth,
        sort=sort,
        theme=theme,
    )


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    if not config_path.exists():
        return AppConfig()
    try:
        with config_path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"could not read {config_path}: {error}") from error
    return _validate_config(data, config_path)
