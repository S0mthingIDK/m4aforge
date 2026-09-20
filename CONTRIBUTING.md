# Contributing

Thanks for considering a contribution to M4AForge.

## Getting set up

```bash
git clone https://github.com/S0mthingIDK/M4AForge.git
cd M4AForge
pip install -r requirements.txt
pytest
```

Python 3.11+ is required. There is no build step — the entry point is
`python -m m4aforge`.

## Project layout

The codebase is organized by responsibility:

- **`m4aforge/core.py`** — enums, constants, the exception
  hierarchy, the `TrackMetadata` / `ScanItem` / `MatchResult` /
  `ProcessResult` dataclasses, and `Config`.
- **`m4aforge/logger.py`** — rotating file handler + console
  handler setup.
- **`m4aforge/store.py`** — SQLite access layer plus
  `MetadataCache` and `CheckpointManager`.
- **`m4aforge/net.py`** — rate limiting, retry/fallback ladder,
  confidence scoring, and the worker thread pool.
- **`m4aforge/safety.py`** — integrity verification, backup
  store, duplicate detection, and album-level validation.
- **`m4aforge/media.py`** — folder scanning, filename parsing,
  tag read/write, artwork download, and lyrics scraping.
- **`m4aforge/pipeline.py`** — provider construction, per-file
  processing, run orchestration, restore, and report emission.
- **`m4aforge/reports.py`** — CSV / JSON / HTML report
  generation.
- **`m4aforge/plugins.py`** — plugin loader for custom providers.
- **`m4aforge/ui.py`** — Rich-based console: banner, progress
  bar, summary tables, and the interactive review menu.
- **`m4aforge/providers/`** — one file per `MetadataProvider`
  implementation (`itunes.py`, `musicbrainz.py`, `discogs.py`,
  `genius.py`), plus `ollama.py` for the local enrichment step
  (which is not a lookup provider).
- **`tests/`** — unit tests, one file per module under test, with
  HTTP mocked.

## Coding style

- PEP 8, type hints on all public functions, docstrings on all
  public modules/classes/functions.
- `pathlib.Path` for filesystem paths — no `os.path`.
- Narrow, typed exceptions per failure mode (see `core.py`). Avoid
  bare `except Exception` — the only place it's acceptable is
  plugin-module loading, where the imported code is untrusted and
  any exception type must be caught without killing the run.
- New metadata sources implement `MetadataProvider.search_multi()`
  in `providers/base.py`; new lyrics sources implement
  `LyricsProvider` in `media.py`. Prefer the plugin system
  (`plugins.py`) over editing the built-in provider list for
  anything experimental.
- Keep new fields on `TrackMetadata` optional (default `None`) so
  existing call sites and cached data don't break.

## Adding a provider

1. Implement `search_multi()` in a new `providers/<name>.py`,
   raising `ProviderBlockedError` on CAPTCHA/anti-bot responses
   rather than working around them — the fallback chain handles
   moving to the next provider.
2. Give it its own rate-limit policy in `net.py`.
3. Add it to `ProviderName` in `core.py` and to the default
   `provider_priority` in `Config`, or ship it as a plugin
   (`plugins.py`) if it's not meant to be built-in.
4. Add unit tests with HTTP mocked — no real network calls in the
   test suite.

## Tests

```bash
pytest
```

New behavior should come with a test. Bug fixes should include a
regression test that fails before the fix.

## Submitting changes

1. Fork and branch from `main`.
2. Keep commits focused; describe *why*, not just *what*, in the
   message.
3. Run `pytest` before opening a PR.
4. Update `README.md` and `CHANGELOG.md` if behavior, config, or the
   CLI surface changes.
5. Open a PR describing the change and the motivation.

## Reporting issues

Open a GitHub issue with: what you ran (command + relevant
`config.json` fields, tokens redacted), what you expected, what
happened instead, and the relevant lines from
`logs/m4aforge.log`.

## Scope notes

- Lyrics scraping is intended for personal use only, not bulk
  redistribution — please keep contributions in that spirit.
- No CAPTCHA/anti-bot circumvention will be merged for any provider.