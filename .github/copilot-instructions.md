# Copilot Instructions

**`CLAUDE.md` in the repository root is the single source of truth for this
project's architecture and conventions. Read it first.**

This file used to carry its own copy of that description and drifted badly out
of date — it claimed cosine similarity over a shared feature space, a weight
table the code no longer uses, and `CATALOG_VERSION = 1`. Rather than maintain
two descriptions that disagree, it now holds only the commands and points at
the one that is kept current.

## Build, test, and lint commands

Dependencies are `mutagen` (tag reading), `dbus-next` (MPRIS media keys) and
`Pillow` (cover-art PNG normalization), listed in `requirements.txt`. `aubio`
is an optional extra for `analyze-bpm` and deliberately not listed. There is no
build step and no lint configuration.

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

Judge a recommender change with the measurement tools in `tools/`, not by
argument — `CLAUDE.md`'s "Key conventions" explains which of the three answers
which question.

## Where things live

| Area | Read |
| --- | --- |
| Architecture, module responsibilities | `CLAUDE.md` § Architecture |
| Invariants a change must not break | `CLAUDE.md` § Key conventions |
| User-facing behaviour, CLI, keybindings | `README.md` |
| Original design and how the build diverged | `DESIGN_DOC.md` |
