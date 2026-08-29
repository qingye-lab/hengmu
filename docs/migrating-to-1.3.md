# Migrating to Hengmu 1.3

Hengmu 1.3 adds benchmark run schema `1.6`. Schemas `1.1` through `1.5` remain
readable and the exact `1.0.0` Ground Truth is retained at
`benchmarks/ground-truth-1.0.0.yaml` for matching historical artifacts.

New runs consume a trusted command-result envelope, preserve one JSONL record
and one trial for every attempt, and record failures instead of stopping the
artifact. A failed trial never counts as solved. Case-level copies of the first
trial are omitted in 1.6.

Context manifest `1.1` permits an optional `case_id`. Resolution chooses an
exact `(condition, skill, case_id)` entry before the `(condition, skill)`
default. Commands bind `{case_id}` whenever any override exists.

Before full evidence, run and verify the 12-attempt canary. Full verification
requires six artifacts—one per exact model and condition—and 270 attempts.
Requested model strings do not establish actual model identity; missing,
aliased, mismatched, or fallback identity blocks matrix validation.
