"""Abstract MetadataProvider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from m4aforge.core import MetadataNotFoundError, TrackMetadata


class MetadataProvider(ABC):
    """Interface all metadata search providers must implement."""

    name: str

    @abstractmethod
    def search_multi(self, query: str, duration: float | None = None) -> list[TrackMetadata]:
        """Return up to ~5 candidates for ``query``, best-guess first.

        Never returns None; never raises for "no match" (that's an
        empty list). May raise ProviderBlockedError / Timeout /
        ResponseError for genuine failures.
        """
        raise NotImplementedError

    def search(self, query: str) -> TrackMetadata:
        """Convenience: first candidate, or raise MetadataNotFoundError."""
        results = self.search_multi(query)
        if not results:
            raise MetadataNotFoundError(f"{self.name} has no match for '{query}'")
        return results[0]