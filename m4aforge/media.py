"""Media handling: folder scanning, filename parsing, tag read/write,
artwork download, and lyrics scraping.

Merged from scanner.py + parser.py + metadata.py + artwork.py +
lyrics.py.
"""

from __future__ import annotations

import io
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup
from mutagen import MutagenError
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image, UnidentifiedImageError
from rapidfuzz import fuzz

from m4aforge.core import (
    AUDIO_EXTENSION,
    DEFAULT_TIMEOUT_SECONDS,
    MetadataReadError,
    MetadataWriteError,
    ParseError,
    ProviderBlockedError,
    ProviderError,
    ProviderResponseError,
    ScanError,
    ScanItem,
    TrackMetadata,
    USER_AGENT,
)
from m4aforge.logger import get_logger
from m4aforge.providers.genius import search_song_hit

logger = get_logger()


# =========================================================================
# Scanner
# =========================================================================

def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _walk(folder: Path) -> Iterator[ScanItem]:
    for path in sorted(folder.rglob(f"*{AUDIO_EXTENSION}")):
        if not path.is_file():
            continue
        relative = path.relative_to(folder)
        if _is_hidden(relative):
            continue
        if path.suffix.lower() != AUDIO_EXTENSION:
            continue
        yield ScanItem(path=path)


def scan_folder(folder: Path) -> list[ScanItem]:
    """Recursively find all .m4a files under ``folder``."""
    if not folder.exists():
        raise ScanError(f"Folder does not exist: {folder}")
    if not folder.is_dir():
        raise ScanError(f"Not a directory: {folder}")
    return list(_walk(folder))


# =========================================================================
# Filename parsing
# =========================================================================

_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\(\s*official\s*(audio|video|music\s*video)\s*\)", re.IGNORECASE),
    re.compile(r"\(\s*lyric(s)?(\s*video)?\s*\)", re.IGNORECASE),
    re.compile(r"\[\s*(hd|4k|hq)\s*\]", re.IGNORECASE),
    re.compile(r"\(\s*(hd|4k|hq)\s*\)", re.IGNORECASE),
    re.compile(r"\(\s*live\b[^)]*\)", re.IGNORECASE),
    re.compile(r"\(\s*re[- ]?master(ed)?\b[^)]*\)", re.IGNORECASE),
]

_FEATURE_PATTERN = re.compile(
    r"\s*[\(\[]?\s*(feat\.?|ft\.?|featuring)\s+[^()\[\]]*?(?=\s*-\s*|[)\]]|$)",
    re.IGNORECASE,
)

_TRACK_NUMBER_PATTERN = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*")
_WHITESPACE_PATTERN = re.compile(r"\s+")

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_DOT_SPACE = re.compile(r"[ .]+$")


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """Replace characters invalid in Windows/Linux/macOS filenames."""
    cleaned = _INVALID_FILENAME_CHARS.sub(replacement, name)
    cleaned = _TRAILING_DOT_SPACE.sub("", cleaned)
    return cleaned or replacement


def clean_filename(path: Path) -> str:
    """Return a cleaned search query derived from a file's stem."""
    stem = path.stem
    if not stem.strip():
        raise ParseError(f"Filename has no usable stem: {path}")

    text = stem
    text = _TRACK_NUMBER_PATTERN.sub("", text)
    text = _FEATURE_PATTERN.sub("", text)
    for pattern in _NOISE_PATTERNS:
        text = pattern.sub("", text)

    text = _WHITESPACE_PATTERN.sub(" ", text).strip(" -_.")

    if not text:
        raise ParseError(f"Filename cleaned to empty string: {path}")

    return text


# =========================================================================
# Tag read/write
# =========================================================================

_TAG_MAP = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "genre": "\xa9gen",
    "year": "\xa9day",
    "album_artist": "aART",
    "composer": "\xa9wrt",
    "comment": "\xa9cmt",
    "copyright": "cprt",
    "lyrics": "\xa9lyr",
}

_COVER_FORMATS = {
    b"\xff\xd8\xff": MP4Cover.FORMAT_JPEG,
    b"\x89PNG": MP4Cover.FORMAT_PNG,
}


def read_tags(path: Path) -> TrackMetadata:
    """Read existing M4A tags from ``path`` into a TrackMetadata object."""
    try:
        audio = MP4(path)
    except (MutagenError, OSError) as exc:
        raise MetadataReadError(f"Could not read tags from {path}: {exc}") from exc

    tags = audio.tags or {}

    def _first(key: str) -> Optional[str]:
        value = tags.get(key)
        if not value:
            return None
        return str(value[0])

    track_number = None
    if "trkn" in tags and tags["trkn"]:
        track_number = tags["trkn"][0][0]

    disc_number = None
    if "disk" in tags and tags["disk"]:
        disc_number = tags["disk"][0][0]

    explicit = None
    if "rtng" in tags and tags["rtng"]:
        explicit = bool(tags["rtng"][0])

    compilation = None
    if "cpil" in tags:
        compilation = bool(tags["cpil"])

    return TrackMetadata(
        title=_first(_TAG_MAP["title"]),
        artist=_first(_TAG_MAP["artist"]),
        album=_first(_TAG_MAP["album"]),
        genre=_first(_TAG_MAP["genre"]),
        year=_first(_TAG_MAP["year"]),
        track_number=track_number,
        album_artist=_first(_TAG_MAP["album_artist"]),
        disc_number=disc_number,
        composer=_first(_TAG_MAP["composer"]),
        comment=_first(_TAG_MAP["comment"]),
        copyright=_first(_TAG_MAP["copyright"]),
        explicit=explicit,
        compilation=compilation,
        lyrics=_first(_TAG_MAP["lyrics"]),
    )


def write_tags(path: Path, metadata: TrackMetadata, overwrite_existing: bool = False) -> None:
    """Write ``metadata`` fields onto the M4A file at ``path``."""
    try:
        audio = MP4(path)
    except (MutagenError, OSError) as exc:
        raise MetadataWriteError(f"Could not open {path} for writing: {exc}") from exc

    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    for field_name, atom in _TAG_MAP.items():
        value = getattr(metadata, field_name)
        if value is None:
            continue
        if not overwrite_existing and tags.get(atom):
            continue
        tags[atom] = [str(value)]

    if metadata.track_number is not None:
        if overwrite_existing or "trkn" not in tags:
            tags["trkn"] = [(metadata.track_number, 0)]

    if metadata.disc_number is not None:
        if overwrite_existing or "disk" not in tags:
            tags["disk"] = [(metadata.disc_number, 0)]

    if metadata.explicit is not None:
        if overwrite_existing or "rtng" not in tags:
            tags["rtng"] = [1 if metadata.explicit else 0]

    if metadata.compilation is not None:
        if overwrite_existing or "cpil" not in tags:
            tags["cpil"] = metadata.compilation

    try:
        audio.save()
    except (MutagenError, OSError) as exc:
        raise MetadataWriteError(f"Could not save tags to {path}: {exc}") from exc

    logger.info("Wrote tags to %s", path)


def write_artwork(path: Path, image_bytes: bytes, overwrite_existing: bool = False) -> None:
    """Embed ``image_bytes`` as cover art (the ``covr`` atom) on ``path``."""
    try:
        audio = MP4(path)
    except (MutagenError, OSError) as exc:
        raise MetadataWriteError(f"Could not open {path} for writing: {exc}") from exc

    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    if not overwrite_existing and tags.get("covr"):
        return

    cover_format = next(
        (fmt for magic, fmt in _COVER_FORMATS.items() if image_bytes.startswith(magic)), None
    )
    if cover_format is None:
        raise MetadataWriteError(f"Artwork for {path} is not a recognized JPEG/PNG image")

    tags["covr"] = [MP4Cover(image_bytes, imageformat=cover_format)]

    try:
        audio.save()
    except (MutagenError, OSError) as exc:
        raise MetadataWriteError(f"Could not save artwork to {path}: {exc}") from exc

    logger.info("Wrote artwork to %s", path)


def resolve_rename_target(path: Path, new_stem: str) -> Path:
    """Return a non-colliding target path for renaming ``path`` to ``new_stem``."""
    new_stem = sanitize_filename(new_stem)
    candidate = path.with_name(f"{new_stem}{path.suffix}")
    if not candidate.exists():
        return candidate
    if candidate.samefile(path):
        return candidate

    counter = 2
    while True:
        candidate = path.with_name(f"{new_stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def rename_file(path: Path, new_stem: str) -> Path:
    """Rename ``path`` to ``new_stem`` (same suffix, same directory)."""
    target = resolve_rename_target(path, new_stem)
    if target.name != f"{new_stem}{path.suffix}":
        logger.warning(
            "Target filename already exists; renaming to %s instead", target.name
        )

    try:
        path.rename(target)
    except OSError as exc:
        raise MetadataWriteError(f"Could not rename {path} to {target}: {exc}") from exc

    logger.info("Renamed %s -> %s", path, target)
    return target


# =========================================================================
# Artwork
# =========================================================================

_ITUNES_SIZE_PATTERN = re.compile(r"\d+x\d+bb\.(jpg|png)$")


def upscale_itunes_artwork_url(url: str, size: int = 1200) -> str:
    """Return an iTunes artwork URL rewritten to request a larger image."""
    if not _ITUNES_SIZE_PATTERN.search(url):
        return url
    return _ITUNES_SIZE_PATTERN.sub(rf"{size}x{size}bb.\1", url)


def download_artwork(
    url: str,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    min_resolution: int = 500,
) -> Optional[bytes]:
    """Download and validate artwork from ``url``. Returns None on any failure."""
    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", USER_AGENT)

    try:
        response = sess.get(url, timeout=timeout)
    except requests.RequestException as exc:
        logger.debug("Artwork download failed for %s: %s", url, exc)
        return None

    if response.status_code != 200:
        logger.debug("Artwork download for %s returned status %d", url, response.status_code)
        return None

    content = response.content
    try:
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError) as exc:
        logger.debug("Artwork at %s is not a valid image: %s", url, exc)
        return None

    if width < min_resolution or height < min_resolution:
        logger.debug(
            "Artwork at %s is below minimum resolution (%dx%d < %d)",
            url, width, height, min_resolution,
        )
        return None

    return content


def fetch_best_artwork(
    urls: list[str],
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    min_resolution: int = 500,
) -> Optional[bytes]:
    """Try each URL in ``urls`` in order, returning the first valid artwork."""
    for url in urls:
        if not url:
            continue
        artwork = download_artwork(
            url, session=session, timeout=timeout, min_resolution=min_resolution
        )
        if artwork is not None:
            return artwork
    return None


# =========================================================================
# Lyrics
# =========================================================================

# Lines that Genius injects into every lyrics page but that are never
# part of the song itself.
_JUNK_LINE_PATTERNS = [
    re.compile(r"^\s*\d*\s*Embed\s*$", re.IGNORECASE),
    re.compile(r"^\s*You might also like\s*$", re.IGNORECASE),
    # "29 Contributors" / "1 Contributor".
    re.compile(r"^\s*\d+\s+Contributors?\s*$", re.IGNORECASE),
    # "Translations" header that precedes the language list.
    re.compile(r"^\s*Translations\s*$", re.IGNORECASE),
    # "Read More" link text.
    re.compile(r"^\s*Read More\s*$", re.IGNORECASE),
    # Production metadata lines: "[Produced by X]", "[Written by Y]".
    re.compile(
        r"^\s*\[(Produced|Written|Directed|Composed|Mixed|Mastered|Arranged)"
        r"\s+by[^\]]*\]\s*$",
        re.IGNORECASE,
    ),
    # "<Anything> Lyrics" heading, e.g. "505 Lyrics" or
    # "Diet Mountain Dew (The Flight Demo) Lyrics". Bracket-free and short.
    re.compile(r"^[^\[\]]{0,90}?\bLyrics\s*$", re.IGNORECASE),
]


# Genius lists translation languages as bare lines before the lyrics body.
_LANGUAGE_NAMES = frozenset(
    name.lower()
    for name in (
        # Major European
        "English", "Español", "Spanish", "Português", "Portuguese",
        "Deutsch", "German", "Français", "French", "Italiano", "Italian",
        "Polski", "Polish", "Nederlands", "Dutch", "Türkçe", "Turkish",
        "Русский", "Russian", "Українська", "Ukrainian",
        "Беларуская", "Belarusian", "Български", "Bulgarian",
        "Српски", "Srpski", "Serbian", "Hrvatski", "Croatian",
        "Bosanski", "Bosnian", "Slovenščina", "Slovenian",
        "Slovenčina", "Slovak", "Česky", "Czech", "Magyar", "Hungarian",
        "Română", "Romanian", "Ελληνικά", "Greek",
        "Svenska", "Swedish", "Norsk", "Norwegian", "Dansk", "Danish",
        "Suomi", "Finnish", "Íslenska", "Icelandic",
        "Latviešu", "Latvian", "Lietuvių", "Lithuanian",
        "Eesti", "Estonian", "Shqip", "Albanian",
        "Cymraeg", "Welsh", "Gaeilge", "Irish", "Gàidhlig",
        "Euskara", "Basque", "Galego", "Galician", "Català", "Catalan",
        # Asian / Middle Eastern
        "日本語", "Japanese", "한국어", "Korean", "中文", "繁體中文", "简体中文",
        "Chinese", "العربية", "Arabic", "עברית", "Hebrew",
        "فارسی", "Persian", "Farsi", "اردو", "Urdu", "پښتو", "Pashto",
        "हिन्दी", "Hindi", "मराठी", "Marathi", "বাংলা", "Bengali",
        "தமிழ்", "Tamil", "తెలుగు", "Telugu", "ગુજરાતી", "Gujarati",
        "ਪੰਜਾਬੀ", "Punjabi", "සිංහල", "Sinhala",
        "ไทย", "Thai", "ລາວ", "Lao", "ភាសាខ្មែរ", "Khmer",
        "မြန်မာဘာသာ", "Burmese", "Tiếng Việt", "Vietnamese",
        "Bahasa Indonesia", "Indonesian", "Bahasa Melayu", "Malay",
        "Basa Jawa", "Javanese", "Filipino", "Tagalog",
        # Other
        "Kiswahili", "Swahili", "Azərbaycan", "Azerbaijani",
        "O'zbek", "Oʻzbek", "Uzbek", "Қазақша", "Kazakh",
        "Romaji", "Romanization", "Romanized",
        "Sakha", "Yakut", "саха тыла",
        "Kreyòl ayisyen", "Haitian Creole",
    )
)


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://genius.com/",
    "Upgrade-Insecure-Requests": "1",
}

_SEARCH_TO_PAGE_DELAY = 0.6
_403_RETRY_DELAY = 3.0

# Minimum fuzzy similarity between our (artist, title) and Genius's
# returned hit. Below this we treat the hit as "not our song".
_LYRICS_MATCH_THRESHOLD = 0.85


class LyricsProvider(ABC):
    """Interface all lyrics providers must implement."""

    @abstractmethod
    def fetch(self, artist: str, title: str) -> Optional[str]:
        raise NotImplementedError


class GeniusLyricsProvider(LyricsProvider):
    """Resolves a song via Genius Search API, then scrapes its lyrics page."""

    def __init__(
        self,
        token: str,
        session: Optional[requests.Session] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._token = token
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch(self, artist: str, title: str) -> Optional[str]:
        hit = search_song_hit(f"{artist} {title}", self._token, self._session, self._timeout)
        if hit is None:
            return None

        # Genius's search will return *any* track that shares a word with
        # the query. Reject hits whose title or artist doesn't fuzzy-match
        # our own metadata. Both sides are lowercased first — RapidFuzz's
        # token_set_ratio is case-sensitive, so "twenty one pilots" vs
        # "Twenty One Pilots" would otherwise score only 0.82.
        hit_title = (hit.get("title") or "").strip()
        hit_artist = ((hit.get("primary_artist") or {}).get("name") or "").strip()

        title_score = (
            fuzz.token_set_ratio(title.lower(), hit_title.lower()) / 100.0
            if title and hit_title
            else 0.0
        )
        artist_score = (
            fuzz.token_set_ratio(artist.lower(), hit_artist.lower()) / 100.0
            if artist and hit_artist
            else 0.0
        )

        if title_score < _LYRICS_MATCH_THRESHOLD or artist_score < _LYRICS_MATCH_THRESHOLD:
            logger.info(
                "Genius returned mismatched lyrics candidate "
                "('%s' by '%s'; title=%.2f, artist=%.2f) — rejecting",
                hit_title,
                hit_artist,
                title_score,
                artist_score,
            )
            return None

        url = hit.get("url")
        if not url:
            return None

        time.sleep(_SEARCH_TO_PAGE_DELAY)
        return self._scrape_lyrics(url)

    def _scrape_lyrics(self, url: str) -> Optional[str]:
        response = self._request_page(url)
        if response is None:
            return None

        if response.status_code == 403:
            logger.info("Genius 403 on %s; retrying after cooldown", url)
            time.sleep(_403_RETRY_DELAY)
            response = self._request_page(url)
            if response is None:
                return None

        if response.status_code in (403, 429):
            raise ProviderBlockedError(
                f"Genius blocked the lyrics page (status {response.status_code})"
            )
        if response.status_code != 200:
            raise ProviderResponseError(
                f"Genius lyrics page returned status {response.status_code}"
            )

        soup = BeautifulSoup(response.text, "html.parser")
        containers = soup.find_all(attrs={"data-lyrics-container": "true"})
        if not containers:
            return None

        text = "\n".join(c.get_text(separator="\n") for c in containers)
        return text if text.strip() else None

    def _request_page(self, url: str) -> Optional[requests.Response]:
        try:
            return self._session.get(url, timeout=self._timeout, headers=_BROWSER_HEADERS)
        except requests.Timeout:
            logger.debug("Genius page timed out: %s", url)
            return None
        except requests.RequestException as exc:
            logger.debug("Genius page request failed: %s", exc)
            return None


def _strip_genius_preamble(lines: list[str]) -> list[str]:
    """Drop the editorial description block that Genius prints before the
    lyrics body.

    Genius puts a short "About this song" paragraph between the language
    header and the actual lyrics. The lyrics body always starts with a
    bracketed section marker — ``[Verse 1]``, ``[Chorus]``,
    ``[Produced by ...]``, ``[Part I]``, etc. So dropping everything up
    to the first such line removes the description in one pass.
    """
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and len(stripped) > 2:
            return lines[i:]

    # No section markers in this song — fall back to dropping through the
    # last "Read More" or ellipsis-terminated line (where descriptions end).
    last_meta = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower() == "read more" or stripped.endswith("…"):
            last_meta = i
    if last_meta >= 0:
        return lines[last_meta + 1:]

    return lines


def _is_language_name(line: str) -> bool:
    """True if ``line`` is just a language name (a Genius translation
    header) rather than a lyric."""
    normalized = line.strip().lower()
    if not normalized:
        return False
    if normalized in _LANGUAGE_NAMES:
        return True
    # Handle bilingual headers like "Русский (Russian)" or "саха тыла (Sakha)".
    match = re.match(r"^([^(]+?)\s*\(([^)]+)\)\s*$", normalized)
    if match:
        left, right = match.group(1).strip(), match.group(2).strip()
        if left in _LANGUAGE_NAMES or right in _LANGUAGE_NAMES:
            return True
    return False


def clean_lyrics(raw_text: str, song_title: Optional[str] = None) -> str:
    """Strip Genius junk from raw lyrics and collapse blank-line runs.

    Three passes:
      1. Drop the editorial description block (everything before the
         first ``[Section]`` marker).
      2. Drop known junk lines (contributor counts, language names,
         "Read More", ``<Title> Lyrics`` headings).
      3. Collapse consecutive blank lines.
    """
    lines = [line.strip() for line in raw_text.splitlines()]
    lines = _strip_genius_preamble(lines)

    kept: list[str] = []
    for line in lines:
        if not line:
            kept.append("")
            continue
        if any(p.match(line) for p in _JUNK_LINE_PATTERNS):
            continue
        if _is_language_name(line):
            continue
        kept.append(line)

    out: list[str] = []
    for line in kept:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)

    return "\n".join(out).strip()


def get_lyrics(
    artist: Optional[str],
    title: Optional[str],
    genius_token: Optional[str],
    embed_lyrics: bool,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[str]:
    if not embed_lyrics or not genius_token or not artist or not title:
        return None

    provider = GeniusLyricsProvider(token=genius_token, session=session, timeout=timeout)

    try:
        raw = provider.fetch(artist, title)
    except ProviderBlockedError as exc:
        logger.warning("Genius lyrics blocked: %s", exc)
        return None
    except ProviderError as exc:
        logger.debug("Genius lyrics failed: %s", exc)
        return None

    if raw is None:
        return None

    return clean_lyrics(raw, song_title=title)