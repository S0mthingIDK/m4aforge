"""Unit tests for media.py (scanner + filename parser).

Merged from test_scanner.py + test_parser.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from m4aforge.core import ParseError, ScanError
from m4aforge.media import clean_filename, scan_folder


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


# --- Scanner ------------------------------------------------------------

def test_scan_folder_finds_m4a_files(tmp_path: Path) -> None:
    _touch(tmp_path / "song1.m4a")
    _touch(tmp_path / "sub" / "song2.m4a")
    _touch(tmp_path / "notes.txt")

    items = scan_folder(tmp_path)

    found = {item.path.name for item in items}
    assert found == {"song1.m4a", "song2.m4a"}


def test_scan_folder_skips_hidden_directories(tmp_path: Path) -> None:
    _touch(tmp_path / ".hidden" / "song.m4a")
    _touch(tmp_path / "visible.m4a")

    items = scan_folder(tmp_path)

    found = {item.path.name for item in items}
    assert found == {"visible.m4a"}


def test_scan_folder_raises_on_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(ScanError):
        scan_folder(tmp_path / "does_not_exist")


def test_scan_folder_raises_on_file_not_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "not_a_dir.m4a"
    _touch(file_path)
    with pytest.raises(ScanError):
        scan_folder(file_path)


# --- Parser -------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,expected",
    [
        ("Artist - Song (Official Audio).m4a", "Artist - Song"),
        ("Artist - Song (Official Video).m4a", "Artist - Song"),
        ("Artist - Song (Lyrics).m4a", "Artist - Song"),
        ("Artist - Song (Lyric Video).m4a", "Artist - Song"),
        ("Artist - Song [HD].m4a", "Artist - Song"),
        ("Artist - Song (Live at Wembley).m4a", "Artist - Song"),
        ("Artist - Song (Remastered 2011).m4a", "Artist - Song"),
        ("Artist - Song (Remaster).m4a", "Artist - Song"),
        ("Artist feat. Other Artist - Song.m4a", "Artist - Song"),
        ("Artist ft. Other - Song.m4a", "Artist - Song"),
        ("01 - Artist - Song.m4a", "Artist - Song"),
        ("01. Artist - Song.m4a", "Artist - Song"),
        ("01_Artist - Song.m4a", "Artist - Song"),
    ],
)
def test_clean_filename_strips_noise(filename: str, expected: str) -> None:
    assert clean_filename(Path(filename)) == expected


def test_clean_filename_raises_on_empty_result() -> None:
    with pytest.raises(ParseError):
        clean_filename(Path("(Official Audio).m4a"))


def test_clean_filename_raises_on_whitespace_only_stem() -> None:
    with pytest.raises(ParseError):
        clean_filename(Path("   .m4a"))


def test_clean_filename_leaves_clean_names_untouched() -> None:
    assert clean_filename(Path("Artist - Song.m4a")) == "Artist - Song"