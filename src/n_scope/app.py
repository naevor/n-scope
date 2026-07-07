from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static

from .git import (
    GitOperationResult,
    RepoStatus,
    find_repos_many,
    iter_repo_operations,
    iter_statuses,
)


def status_cell(repo: RepoStatus) -> Text:
    if repo.error:
        return Text(f"⚠ {repo.error}", style="bold red")
    if repo.is_clean_and_synced:
        return Text("✓ clean", style="bold green")
    chunks: list[Text] = []
    if repo.is_dirty:
        chunks.append(Text("● dirty", style="bold yellow"))
    if repo.ahead:
        chunks.append(Text(f"↑{repo.ahead}", style="bold cyan"))
    if repo.behind:
        chunks.append(Text(f"↓{repo.behind}", style="bold magenta"))
    if repo.detached:
        chunks.append(Text("detached", style="bold red"))
    elif repo.bare:
        chunks.append(Text("bare", style="bold blue"))
    elif not repo.upstream:
        chunks.append(Text("no upstream", style="bold blue"))
    out = Text()
    for i, c in enumerate(chunks):
        if i:
            out.append(" ")
        out.append_text(c)
    return out


def changes_cell(repo: RepoStatus) -> Text:
    if not repo.is_dirty:
        return Text("—", style="dim")
    parts: list[Text] = []
    if repo.staged:
        parts.append(Text(f"+{repo.staged}", style="green"))
    if repo.modified:
        parts.append(Text(f"~{repo.modified}", style="yellow"))
    if repo.untracked:
        parts.append(Text(f"?{repo.untracked}", style="dim white"))
    out = Text()
    for i, p in enumerate(parts):
        if i:
            out.append(" ")
        out.append_text(p)
    return out


def operation_cell(state: tuple[str, str] | None) -> Text:
    if state is None:
        return Text("—", style="dim")
    label, style = state
    return Text(label, style=style)


def fuzzy_match(query: str, candidate: str) -> bool:
    needle = query.casefold().strip()
    if not needle:
        return True
    haystack = candidate.casefold()
    if needle in haystack:
        return True
    characters = iter(haystack)
    return all(character in characters for character in needle)


def split_command(command: str) -> list[str]:
    return shlex.split(command, posix=os.name != "nt")


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "confirm", "Yes"),
        Binding("n", "cancel", "No"),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.confirm_title = title
        self.message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self.confirm_title, id="confirm_title"),
            Static(self.message, id="confirm_message"),
            Horizontal(
                Button("Cancel", id="cancel", variant="default"),
                Button("Confirm", id="confirm", variant="error"),
                id="confirm_buttons",
            ),
            id="confirm_dialog",
        )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.control.id == "confirm")


class NScopeApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "n-scope"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "show_detail", "Detail", priority=True),
        Binding("d", "filter_dirty", "Dirty only"),
        Binding("a", "filter_all", "Show all"),
        Binding("slash", "open_filter", "Filter", key_display="/"),
        Binding("escape", "close_filter", "Clear filter", show=False),
        Binding("s", "sort_name", "Sort: name"),
        Binding("S", "sort_status", "Sort: status"),
        Binding("space", "toggle_select", "Select", key_display="Space"),
        Binding("e", "open_editor", "$EDITOR"),
        Binding("t", "open_shell", "Shell"),
        Binding("c", "copy_path", "Copy path"),
        Binding("f", "fetch", "Fetch"),
        Binding("p", "pull", "Pull ff"),
    ]

    def __init__(
        self,
        roots: tuple[Path, ...],
        max_depth: int,
        sort_mode: str = "status",
        theme: str = "textual-dark",
    ):
        super().__init__()
        if not roots:
            raise ValueError("at least one root is required")
        if sort_mode not in {"name", "status"}:
            raise ValueError(f"unknown sort mode: {sort_mode}")
        if theme not in self.available_themes:
            choices = ", ".join(sorted(self.available_themes))
            raise ValueError(f"unknown theme {theme!r}; available themes: {choices}")
        self.theme = theme
        self.roots = roots
        self.max_depth = max_depth
        self.repos: list[RepoStatus] = []
        self.filter_mode = "all"
        self.filter_query = ""
        self.sort_mode = sort_mode
        self.selected_paths: set[Path] = set()
        self.operation_states: dict[Path, tuple[str, str]] = {}

    @property
    def roots_label(self) -> str:
        if len(self.roots) == 1:
            return str(self.roots[0])
        return f"{len(self.roots)} roots"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="header_bar")
        yield Input(
            placeholder="Filter repositories…",
            id="filter_input",
            disabled=True,
        )
        yield DataTable(zebra_stripes=True, cursor_type="row", id="repos")
        yield Static(
            "[dim]select a repo and press Enter for details[/]", id="detail"
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#repos", DataTable)
        table.add_column("Sel", key="selected", width=3)
        table.add_column("Repo", key="repo")
        table.add_column("Branch", key="branch")
        table.add_column("Status", key="status")
        table.add_column("Changes", key="changes")
        table.add_column("Op", key="operation", width=14)
        table.add_column("Last commit", key="last_commit")
        table.add_column("When", key="when")
        self.action_refresh()

    def action_refresh(self) -> None:
        self.run_worker(
            self._scan_repositories(),
            group="repository-scan",
            exclusive=True,
        )

    async def _scan_repositories(self, *, clear_operations: bool = True) -> None:
        bar = self.query_one("#header_bar", Static)
        bar.add_class("scanning")
        bar.update(
            f"[bold]n-scope[/] · scanning [cyan]{escape(self.roots_label)}[/] "
            f"(depth {self.max_depth})…"
        )

        paths = find_repos_many(self.roots, self.max_depth)
        if clear_operations:
            self.operation_states = {}
        if not paths:
            self.repos = []
            self.selected_paths = set()
            self.populate_table()
            bar.remove_class("scanning")
            bar.update(
                f"[bold red]no git repos found under[/] {escape(self.roots_label)}"
            )
            return

        self.repos = []
        known_paths = set(paths)
        self.selected_paths = {path for path in self.selected_paths if path in known_paths}
        if not clear_operations:
            self.operation_states = {
                path: state for path, state in self.operation_states.items() if path in known_paths
            }
        self.populate_table()
        total = len(paths)
        async for repo in iter_statuses(paths):
            self.repos.append(repo)
            self._sort()
            self.populate_table()
            bar.update(
                f"[bold]n-scope[/] · scanning "
                f"[cyan]{escape(self.roots_label)}[/] · "
                f"[bold]{len(self.repos)}/{total}[/]"
            )

        self._sort()
        self.populate_table()

        clean = sum(1 for r in self.repos if r.is_clean_and_synced)
        dirty = sum(1 for r in self.repos if r.is_dirty)
        ahead = sum(1 for r in self.repos if r.ahead)
        behind = sum(1 for r in self.repos if r.behind)
        bar.remove_class("scanning")
        bar.update(
            f"[bold]n-scope[/] · [cyan]{escape(self.roots_label)}[/] · "
            f"[green]{clean}[/] clean · "
            f"[yellow]{dirty}[/] dirty · "
            f"[cyan]↑{ahead}[/] · [magenta]↓{behind}[/] · "
            f"total [bold]{len(self.repos)}[/]"
        )

    def _sort(self) -> None:
        if self.sort_mode == "name":
            self.repos.sort(key=lambda r: r.name.lower())
        else:
            self.repos.sort(
                key=lambda r: (r.is_clean_and_synced, r.name.lower())
            )

    def _visible_repos(self) -> list[RepoStatus]:
        repos: list[RepoStatus] = []
        for repo in self.repos:
            if self.filter_mode == "dirty" and not repo.is_dirty:
                continue
            if not fuzzy_match(self.filter_query, repo.name):
                continue
            repos.append(repo)
        return repos

    def populate_table(self, preferred_path: Path | None = None) -> None:
        table = self.query_one("#repos", DataTable)
        if preferred_path is None:
            preferred_path = self._selected_repo_path()
        table.clear()
        cursor_row = 0
        for row_index, r in enumerate(self._visible_repos()):
            if preferred_path == r.path:
                cursor_row = row_index
            table.add_row(
                Text("*" if r.path in self.selected_paths else "", style="bold cyan"),
                Text(r.name, style="bold"),
                Text(r.branch or "—", style="cyan" if r.branch else "dim"),
                status_cell(r),
                changes_cell(r),
                operation_cell(self.operation_states.get(r.path)),
                Text((r.last_commit_summary or "—")[:60]),
                Text(r.last_commit_when or "", style="dim"),
                key=str(r.path),
            )
        if table.row_count:
            table.move_cursor(row=min(cursor_row, table.row_count - 1))

    def _selected_repo_path(self) -> Path | None:
        table = self.query_one("#repos", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(
                table.cursor_coordinate
            ).row_key
        except Exception:  # noqa: BLE001
            return None
        if row_key.value is None:
            return None
        return Path(str(row_key.value))

    def _selected_repo(self) -> RepoStatus | None:
        path = self._selected_repo_path()
        if path is None:
            return None
        return next(
            (repo for repo in self.repos if repo.path == path), None
        )

    def _batch_target_paths(self) -> list[Path]:
        selected = [repo.path for repo in self.repos if repo.path in self.selected_paths]
        if selected:
            return selected
        return [repo.path for repo in self.repos]

    def action_show_detail(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        detail = self.query_one("#detail", Static)
        detail.update(
            f"[bold cyan]{escape(str(repo.path))}[/]\n"
            f"[dim]branch:[/] {escape(repo.branch)}   "
            f"[dim]remote:[/] {escape(repo.remote or '—')}   "
            f"[dim]stashes:[/] {repo.stashes}\n"
            f"[dim]last:[/] {escape(repo.last_commit_summary or '—')}\n"
            f"[dim]      {escape(repo.last_commit_author)} · "
            f"{escape(repo.last_commit_when)}[/]\n"
            f"[dim]changes:[/] "
            f"staged=[green]{repo.staged}[/]  "
            f"modified=[yellow]{repo.modified}[/]  "
            f"untracked={repo.untracked}   "
            f"ahead=[cyan]{repo.ahead}[/]  "
            f"behind=[magenta]{repo.behind}[/]"
        )

    def action_toggle_select(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        if repo.path in self.selected_paths:
            self.selected_paths.remove(repo.path)
        else:
            self.selected_paths.add(repo.path)
        self.populate_table(preferred_path=repo.path)

    def action_copy_path(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        self.copy_to_clipboard(str(repo.path))
        self.notify(f"Copied path: {repo.name}", title="n-scope")

    def _run_external_command(
        self,
        command: list[str],
        *,
        cwd: Path,
        label: str,
    ) -> None:
        if not command:
            self.notify(
                f"No command configured for {label}",
                title="n-scope",
                severity="error",
            )
            return
        try:
            with self.suspend():
                result = subprocess.run(command, cwd=cwd, check=False)
        except FileNotFoundError:
            self.notify(f"Command not found: {command[0]}", title=label, severity="error")
            return
        if result.returncode != 0:
            self.notify(
                f"{label} exited with code {result.returncode}",
                title="n-scope",
                severity="warning",
            )

    def action_open_editor(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        editor = os.environ.get("EDITOR")
        if not editor:
            self.notify("$EDITOR is not set", title="n-scope", severity="error")
            return
        command = split_command(editor)
        if not command:
            self.notify("$EDITOR is empty", title="n-scope", severity="error")
            return
        self._run_external_command(
            [*command, str(repo.path)],
            cwd=repo.path,
            label="$EDITOR",
        )

    def action_open_shell(self) -> None:
        repo = self._selected_repo()
        if not repo:
            return
        shell = os.environ.get("SHELL") or os.environ.get("COMSPEC")
        if not shell and os.name == "nt":
            shell = "powershell.exe"
        if not shell:
            self.notify("$SHELL is not set", title="n-scope", severity="error")
            return
        self._run_external_command(split_command(shell), cwd=repo.path, label="shell")

    def _confirm_batch(
        self,
        operation: Literal["fetch", "pull"],
        targets: list[Path],
        on_confirmed: Callable[[bool | None], None],
    ) -> None:
        selected_count = len([path for path in targets if path in self.selected_paths])
        scope = (
            f"{selected_count} selected repositories"
            if selected_count
            else f"all {len(targets)} repositories"
        )
        verb = "Fetch" if operation == "fetch" else "Pull --ff-only"
        message = (
            f"{verb} {scope}?\n\n"
            "This runs git commands in those repositories. "
            "Pull uses --ff-only and will refuse merge commits."
        )
        self.push_screen(
            ConfirmScreen(f"Confirm {operation}", message), callback=on_confirmed
        )

    def _operation_summary(
        self,
        operation: Literal["fetch", "pull"],
        results: list[GitOperationResult],
    ) -> str:
        ok = [result for result in results if result.ok]
        failed = [result for result in results if not result.ok]
        lines = [
            f"[bold]{operation} complete[/]: "
            f"[green]{len(ok)}[/] ok, [red]{len(failed)}[/] failed"
        ]
        for result in failed[:6]:
            lines.append(
                f"[red]{escape(result.name)}[/]: {escape(result.message)}"
            )
        if len(failed) > 6:
            lines.append(f"[dim]…and {len(failed) - 6} more failures[/]")
        if ok and not failed:
            names = ", ".join(result.name for result in ok[:8])
            suffix = "" if len(ok) <= 8 else f", …+{len(ok) - 8}"
            lines.append(f"[dim]ok:[/] {escape(names + suffix)}")
        return "\n".join(lines)

    async def _run_batch_operation(
        self,
        operation: Literal["fetch", "pull"],
        targets: list[Path],
    ) -> None:
        bar = self.query_one("#header_bar", Static)
        detail = self.query_one("#detail", Static)
        verb = "fetch" if operation == "fetch" else "pull --ff-only"
        for path in targets:
            self.operation_states[path] = (f"{operation} queued", "dim")
        self.populate_table()

        results: list[GitOperationResult] = []
        failed = 0
        bar.add_class("scanning")
        bar.update(f"[bold]n-scope[/] · running {verb} · [bold]0/{len(targets)}[/]")
        async for result in iter_repo_operations(targets, operation):
            results.append(result)
            failed += 0 if result.ok else 1
            self.operation_states[result.path] = (
                f"{operation} ok" if result.ok else f"{operation} failed",
                "green" if result.ok else "bold red",
            )
            self.populate_table()
            bar.update(
                f"[bold]n-scope[/] · running {verb} · "
                f"[bold]{len(results)}/{len(targets)}[/] · "
                f"[red]{failed}[/] failed"
            )

        detail.update(self._operation_summary(operation, results))
        bar.remove_class("scanning")
        bar.update(
            f"[bold]n-scope[/] · {verb} done · "
            f"[green]{len(results) - failed}[/] ok · [red]{failed}[/] failed · "
            "refreshing status…"
        )
        await self._scan_repositories(clear_operations=False)

    def _start_batch_operation(self, operation: Literal["fetch", "pull"]) -> None:
        targets = self._batch_target_paths()
        if not targets:
            self.notify("No repositories loaded", title="n-scope", severity="warning")
            return

        def after_confirm(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self.run_worker(
                self._run_batch_operation(operation, targets),
                group="git-operation",
                exclusive=True,
            )

        self._confirm_batch(operation, targets, after_confirm)

    def action_fetch(self) -> None:
        self._start_batch_operation("fetch")

    def action_pull(self) -> None:
        self._start_batch_operation("pull")

    def action_filter_dirty(self) -> None:
        self.filter_mode = "dirty"
        self.populate_table()

    def action_filter_all(self) -> None:
        self.filter_mode = "all"
        self.populate_table()

    def action_open_filter(self) -> None:
        filter_input = self.query_one("#filter_input", Input)
        filter_input.disabled = False
        filter_input.add_class("active")
        filter_input.focus()
        filter_input.cursor_position = len(filter_input.value)

    def action_close_filter(self) -> None:
        filter_input = self.query_one("#filter_input", Input)
        if not filter_input.has_class("active") and not self.filter_query:
            return
        filter_input.value = ""
        filter_input.remove_class("active")
        filter_input.disabled = True
        self.filter_query = ""
        self.populate_table()
        self.query_one("#repos", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter_input":
            return
        self.filter_query = event.value
        self.populate_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter_input":
            self.query_one("#repos", DataTable).focus()

    def action_sort_name(self) -> None:
        self.sort_mode = "name"
        self._sort()
        self.populate_table()

    def action_sort_status(self) -> None:
        self.sort_mode = "status"
        self._sort()
        self.populate_table()
