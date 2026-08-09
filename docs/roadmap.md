# Hengmu roadmap

This is the repository's canonical forward plan. Architecture Decisions record
why a durable choice was accepted, the Changelog records shipped behavior, and
GitHub Issues may track execution only when they link back to this file.

## v1.0.3 release integrity

Status: in progress.

Required outcomes:

- reject unsafe raw ZIP entry names before platform normalization;
- require the complete supported OS/Python quality matrix before publication;
- smoke test both package projections through deterministic Knowledge Selection;
- preserve per-run evidence inputs for specialist audits and solution decisions;
- align remediation evaluations with the accepted-Decision authority boundary;
- record current, time-bound host evidence without inferring unavailable hosts;
- refresh current-version behavior evidence before making quality claims; and
- publish checksums, SPDX SBOMs, and provenance/SBOM attestations for both ZIPs.

Done when the local repository gate, the hosted quality matrix, the release
workflow, package verification, host report, and behavioral evidence all bind
the same v1.0.3 source ancestry.

## Evidence after v1.0.3

- Re-run a named-host smoke when that host or Hengmu's package contract changes.
- Re-run model behavior evidence only when the model, Codex surface, Skill
  contract, fixture, Ground Truth, or claimed quality changes.
- Open a focused compatibility issue when an observed host fails a public
  Hengmu outcome; do not infer a lifecycle adapter from installation syntax.

## Conditional work

- Add an opt-in host lifecycle adapter only when a named host requires it for a
  public Hengmu outcome and supplies versioned test evidence.
- Add internal import-boundary enforcement only after measured contributor or
  change-coupling pressure justifies the migration.
- Keep hosted dashboards, automatic product-code mutation, automatic risk
  approval, and a multi-tenant SaaS control plane outside the current product
  boundary unless a new accepted decision changes the local-first constraint.
