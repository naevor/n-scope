from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import DataTable, Input

import n_scope.app as app_module
from n_scope.app import NScopeApp, fuzzy_match
from n_scope.git import GitOperationResult, RepoStatus


def test_app_renders_repositories_as_statuses_arrive(
    monkeypatch, tmp_path: Path
) -> None:
    release_second = asyncio.Event()
    paths = [tmp_path / "first", tmp_path / "second"]

    async def progressive_statuses(_paths):
        yield RepoStatus(path=paths[0], name="first")
        await release_second.wait()
        yield RepoStatus(path=paths[1], name="second")

    monkeypatch.setattr(app_module, "find_repos_many", lambda *_args: paths)
    monkeypatch.setattr(app_module, "iter_statuses", progressive_statuses)

    async def run_scenario() -> None:
        app = NScopeApp((tmp_path,), max_depth=3)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#repos", DataTable)
            assert table.row_count == 1

            release_second.set()
            await pilot.pause()
            assert table.row_count == 2

    asyncio.run(run_scenario())


@pytest.mark.parametrize(
    "query, candidate, expected",
    [
        ("scope", "n-scope", True),
        ("NS", "n-scope", True),
        ("nsp", "n-scope", True),
        ("nf", "n-scope", False),
        ("", "n-scope", True),
    ],
)
def test_fuzzy_match(query: str, candidate: str, expected: bool) -> None:
    assert fuzzy_match(query, candidate) is expected


def test_slash_opens_live_filter_and_escape_clears_it(
    monkeypatch, tmp_path: Path
) -> None:
    repos = [
        RepoStatus(path=tmp_path / "n-scope", name="n-scope"),
        RepoStatus(path=tmp_path / "n-feed", name="n-feed"),
        RepoStatus(path=tmp_path / "backend", name="backend"),
    ]

    async def immediate_statuses(_paths):
        for repo in repos:
            yield repo

    monkeypatch.setattr(
        app_module,
        "find_repos_many",
        lambda *_args: [repo.path for repo in repos],
    )
    monkeypatch.setattr(app_module, "iter_statuses", immediate_statuses)

    async def run_scenario() -> None:
        app = NScopeApp((tmp_path,), max_depth=3)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#repos", DataTable)
            filter_input = app.query_one("#filter_input", Input)
            assert table.row_count == 3

            await pilot.press("/")
            await pilot.press("n", "s")
            await pilot.pause()
            assert filter_input.has_class("active")
            assert filter_input.value == "ns"
            assert table.row_count == 1

            await pilot.press("escape")
            await pilot.pause()
            assert not filter_input.has_class("active")
            assert filter_input.value == ""
            assert table.row_count == 3

    asyncio.run(run_scenario())


def test_space_toggles_selection_marker(monkeypatch, tmp_path: Path) -> None:
    repo = RepoStatus(path=tmp_path / "alpha", name="alpha")

    async def immediate_statuses(_paths):
        yield repo

    monkeypatch.setattr(app_module, "find_repos_many", lambda *_args: [repo.path])
    monkeypatch.setattr(app_module, "iter_statuses", immediate_statuses)

    async def run_scenario() -> None:
        app = NScopeApp((tmp_path,), max_depth=3)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#repos", DataTable)

            await pilot.press("space")
            await pilot.pause()
            marker = table.get_cell(str(repo.path), "selected")
            assert repo.path in app.selected_paths
            assert marker.plain == "*"

            await pilot.press("space")
            await pilot.pause()
            marker = table.get_cell(str(repo.path), "selected")
            assert repo.path not in app.selected_paths
            assert marker.plain == ""

    asyncio.run(run_scenario())


def test_copy_path_uses_selected_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = RepoStatus(path=tmp_path / "alpha", name="alpha")
    copied: list[str] = []

    async def immediate_statuses(_paths):
        yield repo

    monkeypatch.setattr(app_module, "find_repos_many", lambda *_args: [repo.path])
    monkeypatch.setattr(app_module, "iter_statuses", immediate_statuses)
    monkeypatch.setattr(
        NScopeApp,
        "copy_to_clipboard",
        lambda _self, text: copied.append(text),
    )

    async def run_scenario() -> None:
        app = NScopeApp((tmp_path,), max_depth=3)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("c")

    asyncio.run(run_scenario())

    assert copied == [str(repo.path)]


def test_open_editor_uses_editor_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = RepoStatus(path=tmp_path / "alpha", name="alpha")
    launched: list[tuple[list[str], Path, str]] = []

    async def immediate_statuses(_paths):
        yield repo

    def capture_launch(
        _self: NScopeApp,
        command: list[str],
        *,
        cwd: Path,
        label: str,
    ) -> None:
        launched.append((command, cwd, label))

    monkeypatch.setenv("EDITOR", "test-editor --flag")
    monkeypatch.setattr(app_module, "find_repos_many", lambda *_args: [repo.path])
    monkeypatch.setattr(app_module, "iter_statuses", immediate_statuses)
    monkeypatch.setattr(NScopeApp, "_run_external_command", capture_launch)

    async def run_scenario() -> None:
        app = NScopeApp((tmp_path,), max_depth=3)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("e")

    asyncio.run(run_scenario())

    assert launched == [(["test-editor", "--flag", str(repo.path)], repo.path, "$EDITOR")]


def test_fetch_confirm_cancel_does_not_run_operation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = RepoStatus(path=tmp_path / "alpha", name="alpha")
    calls: list[tuple[list[Path], str]] = []

    async def immediate_statuses(_paths):
        yield repo

    async def operations(paths, operation):
        calls.append((paths, operation))
        for path in paths:
            yield GitOperationResult(path=path, name=path.name, operation=operation, returncode=0)

    monkeypatch.setattr(app_module, "find_repos_many", lambda *_args: [repo.path])
    monkeypatch.setattr(app_module, "iter_statuses", immediate_statuses)
    monkeypatch.setattr(app_module, "iter_repo_operations", operations)

    async def run_scenario() -> None:
        app = NScopeApp((tmp_path,), max_depth=3)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("f")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()

    asyncio.run(run_scenario())

    assert calls == []


def test_fetch_confirm_runs_selected_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repos = [
        RepoStatus(path=tmp_path / "alpha", name="alpha"),
        RepoStatus(path=tmp_path / "beta", name="beta"),
    ]
    calls: list[tuple[list[Path], str]] = []

    async def immediate_statuses(_paths):
        for repo in repos:
            yield repo

    async def operations(paths, operation):
        calls.append((paths, operation))
        for path in paths:
            yield GitOperationResult(path=path, name=path.name, operation=operation, returncode=0)

    monkeypatch.setattr(
        app_module,
        "find_repos_many",
        lambda *_args: [repo.path for repo in repos],
    )
    monkeypatch.setattr(app_module, "iter_statuses", immediate_statuses)
    monkeypatch.setattr(app_module, "iter_repo_operations", operations)

    async def run_scenario() -> None:
        app = NScopeApp((tmp_path,), max_depth=3)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("space")
            await pilot.press("f")
            await pilot.pause()
            await pilot.press("y")
            for _ in range(5):
                await pilot.pause()

            table = app.query_one("#repos", DataTable)
            operation = table.get_cell(str(repos[0].path), "operation")
            assert operation.plain == "fetch ok"

    asyncio.run(run_scenario())

    assert calls == [([repos[0].path], "fetch")]
