"""Core primitives: enums, constants, exceptions, models, and Config.

Merged from the original constants.py + exceptions.py + models.py +
config.py — they are all small, inter-related primitives with no
external dependencies.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# =========================================================================
# Enums
# =========================================================================

class Mode(str, Enum):
    """Pipeline run mode."""

    NORMAL = "normal"
    DRY_RUN = "dry_run"


class ProviderName(str, Enum):
    """Identifiers for supported metadata/lyrics providers."""

    ITUNES = "itunes"
    MUSICBRAINZ = "musicbrainz"
    DISCOGS = "discogs"
    GENIUS = "genius"
    OLLAMA = "ollama"


class LogLevel(str, Enum):
    """Supported logging verbosity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# =========================================================================
# Constants
# =========================================================================

DEFAULT_CONFIG_PATH = Path("config.json")
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "m4aforge.log"

DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_LOG_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_LOG_BACKUP_COUNT = 3

AUDIO_EXTENSION = ".m4a"
USER_AGENT = "M4AForge/1.0.0"


# =========================================================================
# Exceptions
# =========================================================================

class M4AEnricherError(Exception):
    """Base class for all application-specific errors."""


class ConfigError(M4AEnricherError):
    """Config file missing, unreadable, or invalid."""


class ScanError(M4AEnricherError):
    """Folder scan failed (missing folder, permission error, etc.)."""


class ParseError(M4AEnricherError):
    """Filename cannot be parsed into a usable search query."""


class MetadataReadError(M4AEnricherError):
    """M4A file's existing tags cannot be read."""


class MetadataWriteError(M4AEnricherError):
    """Writing tags to an M4A file failed."""


class ProviderError(M4AEnricherError):
    """Base class for metadata provider errors."""


class ProviderBlockedError(ProviderError):
    """Provider blocked the request (e.g. anti-bot response)."""


class ProviderTimeoutError(ProviderError):
    """Provider request exceeded the configured timeout."""


class ProviderResponseError(ProviderError):
    """Provider returned a non-OK HTTP status or malformed JSON."""


class MetadataNotFoundError(ProviderError):
    """Provider responded successfully but has no match."""


class IntegrityError(M4AEnricherError):
    """File's checksum does not match after a write."""


class BackupError(M4AEnricherError):
    """Backup or restore operation failed."""


class PluginError(M4AEnricherError):
    """Plugin module failed to load or is malformed."""


# =========================================================================
# Models
# =========================================================================

@dataclass
class TrackMetadata:
    """Metadata for a single track, whether read from a file or a provider."""

    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[str] = None
    track_number: Optional[int] = None
    artwork_url: Optional[str] = None
    source_provider: Optional[str] = None

    # Duration in seconds — strongest single signal for disambiguating
    # same-titled songs and different versions.
    duration: Optional[float] = None

    album_artist: Optional[str] = None
    disc_number: Optional[int] = None
    composer: Optional[str] = None
    comment: Optional[str] = None
    copyright: Optional[str] = None
    explicit: Optional[bool] = None
    compilation: Optional[bool] = None
    lyrics: Optional[str] = None

    def merge(self, other: "TrackMetadata") -> "TrackMetadata":
        """Layer other's set fields onto self (self's existing values win)."""
        merged_values = {}
        for f in fields(self):
            self_value = getattr(self, f.name)
            other_value = getattr(other, f.name)
            merged_values[f.name] = self_value if self_value is not None else other_value
        return TrackMetadata(**merged_values)


@dataclass
class ScanItem:
    """A single .m4a file discovered by the scanner, prior to processing."""

    path: Path
    cleaned_query: Optional[str] = None


@dataclass
class MatchResult:
    """Outcome of a metadata resolution attempt.

    ``best`` is the highest-scoring candidate if and only if its score
    meets the configured confidence threshold. If no candidate clears
    the bar, ``best`` is None and ``candidates`` still carries every
    scored option (so the caller can report them).
    """

    best: Optional[TrackMetadata]
    confidence: float
    candidates: list[tuple[TrackMetadata, float]] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.best is not None


@dataclass
class ProcessResult:
    """Outcome of processing a single file through the pipeline."""

    scan_item: ScanItem
    success: bool
    metadata: Optional[TrackMetadata] = None
    error_message: Optional[str] = None
    stage_failed: Optional[str] = None
    confidence: Optional[float] = None
    rejected_candidates: list[tuple[TrackMetadata, float]] = field(default_factory=list)


# =========================================================================
# Config
# =========================================================================

@dataclass
class Config:
    """Application configuration, loaded from JSON and overridable via CLI."""

    folder: Path = field(default_factory=lambda: Path("."))
    dry_run: bool = False
    verbose: bool = False
    request_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    overwrite_existing: bool = False

    # Provider credentials
    discogs_token: Optional[str] = None
    genius_token: Optional[str] = None

    # Cache
    cache_enabled: bool = True
    cache_db_path: Path = field(default_factory=lambda: Path("cache.db"))
    cache_max_age_days: int = 30

    # Lyrics / artwork
    embed_lyrics: bool = False
    embed_artwork: bool = True
    min_artwork_resolution: int = 500

    # Ollama enrichment
    ollama_enabled: bool = False
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # Provider fallback chain
    provider_priority: list[str] = field(
        default_factory=lambda: ["itunes", "musicbrainz", "discogs", "genius", "ollama"]
    )

    # Backup / threading
    backup_enabled: bool = True
    backup_root: Path = field(default_factory=lambda: Path("backups"))
    backup_retention: int = 5
    max_workers: int = 4

    # Reports / plugins / UI
    report_dir: Path = field(default_factory=lambda: Path("reports"))
    plugins_dir: Path = field(default_factory=lambda: Path("plugins"))
    interactive: bool = False
    theme: str = "default"

    # Confidence gating
    min_confidence: float = 0.60
    strict_confidence: bool = False

    @classmethod
    def load(cls, config_path: Path = DEFAULT_CONFIG_PATH) -> "Config":
        """Load configuration from JSON. Missing file = defaults (not an error)."""
        if not config_path.exists():
            return cls()

        try:
            raw_text = config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc

        try:
            data: dict[str, Any] = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in config file {config_path}: {exc}") from exc

        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "Config":
        known_fields = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known_fields}
        for path_field in ("folder", "cache_db_path", "backup_root", "report_dir", "plugins_dir"):
            if path_field in filtered:
                filtered[path_field] = Path(filtered[path_field])
        try:
            return cls(**filtered)
        except TypeError as exc:
            raise ConfigError(f"Invalid config content: {exc}") from exc

    def apply_cli_overrides(
        self,
        folder: Optional[str] = None,
        dry_run: Optional[bool] = None,
        verbose: Optional[bool] = None,
        workers: Optional[int] = None,
    ) -> "Config":
        """Return a new Config with CLI-supplied values overriding file/defaults."""
        updated = asdict(self)
        if folder is not None:
            updated["folder"] = Path(folder)
        if dry_run is not None:
            updated["dry_run"] = dry_run
        if verbose is not None:
            updated["verbose"] = verbose
        if workers is not None:
            updated["max_workers"] = workers
        updated["folder"] = Path(updated["folder"])
        return Config(**updated)

    def validate(self) -> None:
        """Raise ConfigError if the configuration is unusable."""
        if not isinstance(self.folder, Path):
            raise ConfigError("folder must be a path")
        if self.request_timeout_seconds <= 0:
            raise ConfigError("request_timeout_seconds must be positive")
        if self.cache_max_age_days <= 0:
            raise ConfigError("cache_max_age_days must be positive")
        if self.min_artwork_resolution <= 0:
            raise ConfigError("min_artwork_resolution must be positive")
        if not self.provider_priority:
            raise ConfigError("provider_priority must not be empty")
        if self.backup_retention <= 0:
            raise ConfigError("backup_retention must be positive")
        if self.max_workers <= 0:
            raise ConfigError("max_workers must be positive")
        if not (0.0 <= self.min_confidence <= 1.0):
            raise ConfigError("min_confidence must be between 0 and 1")