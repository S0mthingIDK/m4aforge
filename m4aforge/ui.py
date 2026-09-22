"""Rich-based console UI: banner, progress, themed output, and the
interactive review menu.

Rich handles terminal capability detection, NO_COLOR/FORCE_COLOR, and
graceful plain-text fallback automatically.
"""

from __future__ import annotations

from dataclasses import fields
from enum import Enum
from typing import Optional

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Prompt
from rich.table import Table
from rich.theme import Theme

from m4aforge import __version__
from m4aforge.core import TrackMetadata

# Fields never shown in the diff: internal bookkeeping, not user-facing tags.
_HIDDEN_FIELDS = {"source_provider"}


DEFAULT_THEME = Theme(
    {
        "success": "bold green",
        "error": "bold red",
        "warning": "yellow",
        "info": "cyan",
        "header": "bold",
        "muted": "dim",
    }
)

PLAIN_THEME = Theme({kind: "" for kind in DEFAULT_THEME.styles})


def get_console(theme_name: str = "default") -> Console:
    """Return a Rich Console configured with the named theme."""
    theme = DEFAULT_THEME if theme_name == "default" else PLAIN_THEME
    return Console(theme=theme, soft_wrap=True)


def print_banner(console: Console) -> None:
    """Print the startup banner."""
    console.print(
        Panel(
            Align.center(
                "[bold cyan]M4AForge[/bold cyan]\n"
                f"[dim]v{__version__}[/dim]"
            ),
            border_style="cyan",
            padding=(1, 4),
        )
    )


def make_progress() -> Progress:
    """Return a Progress instance with a consistent column layout."""
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    )


def print_summary_table(console: Console, report) -> None:
    """Print a compact summary table for a run report."""
    table = Table(title=f"Run {report.run_id}", show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total files", str(report.total_files))
    table.add_row("Succeeded", f"[success]{report.succeeded}[/success]")
    table.add_row("Failed", f"[error]{report.failed}[/error]")
    table.add_row("Elapsed", f"{report.elapsed_seconds:.1f}s")
    console.print(table)


# =========================================================================
# Interactive review menu
# =========================================================================

class ReviewDecision(Enum):
    """The user's choice for a single file under interactive review."""

    ACCEPT = "accept"
    SKIP = "skip"
    QUIT = "quit"


def diff_metadata(
    current: Optional[TrackMetadata], new: TrackMetadata
) -> list[tuple[str, object, object]]:
    """Return (field, current_value, new_value) for every field ``new``
    would change relative to ``current``."""
    changes: list[tuple[str, object, object]] = []
    for f in fields(new):
        if f.name in _HIDDEN_FIELDS:
            continue
        new_value = getattr(new, f.name)
        if new_value is None:
            continue
        current_value = getattr(current, f.name) if current is not None else None
        if current_value != new_value:
            changes.append((f.name, current_value, new_value))
    return changes


class InteractiveMenu:
    """Prompts the user to accept, skip, or quit for each file's proposed changes."""

    def __init__(self, console: Optional[Console] = None) -> None:
        self._console = console or get_console()
        self._accept_all = False

    def review(
        self, display_name: str, current: Optional[TrackMetadata], new: TrackMetadata
    ) -> ReviewDecision:
        """Show the proposed changes for one file and prompt for a decision."""
        if self._accept_all:
            return ReviewDecision.ACCEPT

        changes = diff_metadata(current, new)

        if not changes:
            self._console.print(f"\n[header]{display_name}[/header] [muted](no changes)[/muted]")
        else:
            table = Table(title=display_name, show_lines=False)
            table.add_column("Field", style="bold")
            table.add_column("Current", style="dim")
            table.add_column("Proposed", style="success")
            for field_name, old_value, new_value in changes:
                table.add_row(field_name, repr(old_value), repr(new_value))
            self._console.print(table)

        return self._prompt()

    def _prompt(self) -> ReviewDecision:
        while True:
            answer = Prompt.ask(
                "[bold]Apply changes?[/bold]",
                choices=["y", "n", "a", "q"],
                default="y",
                show_choices=True,
                console=self._console,
            ).lower()

            if answer == "y":
                return ReviewDecision.ACCEPT
            if answer == "n":
                return ReviewDecision.SKIP
            if answer == "a":
                self._accept_all = True
                return ReviewDecision.ACCEPT
            if answer == "q":
                return ReviewDecision.QUIT