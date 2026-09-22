"""Unit tests for net.py (rate limiting, retry ladder, thread pool).

Merged from test_ratelimit.py + test_retry.py + test_threadpool.py.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from m4aforge.core import (
    MetadataNotFoundError,
    ProviderBlockedError,
    ProviderResponseError,
    TrackMetadata,
)
from m4aforge.net import (
    RateLimiter,
    RateLimiterRegistry,
    ThreadSafeCache,
    ThreadSafeCheckpoint,
    WorkerPool,
    build_query_variants,
    resolve_metadata,
)
from m4aforge.providers.base import MetadataProvider
from m4aforge.store import CheckpointManager, MetadataCache


# --- Rate limiter --------------------------------------------------------

def test_rate_limiter_allows_burst_up_to_max_calls() -> None:
    limiter = RateLimiter(max_calls=5, period_seconds=1.0)

    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.2


def test_rate_limiter_blocks_beyond_capacity() -> None:
    limiter = RateLimiter(max_calls=2, period_seconds=0.2)

    start = time.monotonic()
    for _ in range(4):
        limiter.acquire()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.15


def test_rate_limiter_rejects_invalid_config() -> None:
    with pytest.raises(ValueError):
        RateLimiter(max_calls=0)
    with pytest.raises(ValueError):
        RateLimiter(max_calls=5, period_seconds=0)


def test_registry_unregistered_provider_is_noop() -> None:
    registry = RateLimiterRegistry()
    registry.acquire("unknown-provider")


def test_registry_acquire_uses_registered_limiter() -> None:
    registry = RateLimiterRegistry()
    registry.register("itunes", max_calls=100, period_seconds=1.0)

    start = time.monotonic()
    for _ in range(10):
        registry.acquire("itunes")
    elapsed = time.monotonic() - start

    assert elapsed < 0.2


# --- Query variants + resolve ladder ------------------------------------

class FakeProvider(MetadataProvider):
    def __init__(self, name: str, behavior: dict) -> None:
        self.name = name
        self._behavior = behavior
        self.calls: list[str] = []

    def search(self, query: str) -> TrackMetadata:
        self.calls.append(query)
        outcome = self._behavior.get(query, MetadataNotFoundError("no match"))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def search_multi(self, query, duration=None):
        try:
            return [self.search(query)]
        except MetadataNotFoundError:
            return []


def test_build_query_variants_splits_artist_title() -> None:
    variants = build_query_variants(
        Path("01 - Artist - Song (Official Audio).m4a"), "Artist - Song"
    )
    assert variants == [
        "Artist - Song",
        "01 - Artist - Song (Official Audio)",
        "Artist Song",
        "Song",
    ]


def test_build_query_variants_no_separator() -> None:
    variants = build_query_variants(Path("Song.m4a"), "Song")
    assert variants == ["Song"]


def test_resolve_metadata_falls_back_across_providers() -> None:
    match = TrackMetadata(title="Song", artist="Artist", source_provider="p2")
    p1 = FakeProvider("p1", {})
    p2 = FakeProvider("p2", {"Song": match})

    result = resolve_metadata(
        Path("Artist - Song.m4a"), "Artist - Song", [p1, p2],
        max_retries=0, backoff_seconds=0, min_confidence=0.0,
    )

    assert result.best is match


def test_resolve_metadata_stops_variant_loop_on_block() -> None:
    match = TrackMetadata(title="Song", source_provider="p2")
    p1 = FakeProvider("p1", {"Artist - Song": ProviderBlockedError("blocked")})
    p2 = FakeProvider("p2", {"Song": match})

    result = resolve_metadata(
        Path("Artist - Song.m4a"), "Artist - Song", [p1, p2],
        max_retries=0, backoff_seconds=0, min_confidence=0.0,
    )

    assert result.best is match
    assert p1.calls == ["Artist - Song"]


def test_resolve_metadata_raises_when_nothing_matches() -> None:
    p1 = FakeProvider("p1", {})

    result = resolve_metadata(
        Path("X.m4a"), "X", [p1], max_retries=0, backoff_seconds=0,
    )
    assert result.best is None


def test_resolve_metadata_retries_transient_errors() -> None:
    match = TrackMetadata(title="Song", source_provider="p1")
    calls = {"n": 0}

    class FlakyProvider(MetadataProvider):
        name = "p1"

        def search(self, query: str) -> TrackMetadata:
            calls["n"] += 1
            if calls["n"] < 2:
                raise ProviderResponseError("transient")
            return match

        def search_multi(self, query, duration=None):
            return [self.search(query)]

    result = resolve_metadata(
        Path("X.m4a"), "X", [FlakyProvider()],
        max_retries=2, backoff_seconds=0, min_confidence=0.0,
    )

    assert result.best is match
    assert calls["n"] == 2


# --- Thread pool + wrappers ---------------------------------------------

def test_worker_pool_runs_all_items() -> None:
    pool = WorkerPool(max_workers=4)
    results = list(pool.map_unordered(lambda x: x * 2, range(10)))
    assert sorted(results) == [x * 2 for x in range(10)]


def test_worker_pool_rejects_non_positive_workers() -> None:
    with pytest.raises(ValueError):
        WorkerPool(max_workers=0)


def test_worker_pool_propagates_exceptions() -> None:
    pool = WorkerPool(max_workers=2)

    def _boom(x: int) -> int:
        if x == 3:
            raise ValueError("boom")
        return x

    with pytest.raises(ValueError):
        list(pool.map_unordered(_boom, range(5)))


def test_thread_safe_cache_roundtrips(tmp_path: Path) -> None:
    cache = ThreadSafeCache(MetadataCache(db_path=tmp_path / "cache.db"))
    metadata = TrackMetadata(title="Song", artist="Artist", source_provider="itunes")

    cache.set("query", metadata, confidence=0.9)
    result = cache.get("query")
    assert result is not None
    assert result[0] == metadata


def test_thread_safe_checkpoint_roundtrips(tmp_path: Path) -> None:
    checkpoint = ThreadSafeCheckpoint(CheckpointManager(tmp_path / "checkpoint.db"))
    song = tmp_path / "song.m4a"

    checkpoint.mark_done(song, provider_used="itunes")

    assert checkpoint.is_done(song) is True


def test_worker_pool_is_actually_concurrent() -> None:
    pool = WorkerPool(max_workers=5)

    def _sleep(x: int) -> int:
        time.sleep(0.1)
        return x

    start = time.monotonic()
    list(pool.map_unordered(_sleep, range(5)))
    elapsed = time.monotonic() - start

    assert elapsed < 0.4

def test_resolve_metadata_merges_complementary_providers() -> None:
    """When iTunes wins on score but Genius has lyrics/artwork the spine
    lacks, fields should merge."""
    # Spine: iTunes candidate with album + track# but no lyrics.
    itunes_hit = TrackMetadata(
        title="Song",
        artist="Artist",
        album="Album A",
        track_number=3,
        source_provider="itunes",
        artwork_url="http://itunes.example/art.jpg",
    )
    # Complementary: Genius with the same song but no album/track#.
    genius_hit = TrackMetadata(
        title="Song",
        artist="Artist",
        year="2020",
        artwork_url="http://genius.example/art.jpg",
        source_provider="genius",
    )

    class Scripted(MetadataProvider):
        def __init__(self, name: str, hit: TrackMetadata) -> None:
            self.name = name
            self._hit = hit

        def search_multi(self, query, duration=None):
            return [self._hit]

    result = resolve_metadata(
        Path("Artist - Song.m4a"),
        "Artist - Song",
        [Scripted("itunes", itunes_hit), Scripted("genius", genius_hit)],
        max_retries=0,
        backoff_seconds=0,
        min_confidence=0.0,
    )

    assert result.best is not None
    assert result.best.album == "Album A"        # from iTunes
    assert result.best.track_number == 3         # from iTunes
    assert result.best.year == "2020"            # merged from Genius
    assert result.best.artwork_url == "http://itunes.example/art.jpg"  # spine wins