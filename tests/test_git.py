from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import n_scope.git as git_module
from n_scope.git import (
    RepoStatus,
    find_repos,
    find_repos_many,
    gather_all,
    gather_status,
    iter_statuses,
)


def status(path: Path) -> RepoStatus:
    return asyncio.run(gather_status(path))


def test_clean_and_synced_requires_upstream(tmp_path: Path) -> None:
    synced = RepoStatus(path=tmp_path, name="repo", upstream="origin/main")

    assert synced.is_clean_and_synced
    assert not RepoStatus(path=tmp_path, name="repo").is_clean_and_synced
    assert not RepoStatus(
        path=tmp_path,
        name="repo",
        upstream="origin/main",
        detached=True,
    ).is_clean_and_synced
    assert not RepoStatus(
        path=tmp_path,
        name="repo",
        upstream="origin/main",
        bare=True,
    ).is_clean_and_synced


def test_gather_status_reports_clean_tracking_repo(tracking_repos) -> None:
    result = status(tracking_repos.local.path)

    assert result.error == ""
    assert result.upstream == "origin/main"
    assert result.is_clean_and_synced
    assert not result.is_dirty
    assert result.ahead == 0
    assert result.behind == 0


def test_gather_status_counts_worktree_changes(dirty_repo) -> None:
    result = status(dirty_repo.path)

    assert result.error == ""
    assert result.modified == 1
    assert result.staged == 1
    assert result.untracked == 1
    assert result.last_commit_summary == "initial commit"
    assert result.last_commit_author == "Test User"


def test_gather_status_preserves_leading_space_in_porcelain(git_repo) -> None:
    git_repo.write("tracked.txt", "modified only\n")

    result = status(git_repo.path)

    assert result.modified == 1
    assert result.staged == 0


def test_gather_status_reports_ahead_commit(tracking_repos) -> None:
    tracking_repos.local.write("ahead.txt", "ahead\n")
    tracking_repos.local.commit("ahead commit")

    result = status(tracking_repos.local.path)

    assert result.ahead == 1
    assert result.behind == 0


def test_gather_status_reports_behind_commit(tracking_repos) -> None:
    tracking_repos.peer.write("behind.txt", "behind\n")
    tracking_repos.peer.commit("behind commit")
    tracking_repos.peer.git("push", "--quiet", "origin", "main")
    tracking_repos.local.git("fetch", "--quiet", "origin")

    result = status(tracking_repos.local.path)

    assert result.ahead == 0
    assert result.behind == 1


def test_gather_status_reports_diverged_history(diverged_repos) -> None:
    result = status(diverged_repos.local.path)

    assert result.ahead == 1
    assert result.behind == 1


def test_gather_status_handles_repo_without_upstream(git_repo) -> None:
    result = status(git_repo.path)

    assert result.error == ""
    assert result.upstream == ""
    assert not result.is_clean_and_synced


def test_gather_status_handles_repo_without_commits(git_repo_factory) -> None:
    repo = git_repo_factory.create("empty", initial_commit=False)

    result = status(repo.path)

    assert result.error == ""
    assert result.branch == "main"
    assert not result.detached
    assert result.last_commit_summary == ""


def test_gather_status_handles_bare_repo(git_repo_factory) -> None:
    repo = git_repo_factory.create_bare("remote.git")

    result = status(repo.path)

    assert result.error == ""
    assert result.bare
    assert result.branch == "main"
    assert not result.is_dirty


def test_find_repos_detects_bare_repo(git_repo_factory, tmp_path: Path) -> None:
    bare = git_repo_factory.create_bare("remote.git")

    assert find_repos(tmp_path, 1) == [bare.path]


def test_find_repos_detects_worktree_git_file(
    git_repo_factory, tmp_path: Path
) -> None:
    repo = git_repo_factory.create("primary")
    worktree = tmp_path / "worktree"
    repo.git("worktree", "add", "--quiet", "-b", "worktree-branch", str(worktree))

    assert (worktree / ".git").is_file()
    assert find_repos(tmp_path, 1) == [repo.path, worktree]
    assert status(worktree).error == ""


def test_find_repos_stops_at_top_level_repo_and_skips_submodule(
    git_repo_factory, tmp_path: Path
) -> None:
    parent = git_repo_factory.create("parent")
    source = git_repo_factory.create("module-source")
    submodule = parent.path / "modules" / "child"
    parent.git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(source.path),
        "modules/child",
    )

    repos = find_repos(tmp_path, 3)

    assert parent.path in repos
    assert source.path in repos
    assert submodule not in repos
    assert (submodule / ".git").is_file()


def test_find_repos_respects_depth_and_ignores_hidden_directories(
    git_repo_factory, tmp_path: Path
) -> None:
    shallow = git_repo_factory.create("shallow")
    nested = git_repo_factory.create("group/nested")
    git_repo_factory.create(".hidden")

    assert find_repos(tmp_path, 1) == [shallow.path]
    assert find_repos(tmp_path, 2) == [nested.path, shallow.path]


def test_find_repos_many_deduplicates_overlapping_roots(
    git_repo_factory, tmp_path: Path
) -> None:
    first = git_repo_factory.create("first")
    second = git_repo_factory.create("second")

    repos = find_repos_many((tmp_path, first.path, tmp_path), max_depth=1)

    assert repos == [first.path.resolve(), second.path.resolve()]


def test_find_repos_skips_directory_when_permission_is_denied(
    git_repo_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    visible = git_repo_factory.create("visible")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path == blocked:
            raise PermissionError("denied for test")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)

    assert find_repos(tmp_path, 2) == [visible.path]


def test_gather_status_reports_git_permission_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def permission_denied(*_args):
        return 128, "", "fatal: Permission denied"

    monkeypatch.setattr(git_module, "run_git", permission_denied)

    result = status(tmp_path)

    assert result.error == "Permission denied"


def test_gather_all_limits_concurrency_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    active = 0
    peak = 0

    async def measured_status(path: Path) -> RepoStatus:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return RepoStatus(path=path, name=path.name)

    monkeypatch.setattr(git_module, "gather_status", measured_status)
    paths = [tmp_path / str(index) for index in range(20)]

    results = asyncio.run(gather_all(paths, limit=3))

    assert peak == 3
    assert [result.path for result in results] == paths


def test_gather_all_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        asyncio.run(gather_all([], limit=0))


def test_iter_statuses_yields_results_as_they_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def delayed_status(path: Path) -> RepoStatus:
        await asyncio.sleep(int(path.name) * 0.01)
        return RepoStatus(path=path, name=path.name)

    async def collect(paths: list[Path]) -> list[RepoStatus]:
        return [result async for result in iter_statuses(paths, limit=3)]

    monkeypatch.setattr(git_module, "gather_status", delayed_status)
    paths = [tmp_path / name for name in ("3", "1", "2")]

    results = asyncio.run(collect(paths))

    assert [result.name for result in results] == ["1", "2", "3"]


def test_iter_statuses_rejects_invalid_limit() -> None:
    async def collect() -> list[RepoStatus]:
        return [result async for result in iter_statuses([], limit=0)]

    with pytest.raises(ValueError, match="at least 1"):
        asyncio.run(collect())
