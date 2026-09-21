# Music Recommender Design Document v1

> **Status: historical.** This is the design as written *before* implementation.
> It is kept as-is for the record. Several decisions in it were overturned
> once the code met real tag data — see [§9, As built](#9-as-built-v10), which
> is the only part of this document written after the fact.

## 1. Overview

- Goal: Given one track, suggest a musically similar next track using a metadata-based recommendation algorithm.
- Scope: Local, offline, single-user system using only song metadata and basic audio features (no cloud, no user history).
- Approach: Content-based recommender that represents tracks as feature vectors and uses similarity search (e.g. cosine) to pick the next track.[web:3][web:4]

## 2. Requirements

### 2.1 MUST have

- Input: A single track selected from the local library.
- Output: One suggested next track.
- Simple AI-backed algorithm implemented in Python.
- Deterministic core (top-k most similar) with optional randomness.

### 2.2 MIGHT have

- Local song database (JSON or SQLite) to store metadata and precomputed feature vectors.[web:12]
- Song organization features (tags, enable/disable flags).
- Song preprocessing pipeline to scan library and compute metadata and feature vectors.[web:5]
- Actual audio playback component that automatically plays the suggested track.

### 2.3 Will NOT have (v1)

- Complex or graphical user interface.
- Past listening context analysis or collaborative filtering.[web:9]
- Online services or user accounts.

## 3. Architecture

- Preprocessor
  - Scans a configured music folder.
  - Extracts tags (artist, title, genre, BPM if available, year).
  - Canonicalizes genres and normalizes numeric features.
  - Outputs a song catalog with feature vectors (JSON or SQLite).
- Recommender core
  - Loads song catalog and feature vectors.
  - Given a current track ID, computes similarity against all other tracks.
  - Selects a next track using top-k + randomness strategy.
- (Optional) Player
  - Simple CLI or minimal playback wrapper around external player.
  - On track end, calls recommender to select and play the next track.

## 4. Data Model

### 4.1 Track entity

- `id`: Internal unique identifier.
- `path`: File path to audio file.
- `title`: Track title.
- `artist`: Artist name.
- `album`: Album name (optional).
- `genre`: Canonical genre string.
- `bpm`: Beats per minute (float, nullable).
- `year`: Release year (int, nullable).
- `enabled`: Boolean flag for whether track is eligible for recommendation.

### 4.2 Feature vector

- Numeric features
  - Normalized BPM.
  - Normalized year (optional).
- Categorical features
  - One-hot genre vector.
  - Artist encoding (simple index or one-hot, with same-artist bonus in similarity).
- Optional features (future)
  - Mood or energy scores.
  - Basic audio descriptors (e.g. spectral centroid, tempo confidence).[web:4]

## 5. Algorithms

### 5.1 Feature construction

- Input: Track metadata record.
- Steps:
  - Map `genre` to a canonical index and build one-hot vector.
  - Normalize numeric fields (BPM, year) to [0, 1] using min–max scaling.
  - Map `artist` to an index; optionally create a one-hot or simple scalar feature for same artist.
- Output: Fixed-length numeric feature vector (NumPy array).

### 5.2 Similarity computation

- Similarity metric: cosine similarity between feature vectors.
- Alternative metrics (future): Euclidean distance or learned weighted similarity.[web:8]

### 5.3 Next-track selection

- Input: `current_track_id`, song catalog, feature vectors, configuration (top-k, randomness).
- Steps:
  - Build feature vector for current track.
  - For each candidate track where `enabled = True` and `id != current_track_id`:
    - Compute similarity score.
  - Sort candidates by similarity descending.
  - Take top-k candidates.
  - Apply weighted random selection where weights are derived from similarity scores and a randomness parameter.
- Output: One selected track ID.

## 6. Tech Stack

- Language: Python 3.
- Libraries:
  - `numpy` for vector math and similarity.
  - `pandas` (optional) for tabular song catalog management.
  - `mutagen` or `eyed3` for MP3 metadata extraction.
  - `sqlite3` (standard library) if using a relational DB.
  - `librosa` (future) for audio feature extraction.[web:4]

## 7. Directory Layout (proposal)

- `design/`
  - `DESIGN_DOC_v1.md`
- `src/`
  - `preprocess.py` – scan library, build catalog.
  - `features.py` – feature vector construction utilities.
  - `recommend.py` – recommender core and CLI.
  - `player.py` – optional playback integration.
- `data/`
  - `tracks.json` or `tracks.sqlite` – song catalog and feature vectors.

## 8. Future Work

- Incorporate basic audio features (tempo, timbre, energy) to improve similarity beyond metadata.[web:4][web:9]
- Experiment with learned similarity weights or simple classification models based on user feedback.
- Add minimal UI (terminal TUI or web UI) once core recommender is stable.
- Extend system with user listening history and collaborative filtering in later versions.

---

## 9. As built (v1.0)

Where the shipped system differs from the design above, and why. Everything
in §§1-8 is the original plan; this section is the correction.

### 9.1 Similarity: per-pair metadata comparison, not cosine over vectors

**Designed** (§5.1, §5.2): build a fixed-length numeric vector per track
(one-hot genre, min-max normalized BPM and year, artist index) and compare
vectors with cosine similarity.

**Built**: `track_similarity(a, b)` compares two tracks' metadata directly,
summing four independent 0-1 terms weighted by `FEATURE_WEIGHTS`
(genre 0.45, bpm 0.25, artist 0.20, year 0.10).

The vector model broke on three things that only showed up against real tags:

- **Missing data.** Roughly 13% of the reference library has no genre tag. A
  one-hot vector must encode "unknown" as *some* value, and whichever value it
  picks, every untagged track becomes a perfect genre match for every other
  untagged track — a 355-track clique all scoring 1.0. Scoring a missing
  signal as zero is just as wrong: it says "maximally dissimilar" when the
  truth is "not measured". The per-pair form returns `None` for a term either
  side lacks, skips it, and renormalizes the remaining weights. That is
  impossible to express as a fixed vector, because the dimensionality would
  have to change per pair.
- **Year became recency, not similarity.** Encoded as one min-max scaled
  scalar, two 2024 tracks scored a large match and two 1960 tracks scored
  almost nothing — for the identical zero-year gap. Decay over the absolute
  *difference* has no such asymmetry.
- **Tempo is circular.** 87 and 174 BPM are the same groove counted two ways,
  a beat-detection and tagging ambiguity rather than a real difference. A
  linear normalized scalar puts them at opposite ends of the scale. The built
  version wraps the log2 ratio, so octave-equivalent tempi match.

A useful side effect: because nothing is persisted per track, the derived
features are recomputed on load, so a canonicalization fix applies on the next
run instead of requiring every tracked folder to be rescanned.

### 9.2 No numpy, no pandas, no sqlite

**Designed** (§6): numpy for vector math, pandas optionally for the catalog,
sqlite3 if relational.

**Built**: the standard library plus `mutagen`. Once similarity stopped being
vector math, numpy had nothing left to do — the whole scorer is four small
scalar functions. The catalog is one JSON file (`data/library.json`), written
atomically; at a few thousand tracks a linear scan per recommendation is
imperceptible, and a plain file is inspectable, diffable and trivially backed
up. Three runtime dependencies were added that the design did not anticipate:
`dbus-next` (MPRIS media keys), `Pillow` (cover-art PNG normalization), and
optional `aubio` (tempo detection from audio, for the common case of files
with no BPM tag).

### 9.3 Genre: a grouping tree, not a flat canonical index

**Designed** (§5.1): map genre to a canonical index, build a one-hot vector.

**Built**: two stages. Tags are first canonicalized (alias map, then an
ordered keyword-family fallback), then each label is placed in a
`(subfamily, family)` grouping, and similarity is graded: same label > same
subfamily > same family > a small table of cross-family bridges > unrelated.

Flat canonicalization was tried first and lost real information. Of 198
distinct labels in the reference library, 89 occur exactly once and 185 match
none of the broad families — about a fifth of the library sits in labels like
"brostep" or "indietronica" that are similar to nothing under a flat scheme.
Collapsing those into their broad family fixes the isolation but destroys the
distinction, since "electronic" alone is 23% of the library. The tree keeps
both.

### 9.4 The player stopped being optional

**Designed** (§2.2, §3): playback was a "MIGHT have", a "simple CLI or minimal
playback wrapper".

**Built**: a three-column curses TUI over an `mpv` process driven by JSON IPC,
with background-threaded scanning and tempo analysis, live search, sorting, a
settings screen, cover art via the Kitty graphics protocol, and MPRIS media
keys. §2.3 ruled out a "complex or graphical user interface"; a terminal UI
stays within the spirit of that (no GUI toolkit, no web stack) while being
considerably more than the design imagined.

### 9.5 Ranking alone was not enough: queue-assembly filters

**Not designed at all.** §5.3 selects a next track by similarity plus top-k
and randomness, and stops there.

That turned out to be insufficient, for a reason the design could not have
predicted from paper: a track sharing genre and artist with the current one
scores a near-perfect match, so pure ranking queues up one artist at a time,
and overlapping source folders mean the same song is often present several
times over. Two filters sit between ranking and selection, both of which fall
back to the unfiltered pool rather than ever stalling playback:

- an **artist-repeat cap**, counting occurrences across a sliding window
  rather than a trailing run (a dominant artist walks straight past a run
  check: with a cap of 3, "A A A B B B A A A" is never blocked);
- a **work-key cooldown**, which suppresses other *versions* of a recently
  played song — a remix, or a duplicate from another folder — while leaving
  them reachable later in the session.

Generated queues additionally anchor part of each step's score to the seed
track, so a long queue does not drift somewhere unrecognisable one small step
at a time.

### 9.6 Multiple folders, one library

**Designed** (§3): "scans a configured music folder", singular.

**Built**: any number of tracked folders in one `Library`, added and rescanned
from the TUI or the CLI, with the catalog merged across all of them. Scanned
folders are strictly read-only — no catalog file is ever written inside a
music folder.

### 9.7 What the design got right

Worth recording, since most of it survived contact with the data: the
content-based, local, offline, single-user framing (§2.3's exclusions are all
still honoured); `enabled` as the eligibility gate; the `Track` entity's
fields, which are almost exactly `TrackRecord`; top-k plus weighted randomness
as the selection strategy; and the preprocessor/recommender/player split,
which is still the module boundary in `src/`.

### 9.8 Still future work

§8's list is largely untouched. Audio features beyond tempo (timbre, energy)
are not extracted; learned similarity weights are not, and given §2.3 would
need care not to become a listening-history model by the back door. Weight
tuning was attempted and deliberately abandoned: an offline ablation preferred
dropping the tempo term, but a blind A/B listening test could not detect the
difference (10-6, p=0.454), so the weights were left alone and the result
recorded rather than acted on.
