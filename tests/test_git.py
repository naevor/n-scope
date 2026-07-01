from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from n_scope.git import RepoStatus, find_repos, gather_status


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "--quiet")
    git(path, "config", "user.email", "tests@example.com")
    git(path, "config", "user.name", "Test User")


def test_clean_status_excludes_detached_head(tmp_path: Path) -> None:
    assert RepoStatus(path=tmp_path, name="repo").is_clean_and_synced
    assert not RepoStatus(
        path=tmp_path,
        name="repo",
        detached=True,
    ).is_clean_and_synced


def test_find_repos_respects_depth_and_does_not_descend_into_repos(
    tmp_path: Path,
) -> None:
    shallow = tmp_path / "shallow"
    nested = tmp_path / "group" / "nested"
    hidden = tmp_path / ".hidden"
    init_repo(shallow)
    init_repo(nested)
    init_repo(hidden)
    init_repo(shallow / "ignored")

    assert find_repos(tmp_path, 1) == [shallow]
    assert find_repos(tmp_path, 2) == [nested, shallow]


def test_gather_status_counts_worktree_changes(tmp_path: Path) -> None:
    init_repo(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("initial\n", encoding="utf-8")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "--quiet", "-m", "initial commit")

    tracked.write_text("modified\n", encoding="utf-8")
    (tmp_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(tmp_path, "add", "staged.txt")
    (tmp_path / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    status = asyncio.run(gather_status(tmp_path))

    assert status.error == ""
    assert status.modified == 1
    assert status.staged == 1
    assert status.untracked == 1
    assert status.last_commit_summary == "initial commit"
    assert status.last_commit_author == "Test User"


def test_gather_status_supports_repo_without_commits(tmp_path: Path) -> None:
    init_repo(tmp_path)

    status = asyncio.run(gather_status(tmp_path))

    assert status.error == ""
    assert status.branch
    assert not status.detached
    assert status.last_commit_summary == ""
