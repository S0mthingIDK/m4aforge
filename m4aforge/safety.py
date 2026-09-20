"""File-safety concerns: integrity verification, backups, resume,
deduplication, and album validation.

Merged from integrity.py + backup.py + checkpoint.py + dedupe.py +
album.py.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Optional

from mutagen import MutagenError
from mutagen.mp4 import MP4

from m4aforge.core import BackupError, IntegrityError, TrackMetadata
from m4aforge.logger import get_logger

logger = get_logger()

_CHUNK_SIZE = 1024 * 1024
_MANIFEST_NAME = "manifest.json"


# =========================================================================
# Integrity
# =========================================================================

def compute_checksum(path: Path) -> str:
    """Return the SHA256 hex digest of a file's contents."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def verified_write(path: Path) -> Iterator[None]:
    """Wrap a write with before/after SHA256 and post-write M4A validity check.

    The checksum is expected to differ across the write; the corruption
    signal is the file becoming unreadable afterward.
    """
    checksum_before = compute_checksum(path)

    yield

    checksum_after = compute_checksum(path)

    try:
        MP4(path)
    except (MutagenError, OSError) as exc:
        raise IntegrityError(
            f"{path} failed integrity verification after write "
            f"(before={checksum_before[:12]}, after={checksum_after[:12]}): {exc}"
        ) from exc

    logger.info(
        "Integrity verified for %s (before=%s, after=%s)",
        path,
        checksum_before[:12],
        checksum_after[:12],
    )


# =========================================================================
# Backup
# =========================================================================

@dataclass
class BackupEntry:
    """One file's original location and where its pre-modification copy lives."""

    original_path: str
    backup_path: str


class BackupStore:
    """Keeps pre-modification copies of files, grouped by run ID."""

    def __init__(self, backup_root: Path, retention: int = 5) -> None:
        self._root = backup_root
        self._retention = retention
        self._root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_run_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S")

    def _run_dir(self, run_id: str) -> Path:
        return self._root / run_id

    def _manifest_path(self, run_id: str) -> Path:
        return self._run_dir(run_id) / _MANIFEST_NAME

    def backup_file(self, run_id: str, path: Path) -> Path:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        resolved = path.resolve()
        backup_name = f"{abs(hash(str(resolved)))}_{path.name}"
        backup_path = run_dir / backup_name

        try:
            shutil.copy2(path, backup_path)
        except OSError as exc:
            raise BackupError(f"Could not back up {path}: {exc}") from exc

        self._append_manifest(run_id, resolved, backup_path)
        logger.info("Backed up %s -> %s", path, backup_path)
        return backup_path

    def _read_manifest(self, run_id: str) -> list[BackupEntry]:
        manifest_path = self._manifest_path(run_id)
        if not manifest_path.exists():
            return []
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise BackupError(f"Corrupt backup manifest for run '{run_id}': {exc}") from exc
        return [BackupEntry(**row) for row in data]

    def _append_manifest(self, run_id: str, original: Path, backup: Path) -> None:
        entries = self._read_manifest(run_id)
        entries.append(BackupEntry(str(original), str(backup)))
        self._manifest_path(run_id).write_text(
            json.dumps([asdict(e) for e in entries], indent=2), encoding="utf-8"
        )

    def list_runs(self) -> list[str]:
        runs = [p.name for p in self._root.iterdir() if p.is_dir()]
        return sorted(runs, reverse=True)

    def latest_run(self) -> Optional[str]:
        runs = self.list_runs()
        return runs[0] if runs else None

    def restore(self, run_id: str) -> list[Path]:
        entries = self._read_manifest(run_id)
        if not entries:
            raise BackupError(f"No backup entries found for run '{run_id}'")

        restored: list[Path] = []
        for entry in entries:
            backup_path = Path(entry.backup_path)
            original_path = Path(entry.original_path)
            if not backup_path.exists():
                raise BackupError(f"Missing backup copy: {backup_path}")
            try:
                shutil.copy2(backup_path, original_path)
            except OSError as exc:
                raise BackupError(
                    f"Could not restore {original_path} from {backup_path}: {exc}"
                ) from exc
            restored.append(original_path)
            logger.info("Restored %s from %s", original_path, backup_path)

        return restored

    def undo_last_run(self) -> list[Path]:
        run_id = self.latest_run()
        if run_id is None:
            raise BackupError("No backup runs available to undo")
        return self.restore(run_id)

    def prune_old_runs(self) -> list[str]:
        runs = self.list_runs()
        to_remove = runs[self._retention:]
        for run_id in to_remove:
            shutil.rmtree(self._run_dir(run_id), ignore_errors=True)
            logger.info("Pruned old backup run %s", run_id)
        return to_remove


# =========================================================================
# Deduplication
# =========================================================================

def find_duplicates_by_checksum(paths: list[Path]) -> dict[str, list[Path]]:
    """Group files by content checksum. Returns only true byte-for-byte duplicates."""
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        groups[compute_checksum(path)].append(path)
    return {digest: files for digest, files in groups.items() if len(files) > 1}


def find_duplicates_by_metadata(
    tracks: list[tuple[Path, TrackMetadata]],
) -> dict[tuple[str, str], list[Path]]:
    """Secondary duplicate check grouped by case-insensitive (artist, title)."""
    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for path, meta in tracks:
        if not meta.artist or not meta.title:
            continue
        key = (meta.artist.strip().lower(), meta.title.strip().lower())
        groups[key].append(path)
    return {key: files for key, files in groups.items() if len(files) > 1}


# =========================================================================
# Album validation
# =========================================================================

@dataclass
class AlbumIssue:
    """A single album-level problem found across a folder's tracks."""

    folder: str
    issue_type: str
    detail: str
    paths: list[Path]


def group_by_folder(
    tracks: list[tuple[Path, TrackMetadata]],
) -> dict[Path, list[tuple[Path, TrackMetadata]]]:
    groups: dict[Path, list[tuple[Path, TrackMetadata]]] = defaultdict(list)
    for path, meta in tracks:
        groups[path.parent].append((path, meta))
    return groups


def validate_album(folder: Path, tracks: list[tuple[Path, TrackMetadata]]) -> list[AlbumIssue]:
    """Check one folder's tracks for missing/duplicate track numbers and
    an inconsistent album name."""
    issues: list[AlbumIssue] = []

    missing = [path for path, meta in tracks if meta.track_number is None]
    if missing:
        issues.append(
            AlbumIssue(
                str(folder),
                "missing_track_number",
                f"{len(missing)} track(s) missing a track number",
                missing,
            )
        )

    numbers = Counter(meta.track_number for _, meta in tracks if meta.track_number is not None)
    duplicated = {number for number, count in numbers.items() if count > 1}
    if duplicated:
        dup_paths = [path for path, meta in tracks if meta.track_number in duplicated]
        issues.append(
            AlbumIssue(
                str(folder),
                "duplicate_track_number",
                f"Track number(s) {sorted(duplicated)} used more than once",
                dup_paths,
            )
        )

    album_names = {meta.album for _, meta in tracks if meta.album}
    if len(album_names) > 1:
        issues.append(
            AlbumIssue(
                str(folder),
                "inconsistent_album_name",
                f"Multiple album names found in the same folder: {sorted(album_names)}",
                [path for path, _ in tracks],
            )
        )

    return issues


def validate_all(tracks: list[tuple[Path, TrackMetadata]]) -> list[AlbumIssue]:
    """Run album validation across every folder with more than one track."""
    issues: list[AlbumIssue] = []
    for folder, group in group_by_folder(tracks).items():
        if len(group) < 2:
            continue
        issues.extend(validate_album(folder, group))
    return issues