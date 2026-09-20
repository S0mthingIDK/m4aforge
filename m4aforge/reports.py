"""Generates CSV, JSON, and HTML reports from a run's SQLite stats."""

from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from m4aforge.core import ProcessResult

_JSON_INDENT = 2


@dataclass
class ReportRow:
    """One file's outcome, flattened for reporting."""

    path: str
    success: bool
    provider_used: str | None
    stage_failed: str | None
    error_message: str | None
    missing_artwork: bool
    missing_lyrics: bool


@dataclass
class ProviderStatRow:
    """Hit/miss/error counts for a single provider across the run."""

    provider: str
    hits: int
    misses: int
    errors: int


@dataclass
class RunReport:
    """Everything a report needs: summary, per-file rows, provider stats."""

    run_id: str
    started_at: str
    finished_at: str
    elapsed_seconds: float
    total_files: int
    succeeded: int
    failed: int
    rows: list[ReportRow] = field(default_factory=list)
    provider_stats: list[ProviderStatRow] = field(default_factory=list)


def build_report_rows(results: list[ProcessResult]) -> list[ReportRow]:
    """Flatten pipeline results into report-friendly rows."""
    rows: list[ReportRow] = []
    for result in results:
        metadata = result.metadata
        rows.append(
            ReportRow(
                path=str(result.scan_item.path),
                success=result.success,
                provider_used=metadata.source_provider if metadata else None,
                stage_failed=result.stage_failed,
                error_message=result.error_message,
                missing_artwork=not (metadata and metadata.artwork_url),
                missing_lyrics=not (metadata and metadata.lyrics),
            )
        )
    return rows


def build_run_report(
    run_id: str,
    results: list[ProcessResult],
    provider_stats: list[tuple[str, int, int, int]],
    started_at: datetime,
    finished_at: datetime,
) -> RunReport:
    """Assemble a ``RunReport`` from raw pipeline results and DB stats."""
    rows = build_report_rows(results)
    succeeded = sum(1 for r in results if r.success)

    return RunReport(
        run_id=run_id,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        elapsed_seconds=(finished_at - started_at).total_seconds(),
        total_files=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        rows=rows,
        provider_stats=[ProviderStatRow(*row) for row in provider_stats],
    )


def write_csv_report(report: RunReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(ReportRow.__dataclass_fields__)

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in report.rows:
            writer.writerow(asdict(row))

    return path


def write_json_report(report: RunReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = asdict(report)
    path.write_text(json.dumps(payload, indent=_JSON_INDENT), encoding="utf-8")
    return path


def _html_table(headers: list[str], data_rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in data_rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>\n{body}\n</tbody></table>"


def write_html_report(report: RunReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)

    file_rows = [
        [
            row.path,
            "OK" if row.success else "FAILED",
            row.provider_used or "-",
            row.stage_failed or "-",
            "yes" if row.missing_artwork else "no",
            "yes" if row.missing_lyrics else "no",
            row.error_message or "",
        ]
        for row in report.rows
    ]
    provider_rows = [
        [stat.provider, stat.hits, stat.misses, stat.errors] for stat in report.provider_stats
    ]

    body = f"""
    <h1>M4AForge &mdash; Run Report</h1>
    <p>Run ID: {html.escape(report.run_id)}<br>
    Started: {html.escape(report.started_at)} &middot;
    Finished: {html.escape(report.finished_at)} &middot;
    Elapsed: {report.elapsed_seconds:.1f}s</p>
    <p>Total files: {report.total_files} &middot;
    Succeeded: {report.succeeded} &middot;
    Failed: {report.failed}</p>

    <h2>Files</h2>
    {_html_table(
        ["Path", "Status", "Provider", "Failed Stage", "Missing Artwork", "Missing Lyrics", "Error"],
        file_rows,
    )}

    <h2>Provider Stats</h2>
    {_html_table(["Provider", "Hits", "Misses", "Errors"], provider_rows)}
    """

    document = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>M4AForge Report — {html.escape(report.run_id)}</title>
<style>
  body {{ font-family: sans-serif; margin: 2rem; color: #222; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }}
  th {{ background: #f2f2f2; }}
  tr:nth-child(even) {{ background: #fafafa; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
    path.write_text(document, encoding="utf-8")
    return path


def generate_reports(report: RunReport, output_dir: Path) -> dict[str, Path]:
    """Write CSV, JSON, and HTML reports under ``output_dir``."""
    stem = f"report_{report.run_id}"
    return {
        "csv": write_csv_report(report, output_dir / f"{stem}.csv"),
        "json": write_json_report(report, output_dir / f"{stem}.json"),
        "html": write_html_report(report, output_dir / f"{stem}.html"),
    }


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)