# Hengmu roadmap

This is the repository's canonical forward plan. Architecture Decisions record
why a durable choice was accepted, the Changelog records shipped behavior, and
GitHub Issues may track execution only when they link back to this file.

## Released baseline

- v1.0.3 established full-matrix release gating and portable package smoke
  evidence on 2026-08-09.
- v1.0.4 updated GitHub provenance and SPDX SBOM attestation to the verified
  `actions/attest` 4.2.2 commit on 2026-08-10.
- The 2026-08-27 quality-baseline repair aligned the Ruff specification and
  lock and added an Ubuntu Python 3.12 CI lane. It changes development evidence
  only and does not independently justify a release.

## v1.1 engineering trust

Status: in implementation.

Required outcomes:

- maintain at least 68% branch-aware coverage of `resources/scripts`, plus 90%
  coverage of non-deleted Python lines changed under `resources/scripts` and
  maintainer `scripts` by a pull request;
- run strict mypy checking for `resources/scripts` and `scripts` without broad
  suppressions while retaining JSON Schema as the runtime authority;
- support CPython 3.11 through 3.14 with eight hosted endpoint/intermediate
  lanes and one stable event-aware `Quality gate` result;
- keep dependency audit authoritative in the Ubuntu Python 3.14 lane;
- prepare exactly two ZIPs, two checksums, and two SPDX 2.3 SBOMs in a resumable
  draft through the tag workflow, without publishing; and
- publish only through a separate local administrator phase whose first remote
  administration check fails closed unless the read-only repository
  immutable-releases endpoint returns a well-formed `enabled: true`;
- verify published immutable assets and attestations without modifying a
  published Release.

Done when all local gates pass, the hosted eight-lane matrix and summary gate
pass, release-stage evidence reaches V4, a future v1.1.0 tag prepares the exact
six-asset draft, and a separate local administrator publication verifies the
immutable Release. Repository rulesets and immutable-release settings are
enabled only as a separately observed remote administration step. Before the
first v1.1 tag, an administrator must enable immutable releases and the
maintainer must retain an authenticated GET readback; the tag workflow neither
enables the setting nor publishes.

## v1.2 AI Knowledge 2026

Status: planned; blocked on v1.1.

Add versioned, freshness-bound Knowledge for MCP, A2A, Agent Skills,
OpenTelemetry GenAI, agent evaluation design, and secure agent-tool runtimes.
Use official specifications or primary research, add deterministic selection
and exclusion cases, and make no model-quality or legal-compliance claim.
Public Skills, Rule Packs, Evidence Providers, runtime network behavior, and
artifact schemas remain unchanged.

## v1.3 behavioral assurance

Status: planned; blocked on v1.2.

Extend the existing benchmark contract and runner with model/harness identity,
configuration hashes, trial status and failure taxonomy, confidence intervals,
and tokens-per-solved. Preserve schemas 1.1 through 1.5 as readable history.
Run a bounded two-model canary before any full model-behavior evidence and fail
closed when either named model is unavailable.

## v1.4 modular runtime

Status: planned; blocked on v1.1 quality foundations and v1.3 evidence.

Characterize the 31-command CLI before extracting foundation, benchmark,
knowledge, evidence, reviews, decisions, control-plane, gate, and CLI modules.
Keep `resources/scripts/architecture_tool.py` as the compatibility facade and
preserve command paths, parameters, help, output, exit codes, hashes, schemas,
V4/V5 policy, archived evidence semantics, and both portable package paths.

## Conditional work

- Add an opt-in host lifecycle adapter only when a named host requires it for a
  public Hengmu outcome and supplies versioned test evidence.
- Add a new public Skill or Rule Pack only after deterministic activation and
  benchmark evidence demonstrates a distinct unmet user goal or invariant.
- Keep hosted dashboards, automatic product-code mutation, automatic risk
  approval, custom signing keys, and a multi-tenant SaaS control plane outside
  the current local-first product boundary unless a new accepted decision
  changes that constraint.
