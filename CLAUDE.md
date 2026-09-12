# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A local, offline music recommender/player. It scans an mp3 library into a JSON catalog with metadata-derived feature vectors, recommends similar next tracks via cosine similarity, and plays them through a curses-based TUI backed by `mpv`.

## Commands

Install dependencies (single real dependency: `mutagen`):

```bash
pip install -r requirements.txt
# externally-managed systems (Arch/CachyOS): 
pip install --user --break-system-packages -r requirements.txt
```

Run the full test suite / a single module / a single test method (from repo root):

```bash
python -m unittest discover -s tests
python -m unittest tests.test_predictor
python -m unittest tests.test_predictor.QueueGenerationTests.test_queue_chains_without_repeating_tracks
```

Debug CLI (`python -m src.cli --help` for full list):

```bash
python -m src.cli build-catalog --music-dir testTracks --catalog data/tracks.json
python -m src.cli summary --catalog data/tracks.json
python -m src.cli list-tracks --catalog data/tracks.json --limit 20
python -m src.cli inspect-track --catalog data/tracks.json --track-id <track-id>
python -m src.cli recommend --catalog data/tracks.json --track-id <track-id> --show-candidates 10
python -m src.cli queue --catalog data/tracks.json --track-id <track-id> --length 20 --top-k 5 --randomness 0.25
```

`summary`, `list-tracks`, `inspect-track`, and `recommend` auto-create a missing catalog when `--music-dir` is supplied.

Play (requires [mpv](https://mpv.io/) on `PATH`):

```bash
python -m src.cli play
python -m src.cli play --catalog data/tracks.json
```

There is no build step or lint configuration in this repo.

## Settings

`settings.json` (repo root) drives CLI defaults — paths resolve relative to the settings file's directory:

```json
{
  "version": 1,
  "catalog_path": "testTracks/catalog.json",
  "music_folders": ["testTracks"],
  "top_k": 5,
  "randomness": 0.0,
  "queue_length": 10
}
```

Legacy single `music_directory` is auto-migrated to the `music_folders` array on first load. Use `--settings path/to/settings.json` to point at a different file; explicit flags like `--length`/`--randomness` override settings for that run only.

## Architecture

### Core modules
- `src/track_analyzer.py` owns the catalog format and preprocessing: scans supported audio files, derives stable IDs from resolved paths, parses `Artist - Title.ext` filenames, reads embedded tags via `mutagen` (genre/year/album/bpm, falling back to filename-derived defaults when tags are missing/unreadable), canonicalizes genre (alias map + ordered keyword-family fallback, e.g. "album rock"/"glam rock" → `rock`), builds the shared feature space with per-block weighting (`FEATURE_WEIGHTS`: genre 0.35, bpm 0.25, year 0.20, artist 0.20 — each block scaled by `sqrt(weight)` before concatenation), and reads/writes versioned JSON catalogs.
- `src/predictor.py` is the pure recommender core: ranks catalog vectors by cosine similarity, filters ineligible/current/excluded tracks, selects from top-k with optional weighted randomness, and generates no-repeat session queues.
- `src/settings.py` validates version-1 `settings.json`, supports the `music_folders` array with backward compatibility for `music_directory`, and resolves relative paths against the settings file's directory.
- `src/cli.py` wires settings, catalog creation/loading, and recommender operations into `python -m src.cli` commands. Explicit CLI options override settings; commands can build a missing catalog only when a music directory is available. The `play` command launches the 3-column TUI.
- `src/player.py` contains `PlayerEngine` — stateful queue/playback logic and track filtering, testable without curses or mpv.
- `src/mpv_backend.py` controls one background `mpv` process over its Unix JSON IPC socket.

### UI modules (v2, 3-column layout — current)
- `src/folder_panel.py` — left column: folder list from settings, selection tracking, catalog loading.
- `src/songs_panel.py` — middle column: enabled tracks display, selection, scrolling.
- `src/queue_panel.py` — right column: upcoming queue display, scrolling.
- `src/player_bar.py` — bottom bar: current track, play state, status messages.
- `src/player_ui_v2.py` — integrates all four panels into one curses event loop with navigation and playback control. Startup: load settings/catalog → init `FolderPanel` → load first folder's songs into `SongsPanel` → init `PlayerEngine` with first song → render and loop.

### Legacy UI (v1 tabbed layout, archived but functional)
- `src/tui_player.py`, `src/ui_manager.py`, `src/tui_views.py` implement the older Tab/Shift+Tab tabbed player, superseded by v2 but still present.

## Key conventions

- Keep the product local, offline, single-user, and metadata-based; do not introduce cloud services, accounts, listening-history models, or collaborative filtering.
- Treat `Catalog` as a self-contained feature-space snapshot. When metadata changes, rebuild the catalog so each track vector uses the catalog's sorted genre/artist dimensions and its min-max BPM/year ranges.
- `enabled` is the eligibility gate: ranking, queueing, and selectable player tracks must exclude disabled records. Queues exclude every track already selected in that queue, but a player refill starts a new recommendation queue from the current track.
- Preserve catalog serialization compatibility: `CATALOG_VERSION` and settings `version` are both `1`; `save_catalog` emits indented, ASCII JSON with a final newline.
- Keep CLI behavior testable by putting catalog/recommender logic in pure functions. Player tests use `PlayerEngine` and mocked mpv IPC rather than a real terminal or mpv process.
- `testTracks/` is local data; its files and generated `catalog.json` are gitignored, except `testTracks/readme.txt`.
- A track sharing both genre *and* artist with the current track will score a perfect cosine match (1.0) — this is mathematically unavoidable when every weighted dimension agrees. Repetitive queues within one artist/genre are addressed by raising `top_k`/`randomness`, not by changing the scoring.
