from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import DataTable, Input

import n_scope.app as app_module
from n_scope.app import NScopeApp, fuzzy_match
from n_scope.git import RepoStatus


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
