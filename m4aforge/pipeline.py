"""The enrichment pipeline: provider construction, per-file processing,
run orchestration, restore, and report emission."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Callable, Optional

import requests
from mutagen import MutagenError
from mutagen.mp4 import MP4

from m4aforge.core import (
    BackupError,
    Config,
    IntegrityError,
    M4AEnricherError,
    MatchResult,
    MetadataReadError,
    MetadataWriteError,
    ParseError,
    ProcessResult,
    ProviderError,
    ProviderName,
    ScanItem,
    TrackMetadata,
)
from m4aforge.logger import get_logger
from m4aforge.media import (
    clean_filename,
    fetch_best_artwork,
    get_lyrics,
    read_tags,
    rename_file,
    scan_folder,
    upscale_itunes_artwork_url,
    write_artwork,
    write_tags,
)
from m4aforge.net import (
    RateLimiterRegistry,
    ThreadSafeCache,
    ThreadSafeCheckpoint,
    WorkerPool,
    resolve_metadata,
    score_candidate,
    split_query,
)
from m4aforge.plugins import load_metadata_providers
from m4aforge.providers.base import MetadataProvider
from m4aforge.providers.discogs import DiscogsProvider
from m4aforge.providers.genius import GeniusProvider
from m4aforge.providers.itunes import ITunesProvider
from m4aforge.providers.musicbrainz import MusicBrainzProvider
from m4aforge.providers.ollama import OllamaEnricher
from m4aforge.reports import build_run_report, generate_reports, utc_now
from m4aforge.safety import (
    BackupStore,
    find_duplicates_by_checksum,
    validate_all as validate_albums,
    verified_write,
)
from m4aforge.store import CheckpointManager, Database, MetadataCache
from m4aforge.ui import InteractiveMenu, ReviewDecision

logger = get_logger()

# Default requests/second budget for a provider with no specific policy.
_DEFAULT_RATE_LIMIT_CALLS = 20
_DEFAULT_RATE_LIMIT_PERIOD = 60.0

_TOKEN_REQUIRED_PROVIDERS = {ProviderName.DISCOGS.value, ProviderName.GENIUS.value}


# =========================================================================
# Provider construction
# =========================================================================

def build_providers(config: Config, session: requests.Session) -> list[MetadataProvider]:
    """Construct the metadata provider fallback chain from ``config.provider_priority``."""
    factories: dict[str, Callable[[], MetadataProvider]] = {
        ProviderName.ITUNES.value: lambda: ITunesProvider(
            session=session, timeout=config.request_timeout_seconds
        ),
        ProviderName.MUSICBRAINZ.value: lambda: MusicBrainzProvider(
            session=session, timeout=config.request_timeout_seconds
        ),
        ProviderName.DISCOGS.value: lambda: DiscogsProvider(
            token=config.discogs_token, session=session, timeout=config.request_timeout_seconds
        ),
        ProviderName.GENIUS.value: lambda: GeniusProvider(
            token=config.genius_token, session=session, timeout=config.request_timeout_seconds
        ),
    }

    providers: list[MetadataProvider] = []
    for name in config.provider_priority:
        if name == ProviderName.OLLAMA.value:
            continue
        if name in _TOKEN_REQUIRED_PROVIDERS and not getattr(config, f"{name}_token", None):
            logger.warning("Skipping provider '%s': no API token configured", name)
            continue
        factory = factories.get(name)
        if factory is None:
            logger.warning("Unknown provider '%s' in provider_priority; skipping", name)
            continue
        try:
            providers.append(factory())
        except ProviderError as exc:
            logger.warning("Skipping provider '%s': %s", name, exc)

    providers.extend(load_metadata_providers(config.plugins_dir, config))
    return providers


def build_rate_limiters(providers: list[MetadataProvider]) -> RateLimiterRegistry:
    """Register a default rate limiter for every provider in the chain."""
    registry = RateLimiterRegistry()
    for provider in providers:
        registry.register(provider.name, _DEFAULT_RATE_LIMIT_CALLS, _DEFAULT_RATE_LIMIT_PERIOD)
    return registry


# =========================================================================
# Pipeline
# =========================================================================

class Pipeline:
    """Per-file processing logic bundled with everything a worker needs."""

    def __init__(
        self,
        config: Config,
        providers: list[MetadataProvider],
        rate_limiters: RateLimiterRegistry,
        cache: ThreadSafeCache,
        checkpoint: ThreadSafeCheckpoint,
        backup_store: Optional[BackupStore],
        db: Database,
        run_id: str,
        session: requests.Session,
        menu: Optional[InteractiveMenu],
    ) -> None:
        self._config = config
        self._providers = providers
        self._rate_limiters = rate_limiters
        self._cache = cache
        self._checkpoint = checkpoint
        self._backup_store = backup_store
        self._db = db
        self._run_id = run_id
        self._session = session
        self._menu = menu
        self._ollama = (
            OllamaEnricher(host=config.ollama_host, model=config.ollama_model, session=session)
            if config.ollama_enabled
            else None
        )

    def process(self, scan_item: ScanItem) -> ProcessResult:
        """Run one file through: resume-check -> parse -> resolve -> enrich
        -> (review) -> backup -> write -> rename -> checkpoint."""
        if self._checkpoint.is_done(scan_item.path):
            logger.info("Skipping already-processed file (resume): %s", scan_item.path)
            return ProcessResult(scan_item, success=True, error_message="skipped (resumed)")

        try:
            query = clean_filename(scan_item.path)
        except ParseError as exc:
            return self._fail(scan_item, "parse", exc)

        scan_item.cleaned_query = query
        query_duration = self._read_duration(scan_item.path)

        match = self._resolve(scan_item, query, query_duration)
        if isinstance(match, ProcessResult):
            return match

        if not match.confident:
            logger.warning(
                "Low-confidence match for %s (best=%.2f); leaving file untouched",
                scan_item.path.name,
                match.confidence,
            )
            return ProcessResult(
                scan_item,
                success=False,
                error_message=f"below confidence threshold ({match.confidence:.2f})",
                stage_failed="confidence",
                confidence=match.confidence,
                rejected_candidates=match.candidates,
            )

        metadata = match.best
        metadata = self._enrich(metadata)

        if self._config.dry_run:
            logger.info("[dry-run] Would write metadata to %s: %s", scan_item.path, metadata)
            return ProcessResult(
                scan_item, success=True, metadata=metadata, confidence=match.confidence,
            )

        if self._menu is not None:
            decision = self._review(scan_item, metadata)
            if decision is ReviewDecision.QUIT:
                raise KeyboardInterrupt("Interactive review cancelled by user")
            if decision is ReviewDecision.SKIP:
                return ProcessResult(
                    scan_item,
                    success=True,
                    metadata=metadata,
                    error_message="skipped (user declined)",
                    confidence=match.confidence,
                )

        result = self._apply(scan_item, metadata)
        result.confidence = match.confidence
        return result

    @staticmethod
    def _read_duration(path: Path) -> Optional[float]:
        """Read the file's actual audio duration via mutagen."""
        try:
            return float(MP4(path).info.length)
        except (MutagenError, OSError):
            return None

    def _resolve(self, scan_item: ScanItem, query: str, query_duration: Optional[float]):
        cached = self._cache.get(query)
        if cached is not None:
            cached_metadata, cached_confidence = cached
            logger.debug("Cache hit for '%s' (confidence=%.2f)", query, cached_confidence)
            # Re-score against the current query to guard against hash
            # collisions and any threshold changes since the entry was
            # written. A cached match does not automatically pass.
            if cached_confidence >= self._config.min_confidence:
                artist, title = split_query(query)
                revalidated = score_candidate(cached_metadata, artist, title, query_duration)
                if revalidated >= self._config.min_confidence:
                    return MatchResult(
                        best=cached_metadata,
                        confidence=revalidated,
                        candidates=[(cached_metadata, revalidated)],
                    )
            return MatchResult(
                best=None,
                confidence=cached_confidence,
                candidates=[(cached_metadata, cached_confidence)],
            )

        try:
            match = resolve_metadata(
                scan_item.path,
                query,
                self._providers,
                query_duration=query_duration,
                min_confidence=self._config.min_confidence,
                rate_limiters=self._rate_limiters,
            )
        except ProviderError as exc:
            return self._fail(scan_item, "search", exc)

        if match.best is not None:
            if match.best.source_provider:
                self._db.record_provider_result(match.best.source_provider, "hit")
            self._cache.set(query, match.best, match.confidence)
        else:
            self._db.record_provider_result("unmatched", "miss")

        return match

    def _enrich(self, metadata: TrackMetadata) -> TrackMetadata:
        if self._ollama is not None:
            metadata = self._ollama.enrich(metadata)

        lyrics = get_lyrics(
            artist=metadata.artist,
            title=metadata.title,
            genius_token=self._config.genius_token,
            embed_lyrics=self._config.embed_lyrics,
            session=self._session,
            timeout=self._config.request_timeout_seconds,
        )
        if lyrics:
            metadata.lyrics = lyrics

        return metadata

    def _review(self, scan_item: ScanItem, metadata: TrackMetadata) -> ReviewDecision:
        try:
            current = read_tags(scan_item.path)
        except MetadataReadError:
            current = None
        assert self._menu is not None
        return self._menu.review(scan_item.path.name, current, metadata)

    def _apply(self, scan_item: ScanItem, metadata: TrackMetadata) -> ProcessResult:
        path = scan_item.path

        if self._backup_store is not None and self._config.backup_enabled:
            try:
                self._backup_store.backup_file(self._run_id, path)
            except BackupError as exc:
                return self._fail(scan_item, "backup", exc)

        try:
            with verified_write(path):
                write_tags(path, metadata, overwrite_existing=self._config.overwrite_existing)
                self._write_artwork_if_enabled(path, metadata)
        except (MetadataWriteError, IntegrityError) as exc:
            self._checkpoint.mark_failed(path, metadata.source_provider)
            return self._fail(scan_item, "write", exc)

        if metadata.artist and metadata.title:
            new_stem = f"{metadata.artist} - {metadata.title}"
            try:
                path = rename_file(path, new_stem)
                scan_item.path = path
            except MetadataWriteError as exc:
                self._checkpoint.mark_failed(scan_item.path, metadata.source_provider)
                return self._fail(scan_item, "rename", exc)

        self._checkpoint.mark_done(path, metadata.source_provider)
        return ProcessResult(scan_item, success=True, metadata=metadata)

    def _write_artwork_if_enabled(self, path: Path, metadata: TrackMetadata) -> None:
        if not self._config.embed_artwork or not metadata.artwork_url:
            return
        url = upscale_itunes_artwork_url(metadata.artwork_url)
        artwork = fetch_best_artwork(
            [url],
            session=self._session,
            timeout=self._config.request_timeout_seconds,
            min_resolution=self._config.min_artwork_resolution,
        )
        if artwork:
            write_artwork(path, artwork, overwrite_existing=self._config.overwrite_existing)

    @staticmethod
    def _fail(scan_item: ScanItem, stage: str, exc: Exception) -> ProcessResult:
        return ProcessResult(scan_item, success=False, error_message=str(exc), stage_failed=stage)


# =========================================================================
# Run orchestration
# =========================================================================

def write_uncertain_report(
    results: list[ProcessResult], report_dir: Path, run_id: str
) -> Optional[Path]:
    """Write a CSV of every track below the confidence threshold, with
    its top rejected candidate."""
    uncertain = [r for r in results if r.stage_failed == "confidence"]
    if not uncertain:
        return None

    path = report_dir / f"uncertain_{run_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "path",
                "best_confidence",
                "top_candidate_artist",
                "top_candidate_title",
                "top_candidate_provider",
            ]
        )
        for r in uncertain:
            top = r.rejected_candidates[0] if r.rejected_candidates else (None, 0.0)
            cand, _score = top
            writer.writerow(
                [
                    str(r.scan_item.path),
                    f"{(r.confidence or 0):.3f}",
                    (cand.artist if cand else "") or "",
                    (cand.title if cand else "") or "",
                    (cand.source_provider if cand else "") or "",
                ]
            )
    return path


def run_pipeline(config: Config, console, progress) -> tuple[list[ProcessResult], str, Database]:
    """Scan ``config.folder`` and process every file through the full pipeline.

    ``console`` and ``progress`` come from m4aforge.ui so this module
    stays free of Rich imports.
    """
    try:
        scan_items = scan_folder(config.folder)
    except M4AEnricherError as exc:
        logger.error("Scan failed: %s", exc)
        raise

    logger.info("Found %d .m4a file(s) under %s", len(scan_items), config.folder)

    session = requests.Session()
    providers = build_providers(config, session)
    if not providers:
        raise M4AEnricherError("No metadata providers are configured/usable")

    db = Database(config.cache_db_path)
    cache = ThreadSafeCache(
        MetadataCache(config.cache_db_path, config.cache_max_age_days, config.cache_enabled)
    )
    checkpoint = ThreadSafeCheckpoint(CheckpointManager(config.cache_db_path))
    backup_store = (
        BackupStore(config.backup_root, config.backup_retention) if config.backup_enabled else None
    )
    run_id = BackupStore.new_run_id()
    menu = InteractiveMenu(console) if config.interactive else None

    pipeline = Pipeline(
        config=config,
        providers=providers,
        rate_limiters=build_rate_limiters(providers),
        cache=cache,
        checkpoint=checkpoint,
        backup_store=backup_store,
        db=db,
        run_id=run_id,
        session=session,
        menu=menu,
    )

    results: list[ProcessResult] = []
    effective_workers = 1 if config.interactive else config.max_workers
    pool = WorkerPool(max_workers=effective_workers)

    with progress:
        task_id = progress.add_task("Enriching", total=len(scan_items))
        for result in pool.map_unordered(pipeline.process, scan_items):
            results.append(result)
            if not result.success:
                logger.error(
                    "Failed at stage '%s' for %s: %s",
                    result.stage_failed,
                    result.scan_item.path,
                    result.error_message,
                )
            progress.update(
                task_id, advance=1, description=result.scan_item.path.name[:40],
            )

    if config.backup_enabled and backup_store is not None:
        backup_store.prune_old_runs()

    # Album-level validation and duplicate detection.
    resolved_tracks = [(r.scan_item.path, r.metadata) for r in results if r.metadata]
    for issue in validate_albums(resolved_tracks):
        logger.warning("Album issue [%s] in %s: %s", issue.issue_type, issue.folder, issue.detail)
    duplicate_groups = find_duplicates_by_checksum([r.scan_item.path for r in results])
    for digest, paths in duplicate_groups.items():
        logger.warning("Duplicate files (checksum %s): %s", digest[:12], paths)

    uncertain_path = write_uncertain_report(results, config.report_dir, run_id)
    if uncertain_path is not None:
        uncertain_count = sum(1 for r in results if r.stage_failed == "confidence")
        logger.info(
            "Wrote uncertain-matches report: %s (%d tracks)", uncertain_path, uncertain_count,
        )

    return results, run_id, db


def emit_reports(config: Config, results: list[ProcessResult], run_id: str, db: Database, started_at):
    """Build and write the CSV/JSON/HTML reports for this run."""
    report = build_run_report(
        run_id=run_id,
        results=results,
        provider_stats=db.get_provider_stats(),
        started_at=started_at,
        finished_at=utc_now(),
    )
    paths = generate_reports(report, config.report_dir)
    logger.info("Reports written: %s", ", ".join(str(p) for p in paths.values()))
    return report


def handle_restore(config: Config, run_id: str) -> int:
    """Restore a backed-up run (or the most recent) and return an exit code."""
    store = BackupStore(config.backup_root, config.backup_retention)
    try:
        restored = store.undo_last_run() if run_id == "latest" else store.restore(run_id)
    except BackupError as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1

    print(f"Restored {len(restored)} file(s).")
    return 0


def handle_report_only(config: Config) -> int:
    """Regenerate reports from the existing database without touching any files."""
    db = Database(config.cache_db_path)
    latest_run = BackupStore(config.backup_root, config.backup_retention).latest_run()
    run_id = latest_run or "adhoc"
    now = utc_now()
    emit_reports(config, results=[], run_id=run_id, db=db, started_at=now)
    return 0