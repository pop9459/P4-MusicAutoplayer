# Music Autoplayer

A local, offline music recommender that selects a next track using a JSON catalog and metadata-derived feature vectors.

## Settings

`settings.json` stores the project defaults used by the CLI:

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

Edit these values to change normal behavior without repeatedly passing flags. Paths are resolved relative to the settings file. Use a different file with `--settings path/to/settings.json` before the command; explicit CLI flags such as `--length` and `--randomness` override settings for that one run.

**Note:** Legacy single `music_directory` is automatically migrated to `music_folders` array on first load.

## Debug CLI

Run the debug utility from the repository root:

```bash
python -m src.cli --help
```

Build a catalog explicitly:

```bash
python -m src.cli build-catalog --music-dir testTracks --catalog data/tracks.json
```

Inspect catalog statistics and list track IDs:

```bash
python -m src.cli summary --catalog data/tracks.json
python -m src.cli list-tracks --catalog data/tracks.json --limit 20
```

Inspect the stored data for one track:

```bash
python -m src.cli inspect-track --catalog data/tracks.json --track-id <track-id>
```

Show the candidates analyzed for a track and the selected next track:

```bash
python -m src.cli recommend --catalog data/tracks.json --track-id <track-id> --show-candidates 10
```

Generate a no-repeat session queue from a selected track:

```bash
python -m src.cli queue --catalog data/tracks.json --track-id <track-id>
python -m src.cli queue --catalog data/tracks.json --track-id <track-id> --length 20 --top-k 5 --randomness 0.25
```

The queue contains following tracks only, not the selected starting track. Each selection uses the previous recommendation as its current track, and no track repeats within that generated session. If the eligible unseen library runs out, it returns a shorter queue.

Use `--top-k` to set the candidate pool size and `--randomness` from `0.0` through `1.0` to vary the selection. At `0.0`, the highest-ranked candidate is always selected.

For `summary`, `list-tracks`, `inspect-track`, and `recommend`, a missing catalog is created automatically when `--music-dir` is supplied:

```bash
python -m src.cli summary --catalog data/tracks.json --music-dir testTracks
```

The scanner obtains artist and title from filenames formatted as `Artist - Title.ext`, and reads `genre`, `year`, `album`, and `bpm` from embedded audio tags (via `mutagen`) when present, falling back to `unknown`/`None` if a file has no tags or can't be read. Genre tags are canonicalized into broad families (e.g. "classic rock", "album rock", "glam rock" → `rock`) so cross-artist similarity works even when raw tags are highly specific.

### Feature weighting

Track vectors combine four weighted blocks so genre/tempo/era drive similarity more than a raw artist match: `genre=0.35`, `bpm=0.25`, `year=0.20`, `artist=0.20` (see `FEATURE_WEIGHTS` in `src/track_analyzer.py`). Rebuild the catalog after upgrading so existing catalogs pick up real tag data and the new weighting.

**Note:** a track that shares both genre *and* artist with the current track will still score a perfect match (cosine similarity 1.0) — this is mathematically unavoidable when every weighted dimension agrees. If a queue feels too repetitive within one artist/genre, raise `--top-k`/`--randomness` (or the `top_k`/`randomness` settings) so ties don't dominate the sampling pool.

### Dependencies

Install with:

```bash
pip install -r requirements.txt
```

On externally-managed systems (e.g. Arch/CachyOS), use `pip install --user --break-system-packages -r requirements.txt` or install into a virtualenv instead.

## Play (3-Column TUI Player)

Interactive terminal player with 3-column layout: folders (left), songs (middle), queue (right), and player bar (bottom).

Requires [mpv](https://mpv.io/) installed and on `PATH`.

```bash
python -m src.cli play
python -m src.cli play --catalog data/tracks.json
```

### Layout

```
Folders      │ Songs        │ Queue       
folder-1     │ 1. Song A    │ 1. Song X   
folder-2 ◀   │ 2. Song B ◀  │ 2. Song Y   
folder-3     │ [R] Random   │            
             │              │            
[Play] Song B | [Space]Play/Pause [N]ext [R]andom [Q]uit
```

### Controls

**Folder Column (left)**
- `↑` / `k` — Previous folder
- `↓` / `j` — Next folder
- `Tab` — Move to songs column
- `Enter` / `Space` — Load and play first song

**Songs Column (middle)**
- `↑` / `k` — Previous song
- `↓` / `j` — Next song
- `Enter` — Play selected song
- `r` / `R` — Play random song from folder
- `Tab` — Move to queue column
- `Space` / `p` — Play / pause

**Queue Column (right)**
- `↑` / `k` — Scroll up
- `↓` / `j` — Scroll down
- `Tab` — Move to folders column
- `Space` / `p` — Play / pause
- `n` / `N` — Skip to next

**Global**
- `q` / `Q` — Quit
- `Space` — Play / pause (any column)
- `n` / `N` — Skip to next (any column)
- `r` / `R` — Play random (songs column only)

The app starts with the main layout. Select a folder, then a song to begin playback. Random play picks an enabled track from the current folder.
