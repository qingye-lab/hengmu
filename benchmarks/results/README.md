# Preserved model runs

This directory contains versioned, model-visible behavior evidence rather than
golden answers.

- `*.yaml` files preserve every structured trial, model, Codex surface, Skill
  version, duration, Finding, evidence reference, recommendation, and solution
  decision emitted by the benchmark runner.
- `*.log.jsonl` files preserve the hash-bound execution record for every trial;
  schema 1.4 runs bind these logs plus source, tools, dependencies,
  configuration, fixtures, environment, command templates, exact per-trial
  argument vectors, and external runtime executable/version fingerprints.
- `*-score.json` files are deterministic projections produced by
  `architecture_tool.py benchmark-score` against
  `benchmarks/ground-truth.yaml`.

Do not hand-edit a run, log, or score to improve a metric. Rerun the complete corpus
with a new output artifact when a model, Skill, adapter, fixture, schema, or
Ground Truth contract changes. Model-visible fixtures must remain
outcome-neutral.

The current release-candidate interpretation is recorded in
[`benchmarks/reports/1.0.3-model-behavior.md`](../reports/1.0.3-model-behavior.md).
