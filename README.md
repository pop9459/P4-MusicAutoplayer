# Music Autoplayer

A local, offline music recommender and player. Point it at folders of audio
files; it reads their tags into one central library, scores how similar any
two tracks are from that metadata, and plays a continuous queue of similar
tracks through `mpv` in a terminal UI.

Nothing leaves the machine. There are no accounts, no cloud services, no
listening history and no collaborative filtering — the recommendation comes
entirely from what is already in the files.

## How it works

```
music folders ──scan──▶ data/library.json ──▶ track_similarity(a, b) ──▶ queue ──▶ mpv
   (read-only)          tracked folders +      per-pair, weighted           filters
                        merged catalog         metadata comparison
```

1. **Scan.** `add-folder` walks a folder for `.mp3 .m4a .flac .wav .ogg .aac`,
   reads artist/title from the filename (`Artist - Title.ext`) and
   genre/year/album/bpm/duration from the embedded tags via `mutagen`. Scanned
   folders are never written to — everything lands in `data/library.json`.
2. **Canonicalize.** Raw genre tags are a long tail ("album rock", "glam
   rock", "australian rock"), so they are folded into broad families, while
   labels the grouping tree names explicitly keep their own identity and gain
   a `(subfamily, family)` instead — "synthpop" is not flattened into "pop".
3. **Score.** `track_similarity(a, b)` compares two tracks' metadata directly
   (see [Feature weighting](#feature-weighting)). There is no shared vector
   space and no cosine similarity.
4. **Assemble a queue.** Ranking alone queues one artist at a time, so the
   queue is built with filters on top: a top-k pool with optional randomness,
   a same-artist cap over a sliding window, and a cooldown that stops two
   versions of the same song (a remix, a duplicate from an overlapping
   folder) playing back to back.
5. **Play.** `mpv` runs in the background, driven over its JSON IPC socket.

## Install

```bash
pip install -r requirements.txt
```

On externally-managed systems (e.g. Arch/CachyOS) use
`pip install --user --break-system-packages -r requirements.txt`, or a
virtualenv.

Requires [mpv](https://mpv.io/) on `PATH` for playback. Optional: `aubio`
(Arch: `sudo pacman -S python-aubio`) for the `analyze-bpm` command.

Run the tests:

```bash
python -m unittest discover -s tests
```

## Quick start

```bash
python -m src.cli add-folder --path ~/Music    # scan a folder into the library
python -m src.cli play                         # open the player
```

## The library

Tracked folders and the merged catalog live in one file, `data/library.json`.
It is the source of truth for `play`.

```bash
python -m src.cli add-folder --path /path/to/music   # scan and track a folder
python -m src.cli list-folders                       # ids, track counts, paths
python -m src.cli rescan-folder --folder-id <id>     # refresh metadata for a folder
python -m src.cli remove-folder --folder-id <id>     # untrack it and drop its tracks
python -m src.cli analyze-bpm                        # detect tempo where no BPM tag exists
```

Each takes `--library path/to/library.json` to override the default from
`settings.json`. Re-running `add-folder` on an already-tracked path rescans it
rather than erroring: metadata is refreshed, deleted files are dropped, new
files are picked up, and each surviving track keeps its `enabled` flag.

`analyze-bpm` needs the optional `aubio` package. It runs at roughly
0.2–0.5s/track, so it is a separate command rather than part of a scan. It is
resumable — it skips tracks that already have a BPM and checkpoints as it
goes, so Ctrl-C is safe. Without it, the similarity score simply skips the
tempo term and redistributes its weight.

## Settings

`settings.json` in the repository root holds the defaults. Paths resolve
relative to that file.

```json
{
  "version": 1,
  "library_path": "data/library.json",
  "top_k": 5,
  "randomness": 0.15,
  "queue_length": 50,
  "max_consecutive_same_artist": 2,
  "normalize_volume": false
}
```

| Field | Meaning |
| --- | --- |
| `library_path` | Where the library lives. The only path the player treats as authoritative. Defaults to `data/library.json`. |
| `top_k` | How many of the highest-ranked candidates are eligible for each pick. |
| `randomness` | `0.0`–`1.0`. At `0.0` the top candidate always wins; higher values sample the pool by weight. |
| `queue_length` | How many tracks to keep queued ahead. |
| `max_consecutive_same_artist` | Cap on one artist's share of a recent window. `null` disables it. |
| `normalize_volume` | Loudness normalization (mpv's `dynaudnorm`), toggleable live from the settings screen. |

Use a different file with `--settings path/to/settings.json` before the
command. `play --top-k`, `--randomness` and `--length` override the file for
that run only; nothing is written back.

*This repository's own `settings.json` is deliberately left in the pre-library
format (`catalog_path`/`music_directory`) so a fresh run still exercises the
one-time migration into `data/library.json`. The migration never deletes or
modifies the old catalog file.*

## The player

```bash
python -m src.cli play
```

Three columns — tracked folders, the selected folder's tracks, the upcoming
queue — over a player bar showing the current track, a progress bar and a
status line. The queue column is a display; it is not focusable.

```
 [/] Search tracks...
Folders   │ All Tracks                      │ Now Playing
All Track…│ /home/you/Music                 │ Artist - Current Title
Albums    │ 2599 tracks  [sort: artist]     │ Queue (50)
Singles   │ [R] Play Random  [O] Sort       │ Artist - Next Title
          │   1. Title        Artist   3:45 │ Artist - Another Title
          │   2. Title        Artist   4:02 │ …
[A] Add folder …
        [PLAYING] Artist - Title | [Space]Play/Pause [,]Back [N]ext [R]andom [Q]uit
        [========>-----------] 1:23 / 3:45
```

### Controls

**Anywhere in the player**

| Key | Action |
| --- | --- |
| `Space` / `p` | Play / pause |
| `n` | Next track |
| `,` | Previous track |
| `r` | Play a random track from the current list |
| `/` | Open the search box |
| `s` | Open the settings screen |
| `q` | Quit |

**Folder column**

| Key | Action |
| --- | --- |
| `↑` `↓` / `k` `j` | Change folder |
| `Tab` / `Enter` | Move focus to the track list |
| `a` | Add a folder — prompts for a path (a drag-and-dropped path pastes as text) |
| `u` | Rescan the selected folder |
| `b` | Start or stop background tempo analysis |

**Track column**

| Key | Action |
| --- | --- |
| `↑` `↓` / `k` `j` | Change selection |
| `Enter` | Play the selected track and build a queue from it |
| `o` | Cycle sort: default → title → artist → album |
| `Tab` | Move focus back to the folder column |

**Search box** (`/`) — filters the track list live on every keystroke, matching
title or artist, accent-insensitively (`a` matches `á`). `Enter` closes it and
keeps the filter; `Esc` closes it and clears the filter. While it is open it
owns every key, so typing `q` in a query does not quit.

**Settings screen** (`s`) — `↑` `↓` move between fields, `→`/`+`/`l` and
`←`/`-`/`h` adjust, `Enter` edits `library_path` as text, `a` applies and
saves, `Esc` discards and returns, `q` quits.

Scans and tempo analysis run on background threads, so playback and the
controls stay responsive while progress is reported in the folder column.
They are mutually exclusive with each other, since either replaces the
library wholesale.

### Extras

- **Cover art.** In a terminal supporting the Kitty graphics protocol, the
  current track's embedded cover art is shown above the queue. Art is read
  from the file itself, never fetched from the network. In any other
  terminal the feature is inert and reserves no space.
- **Media keys.** An MPRIS service exposes play/pause, next, previous and
  stop on D-Bus, so hardware media keys work even when the terminal is not
  focused.

## Feature weighting

Similarity is computed **per pair from metadata**, not from vectors in a
shared feature space. `track_similarity(a, b)` (`src/track_analyzer.py`) sums
four independent 0–1 terms weighted by `FEATURE_WEIGHTS`: `genre=0.45`,
`bpm=0.25`, `artist=0.20`, `year=0.10`.

The load-bearing detail is what happens to a **missing** signal. A term where
either side has no usable data returns `None` and is *skipped*, and the
remaining weights are renormalized over what is left. It is not scored as
zero, and "unknown" is not a value that matches other unknowns. That per-pair
renormalization is why one weight table works whether or not the library
carries BPM tags: with none, genre/artist/year share the whole budget.

Each term handles its own quirk. Genre closeness comes from a two-level
grouping (same label > same subfamily > same family > unrelated) plus a small
hand-authored table of cross-family bridges. Tempo is a circular distance in
octaves, so 90 and 180 BPM match — half/double-time is a tagging ambiguity,
not a real difference. Artist is a Jaccard overlap of credited artists, so a
solo track and a three-way collaboration are related without being identical.
Year decays over the *gap* between two years, so it measures similarity rather
than recency.

Because the derived features are computed on load rather than persisted, a
canonicalization fix applies on the next run without rescanning anything.

**Note:** two tracks that agree on every compared field score exactly 1.0.
That is a statement about the data — there is nothing left in the metadata to
tell them apart — not an artifact of the scoring. Repetitive queues are
handled by the queue-assembly filters, not by inventing a difference the tags
do not contain.

## Debug CLI

These operate on a raw single-folder catalog file, independent of the library,
for inspection and scripting. Run `python -m src.cli --help` for the full list.

```bash
python -m src.cli build-catalog --music-dir testTracks --catalog data/tracks.json
python -m src.cli summary --catalog data/tracks.json
python -m src.cli list-tracks --catalog data/tracks.json --limit 20 --enabled-only
python -m src.cli inspect-track --catalog data/tracks.json --track-id <track-id>
python -m src.cli recommend --catalog data/tracks.json --track-id <track-id> --show-candidates 10
python -m src.cli queue --catalog data/tracks.json --track-id <track-id> --length 20 --top-k 5 --randomness 0.25
```

`summary`, `list-tracks`, `inspect-track`, `recommend` and `queue` create a
missing catalog automatically when `--music-dir` is supplied.

A generated queue contains only the tracks that follow, not the starting
track, and no track repeats within it. If the eligible library runs out, the
queue comes back shorter. `queue` also accepts `--max-consecutive-artist`.

## Judging a recommender change

Three tools, in increasing cost and decreasing proxy-ness. `CLAUDE.md`'s "Key
conventions" says which to reach for.

```bash
python tools/recommender_report.py    # session shape: saturation, drift, breadth, repeats
python tools/eval_holdout.py          # album-mate retrieval against a random baseline
python tools/ab_listen.py             # blind A/B listening test, with a p-value
```

Measured on the reference library: album-mate retrieval scores P@1 0.733
against a 0.000 random baseline, and in blind listening the recommender beat
a uniform-random control 12-0 (p=0.0005) and a same-genre random control 10-2
(p=0.039). Beating the same-genre control is the one that matters — it means
the tempo, artist and era terms are audible, not just the genre filter. A
third run testing `w_bpm=0` came back 10-6 (p=0.454), i.e. no audible
difference, which is why the weights are left as they are despite an offline
ablation preferring a change.
