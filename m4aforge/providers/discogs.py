"""Discogs API provider (release-level data only — no tracklist)."""

from __future__ import annotations

import requests

from m4aforge.core import (
    DEFAULT_TIMEOUT_SECONDS,
    ProviderBlockedError,
    ProviderError,
    ProviderName,
    ProviderResponseError,
    ProviderTimeoutError,
    TrackMetadata,
    USER_AGENT,
)
from m4aforge.logger import get_logger
from m4aforge.providers.base import MetadataProvider

logger = get_logger()

_SEARCH_URL = "https://api.discogs.com/database/search"
_MAX_CANDIDATES = 5


class DiscogsProvider(MetadataProvider):
    """Queries the Discogs database search API for release metadata."""

    name = ProviderName.DISCOGS.value

    def __init__(
        self,
        token: str | None,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not token:
            raise ProviderError("Discogs provider requires a configured API token")
        self._token = token
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", USER_AGENT)
        self._timeout = timeout

    def search_multi(self, query: str, duration: float | None = None) -> list[TrackMetadata]:
        params = {
            "q": query,
            "type": "release",
            "token": self._token,
            "per_page": _MAX_CANDIDATES,
        }

        try:
            response = self._session.get(_SEARCH_URL, params=params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise ProviderTimeoutError(f"Discogs request timed out for '{query}'") from exc
        except requests.RequestException as exc:
            raise ProviderResponseError(f"Discogs request failed for '{query}': {exc}") from exc

        if response.status_code in (401, 403, 429):
            raise ProviderBlockedError(
                f"Discogs blocked the request (status {response.status_code})"
            )
        if response.status_code != 200:
            raise ProviderResponseError(
                f"Discogs returned status {response.status_code} for '{query}'"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError(f"Discogs returned invalid JSON: {exc}") from exc

        candidates: list[TrackMetadata] = []
        for result in data.get("results") or []:
            formats = result.get("format") or []
            genres = result.get("genre") or []
            candidates.append(
                TrackMetadata(
                    # Discogs never gives a track title; leave it None so
                    # the confidence scorer treats it as "no data here".
                    album=result.get("title"),
                    genre=genres[0] if genres else None,
                    year=str(result.get("year")) if result.get("year") else None,
                    artwork_url=result.get("cover_image"),
                    compilation=any(str(f).lower() == "compilation" for f in formats),
                    source_provider=self.name,
                )
            )
        return candidates