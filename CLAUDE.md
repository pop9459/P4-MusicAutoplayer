# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A local, offline music recommender/player. It scans mp3 folders into a central JSON library with metadata-derived feature vectors, recommends similar next tracks via cosine similarity, and plays them through a curses-based TUI backed by `mpv`. Tracked folders are never written to — all catalog/library data lives centrally (`data/library.json`), never inside a scanned music folder.

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

Library commands (manage tracked folders; the source of truth for `play`):

```bash
python -m src.cli add-folder --path /path/to/music     # recursive scan; skips a path already tracked
python -m src.cli list-folders
python -m src.cli remove-folder --folder-id <folder-id>
```

Each defaults to `settings.json`'s `library_path`, or pass `--library path/to/library.json` explicitly. `add-folder` never writes to the scanned folder.

Debug CLI (`python -m src.cli --help` for full list) — these operate on a raw single-folder `--catalog` file, independent of the library, for quick inspection/scripting:

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
```

`play` loads `settings.library_path` (auto-migrating a pre-library `settings.json` the first time — see Settings below); it no longer takes `--catalog`/`--music-dir`.

There is no build step or lint configuration in this repo.

## Settings

`settings.json` (repo root) drives CLI defaults — paths resolve relative to the settings file's directory:

```json
{
  "version": 1,
  "library_path": "data/library.json",
  "top_k": 5,
  "randomness": 0.0,
  "queue_length": 10
}
```

`library_path` defaults to `data/library.json` when omitted. This repo's own `settings.json` is intentionally left in its pre-library legacy form (`catalog_path`/`music_folders`/`music_directory`, no `library_path`) so a fresh `play`/`add-folder` run continues to exercise `migrate_settings_to_library` (`src/library.py`): it builds `data/library.json` from the legacy fields without deleting or modifying the old `testTracks/catalog.json`. Once a `Settings` has been through migration (or saved from the in-TUI settings screen), `save_settings` stops emitting the legacy fields and the file becomes preferences-only. Use `--settings path/to/settings.json` to point at a different file; explicit flags like `--length`/`--randomness` override settings for that run only.

## Architecture

### Core modules
- `src/track_analyzer.py` owns the catalog format and preprocessing: scans supported audio files, derives stable IDs from resolved paths, parses `Artist - Title.ext` filenames, reads embedded tags via `mutagen` (genre/year/album/bpm, falling back to filename-derived defaults when tags are missing/unreadable), canonicalizes genre (alias map + ordered keyword-family fallback, e.g. "album rock"/"glam rock" → `rock`), builds the shared feature space with per-block weighting (`FEATURE_WEIGHTS`: genre 0.35, bpm 0.25, year 0.20, artist 0.20 — each block scaled by `sqrt(weight)` before concatenation), and reads/writes versioned JSON catalogs. `TrackRecord.folder_id` ties a track back to the `LibraryFolder` it was scanned from. `scan_library`/`scan_library_with_progress` are read-only — they never write into the folder being scanned.
- `src/library.py` owns tracked folders and the merged catalog in one central `Library` (`folders: list[LibraryFolder]`, `catalog: Catalog`), persisted as one `data/library.json`. `add_folder` scans only the new folder, merges its tracks into the existing catalog, and rebuilds the catalog's feature space over *all* tracks (not just the new folder) so the feature-space invariant below still holds; adding an already-tracked (resolved-path) folder is a no-op. `remove_folder` is the CLI-only counterpart. `migrate_settings_to_library` is a one-time, non-destructive migration from pre-library `settings.json`/`catalog_path` setups — it never deletes the old catalog file. `start_add_folder_task` runs a scan on a background `threading.Thread` (never touching curses/mpv) so the TUI can poll `ScanTask.progress()` each render tick instead of freezing during a large scan.
- `src/predictor.py` is the pure recommender core: ranks catalog vectors by cosine similarity, filters ineligible/current/excluded tracks, selects from top-k with optional weighted randomness, and generates no-repeat session queues.
- `src/settings.py` validates version-1 `settings.json` and resolves relative paths (including `library_path`) against the settings file's directory. `catalog_path`/`music_directory`/`music_folders` are retained only as legacy/migration fields (see Settings above) — `library_path` is the only field the TUI/library commands treat as authoritative.
- `src/cli.py` wires settings, the library, and recommender operations into `python -m src.cli` commands. The `play` command launches the 3-column TUI against a `Library`; `add-folder`/`list-folders`/`remove-folder` manage tracked folders; the remaining debug commands still operate on a raw `--catalog` file.
- `src/player.py` contains `PlayerEngine` — stateful queue/playback logic and track filtering, testable without curses or mpv.
- `src/mpv_backend.py` controls one background `mpv` process over its Unix JSON IPC socket.

### UI modules (v2, 3-column layout — current)
- `src/folder_panel.py` — left column: a virtual "All Tracks" `FolderEntry` (aggregates every tracked folder) followed by one `FolderEntry` per tracked folder, built from a `Library` via `load_from_library`.
- `src/songs_panel.py` — middle column: enabled tracks for the selected folder entry (or every enabled track, for "All Tracks"), selection, scrolling.
- `src/queue_panel.py` — right column: upcoming queue display, scrolling.
- `src/player_bar.py` — bottom bar: current track, play state, status messages.
- `src/player_ui_v2.py` — integrates all four panels into one curses event loop with navigation and playback control. Startup: load settings/library → init `FolderPanel` → load the selected folder's songs into `SongsPanel` → init `PlayerEngine` with first song → render and loop. The middle column's header shows the selected folder's name/path/track-count above its song list. Pressing `a` in the folder column prompts (blocking `curses.echo()`/`getstr()`, the same pattern used for settings text edits — a path pasted via drag-and-drop lands in the same prompt) for a folder path and runs the scan on a background thread (`library.start_add_folder_task`), polled each 200ms loop tick so scan progress ("Scanning: N/Total") renders without freezing playback controls.

### Legacy UI (v1 tabbed layout, archived but functional)
- `src/tui_player.py`, `src/ui_manager.py`, `src/tui_views.py` implement the older Tab/Shift+Tab tabbed player, superseded by v2 but still present.

## Key conventions

- Keep the product local, offline, single-user, and metadata-based; do not introduce cloud services, accounts, listening-history models, or collaborative filtering.
- Treat `Catalog` as a self-contained feature-space snapshot. When metadata changes, rebuild the catalog so each track vector uses the catalog's sorted genre/artist dimensions and its min-max BPM/year ranges — this applies across the whole `Library`, not per folder, so `add_folder`/`remove_folder` always rebuild the merged catalog's feature space, not just the affected folder's tracks.
- `enabled` is the eligibility gate: ranking, queueing, and selectable player tracks must exclude disabled records. Queues exclude every track already selected in that queue, but a player refill starts a new recommendation queue from the current track.
- Preserve catalog serialization compatibility: `CATALOG_VERSION`, `LIBRARY_VERSION`, and settings `version` are all `1`; `save_catalog`/`save_library` emit indented, ASCII JSON with a final newline.
- Never write into a tracked/scanned music folder. All catalog/library data lives centrally (`settings.library_path`, default `data/library.json`); `scan_library`/`add_folder` only read.
- Keep CLI behavior testable by putting catalog/library/recommender logic in pure functions. Player tests use `PlayerEngine` and mocked mpv IPC rather than a real terminal or mpv process.
- `testTracks/` is local data; its files and generated `catalog.json` are gitignored, except `testTracks/readme.txt`. `data/library.json` is likewise gitignored.
- A track sharing both genre *and* artist with the current track will score a perfect cosine match (1.0) — this is mathematically unavoidable when every weighted dimension agrees. Repetitive queues within one artist/genre are addressed by raising `top_k`/`randomness`, not by changing the scoring.
