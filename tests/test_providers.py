"""Unit tests for all metadata providers (mocked HTTP).

Merged from test_discogs.py + test_genius.py + test_itunes.py +
test_musicbrainz.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from m4aforge.core import (
    MetadataNotFoundError,
    ProviderBlockedError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from m4aforge.providers.discogs import DiscogsProvider
from m4aforge.providers.genius import GeniusProvider, search_song_hit
from m4aforge.providers.itunes import ITunesProvider
from m4aforge.providers.musicbrainz import MusicBrainzProvider


def _mock_session(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    session.get.return_value = response
    return session


# --- iTunes -------------------------------------------------------------

def test_itunes_search_returns_metadata_on_match() -> None:
    session = _mock_session(
        200,
        {
            "results": [
                {
                    "trackName": "Song",
                    "artistName": "Artist",
                    "collectionName": "Album",
                    "primaryGenreName": "Pop",
                    "releaseDate": "2020-01-01T00:00:00Z",
                    "trackNumber": 3,
                    "artworkUrl100": "http://example.com/art.jpg",
                }
            ]
        },
    )
    provider = ITunesProvider(session=session)
    result = provider.search("Artist Song")

    assert result.title == "Song"
    assert result.artist == "Artist"
    assert result.album == "Album"
    assert result.year == "2020"
    assert result.track_number == 3
    assert result.source_provider == "itunes"


def test_itunes_search_raises_not_found_on_empty_results() -> None:
    session = _mock_session(200, {"results": []})
    provider = ITunesProvider(session=session)
    with pytest.raises(MetadataNotFoundError):
        provider.search("Nonexistent Song")


def test_itunes_search_raises_blocked_on_403() -> None:
    session = _mock_session(403, {})
    provider = ITunesProvider(session=session)
    with pytest.raises(ProviderBlockedError):
        provider.search("Artist Song")


def test_itunes_search_raises_response_error_on_bad_status() -> None:
    session = _mock_session(500, {})
    provider = ITunesProvider(session=session)
    with pytest.raises(ProviderResponseError):
        provider.search("Artist Song")


def test_itunes_search_raises_timeout_error() -> None:
    session = _mock_session()
    session.get.side_effect = requests.Timeout()
    provider = ITunesProvider(session=session)
    with pytest.raises(ProviderTimeoutError):
        provider.search("Artist Song")


# --- MusicBrainz --------------------------------------------------------

def test_musicbrainz_search_returns_metadata_on_match() -> None:
    session = _mock_session(
        200,
        {
            "recordings": [
                {
                    "title": "Song",
                    "artist-credit": [{"name": "Artist"}],
                    "releases": [{"title": "Album", "date": "2020-01-01"}],
                }
            ]
        },
    )
    provider = MusicBrainzProvider(session=session)
    result = provider.search("Artist Song")

    assert result.title == "Song"
    assert result.artist == "Artist"
    assert result.album == "Album"
    assert result.year == "2020"
    assert result.source_provider == "musicbrainz"


def test_musicbrainz_raises_not_found_on_empty() -> None:
    session = _mock_session(200, {"recordings": []})
    provider = MusicBrainzProvider(session=session)
    with pytest.raises(MetadataNotFoundError):
        provider.search("Nonexistent Song")


def test_musicbrainz_raises_blocked_on_403() -> None:
    """403 is an explicit refusal — the fallback chain should skip
    MusicBrainz for the rest of the track."""
    session = _mock_session(403, {})
    provider = MusicBrainzProvider(session=session)
    with pytest.raises(ProviderBlockedError):
        provider.search("Artist Song")


def test_musicbrainz_raises_response_error_on_503() -> None:
    """503 is transient overload — the retry ladder should handle it,
    not treat it as a block."""
    session = _mock_session(503, {})
    provider = MusicBrainzProvider(session=session)
    with pytest.raises(ProviderResponseError):
        provider.search("Artist Song")


# --- Discogs ------------------------------------------------------------

def test_discogs_provider_requires_token() -> None:
    with pytest.raises(ProviderError):
        DiscogsProvider(token=None)


def test_discogs_returns_album_level_fields_only() -> None:
    session = _mock_session(
        200,
        {
            "results": [
                {
                    "title": "Artist - Album",
                    "genre": ["Rock"],
                    "year": "2020",
                    "cover_image": "http://example.com/cover.jpg",
                    "format": ["Vinyl", "Compilation"],
                }
            ]
        },
    )
    provider = DiscogsProvider(token="tok", session=session)
    result = provider.search("Artist Album")

    assert result.title is None
    assert result.album == "Artist - Album"
    assert result.genre == "Rock"
    assert result.compilation is True


def test_discogs_raises_blocked_on_429() -> None:
    session = _mock_session(429, {})
    provider = DiscogsProvider(token="tok", session=session)
    with pytest.raises(ProviderBlockedError):
        provider.search("Artist Album")


# --- Genius -------------------------------------------------------------

_HIT = {
    "title": "Song",
    "primary_artist": {"name": "Artist"},
    "release_date_for_display": "January 1, 2020",
    "song_art_image_url": "http://example.com/art.jpg",
    "url": "http://genius.com/artist-song-lyrics",
}


def test_genius_provider_requires_token() -> None:
    with pytest.raises(ProviderError):
        GeniusProvider(token=None)


def test_genius_returns_metadata_on_match() -> None:
    session = _mock_session(200, {"response": {"hits": [{"result": _HIT}]}})
    provider = GeniusProvider(token="tok", session=session)
    result = provider.search("Artist Song")

    assert result.title == "Song"
    assert result.artist == "Artist"
    assert result.year == "2020"


def test_genius_raises_not_found_on_no_hits() -> None:
    session = _mock_session(200, {"response": {"hits": []}})
    provider = GeniusProvider(token="tok", session=session)
    with pytest.raises(MetadataNotFoundError):
        provider.search("Nonexistent Song")


def test_genius_raises_blocked_on_401() -> None:
    session = _mock_session(401, {})
    provider = GeniusProvider(token="tok", session=session)
    with pytest.raises(ProviderBlockedError):
        provider.search("Artist Song")


def test_search_song_hit_returns_none_without_hits() -> None:
    session = _mock_session(200, {"response": {"hits": []}})
    assert search_song_hit("q", "tok", session) is None


def test_search_song_hit_returns_top_result() -> None:
    session = _mock_session(200, {"response": {"hits": [{"result": _HIT}]}})
    assert search_song_hit("q", "tok", session) == _HIT