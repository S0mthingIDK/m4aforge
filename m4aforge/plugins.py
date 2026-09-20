"""Plugin loader for custom MetadataProvider / LyricsProvider modules."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

from m4aforge.core import PluginError
from m4aforge.logger import get_logger
from m4aforge.media import LyricsProvider
from m4aforge.providers.base import MetadataProvider

if TYPE_CHECKING:
    from m4aforge.core import Config

logger = get_logger()

_PROVIDER_FACTORY_ATTR = "create_provider"
_LYRICS_FACTORY_ATTR = "create_lyrics_provider"


def discover_plugin_files(plugins_dir: Path) -> list[Path]:
    """Return every plugin module in ``plugins_dir``, sorted by name."""
    if not plugins_dir.exists():
        return []
    return sorted(p for p in plugins_dir.glob("*.py") if not p.name.startswith("_"))


def load_plugin_module(path: Path):
    """Import a single plugin file as a standalone module."""
    module_name = f"m4aforge_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginError(f"Could not create import spec for plugin {path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # plugin code is untrusted
        raise PluginError(f"Plugin {path} raised while loading: {exc}") from exc

    return module


def load_metadata_providers(plugins_dir: Path, config: "Config") -> list[MetadataProvider]:
    """Instantiate every plugin's ``create_provider(config)``, if defined."""
    providers: list[MetadataProvider] = []

    for path in discover_plugin_files(plugins_dir):
        try:
            module = load_plugin_module(path)
        except PluginError as exc:
            logger.warning("Skipping plugin %s: %s", path.name, exc)
            continue

        factory = getattr(module, _PROVIDER_FACTORY_ATTR, None)
        if factory is None:
            continue

        try:
            provider = factory(config)
        except Exception as exc:
            logger.warning("Plugin %s failed to create a provider: %s", path.name, exc)
            continue

        if not isinstance(provider, MetadataProvider):
            logger.warning(
                "Plugin %s's create_provider() did not return a MetadataProvider; skipping",
                path.name,
            )
            continue

        logger.info("Loaded metadata provider '%s' from plugin %s", provider.name, path.name)
        providers.append(provider)

    return providers


def load_lyrics_providers(plugins_dir: Path, config: "Config") -> list[LyricsProvider]:
    """Instantiate every plugin's ``create_lyrics_provider(config)``, if defined."""
    providers: list[LyricsProvider] = []

    for path in discover_plugin_files(plugins_dir):
        try:
            module = load_plugin_module(path)
        except PluginError as exc:
            logger.warning("Skipping plugin %s: %s", path.name, exc)
            continue

        factory = getattr(module, _LYRICS_FACTORY_ATTR, None)
        if factory is None:
            continue

        try:
            provider = factory(config)
        except Exception as exc:
            logger.warning("Plugin %s failed to create a lyrics provider: %s", path.name, exc)
            continue

        if not isinstance(provider, LyricsProvider):
            logger.warning(
                "Plugin %s's create_lyrics_provider() did not return a LyricsProvider; skipping",
                path.name,
            )
            continue

        logger.info("Loaded lyrics provider from plugin %s", path.name)
        providers.append(provider)

    return providers