from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class GitRepo:
    path: Path

    def git(self, *args: str) -> str:
        return run_git(self.path, *args)

    def write(self, relative_path: str, content: str) -> Path:
        path = self.path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def commit(self, message: str) -> None:
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", message)


@dataclass(frozen=True)
class TrackingRepos:
    remote: GitRepo
    local: GitRepo
    peer: GitRepo


class GitRepoFactory:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _configure_identity(repo: GitRepo) -> None:
        repo.git("config", "user.email", "tests@example.com")
        repo.git("config", "user.name", "Test User")

    def create(self, name: str, *, initial_commit: bool = True) -> GitRepo:
        path = self.root / name
        path.mkdir(parents=True)
        run_git(path, "init", "--quiet", "--initial-branch=main")
        repo = GitRepo(path)
        self._configure_identity(repo)
        if initial_commit:
            repo.write("tracked.txt", "initial\n")
            repo.commit("initial commit")
        return repo

    def create_bare(self, name: str) -> GitRepo:
        path = self.root / name
        path.mkdir(parents=True)
        run_git(path, "init", "--bare", "--quiet", "--initial-branch=main")
        return GitRepo(path)

    def clone(self, source: GitRepo, name: str) -> GitRepo:
        path = self.root / name
        subprocess.run(
            ["git", "clone", "--quiet", str(source.path), str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        repo = GitRepo(path)
        self._configure_identity(repo)
        return repo

    def create_tracking(self, name: str = "tracking") -> TrackingRepos:
        remote = self.create_bare(f"{name}-remote.git")
        local = self.create(f"{name}-local")
        local.git("remote", "add", "origin", str(remote.path))
        local.git("push", "--quiet", "--set-upstream", "origin", "main")
        peer = self.clone(remote, f"{name}-peer")
        return TrackingRepos(remote=remote, local=local, peer=peer)


@pytest.fixture
def git_repo_factory(tmp_path: Path) -> GitRepoFactory:
    return GitRepoFactory(tmp_path)


@pytest.fixture
def git_repo(git_repo_factory: GitRepoFactory) -> GitRepo:
    return git_repo_factory.create("repo")


@pytest.fixture
def dirty_repo(git_repo: GitRepo) -> GitRepo:
    git_repo.write("tracked.txt", "modified\n")
    git_repo.write("staged.txt", "staged\n")
    git_repo.git("add", "staged.txt")
    git_repo.write("untracked.txt", "untracked\n")
    return git_repo


@pytest.fixture
def tracking_repos(git_repo_factory: GitRepoFactory) -> TrackingRepos:
    return git_repo_factory.create_tracking()


@pytest.fixture
def diverged_repos(tracking_repos: TrackingRepos) -> TrackingRepos:
    tracking_repos.local.write("local.txt", "ahead\n")
    tracking_repos.local.commit("local commit")
    tracking_repos.peer.write("peer.txt", "behind\n")
    tracking_repos.peer.commit("peer commit")
    tracking_repos.peer.git("push", "--quiet", "origin", "main")
    tracking_repos.local.git("fetch", "--quiet", "origin")
    return tracking_repos
