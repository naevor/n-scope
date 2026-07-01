# n-scope

> Pretty TUI for surveying git repositories — see what's dirty, ahead, behind, or stale across all your projects at a glance.

## Features

- Recursively scans a directory for git repos with depth control
- One screen showing: branch, working-tree status, ahead/behind counts, changes summary, last commit
- Color-coded status badges: ✓ clean, ● dirty, ↑ahead, ↓behind, detached HEAD
- Live filtering (`d` for dirty-only, `a` for all)
- Sort by status or name
- Detail panel for the selected repo (path, remote, stashes, full numbers)
- Async git calls — scans dozens of repos in parallel

## Install

```bash
git clone https://github.com/naevor/n-scope.git
cd n-scope
pip install -e .
```

Requires Python 3.10+.

## Usage

```bash
n-scope                    # scan current directory
n-scope ~/projects         # scan a specific path
n-scope ~/code --depth 4   # set recursion depth (default: 3)
```

Or as a module without installing the script:

```bash
python -m n_scope ~/projects
```

## Keybindings

| Key       | Action                |
|-----------|-----------------------|
| `↑` / `↓` | navigate rows         |
| `Enter`   | show detail panel     |
| `r`       | refresh               |
| `d`       | filter: dirty only    |
| `a`       | filter: show all      |
| `s`       | sort by name          |
| `S`       | sort by status        |
| `q`       | quit                  |

## Roadmap

- [x] core scanner + TUI
- [x] proper package layout, `n-scope` console script
- [ ] diff preview in the detail panel
- [ ] batch operations (multi-select + `git fetch` etc.)
- [ ] sqlite snapshot persistence + history charts
- [ ] alternative themes (BIOS / CRT terminal aesthetic)

## Development

```bash
pip install -e ".[dev]"
ruff check .
```

## License

MIT — see [LICENSE](LICENSE).
