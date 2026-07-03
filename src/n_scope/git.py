from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONCURRENCY = 16


@dataclass
class RepoStatus:
    path: Path
    name: str
    branch: str = ""
    detached: bool = False
    bare: bool = False
    upstream: str = ""
    modified: int = 0
    staged: int = 0
    untracked: int = 0
    ahead: int = 0
    behind: int = 0
    stashes: int = 0
    last_commit_summary: str = ""
    last_commit_when: str = ""
    last_commit_author: str = ""
    remote: str = ""
    error: str = ""

    @property
    def is_dirty(self) -> bool:
        return bool(self.modified or self.staged or self.untracked)

    @property
    def is_clean_and_synced(self) -> bool:
        return (
            not self.is_dirty
            and not self.ahead
            and not self.behind
            and not self.detached
            and not self.bare
            and bool(self.upstream)
            and not self.error
        )


async def run_git(repo: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command in *repo* and return its code, stdout, and stderr."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode,
        stdout.decode("utf-8", errors="replace").rstrip("\r\n"),
        stderr.decode("utf-8", errors="replace").rstrip("\r\n"),
    )


def _git_error(stderr: str, fallback: str) -> str:
    if not stderr:
        return fallback
    message = stderr.splitlines()[-1]
    for prefix in ("fatal: ", "error: "):
        if message.lower().startswith(prefix):
            message = message[len(prefix) :]
            break
    return message[:200]


def _parse_porcelain(porcelain: str, status: RepoStatus) -> None:
    records = porcelain.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        if not record:
            index += 1
            continue
        xy = record[:2]
        if xy == "??":
            status.untracked += 1
        else:
            x, y = xy
            if x not in (" ", "?"):
                status.staged += 1
            if y not in (" ", "?"):
                status.modified += 1
        index += 2 if any(code in "RC" for code in xy) else 1


async def gather_status(repo: Path) -> RepoStatus:
    """Build a full RepoStatus snapshot for one repo."""
    status = RepoStatus(path=repo, name=repo.name)
    try:
        rc, bare, stderr = await run_git(repo, "rev-parse", "--is-bare-repository")
        if rc != 0:
            status.error = _git_error(stderr, "not a Git repository")
            return status
        status.bare = bare == "true"

        rc, branch, _ = await run_git(
            repo, "symbolic-ref", "--quiet", "--short", "HEAD"
        )
        if rc == 0:
            status.branch = branch
        else:
            rc, sha, stderr = await run_git(repo, "rev-parse", "--short", "HEAD")
            if rc != 0:
                status.error = _git_error(stderr, "invalid HEAD")
                return status
            status.detached = True
            status.branch = f"detached@{sha}"

        if not status.bare:
            rc, porcelain, stderr = await run_git(
                repo, "status", "--porcelain=v1", "-z"
            )
            if rc != 0:
                status.error = _git_error(stderr, "could not read worktree status")
                return status
            _parse_porcelain(porcelain, status)

        rc, upstream, _ = await run_git(
            repo,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        )
        if rc == 0:
            status.upstream = upstream
            rc, counts, _ = await run_git(
                repo, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"
            )
            if rc == 0:
                ahead_str, behind_str = counts.split(maxsplit=1)
                status.ahead = int(ahead_str)
                status.behind = int(behind_str)

        if not status.bare:
            _, stash_list, _ = await run_git(repo, "stash", "list")
            status.stashes = sum(1 for line in stash_list.splitlines() if line)

        _, log, _ = await run_git(
            repo, "log", "-1", "--pretty=format:%s%x1f%cr%x1f%an"
        )
        if log:
            parts = log.split("\x1f")
            if len(parts) == 3:
                (
                    status.last_commit_summary,
                    status.last_commit_when,
                    status.last_commit_author,
                ) = parts

        _, remote, _ = await run_git(repo, "config", "--get", "remote.origin.url")
        status.remote = remote

    except Exception as e:  # noqa: BLE001
        status.error = type(e).__name__

    return status


async def gather_all(
    paths: list[Path], limit: int = DEFAULT_CONCURRENCY
) -> list[RepoStatus]:
    """Gather statuses while limiting the number of concurrently scanned repos."""
    if limit < 1:
        raise ValueError("concurrency limit must be at least 1")

    semaphore = asyncio.Semaphore(limit)

    async def gather_one(path: Path) -> RepoStatus:
        async with semaphore:
            return await gather_status(path)

    return list(await asyncio.gather(*(gather_one(path) for path in paths)))


async def iter_statuses(
    paths: list[Path], limit: int = DEFAULT_CONCURRENCY
) -> AsyncIterator[RepoStatus]:
    """Yield statuses as they complete while enforcing the concurrency limit."""
    if limit < 1:
        raise ValueError("concurrency limit must be at least 1")

    semaphore = asyncio.Semaphore(limit)

    async def gather_one(path: Path) -> RepoStatus:
        async with semaphore:
            return await gather_status(path)

    tasks = [asyncio.create_task(gather_one(path)) for path in paths]
    try:
        for task in asyncio.as_completed(tasks):
            yield await task
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _looks_like_bare_repo(path: Path) -> bool:
    return (
        (path / "HEAD").is_file()
        and (path / "objects").is_dir()
        and (path / "refs").is_dir()
    )


def find_repos(root: Path, max_depth: int) -> list[Path]:
    """Walk *root* up to *max_depth* and return paths that contain a `.git` entry."""
    repos: list[Path] = []

    def walk(p: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if (p / ".git").exists() or _looks_like_bare_repo(p):
            repos.append(p)
            return  # top-level repos own their worktrees and submodules
        try:
            for child in sorted(p.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    walk(child, depth + 1)
        except (PermissionError, OSError):
            pass

    walk(root, 0)
    return repos


def find_repos_many(roots: tuple[Path, ...], max_depth: int) -> list[Path]:
    """Find repositories under multiple roots, removing resolved duplicates."""
    repos: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for repo in find_repos(root, max_depth):
            resolved = repo.resolve()
            if resolved not in seen:
                seen.add(resolved)
                repos.append(resolved)
    return repos
