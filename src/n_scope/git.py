from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoStatus:
    path: Path
    name: str
    branch: str = ""
    detached: bool = False
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
            and not self.error
        )


async def run_git(repo: Path, *args: str) -> tuple[int, str]:
    """Run a git command in *repo* and return (returncode, stdout)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return proc.returncode, stdout.decode("utf-8", errors="replace").strip()


async def gather_status(repo: Path) -> RepoStatus:
    """Build a full RepoStatus snapshot for one repo."""
    status = RepoStatus(path=repo, name=repo.name)
    try:
        rc, branch = await run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        if rc == 0:
            status.branch = branch
        else:
            rc, sha = await run_git(repo, "rev-parse", "--short", "HEAD")
            if rc != 0:
                status.error = "invalid HEAD"
                return status
            status.detached = True
            status.branch = f"detached@{sha}"

        _, porcelain = await run_git(repo, "status", "--porcelain=v1")
        for line in porcelain.splitlines():
            if not line:
                continue
            xy = line[:2]
            if xy == "??":
                status.untracked += 1
                continue
            x, y = xy[0], xy[1]
            if x not in (" ", "?"):
                status.staged += 1
            if y not in (" ", "?"):
                status.modified += 1

        rc, ab = await run_git(
            repo, "rev-list", "--left-right", "--count", "HEAD...@{u}"
        )
        if rc == 0 and "\t" in ab:
            ahead_str, behind_str = ab.split("\t", 1)
            status.ahead = int(ahead_str or 0)
            status.behind = int(behind_str or 0)

        _, stash_list = await run_git(repo, "stash", "list")
        status.stashes = sum(1 for line in stash_list.splitlines() if line)

        _, log = await run_git(
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

        _, remote = await run_git(repo, "config", "--get", "remote.origin.url")
        status.remote = remote

    except Exception as e:  # noqa: BLE001
        status.error = type(e).__name__

    return status


def find_repos(root: Path, max_depth: int) -> list[Path]:
    """Walk *root* up to *max_depth* and return paths that contain a `.git` entry."""
    repos: list[Path] = []

    def walk(p: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if (p / ".git").exists():
            repos.append(p)
            return  # don't descend into a repo
        try:
            for child in sorted(p.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    walk(child, depth + 1)
        except (PermissionError, OSError):
            pass

    walk(root, 0)
    return repos
