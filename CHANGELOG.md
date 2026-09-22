# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.0.1] — Merged metadata pipeline, lyrics cleanup, scoring fixes

### Added
- **Merged multi-provider pipeline.** Every provider is queried on every
  track; the highest-scoring candidate becomes the "spine," and any
  candidate from a different provider whose artist+title match the spine
  at ≥90% contributes its non-empty fields to fill gaps. This is how
  iTunes's album and track number survive when Genius supplied the title,
  and how Genius's lyrics reach a track whose spine came from iTunes.
- **Bad-album penalty in scoring.** Candidates whose album title looks
  like a live set, compilation, promo, or film soundtrack score 25%
  lower, so a canonical studio release from another provider wins the
  spine vote when both match.
- **Genius lyrics hit validation.** Genius hits whose returned artist or
  title don't fuzzy-match the track's metadata at ≥0.85 are rejected,
  preventing an unrelated compilation from supplying the lyrics.

### Changed
- **`min_confidence` default lowered from 0.72 to 0.60.** With the merge
  step in place, the spine is guaranteed to be the highest-scoring
  candidate, so a slightly looser gate no longer risks garbage matches —
  it just stops rejecting tracks whose best provider happened to score
  lower on completeness.
- **Merging prefers clean-album candidates per provider.** When
  MusicBrainz returns both the canonical studio release and a live
  album for the same song, the clean-album version contributes rather
  than whichever candidate happened to sort first.
- **Fuzzy comparisons are now case-insensitive.** `twenty one pilots`
  vs `Twenty One Pilots` previously scored 0.82 and was rejected by
  the lyrics validator; it now scores 1.00 and passes.

### Fixed
- **Lyrics contained Genius editorial descriptions and junk headers.**
  The description paragraph, `N Contributors`, `Translations`,
  language-name lines, `Read More`, `[Produced by …]` brackets, and
  `<Title> Lyrics` headings are now all stripped before the text is
  embedded. Lyrics start directly at the first `[Verse]`/`[Chorus]`
  marker.
- **Discogs release-level results could win the spine vote with no
  title and no artist**, then write an unrelated album name into the
  file. Candidates that supply neither field are now scored 0 outright.
- **MusicBrainz `503` responses were treated as permanent blocks.**
  `503` means transient overload, not a refusal — removed from the
  blocked-status set so the retry ladder backs off and retries instead
  of skipping MusicBrainz for the rest of the track.
- **MusicBrainz provider weight lowered** from 1.0 to 0.85 (now tied
  with iTunes), so its occasional compilations no longer dominate the
  spine vote.
- **Floating-point threshold comparison** — a score mathematically
  equal to the threshold is no longer rejected by rounding. Uses a
  1e-9 epsilon.
- **Bare `except Exception` in `integrity.py` / `metadata.py`**
  replaced with `except (MutagenError, OSError)` in every mutagen call.
- **`PIL.Image` handle leak in `artwork.py`** — image objects are now
  used as context managers.
- **`KeyboardInterrupt` in the worker pool** now cancels pending
  futures and shuts the executor down immediately instead of waiting on
  in-flight network I/O.
- **`ALTER TABLE … ADD COLUMN` in `db.py`** is now guarded by a
  whitelist, preventing any future refactor from interpolating
  user-controlled values into DDL.
- **Rate limiter minimum sleep** reduced from 10 ms to 1 ms, removing
  measurable latency on high-throughput limits.

## [1.0.0] — Initial public release

### Added
- Multi-provider metadata lookup (iTunes, MusicBrainz, Discogs, Genius)
  with automatic fallback.
- Smart filename cleaning: strips `(Official Audio)`, `[HD]`,
  `(Live)`, `(Remastered)`, `feat./ft.`, leading track numbers.
- Extended tag support: title, artist, album, album artist, genre,
  year, track/disc number, composer, comment, copyright, explicit,
  compilation, artwork, lyrics.
- Confidence-scored candidate matching with duration cross-check and
  provider cross-validation; candidates below `min_confidence` are
  left untouched and reported to `reports/uncertain_<run_id>.csv`.
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
- No provider attempts to circumvent CAPTCHAs or anti-bot blocks — a
  block is logged and the pipeline moves on.
- Scraped lyrics are intended for personal use, not redistribution.
- Automatic update checking is deliberately **not** implemented — it
  would imply phoning home to a version endpoint.