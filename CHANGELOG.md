# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.0.0] — Initial public release

### Added
- Multi-provider metadata lookup with automatic fallback: iTunes →
  MusicBrainz → Discogs → Genius, in a configurable per-run order.
- Interactive provider-selection prompt with per-provider status
  (ready / unavailable-and-why) and comma-separated ordering input.
  `-y` / `--no-prompt` skips it; the prompt auto-skips on non-TTY stdin.
- Smart filename cleaning: strips `(Official Audio)`, `[HD]`,
  `(Live)`, `(Remastered)`, `feat./ft.`, leading track numbers.
- Extended tag support: title, artist, album, album artist, genre,
  year, track/disc number, composer, comment, copyright, explicit,
  compilation, artwork, lyrics.
- Confidence-scored candidate matching with duration cross-check
  and provider cross-validation; candidates below `min_confidence`
  are left untouched and reported to
  `reports/uncertain_<run_id>.csv` rather than guessed at.
- Local SQLite cache so re-runs don't re-hit provider APIs.
- Per-provider token-bucket rate limiter shared across worker threads.
- SHA256 integrity verification around every write.
- Pre-modification backup store with `--restore` (whole-run undo).
- Per-file checkpointing so an interrupted run resumes.
- Multi-threaded execution (`--workers N`) with rate limiting.
- Duplicate detection (checksum + optional metadata key).
- Album-level validation (missing/duplicate track numbers,
  inconsistent album names).
- CSV / JSON / HTML run reports.
- Plugin loader for custom `MetadataProvider` / `LyricsProvider`
  modules.
- Optional local LLM genre normalization via Ollama.
- Rich console UI: banner, live progress bar, summary tables,
  interactive review mode, automatic plain-text fallback.

### Notes
- No provider attempts to circumvent CAPTCHAs or anti-bot blocks —
  a block is logged and the pipeline moves on.
- Scraped lyrics are intended for personal use, not redistribution.
- Automatic update checking is deliberately **not** implemented —
  it would imply phoning home to a version endpoint.
