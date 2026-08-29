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
- v1.1.0 was tagged on 2026-08-27 but remained unpublished because GitHub
  skipped the reusable release job after the successful summary gate.
- v1.1.1 was tagged on 2026-08-28 but remained unpublished. Its tag workflow
  retained an exact six-asset draft, then failed because the post-upload lookup
  used GitHub's published-only tag endpoint, which does not return drafts.
- v1.1.2 was published as immutable on 2026-08-29T02:49:40Z after authenticated
  draft discovery, exact-six inventory verification, attestations, and the
  separate administrator publication phase completed.
- v1.1.3 was published as immutable on 2026-08-29T06:19:03Z after the patched
  GitHub CLI publication preflight, hosted pull-request/main/tag `Quality gate`
  runs, exact-six asset and attestation verification, and current-host smoke
  completed.

## v1.1 engineering trust

Status: complete through the immutable v1.1.3 publication.

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

The v1.1.1 patch repaired tag-job orchestration and successfully retained its
exact six-asset draft, but the publisher then treated GitHub's documented 404
for an authenticated draft on `/releases/tags/{tag}` as absence. v1.1.2 keeps
that endpoint as the published fast path and, only after a 404, completely
paginates authenticated Releases, requires one exact tag match, and reads the
numeric Release ID as the authoritative draft, immutable, and asset state.
The retained v1.1.1 tag and draft are not moved, published, or mutated.
v1.1.3 adds a fail-closed GitHub CLI version check to the separately authorized
publication phase: local asset enumeration still happens first, while every
remote administration, Release lookup, publication, and verification call is
blocked unless a stable GitHub CLI version at or above 2.93.0 is identified.

The v1.1.3 pull-request, main, and tag workflows passed the stable summary
gate; its exact-six draft, attestations, immutable publication, and current-host
installation, routing, and Knowledge replay were independently verified.
Repository rulesets and immutable-release settings remain separately observed
remote administration; the tag workflow neither enables the setting nor
publishes.

## v1.2 AI Knowledge 2026

Status: source implementation in progress; release gates remain pending.

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
