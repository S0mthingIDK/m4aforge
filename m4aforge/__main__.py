"""CLI entry point: argument parsing -> pipeline invocation.

Run with: python -m m4aforge
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from rich_argparse import RichHelpFormatter

from m4aforge.core import (
    Config,
    DEFAULT_CONFIG_PATH,
    M4AEnricherError,
)
from m4aforge.logger import setup_logger
from m4aforge.pipeline import (
    emit_reports,
    handle_report_only,
    handle_restore,
    run_pipeline,
)
from m4aforge.reports import utc_now
from m4aforge.ui import (
    get_console,
    make_progress,
    print_banner,
    print_summary_table,
    prompt_provider_selection,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="m4aforge",
        description="Enrich .m4a file metadata from online providers.",
        formatter_class=RichHelpFormatter,
    )
    parser.add_argument("--folder", type=str, default=None, help="Folder to scan for .m4a files")
    parser.add_argument(
        "--config", type=str, default=str(DEFAULT_CONFIG_PATH), help="Path to config.json"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=None, help="Preview changes without writing"
    )
    parser.add_argument(
        "--workers", type=int, default=None, help="Number of worker threads (default from config)"
    )
    parser.add_argument(
        "--restore",
        type=str,
        nargs="?",
        const="latest",
        default=None,
        metavar="RUN_ID",
        help="Restore a backed-up run (defaults to the most recent run) and exit",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Regenerate reports from the existing database without scanning or writing",
    )
    parser.add_argument(
        "--verbose", action="store_true", default=None, help="Enable verbose (debug) logging"
    )
    parser.add_argument(
        "--no-prompt",
        "-y",
        action="store_true",
        dest="no_prompt",
        help="Skip the interactive provider-selection prompt (use config order)",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> Config:
    config = Config.load(Path(args.config))
    config = config.apply_cli_overrides(
        folder=args.folder, dry_run=args.dry_run, verbose=args.verbose, workers=args.workers
    )
    config.validate()
    return config


def _apply_provider_selection(console, config: Config) -> Config | None:
    """Prompt the user for provider order; return an updated Config.

    Returns None if the user cancelled.
    """
    disabled: dict[str, str] = {}
    if not config.discogs_token:
        disabled["discogs"] = "no discogs_token configured"
    if not config.genius_token:
        disabled["genius"] = "no genius_token configured"

    all_providers = ["itunes", "musicbrainz", "discogs", "genius", "ollama"]

    try:
        selected = prompt_provider_selection(
            console, all_providers, config.provider_priority, disabled,
        )
    except (KeyboardInterrupt, EOFError):
        console.print("\n[warning]Cancelled.[/warning]")
        return None

    return replace(config, provider_priority=selected)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        config = build_config(args)
    except M4AEnricherError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logger(verbose=config.verbose)
    console = get_console(config.theme)
    print_banner(console)

    # --restore and --report-only never do lookups, so no prompt for them.
    if args.restore is not None:
        return handle_restore(config, args.restore)

    if args.report_only:
        return handle_report_only(config)

    # Interactive provider selection (skipped by -y / --no-prompt, and by
    # non-TTY stdin automatically via prompt_provider_selection()).
    if not args.no_prompt:
        updated = _apply_provider_selection(console, config)
        if updated is None:
            return 1
        config = updated

    started_at = utc_now()
    try:
        results, run_id, db = run_pipeline(config, console, make_progress())
    except M4AEnricherError as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 1

    report = emit_reports(config, results, run_id, db, started_at)
    print_summary_table(console, report)

    succeeded = sum(1 for r in results if r.success)
    failed = len(results) - succeeded
    console.print(f"\nDone: [success]{succeeded} succeeded[/success], [error]{failed} failed[/error].")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())