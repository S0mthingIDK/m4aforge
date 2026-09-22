# M4AForge

Enrich `.m4a` music files with metadata — title, artist, album, genre,
year, track/disc number, composer, comment, copyright, explicit flag,
compilation flag, artwork, and lyrics — pulled automatically from
iTunes, MusicBrainz, Discogs, and Genius.

Built with caching, retries, pre-write backups, resume support, and
multi-threading, so it's safe to point at a real library and walk away.

## How it works

Every track goes through a **merged pipeline** — no provider is
"chosen" over the others. Each provider is queried, and the best
fields from each are combined into one record:

```
1. Clean the filename  →  "Artist - Title"
2. Ask every provider for candidates
3. Score every candidate
   (title, artist, duration, completeness, provider reliability,
    album-quality penalty)
4. Highest score = the "spine"
5. For every empty field on the spine, borrow from any candidate
   from a different provider whose artist+title match the spine
   at ≥90% — preferring candidates whose album looks like a
   canonical release over compilations/live/promo discs
6. Validate lyrics separately (reject Genius hits whose returned
   artist or title don't fuzzy-match our metadata at ≥85%)
7. Write the merged metadata + artwork + lyrics, then rename + backup
```

**What each provider contributes to the final record:**

| Field | iTunes | MusicBrainz | Discogs | Genius |
|---|---|---|---|---|
| title, artist | ✅ | ✅ | | ✅ |
| album | ✅ | ✅ | ✅ | |
| genre | ✅ | | ✅ | |
| year | ✅ | ✅ | ✅ | ✅ |
| track number | ✅ | | | |
| duration | ✅ | ✅ | | |
| artwork | ✅ | | ✅ | ✅ |
| compilation flag | | | ✅ | |
| **lyrics** | | | | ✅ |

Genius is the only provider that can supply lyrics; iTunes supplies
the most fields in a single response. The merge step ensures you get
both without having to pick.

## Features

- **Merged multi-provider lookup** — no provider-selection prompts,
  no "pick the best one." Every track gets the union of what every
  provider knows.
- **Smart filename cleaning** strips `(Official Audio)`, `[HD]`,
  `feat. X`, `(Live)`, `(Remastered)`, leading track numbers, and
  other common download noise before searching.
- **Confidence gating** — candidates below a score threshold are
  left untouched and reported separately, rather than written on a
  guess. Duration and cross-provider agreement feed the score, and
  candidates whose album looks like a live set, compilation, promo,
  or film soundtrack are penalized so the canonical studio release
  wins the vote.
- **Lyrics validation** — Genius hits whose returned artist or title
  don't match the track are rejected, so an unrelated song's lyrics
  never end up embedded.
- **Lyrics cleanup** — Genius's editorial description, contributor
  counts, translation language headers, and production credits are
  all stripped, leaving only the lyrics body.
- **Local cache** (SQLite) so re-running on the same library doesn't
  re-hit provider APIs.
- **File safety**: SHA256 integrity check around every write;
  pre-modification backups; `--restore` to undo a whole run.
- **Resumable** — an interrupted run picks up where it left off.
- **Multi-threaded**, with a per-provider rate limiter so concurrency
  never trips an API ban.
- **Reports** — CSV, JSON, and HTML summaries of every run, plus a
  dedicated `uncertain_<run_id>.csv` for tracks below the confidence
  threshold.
- **Plugin system** for dropping in a custom provider without editing
  core code.
- **Optional local LLM enrichment** via [Ollama](https://ollama.com).
- **Rich console UI** — banner, live progress bar, summary tables,
  and an interactive review mode, with automatic plain-text fallback
  on non-TTY output.

## Installation

Requires **Python 3.11+**.

```bash
git clone https://github.com/S0mthingIDK/m4aforge.git
cd m4aforge
pip install -r requirements.txt
```

No build step — run it with `python -m m4aforge`.

## Usage

```bash
# Enrich a folder (dry run: shows what would change, writes nothing)
python -m m4aforge --folder "C:\Users\you\Music\Downloads" --dry-run

# Real run
python -m m4aforge --folder "C:\Users\you\Music\Downloads"

# Use a specific config and 8 worker threads
python -m m4aforge --folder ~/Music/Downloads --config myconfig.json --workers 8

# Undo the most recent run
python -m m4aforge --restore

# Undo a specific run
python -m m4aforge --restore 20260731-142233

# Regenerate reports from the existing database without touching files
python -m m4aforge --report-only

# Verbose (debug) logging
python -m m4aforge --folder ~/Music/Downloads --verbose
```

### CLI reference

| Flag | Description |
|---|---|
| `--folder PATH` | Folder to scan for `.m4a` files (recursive). |
| `--config PATH` | Path to `config.json` (default: `config.json` in the CWD). |
| `--dry-run` | Preview matches and changes; nothing is written. |
| `--workers N` | Number of worker threads (overrides config). |
| `--restore [RUN_ID]` | Restore a backed-up run (defaults to the latest) and exit. |
| `--report-only` | Regenerate CSV/JSON/HTML reports from the existing database and exit. |
| `--verbose` | Enable debug-level logging. |
| `--help` | Show all options. |

## Configuration

All settings live in `config.json` at the project root (CLI flags
override it). Every field is optional — omitted fields fall back to
sensible defaults, so a missing `config.json` is not an error.

```json
{
  "folder": ".",
  "overwrite_existing": false,
  "provider_priority": ["itunes", "musicbrainz", "discogs", "genius", "ollama"],

  "discogs_token": "",
  "genius_token": "",

  "cache_enabled": true,
  "cache_db_path": "cache.db",
  "cache_max_age_days": 30,

  "embed_lyrics": false,
  "embed_artwork": true,
  "min_artwork_resolution": 500,

  "ollama_enabled": false,
  "ollama_host": "http://localhost:11434",
  "ollama_model": "llama3",

  "backup_enabled": true,
  "backup_root": "backups",
  "backup_retention": 5,
  "max_workers": 4,

  "report_dir": "reports",
  "plugins_dir": "plugins",
  "interactive": false,
  "theme": "default",

  "min_confidence": 0.60,
  "strict_confidence": false
}
```

| Field | Default | Notes |
|---|---|---|
| `folder` | `"."` | Folder scanned recursively for `.m4a` files. |
| `overwrite_existing` | `false` | If `false`, an already-populated tag is left untouched. |
| `provider_priority` | see above | Order providers are queried in. With the merged pipeline every provider contributes regardless of order — this only matters when one provider errors out. |
| `discogs_token` / `genius_token` | `null` | Required to enable those providers; missing tokens skip the provider with a warning. |
| `cache_enabled` | `true` | Store lookup results locally so re-runs don't re-hit APIs. |
| `cache_db_path` | `"cache.db"` | SQLite file used for the cache, checkpoints, and stats. |
| `cache_max_age_days` | `30` | How long a cached result stays valid. |
| `embed_lyrics` | `false` | Off by default — scraped lyrics are for personal use, not redistribution. |
| `embed_artwork` | `true` | Whether to download and embed cover art into the `covr` tag. |
| `min_artwork_resolution` | `500` | Minimum pixel dimension an image must meet. |
| `ollama_enabled` | `false` | Run a local model over fetched metadata to clean up genre text. |
| `ollama_host` / `ollama_model` | `http://localhost:11434` / `llama3` | Local Ollama endpoint and model. |
| `backup_enabled` | `true` | Copy each file before modifying it, enabling `--restore`. |
| `backup_root` | `"backups"` | Folder where pre-modification copies live. |
| `backup_retention` | `5` | Number of past runs kept before older backups are pruned. |
| `max_workers` | `4` | Concurrent worker threads. |
| `report_dir` | `"reports"` | Folder for CSV/JSON/HTML reports. |
| `plugins_dir` | `"plugins"` | Folder scanned for custom plugin `.py` files. |
| `interactive` | `false` | Review each file's proposed changes before writing. Forces single-threaded execution. |
| `theme` | `"default"` | `"default"` (color) or `"plain"`. Color also auto-disables on non-TTY output or when `NO_COLOR` is set. |
| `min_confidence` | `0.60` | Minimum score required before any tag is written. |
| `strict_confidence` | `false` | If `true`, an uncertain match raises instead of being recorded. |

Every field is explained in more depth, with examples, in
[`config.annotated.md`](config.annotated.md).

## Providers

| Provider | Supplies | Requires |
|---|---|---|
| iTunes | title, artist, album, genre, year, track number, artwork, duration | Nothing (public API) |
| MusicBrainz | title, artist, album, year, duration | Nothing (public API; identifies itself via `User-Agent` per MusicBrainz's usage policy) |
| Discogs | album, genre, artwork, compilation flag | `discogs_token` |
| Genius | title, artist, year, artwork, lyrics | `genius_token` |
| Ollama | genre normalization (enrichment only, not a lookup source) | A running local Ollama instance |

Discogs' search endpoint returns release-level data with no
tracklist, so it never supplies a track title — it's included in the
chain for album/genre/artwork/compilation data, with the title itself
coming from iTunes, MusicBrainz, or Genius.

Lyrics are fetched by resolving the song via Genius's official Search
API, then reading the full lyrics body from the matched song page.
No provider attempts to circumvent CAPTCHAs or anti-bot blocks — a
block is logged and the pipeline moves to the next provider or file.

## Plugins

Drop a `.py` file into the `plugins_dir` (default: `plugins/`) that
defines either or both of:

```python
def create_provider(config):
    """Return a MetadataProvider instance."""

def create_lyrics_provider(config):
    """Return a LyricsProvider instance."""
```

The plugin receives the loaded `Config`, so it can read its own
settings from `config.json`. A plugin that fails to load or raises
while constructing its provider is logged and skipped — it never
aborts the run.

## Reports

Every run writes `reports/report_<run_id>.{csv,json,html}` covering:
which files succeeded or failed and at which stage, missing artwork
or lyrics, which provider matched each file, per-provider
hit/miss/error counts, and total run time. Files that fell below the
confidence threshold are additionally written to
`reports/uncertain_<run_id>.csv` with their top rejected candidate.

The plain-text log (`logs/m4aforge.log`) still has full debug detail;
reports are the human-facing summary.

## Troubleshooting

- **A provider keeps getting skipped** — check that its token
  (`discogs_token` / `genius_token`) is set in `config.json`; a
  missing token skips the provider with a warning, it doesn't error
  out the run.
- **"No metadata providers are configured/usable"** — every provider
  in `provider_priority` was skipped (missing tokens, unknown names).
  Leave at least iTunes or MusicBrainz — both need no tokens.
- **A run seems to hang** — a provider may be rate-limiting or
  blocking; check `logs/m4aforge.log` for `ProviderBlockedError`
  entries. Blocking is never circumvented; the run falls through to
  the next provider.
- **Metadata is incomplete** — check `reports/uncertain_<run_id>.csv`
  for the track; the merged pipeline should fill everything except
  `album_artist`, which no provider exposes. If a specific field is
  missing, open the report's JSON version and check which candidates
  were rejected and why.
- **Lyrics are missing on some tracks** — Genius's search sometimes
  returns a cover, translation, or unrelated compilation as its top
  hit. When that happens, M4AForge rejects the hit rather than embed
  the wrong lyrics, and the track is left with `lyrics=None`. This is
  intentional.
- **Undo a bad run** — `python -m m4aforge --restore` restores the
  most recent backup; pass a specific run ID to restore an older one.
- **Colors look garbled in CI logs** — set `NO_COLOR=1` or
  `"theme": "plain"`. Rich falls back to plain text automatically
  when output is piped.

## Project layout

```
m4aforge/
  __init__.py    package version
  __main__.py    CLI entry point (python -m m4aforge)
  core.py        enums, constants, exceptions, models, Config
  logger.py      rotating file + console logging
  store.py       SQLite: cache, checkpoints, provider stats
  net.py         rate limiting, retries, scoring, thread pool, merging
  safety.py      integrity, backups, dedupe, album validation
  media.py       scanning, parsing, tag I/O, artwork, lyrics
  pipeline.py    per-file processing, run orchestration
  reports.py     CSV / JSON / HTML reports
  plugins.py     plugin loader
  ui.py          Rich console, progress, banner, review menu
  providers/     one file per metadata source
tests/           pytest suite, HTTP mocked
```

## License

MIT — see [LICENSE](LICENSE).