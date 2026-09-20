"""MusicBrainz API implementation of the MetadataProvider interface."""

from __future__ import annotations

import requests

from m4aforge.core import (
    DEFAULT_TIMEOUT_SECONDS,
    ProviderBlockedError,
    ProviderName,
    ProviderResponseError,
    ProviderTimeoutError,
    TrackMetadata,
)
from m4aforge.logger import get_logger
from m4aforge.providers.base import MetadataProvider

logger = get_logger()

_SEARCH_URL = "https://musicbrainz.org/ws/2/recording/"
_MAX_CANDIDATES = 8

_USER_AGENT = "M4AForge/1.0.0 (https://github.com/S0mthingIDK/M4AForge)"


class MusicBrainzProvider(MetadataProvider):
    """Queries the MusicBrainz recording search API for song metadata."""

    name = ProviderName.MUSICBRAINZ.value

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", _USER_AGENT)
        self._timeout = timeout

    def search_multi(self, query: str, duration: float | None = None) -> list[TrackMetadata]:
        params = {"query": query, "fmt": "json", "limit": _MAX_CANDIDATES}

        try:
            response = self._session.get(_SEARCH_URL, params=params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise ProviderTimeoutError(f"MusicBrainz request timed out for '{query}'") from exc
        except requests.RequestException as exc:
            raise ProviderResponseError(f"MusicBrainz request failed for '{query}': {exc}") from exc

        if response.status_code in (403, 429, 503):
            raise ProviderBlockedError(
                f"MusicBrainz blocked the request (status {response.status_code})"
            )
        if response.status_code != 200:
            raise ProviderResponseError(
                f"MusicBrainz returned status {response.status_code} for '{query}'"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError(f"MusicBrainz returned invalid JSON: {exc}") from exc

        candidates: list[TrackMetadata] = []
        for recording in data.get("recordings") or []:
            artist_credit = recording.get("artist-credit") or []
            artist = artist_credit[0].get("name") if artist_credit else None
            releases = recording.get("releases") or []
            release = releases[0] if releases else {}
            length_ms = recording.get("length")

            candidates.append(
                TrackMetadata(
                    title=recording.get("title"),
                    artist=artist,
                    album=release.get("title"),
                    year=self._extract_year(release.get("date")),
                    duration=(length_ms / 1000.0) if length_ms else None,
                    source_provider=self.name,
                )
            )
        return candidates

    @staticmethod
    def _extract_year(date: str | None) -> str | None:
        if not date or len(date) < 4:
            return None
        return date[:4]