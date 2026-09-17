# Evaluation artifacts

`ab_listening_log.jsonl` is written by `tools/ab_listen.py` and read only by
`tools/ab_listen.py --report`.

**Nothing under `src/` may read this directory.** It records which of two
unlabelled queues a listener preferred, which makes it exactly the kind of data
that must not reach the recommender: `CLAUDE.md` keeps this product free of
listening-history models, and feeding these verdicts back into ranking would
build one. It is an evaluation artifact used to judge the recommender from the
outside, not a user model the recommender reads from the inside.

The log is gitignored — it is personal listening data.

Each line is one trial (`schema: 1`). `verdict` holds an arm *key* (`"A"`,
`"B"`, `"tie"`, `"skip"`, `"quit"`); resolve it through `arms[key].name` before
counting, since the same letter means a different config in every session.
`presented` records the blinding order so a trial can be audited afterwards,
and `rng_seed` makes the whole session reproducible once it is over.
