# config.json — Annotated Reference

Standard JSON doesn't support comments, so this file explains every
setting. The actual file the app reads is the plain `config.json`
next to it — edit that one, use this one as your cheat sheet.

---

## General

```json
"folder": "~/Music/Downloads"
```
- **What it is:** The folder the tool scans (recursively) for `.m4a` files.
- **Example:** `"folder": "/home/you/Music/Downloads"` or `"folder": "D:/Music"`
- **Default:** `"."` (current directory) if omitted.

```json
"overwrite_existing": false
```
- **What it is:** Whether to replace a tag that already has a value.
- **`false`:** Only fills in *missing* tags. A track that already has an artist keeps it, even if a provider disagrees.
- **`true`:** Providers' values always win, overwriting whatever tags are already on the file.
- **Default:** `false`.

---

## Provider order

```json
"provider_priority": ["itunes", "musicbrainz", "discogs", "genius", "ollama"]
```
- **What it is:** The order metadata sources are tried in. Each provider is tried in turn; if one fails or can't find a match, the next one is tried.
- **Example:** `["musicbrainz", "itunes", "genius"]` to prefer MusicBrainz first and drop Discogs entirely.
- **Note:** `"ollama"` is enrichment-only (genre cleanup) — it never does the actual title/artist lookup, regardless of where it sits in the list.
- **Default:** `["itunes", "musicbrainz", "discogs", "genius", "ollama"]`.

---

## Provider credentials

```json
"discogs_token": ""
```
- **What it is:** Your personal API token from Discogs (needed for the Discogs provider — supplies album/genre/artwork/compilation data).
- **Example:** `"discogs_token": "AbCdEf123456"`
- **If empty/missing:** Discogs is skipped with a warning; the rest of the chain still runs.

```json
"genius_token": ""
```
- **What it is:** Your personal API token from Genius (needed for title/artist/year/artwork lookup *and* for locating lyrics pages).
- **Example:** `"genius_token": "xyz789tokenvalue"`
- **If empty/missing:** Genius (and therefore lyrics) is skipped with a warning.

---

## Cache

```json
"cache_enabled": true
```
- **What it is:** Whether to store lookup results locally (SQLite) so re-running on the same files doesn't re-hit provider APIs.
- **`true`:** Faster re-runs, fewer API calls, less risk of rate-limiting.
- **`false`:** Every run re-queries every provider from scratch.
- **Default:** `true`.

```json
"cache_db_path": "cache.db"
```
- **What it is:** File path for the cache database.
- **Example:** `"cache_db_path": "data/cache.db"`
- **Default:** `"cache.db"` in the project root.

```json
"cache_max_age_days": 30
```
- **What it is:** How long a cached result stays valid before it's re-fetched.
- **Example:** `"cache_max_age_days": 7` to refresh weekly; `0` to effectively disable caching's time benefit (always stale).
- **Default:** `30`.

---

## Lyrics & artwork

```json
"embed_lyrics": false
```
- **What it is:** Whether to embed the full lyrics text (scraped via Genius) into the file's tags.
- **`false`:** Lyrics are looked up but not written into the file. Off by default since embedding full lyrics text is a heavier, opt-in feature — scraped lyrics are for personal use, not redistribution.
- **`true`:** Full lyrics get embedded in the M4A tag.
- **Default:** `false`.

```json
"embed_artwork": true
```
- **What it is:** Whether to download and embed cover art into the file.
- **`true`:** Album art is fetched and written to the `covr` tag.
- **`false`:** Artwork lookup/embedding is skipped entirely.
- **Default:** `true`.

```json
"min_artwork_resolution": 500
```
- **What it is:** Minimum pixel dimension (width/height) an image must meet to be accepted as artwork.
- **Example:** `"min_artwork_resolution": 800` to reject smaller/thumbnail images.
- **Default:** `500`.

---

## Ollama (local LLM enrichment)

```json
"ollama_enabled": false
```
- **What it is:** Whether to run a local Ollama model over fetched metadata to clean up messy genre text (e.g. normalize "Hip Hop/Rap" → "Hip-Hop").
- **`true`:** Runs after the provider chain, only touches genre/tag text, requires Ollama running locally.
- **`false`:** Step is skipped entirely.
- **Default:** `false`.

```json
"ollama_host": "http://localhost:11434"
```
- **What it is:** URL of your local Ollama server.
- **Example:** `"ollama_host": "http://192.168.1.50:11434"` if Ollama runs on another machine on your network.
- **Default:** `"http://localhost:11434"`.

```json
"ollama_model": "llama3"
```
- **What it is:** Which locally-installed Ollama model to use for the enrichment step.
- **Example:** `"ollama_model": "mistral"`
- **Default:** `"llama3"`.

---

## Backup & threading

```json
"backup_enabled": true
```
- **What it is:** Whether to copy each file before modifying it, enabling `--restore`.
- **`true`:** Safer — you can undo a run.
- **`false`:** No backup copies made; a `--restore` will have nothing to restore from.
- **Default:** `true`.

```json
"backup_root": "backups"
```
- **What it is:** Folder where pre-modification backup copies are stored.
- **Example:** `"backup_root": "/mnt/external-drive/m4a-backups"`
- **Default:** `"backups"` in the project root.

```json
"backup_retention": 5
```
- **What it is:** How many past runs' backups to keep before older ones are pruned.
- **Example:** `"backup_retention": 10` to keep more history; `1` to only ever keep the most recent run.
- **Default:** `5`.

```json
"max_workers": 4
```
- **What it is:** Number of worker threads processing files concurrently.
- **Example:** `"max_workers": 8` for faster runs on a large library (rate limiters still prevent API bans); `1` for strictly sequential processing.
- **Default:** `4`.

---

## Reports, plugins, UI

```json
"report_dir": "reports"
```
- **What it is:** Folder where CSV/JSON/HTML run reports are written.
- **Example:** `"report_dir": "output/reports"`
- **Default:** `"reports"`.

```json
"plugins_dir": "plugins"
```
- **What it is:** Folder scanned for custom plugin `.py` files (custom providers) at startup.
- **Example:** `"plugins_dir": "my_plugins"`
- **Default:** `"plugins"`.

```json
"interactive": false
```
- **What it is:** Whether to pause and show you each file's proposed changes before writing them.
- **`true`:** You review/approve each file individually. Forces single-threaded execution (`max_workers` is effectively ignored).
- **`false`:** Runs fully automated, no per-file prompts.
- **Default:** `false`.

```json
"theme": "default"
```
- **What it is:** Console output color scheme.
- **`"default"`:** Colored output (auto-disables on non-TTY output or if `NO_COLOR` env var is set).
- **`"plain"`:** No ANSI color codes at all — useful for logging to a file or unsupported terminals.
- **Default:** `"default"`.
