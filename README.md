# Music Autoplayer

A local, offline music recommender that selects a next track using a JSON catalog and metadata-derived feature vectors.

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

Use `--top-k` to set the candidate pool size and `--randomness` from `0.0` through `1.0` to vary the selection. At `0.0`, the highest-ranked candidate is always selected.

For `summary`, `list-tracks`, `inspect-track`, and `recommend`, a missing catalog is created automatically when `--music-dir` is supplied:

```bash
python -m src.cli summary --catalog data/tracks.json --music-dir testTracks
```

The current scanner obtains artist and title from filenames formatted as `Artist - Title.ext`. Embedded audio-tag extraction, BPM, year, and genre enrichment are not implemented yet, so those values are usually `unknown` in a freshly scanned catalog.
