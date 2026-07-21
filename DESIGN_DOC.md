# Music Recommender Design Document v1

## 1. Overview

- Goal: Given one track, suggest a musically similar next track using a metadataâ€‘based recommendation algorithm.
- Scope: Local, offline, singleâ€‘user system using only song metadata and basic audio features (no cloud, no user history).
- Approach: Contentâ€‘based recommender that represents tracks as feature vectors and uses similarity search (e.g. cosine) to pick the next track.[web:3][web:4]

## 2. Requirements

### 2.1 MUST have

- Input: A single track selected from the local library.
- Output: One suggested next track.
- Simple AIâ€‘backed algorithm implemented in Python.
- Deterministic core (topâ€‘k most similar) with optional randomness.

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
  - Selects a next track using topâ€‘k + randomness strategy.
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
  - Oneâ€‘hot genre vector.
  - Artist encoding (simple index or oneâ€‘hot, with sameâ€‘artist bonus in similarity).
- Optional features (future)
  - Mood or energy scores.
  - Basic audio descriptors (e.g. spectral centroid, tempo confidence).[web:4]

## 5. Algorithms

### 5.1 Feature construction

- Input: Track metadata record.
- Steps:
  - Map `genre` to a canonical index and build oneâ€‘hot vector.
  - Normalize numeric fields (BPM, year) to [0, 1] using minâ€“max scaling.
  - Map `artist` to an index; optionally create a oneâ€‘hot or simple scalar feature for same artist.
- Output: Fixedâ€‘length numeric feature vector (NumPy array).

### 5.2 Similarity computation

- Similarity metric: cosine similarity between feature vectors.
- Alternative metrics (future): Euclidean distance or learned weighted similarity.[web:8]

### 5.3 Nextâ€‘track selection

- Input: `current_track_id`, song catalog, feature vectors, configuration (topâ€‘k, randomness).
- Steps:
  - Build feature vector for current track.
  - For each candidate track where `enabled = True` and `id != current_track_id`:
    - Compute similarity score.
  - Sort candidates by similarity descending.
  - Take topâ€‘k candidates.
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
  - `preprocess.py` â€“ scan library, build catalog.
  - `features.py` â€“ feature vector construction utilities.
  - `recommend.py` â€“ recommender core and CLI.
  - `player.py` â€“ optional playback integration.
- `data/`
  - `tracks.json` or `tracks.sqlite` â€“ song catalog and feature vectors.

## 8. Future Work

- Incorporate basic audio features (tempo, timbre, energy) to improve similarity beyond metadata.[web:4][web:9]
- Experiment with learned similarity weights or simple classification models based on user feedback.
- Add minimal UI (terminal TUI or web UI) once core recommender is stable.
- Extend system with user listening history and collaborative filtering in later versions.
