"""Genius API provider for song/artist matching."""

from __future__ import annotations

import re
from typing import Any, Optional

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

_SEARCH_URL = "https://api.genius.com/search"
_MAX_CANDIDATES = 5

_YEAR_PATTERN = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def search_song_hits(
    query: str,
    token: str,
    session: requests.Session,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    limit: int = _MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` hit result dicts from Genius's Search API."""
    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": query, "per_page": limit}

    try:
        response = session.get(_SEARCH_URL, params=params, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise ProviderTimeoutError(f"Genius request timed out for '{query}'") from exc
    except requests.RequestException as exc:
        raise ProviderResponseError(f"Genius request failed for '{query}': {exc}") from exc

    if response.status_code in (401, 403, 429):
        raise ProviderBlockedError(f"Genius blocked the request (status {response.status_code})")
    if response.status_code != 200:
        raise ProviderResponseError(f"Genius returned status {response.status_code} for '{query}'")

    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderResponseError(f"Genius returned invalid JSON: {exc}") from exc

    hits = (data.get("response") or {}).get("hits") or []
    return [h["result"] for h in hits[:limit] if h.get("result")]


def search_song_hit(
    query: str,
    token: str,
    session: requests.Session,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[dict[str, Any]]:
    """Compatibility shim: first hit or None."""
    hits = search_song_hits(query, token, session, timeout, limit=1)
    return hits[0] if hits else None


class GeniusProvider(MetadataProvider):
    """Queries the Genius Search API for song/artist metadata."""

    name = ProviderName.GENIUS.value

    def __init__(
        self,
        token: str | None,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not token:
            raise ProviderError("Genius provider requires a configured API token")
        self._token = token
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", USER_AGENT)
        self._timeout = timeout

    def search_multi(self, query: str, duration: float | None = None) -> list[TrackMetadata]:
        hits = search_song_hits(query, self._token, self._session, self._timeout)

        candidates: list[TrackMetadata] = []
        for hit in hits:
            primary_artist = hit.get("primary_artist") or {}
            candidates.append(
                TrackMetadata(
                    title=hit.get("title"),
                    artist=primary_artist.get("name"),
                    year=self._extract_year(hit.get("release_date_for_display")),
                    artwork_url=hit.get("song_art_image_url"),
                    source_provider=self.name,
                )
            )
        return candidates

    @staticmethod
    def _extract_year(display_date: str | None) -> str | None:
        if not display_date:
            return None
        match = _YEAR_PATTERN.search(display_date)
        return match.group(0) if match else None