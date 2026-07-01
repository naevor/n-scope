from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from .git import RepoStatus, find_repos, gather_all


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


class NScopeApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "n-scope"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "show_detail", "Detail", priority=True),
        Binding("d", "filter_dirty", "Dirty only"),
        Binding("a", "filter_all", "Show all"),
        Binding("s", "sort_name", "Sort: name"),
        Binding("S", "sort_status", "Sort: status"),
    ]

    def __init__(self, root: Path, max_depth: int):
        super().__init__()
        self.root = root
        self.max_depth = max_depth
        self.repos: list[RepoStatus] = []
        self.filter_mode = "all"
        self.sort_mode = "status"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="header_bar")
        yield DataTable(zebra_stripes=True, cursor_type="row", id="repos")
        yield Static(
            "[dim]select a repo and press Enter for details[/]", id="detail"
        )
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#repos", DataTable)
        table.add_columns(
            "Repo", "Branch", "Status", "Changes", "Last commit", "When"
        )
        await self.action_refresh()

    async def action_refresh(self) -> None:
        bar = self.query_one("#header_bar", Static)
        bar.add_class("scanning")
        bar.update(
            f"[bold]n-scope[/] · scanning [cyan]{escape(str(self.root))}[/] "
            f"(depth {self.max_depth})…"
        )

        paths = find_repos(self.root, self.max_depth)
        if not paths:
            self.repos = []
            self.populate_table()
            bar.remove_class("scanning")
            bar.update(
                f"[bold red]no git repos found under[/] {escape(str(self.root))}"
            )
            return

        self.repos = await gather_all(paths)
        self._sort()
        self.populate_table()

        clean = sum(1 for r in self.repos if r.is_clean_and_synced)
        dirty = sum(1 for r in self.repos if r.is_dirty)
        ahead = sum(1 for r in self.repos if r.ahead)
        behind = sum(1 for r in self.repos if r.behind)
        bar.remove_class("scanning")
        bar.update(
            f"[bold]n-scope[/] · [cyan]{escape(str(self.root))}[/] · "
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

    def populate_table(self) -> None:
        table = self.query_one("#repos", DataTable)
        table.clear()
        for r in self.repos:
            if self.filter_mode == "dirty" and not r.is_dirty:
                continue
            table.add_row(
                Text(r.name, style="bold"),
                Text(r.branch or "—", style="cyan" if r.branch else "dim"),
                status_cell(r),
                changes_cell(r),
                Text((r.last_commit_summary or "—")[:60]),
                Text(r.last_commit_when or "", style="dim"),
                key=str(r.path),
            )
        if table.row_count:
            table.move_cursor(row=0)

    def _selected_repo(self) -> RepoStatus | None:
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
        return next(
            (r for r in self.repos if str(r.path) == row_key.value), None
        )

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

    def action_filter_dirty(self) -> None:
        self.filter_mode = "dirty"
        self.populate_table()

    def action_filter_all(self) -> None:
        self.filter_mode = "all"
        self.populate_table()

    def action_sort_name(self) -> None:
        self.sort_mode = "name"
        self._sort()
        self.populate_table()

    def action_sort_status(self) -> None:
        self.sort_mode = "status"
        self._sort()
        self.populate_table()
