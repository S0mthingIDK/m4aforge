"""iTunes Search API implementation of the MetadataProvider interface."""

from __future__ import annotations

import requests

from m4aforge.core import (
    DEFAULT_TIMEOUT_SECONDS,
    ProviderBlockedError,
    ProviderResponseError,
    ProviderTimeoutError,
    TrackMetadata,
    USER_AGENT,
    ProviderName,
)
from m4aforge.logger import get_logger
from m4aforge.providers.base import MetadataProvider

logger = get_logger()

_SEARCH_URL = "https://itunes.apple.com/search"
_MAX_CANDIDATES = 8


class ITunesProvider(MetadataProvider):
    """Queries the public iTunes Search API for song metadata."""

    name = ProviderName.ITUNES.value

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", USER_AGENT)
        self._timeout = timeout

    def search_multi(self, query: str, duration: float | None = None) -> list[TrackMetadata]:
        params = {
            "term": query,
            "media": "music",
            "entity": "song",
            "limit": _MAX_CANDIDATES,
        }

        try:
            response = self._session.get(_SEARCH_URL, params=params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise ProviderTimeoutError(f"iTunes request timed out for '{query}'") from exc
        except requests.RequestException as exc:
            raise ProviderResponseError(f"iTunes request failed for '{query}': {exc}") from exc

        if response.status_code in (403, 429):
            raise ProviderBlockedError(
                f"iTunes blocked the request (status {response.status_code})"
            )
        if response.status_code != 200:
            raise ProviderResponseError(
                f"iTunes returned status {response.status_code} for '{query}'"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError(f"iTunes returned invalid JSON: {exc}") from exc

        candidates: list[TrackMetadata] = []
        for result in data.get("results") or []:
            track_ms = result.get("trackTimeMillis")
            candidates.append(
                TrackMetadata(
                    title=result.get("trackName"),
                    artist=result.get("artistName"),
                    album=result.get("collectionName"),
                    genre=result.get("primaryGenreName"),
                    year=self._extract_year(result.get("releaseDate")),
                    track_number=result.get("trackNumber"),
                    artwork_url=result.get("artworkUrl100"),
                    duration=(track_ms / 1000.0) if track_ms else None,
                    source_provider=self.name,
                )
            )
        return candidates

    @staticmethod
    def _extract_year(release_date: str | None) -> str | None:
        if not release_date or len(release_date) < 4:
            return None
        return release_date[:4]