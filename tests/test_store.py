"""Unit tests for store.py (Database, MetadataCache, CheckpointManager).

Merged from test_cache.py + test_checkpoint.py.
"""

from __future__ import annotations

from pathlib import Path

from m4aforge.core import TrackMetadata
from m4aforge.store import CheckpointManager, MetadataCache


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    cache = MetadataCache(db_path=tmp_path / "cache.db")
    assert cache.get("nonexistent query") is None


def test_cache_set_then_get_roundtrips(tmp_path: Path) -> None:
    cache = MetadataCache(db_path=tmp_path / "cache.db")
    metadata = TrackMetadata(title="Song", artist="Artist", source_provider="itunes")

    cache.set("Artist Song", metadata, confidence=0.9)

    result = cache.get("Artist Song")
    assert result is not None
    cached_metadata, confidence = result
    assert cached_metadata == metadata
    assert confidence == 0.9


def test_cache_key_is_case_and_whitespace_insensitive(tmp_path: Path) -> None:
    cache = MetadataCache(db_path=tmp_path / "cache.db")
    metadata = TrackMetadata(title="Song", source_provider="itunes")

    cache.set("Artist Song", metadata, confidence=0.8)

    result = cache.get("  artist song  ")
    assert result is not None
    assert result[0] == metadata


def test_cache_disabled_never_persists(tmp_path: Path) -> None:
    cache = MetadataCache(db_path=tmp_path / "cache.db", enabled=False)
    metadata = TrackMetadata(title="Song", source_provider="itunes")

    cache.set("Artist Song", metadata, confidence=0.9)

    assert cache.get("Artist Song") is None


def test_prune_expired_disabled_returns_zero(tmp_path: Path) -> None:
    cache = MetadataCache(db_path=tmp_path / "cache.db", enabled=False)
    assert cache.prune_expired() == 0


def test_file_not_done_by_default(tmp_path: Path) -> None:
    checkpoint = CheckpointManager(tmp_path / "checkpoint.db")
    assert checkpoint.is_done(tmp_path / "song.m4a") is False


def test_mark_done_makes_is_done_true(tmp_path: Path) -> None:
    checkpoint = CheckpointManager(tmp_path / "checkpoint.db")
    song = tmp_path / "song.m4a"

    checkpoint.mark_done(song, provider_used="itunes")

    assert checkpoint.is_done(song) is True


def test_mark_failed_does_not_mark_done(tmp_path: Path) -> None:
    checkpoint = CheckpointManager(tmp_path / "checkpoint.db")
    song = tmp_path / "song.m4a"

    checkpoint.mark_failed(song, provider_used="itunes")

    assert checkpoint.is_done(song) is False