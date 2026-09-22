"""Concurrency and network concerns: rate limiting, retry/fallback,
confidence scoring, and the thread pool.

Merged from ratelimit.py + retry.py + match.py + threadpool.py.
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Iterator, Optional, TypeVar

from rapidfuzz import fuzz

from m4aforge.core import (
    MatchResult,
    MetadataNotFoundError,
    ProviderBlockedError,
    ProviderError,
    TrackMetadata,
)
from m4aforge.logger import get_logger

if TYPE_CHECKING:
    from m4aforge.providers.base import MetadataProvider
    from m4aforge.store import CheckpointManager, MetadataCache

logger = get_logger()

T = TypeVar("T")
R = TypeVar("R")


# =========================================================================
# Rate limiting
# =========================================================================

class RateLimiter:
    """Thread-safe token-bucket limiter for a single provider."""

    def __init__(self, max_calls: int, period_seconds: float = 1.0) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        if period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        self._max_calls = max_calls
        self._period = period_seconds
        self._tokens = float(max_calls)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_time = (1 - self._tokens) * (self._period / self._max_calls)
            time.sleep(max(wait_time, 0.001))

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        refill_rate = self._max_calls / self._period
        self._tokens = min(self._max_calls, self._tokens + elapsed * refill_rate)


class RateLimiterRegistry:
    """Holds one RateLimiter per provider name, created via register()."""

    def __init__(self) -> None:
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()

    def register(self, provider: str, max_calls: int, period_seconds: float = 1.0) -> None:
        with self._lock:
            self._limiters[provider] = RateLimiter(max_calls, period_seconds)

    def get(self, provider: str) -> Optional[RateLimiter]:
        return self._limiters.get(provider)

    def acquire(self, provider: str) -> None:
        """Acquire a token for ``provider``. No-op if unregistered."""
        limiter = self.get(provider)
        if limiter is not None:
            limiter.acquire()


# =========================================================================
# Confidence scoring
# =========================================================================

_WEIGHT_TITLE = 0.35
_WEIGHT_ARTIST = 0.30
_WEIGHT_DURATION = 0.20
_WEIGHT_COMPLETENESS = 0.10
_WEIGHT_PROVIDER = 0.05

# Provider reliability priors. MusicBrainz and iTunes share the top
# weight: MusicBrainz has the most complete release data, but its search
# tends to surface compilations and live albums over the canonical
# studio release, so it shouldn't outweigh iTunes when both agree.
_PROVIDER_WEIGHTS = {
    "musicbrainz": 0.85,
    "itunes": 0.85,
    "discogs": 0.70,
    "genius": 0.80,
    "ollama": 0.0,
}

_DURATION_PERFECT = 2.0
_DURATION_MAX = 5.0


# Album titles matching any of these are compilations, live albums, or
# promo releases. MusicBrainz in particular surfaces these over the
# canonical studio release, so we penalize the whole candidate score
# when its album looks like one of them.
_BAD_ALBUM_PATTERNS = [
    re.compile(r"\blive\b", re.IGNORECASE),
    re.compile(r"\btour\b", re.IGNORECASE),
    re.compile(r"\bconcert\b", re.IGNORECASE),
    re.compile(r"\bfestival\b", re.IGNORECASE),
    re.compile(r"\bgreatest hits\b", re.IGNORECASE),
    re.compile(r"\bbest of\b", re.IGNORECASE),
    re.compile(r"\bessential\b", re.IGNORECASE),
    re.compile(r"\bcompilation\b", re.IGNORECASE),
    re.compile(r"\bsampler\b", re.IGNORECASE),
    re.compile(r"\bpromo only\b", re.IGNORECASE),
    re.compile(r"\banthology\b", re.IGNORECASE),
    re.compile(r"\bcollection\b", re.IGNORECASE),
    re.compile(r"\bmixtape\b", re.IGNORECASE),
    re.compile(r"\bdeluxe\b", re.IGNORECASE),
    re.compile(r"\bremaster(ed)?\b", re.IGNORECASE),
    re.compile(r"\bwartime\b", re.IGNORECASE),
    # Film/TV soundtracks — usually a compilation, rarely the album a
    # user actually owns for a mainstream song.
    re.compile(r"\boriginal motion picture\b", re.IGNORECASE),
    re.compile(r"\boriginal soundtrack\b", re.IGNORECASE),
    re.compile(r"\bmotion picture soundtrack\b", re.IGNORECASE),
    # Date-prefixed live bootlegs: "2022-08-18, The Icy Tour: ...".
    re.compile(r"^\d{4}[-:/ ]"),
    re.compile(r"\bexpanded edition\b", re.IGNORECASE),
    re.compile(r"\bdisco fever\b", re.IGNORECASE),
    re.compile(r"\d+\s+(joints|hits|tracks|songs|classics)\b", re.IGNORECASE),
    re.compile(r"\(\d{4}\s+yt\)", re.IGNORECASE),
]

_BAD_ALBUM_PENALTY = 0.75


def _album_quality(candidate: TrackMetadata) -> float:
    """Multiplier in (0, 1] applied to a candidate's score.

    Canonical releases get 1.0; candidates whose album looks like a
    live set, compilation, or promo get ``_BAD_ALBUM_PENALTY``.
    """
    if not candidate.album:
        return 1.0
    for pattern in _BAD_ALBUM_PATTERNS:
        if pattern.search(candidate.album):
            return _BAD_ALBUM_PENALTY
    return 1.0


def _fuzzy(a: Optional[str], b: Optional[str]) -> Optional[float]:
    """Case-insensitive fuzzy similarity between two strings.

    RapidFuzz's token_set_ratio is case-sensitive, which would score
    "twenty one pilots" vs "Twenty One Pilots" at only 0.82. We
    lowercase both sides first to avoid spurious rejections.
    """
    if not a or not b:
        return None
    return fuzz.token_set_ratio(a.lower(), b.lower()) / 100.0


def _duration_score(query: Optional[float], candidate: Optional[float]) -> Optional[float]:
    if query is None or candidate is None:
        return None
    diff = abs(query - candidate)
    if diff <= _DURATION_PERFECT:
        return 1.0
    if diff >= _DURATION_MAX:
        return 0.0
    return 1.0 - (diff - _DURATION_PERFECT) / (_DURATION_MAX - _DURATION_PERFECT)


def _completeness(candidate: TrackMetadata) -> float:
    fields_present = sum(
        1
        for v in (candidate.album, candidate.year, candidate.artwork_url, candidate.track_number)
        if v
    )
    return fields_present / 4.0


def score_candidate(
    candidate: TrackMetadata,
    query_artist: Optional[str],
    query_title: Optional[str],
    query_duration: Optional[float],
) -> float:
    """Return a 0..1 confidence score for a single candidate.

    A candidate that supplies neither a title nor an artist is scored 0
    outright — there is nothing to verify it against, and letting such
    candidates win the spine vote (as Discogs release-level results
    occasionally did) corrupts the file with an unrelated album name.
    """
    if candidate.title is None and candidate.artist is None:
        return 0.0

    signals: list[tuple[float, float]] = []

    title_sim = _fuzzy(query_title, candidate.title)
    if title_sim is not None:
        signals.append((title_sim, _WEIGHT_TITLE))

    artist_sim = _fuzzy(query_artist, candidate.artist)
    if artist_sim is not None:
        signals.append((artist_sim, _WEIGHT_ARTIST))

    dur_score = _duration_score(query_duration, candidate.duration)
    if dur_score is not None:
        signals.append((dur_score, _WEIGHT_DURATION))

    signals.append((_completeness(candidate), _WEIGHT_COMPLETENESS))

    provider_weight = _PROVIDER_WEIGHTS.get(candidate.source_provider or "", 0.5)
    signals.append((provider_weight, _WEIGHT_PROVIDER))

    total_weight = sum(w for _, w in signals)
    if total_weight <= 0:
        return 0.0
    base_score = sum(s * w for s, w in signals) / total_weight
    return base_score * _album_quality(candidate)


def cross_validate(
    scored: list[tuple[TrackMetadata, float]],
) -> list[tuple[TrackMetadata, float]]:
    """Boost scores for candidates that multiple providers agree on."""
    if len(scored) < 2:
        return scored

    result: list[tuple[TrackMetadata, float]] = []
    for i, (cand_i, score_i) in enumerate(scored):
        boosted = score_i
        for j, (cand_j, _) in enumerate(scored):
            if i == j:
                continue
            if cand_i.source_provider == cand_j.source_provider:
                continue
            artist_agree = _fuzzy(cand_i.artist, cand_j.artist) or 0.0
            title_agree = _fuzzy(cand_i.title, cand_j.title) or 0.0
            if artist_agree >= 0.9 and title_agree >= 0.9:
                boosted = min(1.0, boosted + 0.1)
                break
        result.append((cand_i, boosted))
    return result


# =========================================================================
# Complementary-field merging
# =========================================================================

def _merge_complementary(
    spine: TrackMetadata,
    others: list[tuple[TrackMetadata, float]],
) -> TrackMetadata:
    """Fill empty fields on ``spine`` from other high-agreement candidates.

    Candidates are grouped by provider. For each provider we pick the
    best matching candidate, preferring ones whose album is not flagged
    by ``_album_quality`` — this is what keeps MusicBrainz from
    contributing a live-album or compilation name when it also happened
    to return the canonical studio release.
    """
    contributing: set[str] = set()
    if spine.source_provider:
        contributing.add(spine.source_provider)

    # Group non-spine candidates by provider, preserving score order.
    by_provider: dict[str, list[TrackMetadata]] = defaultdict(list)
    provider_order: list[str] = []
    for candidate, _score in others:
        provider = candidate.source_provider or ""
        if provider in contributing:
            continue
        if provider not in by_provider:
            provider_order.append(provider)
        by_provider[provider].append(candidate)

    merged = spine
    for provider in provider_order:
        chosen: Optional[TrackMetadata] = None
        fallback: Optional[TrackMetadata] = None

        for candidate in by_provider[provider]:
            artist_agree = _fuzzy(candidate.artist, merged.artist) or 0.0
            title_agree = _fuzzy(candidate.title, merged.title) or 0.0
            if artist_agree < 0.9 or title_agree < 0.9:
                continue
            if fallback is None:
                fallback = candidate
            if _album_quality(candidate) == 1.0:
                chosen = candidate
                break

        if chosen is None:
            chosen = fallback
        if chosen is None:
            continue

        merged = merged.merge(chosen)
        contributing.add(provider)

    return merged


# =========================================================================
# Query variants + fallback ladder
# =========================================================================

DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MIN_CONFIDENCE = 0.60

_ARTIST_TITLE_SEPARATOR = re.compile(r"\s+-\s+")


def build_query_variants(original_path: Path, cleaned_query: str) -> list[str]:
    """Ordered, de-duplicated query strings to try."""
    variants: list[str] = []

    def _add(candidate: Optional[str]) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    _add(cleaned_query)
    _add(original_path.stem)

    parts = _ARTIST_TITLE_SEPARATOR.split(cleaned_query, maxsplit=1)
    if len(parts) == 2:
        artist, title = (p.strip() for p in parts)
        _add(f"{artist} {title}")
        _add(title)

    return variants


def split_query(query: str) -> tuple[Optional[str], Optional[str]]:
    """Split a cleaned query into (artist, title) for scoring."""
    parts = _ARTIST_TITLE_SEPARATOR.split(query, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip() or None, parts[1].strip() or None
    return None, query.strip() or None


def _collect_from_provider(
    provider: "MetadataProvider",
    variants: list[str],
    query_duration: Optional[float],
    max_retries: int,
    backoff_seconds: float,
    rate_limiters: Optional[RateLimiterRegistry],
) -> list[TrackMetadata]:
    """Try each variant against one provider, returning everything collected."""
    collected: list[TrackMetadata] = []

    for query in variants:
        attempt = 0
        while True:
            if rate_limiters is not None:
                rate_limiters.acquire(provider.name)
            try:
                results = provider.search_multi(query, duration=query_duration)
                collected.extend(results)
                break
            except ProviderBlockedError:
                logger.warning("Provider '%s' blocked; moving on", provider.name)
                return collected
            except ProviderError as exc:
                attempt += 1
                if attempt > max_retries:
                    logger.debug(
                        "Provider '%s' failed on variant '%s': %s",
                        provider.name,
                        query,
                        exc,
                    )
                    break
                wait = backoff_seconds * (2 ** (attempt - 1))
                time.sleep(wait)

    return collected


def resolve_metadata(
    original_path: Path,
    cleaned_query: str,
    providers: list["MetadataProvider"],
    query_duration: Optional[float] = None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    rate_limiters: Optional[RateLimiterRegistry] = None,
) -> MatchResult:
    """Run the full pipeline and return a scored MatchResult.

    Every provider in ``providers`` is queried. The highest-scoring
    candidate becomes the "spine"; any candidate from a different
    provider whose artist+title match the spine at >=0.9 contributes
    its non-empty fields to fill gaps the spine left empty.
    """
    variants = build_query_variants(original_path, cleaned_query)
    query_artist, query_title = split_query(cleaned_query)

    all_candidates: list[TrackMetadata] = []
    for provider in providers:
        all_candidates.extend(
            _collect_from_provider(
                provider, variants, query_duration, max_retries, backoff_seconds, rate_limiters,
            )
        )

    if not all_candidates:
        logger.info("No candidates at all for %s", original_path.name)
        return MatchResult(best=None, confidence=0.0, candidates=[])

    scored: list[tuple[TrackMetadata, float]] = [
        (cand, score_candidate(cand, query_artist, query_title, query_duration))
        for cand in all_candidates
    ]
    scored = cross_validate(scored)
    scored.sort(key=lambda pair: pair[1], reverse=True)

    spine_metadata, spine_score = scored[0]
    merged_metadata = _merge_complementary(spine_metadata, scored[1:])

    scored[0] = (merged_metadata, spine_score)

    logger.info(
        "Top candidate for %s: '%s' by '%s' (spine=%s, confidence=%.2f, %d candidates)",
        original_path.name,
        merged_metadata.title,
        merged_metadata.artist,
        spine_metadata.source_provider,
        spine_score,
        len(scored),
    )

    # Epsilon guards against float rounding: a score of 0.72 that lands
    # as 0.7199999999 on disk should not be rejected by a 0.72 gate.
    if spine_score + 1e-9 < min_confidence:
        logger.info(
            "Below confidence threshold (%.2f < %.2f); not writing %s",
            spine_score,
            min_confidence,
            original_path.name,
        )
        return MatchResult(best=None, confidence=spine_score, candidates=scored)

    return MatchResult(best=merged_metadata, confidence=spine_score, candidates=scored)


# =========================================================================
# Thread pool + thread-safe wrappers
# =========================================================================

class WorkerPool:
    """Runs a callable across items using a bounded thread pool."""

    def __init__(self, max_workers: int = 4) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self._max_workers = max_workers

    def map_unordered(self, fn: Callable[[T], R], items: Iterable[T]) -> Iterator[R]:
        """Submit ``fn(item)`` for every item and yield results as they complete."""
        executor = ThreadPoolExecutor(max_workers=self._max_workers)
        try:
            futures = {executor.submit(fn, item): item for item in items}
            for future in as_completed(futures):
                yield future.result()
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)


class ThreadSafeCache:
    """Serializes access to a ``MetadataCache`` across worker threads."""

    def __init__(self, cache: "MetadataCache") -> None:
        self._cache = cache
        self._lock = threading.Lock()

    def get(self, query: str) -> Optional[tuple[TrackMetadata, float]]:
        with self._lock:
            return self._cache.get(query)

    def set(self, query: str, metadata: TrackMetadata, confidence: float) -> None:
        with self._lock:
            self._cache.set(query, metadata, confidence)


class ThreadSafeCheckpoint:
    """Serializes access to a ``CheckpointManager`` across worker threads."""

    def __init__(self, checkpoint: "CheckpointManager") -> None:
        self._checkpoint = checkpoint
        self._lock = threading.Lock()

    def is_done(self, path: Path) -> bool:
        with self._lock:
            return self._checkpoint.is_done(path)

    def mark_done(self, path: Path, provider_used: Optional[str] = None) -> None:
        with self._lock:
            self._checkpoint.mark_done(path, provider_used)

    def mark_failed(self, path: Path, provider_used: Optional[str] = None) -> None:
        with self._lock:
            self._checkpoint.mark_failed(path, provider_used)