# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.4] - 2026-08-10

### Changed

- Release provenance, Codex package SBOM, and Agent Plugins package SBOM
  attestations now use the verified `actions/attest` 4.2.2 commit pin.

## [1.0.3] - 2026-08-09

### Added

- Extracted Knowledge Selection smoke tests for both the native Codex archive
  and the portable Agent Plugins archive.
- Per-run facts, Profile, Knowledge Selection, and compact-context paths for
  AI-agent, mobile, and solution-decision workflows.
- A canonical repository roadmap and a time-bound host compatibility evidence
  report for release validation.
- Two-model, three-trial-per-case 1.0.3 behavior evidence with strict source,
  runtime, execution-log, and fixture provenance.

### Changed

- Tag publication now runs only after the complete Linux, macOS, and Windows
  quality matrix succeeds for the same workflow and commit.
- Remediation routing cases now require an accepted Architecture Decision and
  preserve the separate authority for solution selection.
- The full Solution Advisor benchmark treatment now supplies the declared
  decision styles, guides, and case studies required by its decision corpus.

### Fixed

- ZIP smoke validation now checks original entry names before Windows path
  normalization and rejects Windows drive-relative or drive-absolute entries.
- Specialized audits now bootstrap missing project governance only when
  persistence is allowed and preserve read-only requests without mutation.

## [1.0.2] - 2026-08-08

### Added

- Versioned installation and invocation instructions for Codex and ChatGPT
  desktop, Cursor, VS Code and GitHub Copilot, and Kiro in both English and
  Simplified Chinese READMEs.

### Changed

- The compatibility boundary now distinguishes full Agent Plugins installation
  from Kiro's complete Agent Skills and shared-resources projection, and names
  the host-specific controls that remain outside Hengmu's portable core.

## [1.0.1] - 2026-08-08

### Added

- A host-compatibility matrix and accepted cross-host equivalence decision that
  distinguish portable workflow outcomes from client-specific lifecycle and
  permission controls.
- An extracted Agent Plugins archive smoke test that runs the primary audit
  path through deterministic Knowledge Selection in CI and release builds.

### Changed

- The Agent Plugins discovery manifest is now checked in at repository root,
  host-neutral, and identity-aligned with the native Codex manifest.

### Fixed

- Portable archives now retain the native manifest as inert provenance data,
  preventing Knowledge Selection from failing after extraction while
  preserving existing selector hashes and artifact contracts.

## [1.0.0] - 2026-08-06

### Added

- Constrained Greenfield design through the existing
  `architecture-solution-advisor`: Brief 1.1 records required, preferred, and
  prohibited constraints, and Decision 1.4 records their assessments and the
  complete target architecture.
- Greenfield target planning in Plan 1.3, bound directly to the accepted Brief
  and Decision with no fabricated Findings, mapped to runtime/deployment units,
  data ownership, interfaces, trust boundaries, critical flows, operations, and
  constraints.
- Routing and evaluation coverage for open/constrained design, specification,
  constraint conflicts, Chinese equivalents, target-architecture completeness,
  and Greenfield plan source validity.
- Compatibility and migration guidance for the one coordinated 1.0 artifact
  release, plus an accepted constrained-target architecture ADR.

### Changed

- Public positioning now presents Hengmu as an evidence-bound architecture
  design, decision, and governance system with two first-class entry paths:
  current-state assessment and open/constrained target design. The README,
  website, plugin metadata, workflow metadata, banners, governance diagrams,
  and editorial figures use the same model.
- The stable `hengmu` router routes design, specify, constrain, and their Chinese
  equivalents to the existing Solution Advisor. The public surface remains one
  router plus eight focused workflow Skills.
- Required constraints are challenged before becoming hard requirements;
  preferred constraints may lose with a recorded trade-off; prohibited options
  are hard-eliminated. Constraint inputs never substitute for proof.
- Technology-evolution remains a narrow evidence lens. Version pins require
  current official or repository evidence and are never supplied from memory.

### Compatibility

- Brief 1.0, Decision artifacts through 1.3, and Plan artifacts through 1.2
  remain readable. Brief 1.1, Decision 1.4, and Plan 1.3 ship together as the
  current 1.0 open/constrained target-design contract.

### Other work included in 1.0.0

#### Added

- A stable `$hengmu` entry point that shows the full capability menu, accepts
  natural-language goals, and routes to one of eight focused workflow Skills
  without requiring users to remember their public names.
- An idempotent `prepare-project-audit` command that creates a facts-derived
  `.architecture/` control plane when missing, validates existing governance,
  and refuses to overwrite partial user-owned state.
- Localized 青野/Hengmu icons and banners, an original 青野 editorial
  character, three paired English/Chinese article illustrations including the
  target-design hero, and paired Mermaid/Excalidraw/SVG/PNG governance flows.
- A full Simplified Chinese README that mirrors the English quick start,
  trust model, workflows, development commands, and project policies.
- Consumption-time `validate-knowledge-context` checks for the Selection lock,
  result hash, and exact ordered selected-entry projection.
- CI and release ancestry checks for the Selector source and latest reviewed
  implementation commits.

#### Changed

- The public surface now distinguishes one discoverability-only entry Skill
  from eight directly invocable workflow contracts; audit, verification,
  decision, planning, and gate authority remain separate.
- Dependabot now groups all Python version updates into one PR and caps routine
  Python and GitHub Actions update queues at two and one open PR respectively.
- GitHub workflows now use the pinned Node 24 releases of Checkout 7.0.1,
  Setup Python 7.0.0, and Dependency Review 5.0.0.
- `project-architecture-audit` now treats missing governance as a bootstrap
  condition and persists audits by default; Advisory mode requires an
  explicitly read-only request.
- The project, repository, installable plugin ID, release archive prefix, SBOM,
  and public tooling identity are now **Hengmu**. Pre-rename self-review
  artifacts retain their recorded identity as immutable internal evidence.
- The development lock now requires `pytest` 9.1.1 or newer within the 9.x
  series, removing the vulnerable 8.4.2 test-runner dependency.
- Runtime dependency bounds now require JSON Schema 4.26.0 and PyYAML 6.0.3,
  matching the verified lock files already used by installation and CI.
- Git-verified archived Selection locks remain historically readable but can no
  longer create new trusted Review, Decision, Plan, coverage, or Gate chains;
  current deterministic replay is required.

#### Fixed

- Profile construction now resolves facts stored at any depth under
  `.architecture/` from the repository root and records portable relative
  bindings instead of treating a per-run `reviews/inputs/` directory as the
  project.

## [0.4.2] - 2026-07-29

### Added

- Repository-facts schema `1.1` roles, role-aware Profile/Knowledge routing,
  and regression coverage so tests, examples, documentation, generated code,
  vendor trees, and benchmark fixtures cannot infer product architecture.
- Knowledge-selection schema `1.4` kind/maturity bindings, per-kind budgets,
  a source-anchored complete Selector Runtime Input Manifest, distinct project
  and plugin commits, current-runtime replay, safe archived Git-blob
  verification, and rejection of unverifiable locks from trusted chains.
- Compact model-facing Knowledge context sidecars that omit the full exclusion
  ledger while binding its machine-facing selection lock by SHA-256.
- Decision-intent namespaces for data-authority and plugin-runtime topology,
  preventing ambiguous `local-first` wording from loading unrelated client
  state guidance.
- A schema `1.5` Base/Full/Compressed behavior-benchmark treatment manifest,
  compact Skill assets, corpus-level declared-context proxy, and condition/
  manifest-bound execution provenance.
- Optional informational governance-run manifests and validation for high-risk
  Governed/Enforced trajectory records. They are deliberately excluded from
  the trusted Gate evidence chain.
- Advisory, Governed, and Enforced operating-mode guidance, plus a 0.4.2
  migration guide.
- A failure-driven Knowledge curation policy: new Golden knowledge must trace
  to a concrete decision-quality gap and add a representative regression.

### Changed

- The Solution Advisor Skill now keeps behavioral rules in its lean entry point
  and moves artifact-command detail into a focused reference.
- Gate policy schema `1.2` declares `product_mode`; its label cannot weaken or
  bypass deterministic Gate outcomes.
- Benchmark scoring reports tool-call telemetry when an invoked surface emits
  it, and distinguishes missing field-level usage telemetry from zero.

### Fixed

- Historical Knowledge selections are no longer re-evaluated with a future
  Selector or Knowledge tree; exact deterministic replay occurs only when the
  recorded creation runtime still matches.
- Python dependency inspection now parses normalized PEP 508 package names and
  applies exact aliases, avoiding `nextcloud-client`, `agentscope`, and
  `pgvector` substring false positives.

## [0.4.0] - 2026-07-29

### Added

- Twenty-one curated golden knowledge entries with named options, concrete
  operating mechanisms, failure and exit semantics, claim-to-source mappings,
  and maintainer curation provenance.
- A Greenfield Design Brief contract, template, validator, decision bindings,
  and Architecture Decision schema `1.3` path that does not manufacture a
  source review.
- Solution-decision benchmark observations and scoring for recommendation
  accuracy, over-design, trade-off coverage, knowledge citation validity,
  rejected-option explanations, migration actionability, and stability.
- A read-only Codex benchmark adapter with machine Rule IDs, canonical
  decision vocabulary, structured-output validation, and one bounded
  evidence-only correction that never receives ground truth.
- Benchmark schema `1.4` provenance that binds source, environment,
  dependencies, configuration, plugin manifest and Skill version, fixtures,
  runner and adapter bytes, reconstructible command templates, exact trial
  commands, external runtime fingerprints, and hash-verified execution logs.
- A Git-bound archived benchmark verification mode for cross-platform
  reviewers; strict current-runtime replay remains the default.
- Severity- and stage-aware verification policy: critical V3, high V2,
  medium/low V1, and V4 for risk acceptance and release.

### Changed

- Generated knowledge now defaults to draft and cannot become active without a
  non-generated curation record.
- Knowledge selection uses canonical domain IDs, includes `plugin-platform`,
  labels required/recommended/optional context, performs bounded one-hop
  relation expansion, and downweights generic reference-architecture matches.
- Knowledge validation rejects golden entries that are template-similar,
  omit named option trade-offs, or leave claims without authoritative sources.
- Backend API and web frontend domain guidance now cites relevant HTTP,
  OpenAPI, WCAG, and web-performance sources.
- Adversarial fixture paths and prose are outcome-neutral, with a regression
  test preventing expected decisions from leaking into model-visible inputs.
- Benchmark scores distinguish absent usage telemetry from actual zero
  token/cost consumption.
- Base-commit gates now require re-review only when a classified critical or
  security path changed after the reviewed commit; later governance-only
  records no longer create an impossible review/HEAD self-reference.
- An accepted `keep-current` decision with explicit migration slices,
  rollback, validation, and exact affected-path coverage now satisfies
  compatible migration governance without manufacturing a remediation plan
  for a non-risk Finding.

### Security

- Greenfield decisions bind the exact Design Brief and knowledge-selection
  bytes; remediation decisions retain verified-review provenance.
- Persistent risk acceptance and release evidence can no longer rely on the
  same global V1 verification floor as low-risk findings.

## [0.3.2] - 2026-07-29

### Fixed

- Made the benchmark command-rendering assertion compare against the native
  path representation so the cross-platform safety test passes on Windows
  without weakening its argument-boundary guarantee.

## [0.3.1] - 2026-07-29

### Fixed

- Added the Windows-only `colorama` dependency to the exact development lock
  so hash-enforced CI installation works on every supported runner.
- Bound SBOM generation, attestation, and release upload to one exact artifact
  path instead of passing an unexpanded glob to `actions/attest`.

## [0.3.0] - 2026-07-29

### Added

- Deterministic repository-facts inspection, provisional Profile construction,
  task-scoped knowledge selection, coverage validation, artifact
  fingerprinting, and safe legacy Review migration commands.
- Review, Finding, Architecture Decision, and Remediation Plan schema `1.2`
  bindings for facts, selected knowledge, critical flows, evidence
  fingerprints, finding fingerprints, assumptions, and migration slices.
- Ten Markdown/frontmatter Knowledge Packs containing 205 validated entries:
  foundations, domains, decision guides, architecture styles, patterns,
  technology profiles, reference architectures, migration guides,
  anti-patterns, and case studies.
- Knowledge manifest and entry schemas, relationship validation, source
  policy, freshness windows, stale-entry rejection, and explicit selection
  reasons and exclusions.
- Dedicated routing, knowledge-selection, decision-quality, false-positive,
  and artifact-validity evaluation corpora.
- Target-architecture, knowledge-authoring, 0.3 migration, and implementation
  documentation plus an accepted decision for the workflow/knowledge/script
  separation.

### Changed

- The public surface now contains exactly eight workflow Skills. Knowledge
  curation moved to `maintainer/skills/` because it is a release-maintenance
  role rather than an end-user architecture workflow.
- New project initialization records deterministic repository facts and keeps
  detected, declared, and inferred Profile inputs separate.
- New audits and decisions use schema `1.2`; schema `1.0` and `1.1` remain
  readable and trusted `1.1` artifacts retain their 0.2 compatibility path.
- Architecture knowledge is selected per repository, task, and Skill instead
  of loading every bundled catalog into context.
- Plugin and portable CLI version are now `0.3.0`.

### Security

- Legacy verified Reviews are migrated only as candidates. Migration cannot
  synthesize independent verification, critical-flow coverage, or current
  trust.
- Fact, Profile, selection, knowledge-entry, Finding, Review, Decision, Plan,
  and completion-evidence hashes are checked at their owning boundaries.

## [0.2.0] - 2026-07-28

### Added

- Architecture Solution Advisor and Architecture Knowledge Curator Skills.
- Eight machine-readable quality, style, pattern, technology, reference
  architecture, migration, domain, and decision-guide catalogs with 128
  sourced entries.
- Architecture Decision, Risk Acceptance, Rule Pack, Knowledge, Evidence
  Provider, and Benchmark schemas.
- Verified-review provenance bindings, verification levels, Finding
  fingerprints, Git evidence resolution, repository path containment, and
  machine-complete Rule Pack coverage.
- Contract, Finding, Change, and Release gate stages with exact, ancestor, and
  diff-aware freshness.
- Nineteen bundled core/domain Rule Packs plus validated repository-local
  organization Rule Packs.
- Eleven executable Evidence Provider adapters with shell-free invocation,
  executable/config/output hashing, timeout, safe environment propagation, and
  JSON/SARIF/JUnit structural validation.
- Role separation, human V3–V5 assurance requirements, deterministic evidence
  for V4–V5, and detached SSH review signatures at V5.
- Knowledge-bound three-option architecture decisions, hashed plan completion
  evidence, required-review enforcement, base-commit change classification,
  and review diffing.
- Ten adversarial architecture fixtures, repeated-trial stability, duration
  and optional usage metrics, deterministic benchmark scoring, and a
  caller-supplied forward-test harness.
- Fingerprint-bound baselines and waivers plus separately authorized,
  expiring risk acceptance.
- Fully hashed runtime/development locks, cross-platform checksum verification,
  license allow/deny audit, license-complete SPDX SBOM generation, dependency
  audit, SARIF output, and GitHub provenance/SBOM release attestations.

### Changed

- Trusted enforcement now requires artifact schema `1.1`; schema `1.0`
  remains readable for migration.
- Remediation planning consumes an accepted architecture decision instead of
  selecting technology and target architecture itself.
- CI now covers Python 3.11 and 3.13 on Ubuntu, macOS, and Windows.
- Plugin and portable CLI version are now `0.2.0`.

## [0.1.0] - 2026-07-28

### Added

- Seven focused architecture-governance Skills for project, AI-agent, mobile,
  portfolio, verification, remediation-planning, and quality-gate workflows.
- Versioned review, finding, profile, portfolio, remediation, baseline, and
  policy schemas.
- A portable CLI for initialization, validation, and deterministic policy
  evaluation.
- Repository validation, activation eval corpus, deterministic plugin
  packaging, CI, release automation, and open-source governance documents.
- A dogfooded project architecture profile with explicit constraints and
  critical flows.
