"""Optional local enrichment step (genre/tag cleanup) via Ollama.

Does NOT implement MetadataProvider — no search() method, so it can't
be wired into the fallback ladder. Runs after the provider chain.
"""

from __future__ import annotations

from dataclasses import replace

import requests

from m4aforge.core import (
    ProviderName,
    ProviderResponseError,
    ProviderTimeoutError,
    TrackMetadata,
)
from m4aforge.logger import get_logger

logger = get_logger()

name = ProviderName.OLLAMA.value

_GENERATE_ENDPOINT = "/api/generate"

_GENRE_CLEANUP_PROMPT = (
    "Normalize this music genre string to a single, common genre name "
    "(e.g. 'Rock/Pop' -> 'Rock', 'Hip-Hop/Rap' -> 'Hip-Hop'). "
    "Reply with only the normalized genre name and nothing else.\n\nGenre: {genre}"
)


class OllamaEnricher:
    """Calls a local Ollama instance to clean up/normalize metadata fields."""

    def __init__(
        self,
        host: str,
        model: str,
        session: requests.Session | None = None,
        timeout: int = 15,
    ) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._session = session or requests.Session()
        self._timeout = timeout

    def enrich(self, metadata: TrackMetadata) -> TrackMetadata:
        """Return a copy of ``metadata`` with the genre field normalized.

        Any failure logs a warning and returns ``metadata`` unchanged —
        enrichment is best-effort and must never fail the pipeline.
        """
        if not metadata.genre:
            return metadata

        try:
            normalized = self._generate(_GENRE_CLEANUP_PROMPT.format(genre=metadata.genre))
        except (ProviderTimeoutError, ProviderResponseError) as exc:
            logger.warning("Ollama enrichment skipped: %s", exc)
            return metadata

        if not normalized:
            return metadata

        return replace(metadata, genre=normalized.strip())

    def _generate(self, prompt: str) -> str | None:
        url = f"{self._host}{_GENERATE_ENDPOINT}"
        payload = {"model": self._model, "prompt": prompt, "stream": False}

        try:
            response = self._session.post(url, json=payload, timeout=self._timeout)
        except requests.Timeout as exc:
            raise ProviderTimeoutError(f"Ollama request timed out: {exc}") from exc
        except requests.RequestException as exc:
            raise ProviderResponseError(f"Ollama request failed: {exc}") from exc

        if response.status_code != 200:
            raise ProviderResponseError(f"Ollama returned status {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError(f"Ollama returned invalid JSON: {exc}") from exc

        return data.get("response")