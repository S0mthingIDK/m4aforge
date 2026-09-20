"""SQLite store + metadata cache (merged from db.py + cache.py)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Optional

from m4aforge.core import TrackMetadata
from m4aforge.logger import get_logger

logger = get_logger()

# Bump whenever the cache schema changes shape. Old entries are treated
# as misses and re-resolved.
CACHE_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_files (
    file_path TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    provider_used TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS metadata_cache (
    query_hash TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    provider_used TEXT,
    confidence REAL,
    cache_version INTEGER,
    cached_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_stats (
    provider TEXT PRIMARY KEY,
    hits INTEGER NOT NULL DEFAULT 0,
    misses INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0
);
"""

_STAT_COLUMNS = {"hit": "hits", "miss": "misses", "error": "errors"}

# Whitelist for ALTER TABLE column additions — prevents any future
# refactor from accidentally interpolating user-controlled values into
# DDL.
_ALLOWED_MIGRATION_COLUMNS = {
    ("metadata_cache", "confidence", "REAL"),
    ("metadata_cache", "cache_version", "INTEGER"),
}


class Database:
    """Thin access layer around the SQLite store."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._ensure_column(conn, "metadata_cache", "confidence", "REAL")
            self._ensure_column(conn, "metadata_cache", "cache_version", "INTEGER")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, sql_type: str) -> None:
        key = (table, column, sql_type)
        if key not in _ALLOWED_MIGRATION_COLUMNS:
            raise ValueError(f"Refusing to add unmigrated column: {key}")
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- metadata_cache --------------------------------------------------

    def get_cached_metadata(
        self, query_hash: str
    ) -> Optional[tuple[str, Optional[str], Optional[float], Optional[int]]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT metadata_json, provider_used, confidence, cache_version
                FROM metadata_cache WHERE query_hash = ?
                """,
                (query_hash,),
            ).fetchone()
        return (row[0], row[1], row[2], row[3]) if row else None

    def set_cached_metadata(
        self,
        query_hash: str,
        query: str,
        metadata_json: str,
        provider_used: Optional[str],
        confidence: Optional[float] = None,
        cache_version: Optional[int] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metadata_cache
                    (query_hash, query, metadata_json, provider_used,
                     confidence, cache_version, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(query_hash) DO UPDATE SET
                    metadata_json = excluded.metadata_json,
                    provider_used = excluded.provider_used,
                    confidence = excluded.confidence,
                    cache_version = excluded.cache_version,
                    cached_at = excluded.cached_at
                """,
                (query_hash, query, metadata_json, provider_used, confidence, cache_version),
            )

    def prune_cache_older_than(self, max_age_days: int) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM metadata_cache WHERE cached_at < datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
            return cursor.rowcount

    # --- processed_files --------------------------------------------------

    def mark_processed(self, file_path: str, status: str, provider_used: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_files (file_path, status, provider_used, processed_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(file_path) DO UPDATE SET
                    status = excluded.status,
                    provider_used = excluded.provider_used,
                    processed_at = excluded.processed_at
                """,
                (file_path, status, provider_used),
            )

    def is_processed(self, file_path: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_files WHERE file_path = ? AND status = 'success'",
                (file_path,),
            ).fetchone()
        return row is not None

    # --- provider_stats ---------------------------------------------------

    def record_provider_result(self, provider: str, outcome: str) -> None:
        column = _STAT_COLUMNS[outcome]
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO provider_stats (provider, {column})
                VALUES (?, 1)
                ON CONFLICT(provider) DO UPDATE SET {column} = {column} + 1
                """,
                (provider,),
            )

    def get_provider_stats(self) -> list[tuple[str, int, int, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT provider, hits, misses, errors FROM provider_stats"
            ).fetchall()
        return [tuple(row) for row in rows]


class MetadataCache:
    """Caches provider results so re-runs don't re-hit APIs."""

    def __init__(self, db_path: Path, max_age_days: int = 30, enabled: bool = True) -> None:
        self._enabled = enabled
        self._max_age_days = max_age_days
        self._db = Database(db_path) if enabled else None

    @staticmethod
    def hash_query(query: str) -> str:
        return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()

    def get(self, query: str) -> Optional[tuple[TrackMetadata, float]]:
        """Return (metadata, confidence) for a cached query, or None on a miss."""
        if not self._enabled or self._db is None:
            return None

        row = self._db.get_cached_metadata(self.hash_query(query))
        if row is None:
            return None

        metadata_json, _provider_used, confidence, cache_version = row

        if cache_version != CACHE_VERSION:
            return None
        if confidence is None:
            return None

        try:
            data = json.loads(metadata_json)
        except ValueError:
            logger.warning("Corrupt cache entry for query '%s'; ignoring", query)
            return None

        return TrackMetadata(**data), float(confidence)

    def set(self, query: str, metadata: TrackMetadata, confidence: float) -> None:
        if not self._enabled or self._db is None:
            return

        self._db.set_cached_metadata(
            query_hash=self.hash_query(query),
            query=query,
            metadata_json=json.dumps(asdict(metadata)),
            provider_used=metadata.source_provider,
            confidence=confidence,
            cache_version=CACHE_VERSION,
        )

    def prune_expired(self) -> int:
        if not self._enabled or self._db is None:
            return 0
        return self._db.prune_cache_older_than(self._max_age_days)


class CheckpointManager:
    """Persist per-file progress so an interrupted run resumes."""

    def __init__(self, db_path: Path) -> None:
        self._db = Database(db_path)

    def is_done(self, path: Path) -> bool:
        return self._db.is_processed(str(path.resolve()))

    def mark_done(self, path: Path, provider_used: Optional[str] = None) -> None:
        self._db.mark_processed(str(path.resolve()), status="success", provider_used=provider_used)

    def mark_failed(self, path: Path, provider_used: Optional[str] = None) -> None:
        self._db.mark_processed(str(path.resolve()), status="failed", provider_used=provider_used)