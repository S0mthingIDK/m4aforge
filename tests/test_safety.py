"""Unit tests for safety.py (integrity, backup, dedupe, album validation).

Merged from test_integrity.py + test_backup.py + test_dedupe.py +
test_album.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mutagen.mp4 import MP4

from m4aforge.core import BackupError, IntegrityError, TrackMetadata
from m4aforge.safety import (
    BackupStore,
    compute_checksum,
    find_duplicates_by_checksum,
    find_duplicates_by_metadata,
    validate_all,
    verified_write,
)


# --- Integrity ----------------------------------------------------------

def test_compute_checksum_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello world")
    assert compute_checksum(path) == compute_checksum(path)


def test_compute_checksum_changes_with_content(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    path.write_bytes(b"hello")
    before = compute_checksum(path)
    path.write_bytes(b"goodbye")
    assert compute_checksum(path) != before


def test_verified_write_passes_for_valid_m4a(sample_m4a: Path) -> None:
    with verified_write(sample_m4a):
        audio = MP4(sample_m4a)
        audio.tags["\xa9nam"] = ["New Title"]
        audio.save()

    assert MP4(sample_m4a).tags["\xa9nam"][0] == "New Title"


def test_verified_write_raises_on_corruption(sample_m4a: Path) -> None:
    with pytest.raises(IntegrityError):
        with verified_write(sample_m4a):
            sample_m4a.write_bytes(b"not a valid m4a file anymore")


# --- Backup -------------------------------------------------------------

def test_backup_and_restore_roundtrips(tmp_path: Path) -> None:
    original = tmp_path / "song.m4a"
    original.write_bytes(b"original content")

    store = BackupStore(tmp_path / "backups")
    run_id = store.new_run_id()
    store.backup_file(run_id, original)

    original.write_bytes(b"modified content")
    restored = store.restore(run_id)

    assert restored == [original.resolve()]
    assert original.read_bytes() == b"original content"


def test_undo_last_run_restores_most_recent(tmp_path: Path) -> None:
    original = tmp_path / "song.m4a"
    original.write_bytes(b"v1")

    store = BackupStore(tmp_path / "backups")
    store.backup_file(store.new_run_id(), original)
    original.write_bytes(b"v2")
    store.backup_file(store.new_run_id(), original)
    original.write_bytes(b"v3 - corrupted")

    store.undo_last_run()

    assert original.read_bytes() == b"v2"


def test_undo_last_run_raises_when_no_runs(tmp_path: Path) -> None:
    store = BackupStore(tmp_path / "backups")
    with pytest.raises(BackupError):
        store.undo_last_run()


def test_restore_raises_for_unknown_run(tmp_path: Path) -> None:
    store = BackupStore(tmp_path / "backups")
    with pytest.raises(BackupError):
        store.restore("no-such-run")


def test_prune_old_runs_keeps_only_retention_count(tmp_path: Path) -> None:
    original = tmp_path / "song.m4a"
    original.write_bytes(b"content")

    store = BackupStore(tmp_path / "backups", retention=2)
    for _ in range(4):
        run_id = store.new_run_id() + f"-{_}"
        store.backup_file(run_id, original)

    removed = store.prune_old_runs()

    assert len(removed) == 2
    assert len(store.list_runs()) == 2


# --- Dedupe -------------------------------------------------------------

def test_find_duplicates_by_checksum_groups_identical_content(tmp_path: Path) -> None:
    a = tmp_path / "a.m4a"
    b = tmp_path / "b.m4a"
    c = tmp_path / "c.m4a"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    c.write_bytes(b"different content")

    duplicates = find_duplicates_by_checksum([a, b, c])

    assert len(duplicates) == 1
    (group,) = duplicates.values()
    assert set(group) == {a, b}


def test_find_duplicates_by_checksum_no_duplicates(tmp_path: Path) -> None:
    a = tmp_path / "a.m4a"
    b = tmp_path / "b.m4a"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    assert find_duplicates_by_checksum([a, b]) == {}


def test_find_duplicates_by_metadata_matches_case_insensitively(tmp_path: Path) -> None:
    a = tmp_path / "a.m4a"
    b = tmp_path / "b.m4a"
    tracks = [
        (a, TrackMetadata(artist="Artist", title="Song")),
        (b, TrackMetadata(artist="  artist  ", title="SONG")),
    ]

    duplicates = find_duplicates_by_metadata(tracks)

    assert len(duplicates) == 1
    (group,) = duplicates.values()
    assert set(group) == {a, b}


def test_find_duplicates_by_metadata_skips_incomplete_entries(tmp_path: Path) -> None:
    a = tmp_path / "a.m4a"
    b = tmp_path / "b.m4a"
    tracks = [
        (a, TrackMetadata(artist="Artist", title=None)),
        (b, TrackMetadata(artist=None, title="Song")),
    ]
    assert find_duplicates_by_metadata(tracks) == {}


# --- Album validation ---------------------------------------------------

def test_validate_all_flags_missing_track_number(tmp_path: Path) -> None:
    folder = tmp_path / "album"
    tracks = [
        (folder / "1.m4a", TrackMetadata(album="A", track_number=1)),
        (folder / "2.m4a", TrackMetadata(album="A", track_number=None)),
    ]
    issues = validate_all(tracks)
    assert any(issue.issue_type == "missing_track_number" for issue in issues)


def test_validate_all_flags_duplicate_track_number(tmp_path: Path) -> None:
    folder = tmp_path / "album"
    tracks = [
        (folder / "1.m4a", TrackMetadata(album="A", track_number=1)),
        (folder / "2.m4a", TrackMetadata(album="A", track_number=1)),
    ]
    issues = validate_all(tracks)
    assert any(issue.issue_type == "duplicate_track_number" for issue in issues)


def test_validate_all_flags_inconsistent_album_name(tmp_path: Path) -> None:
    folder = tmp_path / "album"
    tracks = [
        (folder / "1.m4a", TrackMetadata(album="Album A", track_number=1)),
        (folder / "2.m4a", TrackMetadata(album="Album A (Remastered)", track_number=2)),
    ]
    issues = validate_all(tracks)
    assert any(issue.issue_type == "inconsistent_album_name" for issue in issues)


def test_validate_all_returns_no_issues_for_clean_album(tmp_path: Path) -> None:
    folder = tmp_path / "album"
    tracks = [
        (folder / "1.m4a", TrackMetadata(album="A", track_number=1)),
        (folder / "2.m4a", TrackMetadata(album="A", track_number=2)),
    ]
    assert validate_all(tracks) == []


def test_validate_all_skips_folders_with_a_single_track(tmp_path: Path) -> None:
    folder = tmp_path / "album"
    tracks = [(folder / "1.m4a", TrackMetadata(album="A", track_number=None))]
    assert validate_all(tracks) == []