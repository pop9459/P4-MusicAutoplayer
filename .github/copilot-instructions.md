# Copilot Instructions

## Build, test, and lint commands

This project has one real dependency: `mutagen` (audio tag reading), listed in `requirements.txt`. No build step or lint configuration is committed.

```bash
pip install -r requirements.txt
```

On externally-managed environments (e.g. Arch/CachyOS), use
`pip install --user --break-system-packages -r requirements.txt` or a virtualenv.

```bash
# Run the complete test suite from the repository root.
python -m unittest discover -s tests

# Run one test module.
python -m unittest tests.test_predictor

# Run one test method.
python -m unittest tests.test_predictor.QueueGenerationTests.test_queue_chains_without_repeating_tracks
```

## Architecture

### Core Modules
- `src/track_analyzer.py` owns the catalog format and preprocessing. It scans supported audio files, derives stable IDs from resolved paths, parses `Artist - Title.ext` filenames, reads embedded tags via `mutagen` (genre/year/album/bpm, with graceful fallback to filename-derived defaults on missing/unreadable tags), canonicalizes metadata (genre canonicalization uses an alias map plus an ordered keyword-family fallback so specific tags like "album rock"/"glam rock" collapse to `rock`), constructs a shared feature space with per-block weighting (`FEATURE_WEIGHTS`: genre 0.35, bpm 0.25, year 0.20, artist 0.20 — each block scaled by `sqrt(weight)` before concatenation), and reads/writes versioned JSON catalogs.
- `src/predictor.py` is the pure recommender core. It ranks catalog vectors by cosine similarity, filters ineligible/current/excluded tracks, chooses from top-k with optional weighted randomness, and generates no-repeat queues.
- `src/settings.py` validates version-1 `settings.json` files, supports `music_folders` array with backward compatibility for single `music_directory`, and resolves relative paths against the settings file's directory.
- `src/cli.py` wires settings, catalog creation/loading, and recommender operations into the `python -m src.cli` commands. Explicit command-line options override settings values; commands can build a missing catalog only when a music directory is available. The `play` command launches the new 3-column TUI.
- `src/player.py` contains the core `PlayerEngine` class (stateful queue/playback logic) and track filtering; testable without curses or mpv.
- `src/mpv_backend.py` controls one background `mpv` process over its Unix JSON IPC socket.

### UI Modules (v2 3-Column Layout)
- `src/folder_panel.py` manages the left column: folder list from settings, selection tracking, catalog loading.
- `src/songs_panel.py` manages the middle column: enabled tracks display, selection, scrolling.
- `src/queue_panel.py` manages the right column: upcoming queue display, scrolling.
- `src/player_bar.py` manages the bottom bar: current track, play state, status messages.
- `src/player_ui_v2.py` integrates all four panels into one event loop with curses rendering, navigation, and playback control.

### Legacy UI Module (v1 Tabbed Layout, archived)
- `src/tui_player.py` implements the older tabbed player (Tab/Shift+Tab navigation). Still in codebase but superseded by v2.
- `src/ui_manager.py` and `src/tui_views.py` support the tabbed layout (archived but functional).

## Key conventions

- Keep the v1 product local, offline, single-user, and metadata-based; do not introduce cloud services, accounts, listening-history models, or collaborative filtering.
- Treat `Catalog` as a self-contained feature-space snapshot. When metadata changes, rebuild the catalog so each track vector uses the catalog's sorted genre/artist dimensions and its min-max BPM/year ranges.
- `enabled` is the eligibility gate: ranking, queueing, and selectable player tracks must exclude disabled records. Queues exclude every track already selected in that queue, but a player refill starts a new recommendation queue from the current track.
- Preserve catalog serialization compatibility: `CATALOG_VERSION` and settings `version` are both `1`; `save_catalog` emits indented, ASCII JSON with a final newline.
- Keep CLI behavior testable by putting catalog/recommender logic in pure functions. The player tests use `PlayerEngine` and mocked mpv IPC rather than a real terminal or mpv process.
- `testTracks/` is local data. Its files and generated `catalog.json` are ignored, except `testTracks/readme.txt`.

## 3-Column TUI Player (src/player_ui_v2.py)

The current player displays a 3-column layout with folders (left), songs (middle), queue (right), and player bar (bottom).

**Columns:**
- **Folders** (left): List of folders from `settings.music_folders`. Selection tracking with up/down navigation.
- **Songs** (middle): Enabled tracks from the current catalog. Scrolls to show visible subset. Selection tracking.
- **Queue** (right): Upcoming tracks from the player engine. View-only, scrolling display.
- **Player Bar** (bottom): Current track, play state, status messages, and control hints.

**Navigation:**
- `Tab` cycles between columns: folders → songs → queue → folders
- `↑`/`k` and `↓`/`j` navigate within active column
- `Enter`/`Space` confirms action (plays selected song or loads first song from folder)
- `r`/`R` plays random song from current folder
- `n`/`N` skips to next track
- `Space` or `p`/`P` toggles play/pause
- `q`/`Q` quits

**Features:**
- Starts with the main 3-column screen (no track picker).
- Folder selection automatically loads songs from that folder's catalog.
- Song selection initializes playback and generates queue.
- Random play picks an enabled track uniformly at random from current folder.
- Queue auto-refills when exhausted using the last played track as the new starting point.
- All `enabled` flags are respected; disabled tracks are hidden and skipped.

**Startup Flow:**
1. Load settings and catalog.
2. Initialize FolderPanel with folders from settings.
3. If folders exist, load first folder's songs into SongsPanel.
4. If songs exist, initialize PlayerEngine with first song.
5. Render main layout and begin event loop.
