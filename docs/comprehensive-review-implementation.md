# Comprehensive review implementation matrix

This document maps the recommendations in
`codex-architecture-governance-comprehensive-review.md` against version 0.3.
It distinguishes executable capability from documentation and from evidence
that only an external model or repository can produce.

## Outcome

The review's central gap is closed as an end-to-end contract:

```text
project context
→ current architecture and candidate findings
→ independent, provenance-bound verification
→ knowledge-bound option decision
→ accepted target
→ migration plan
→ hashed completion evidence
→ layered deterministic gate
```

The plugin therefore does more than report missing capabilities. A confirmed
gap can be related to sourced architecture knowledge, compared against
keep-current and structural options, accepted by an authorized decision maker,
planned as reversible slices, and proven complete with evidence.

## Trust-chain findings

| Review item | Implemented control | Executable evidence |
| --- | --- | --- |
| P0-1: `verified` was a writable label | Schema 1.1 binds verifier type/identity/run/time, candidate ID/hash, Profile/rule hashes, scope, commit, dirty state, and Finding fingerprint. | `validate-review --project`, `review-bindings`; provenance and tamper tests. |
| P0-2: same actor could claim independence | Policy roles cover auditor, verifier, decision maker, risk acceptor, policy owner, and maintainer; explicit role-separation pairs are enforced. V3–V5 require a human. | Gate role tests; `verify-review-signature` for V5. |
| P0-3: `accepted-risk` bypass | Acceptance moved to a separate two-party registry with exact fingerprint, authorized accepter/approver, controls, time, and expiry. | Risk registry validator and gate tests. |
| P0-4: incomplete coverage | Every trusted review covers every rule from exactly the declared Rule Packs; missing, duplicate, unknown, and cross-kind rules fail. Every required workflow needs a fresh trusted artifact. | Project/review validators and missing-review tests. |
| P0-5: unverified evidence | Git paths, commits, blobs, line ranges, symbols, excerpt hashes, and Evidence Provider runs resolve inside the repository. | `verify-evidence`, `run-evidence-provider`, `validate-evidence-run`; tamper tests. |
| P0-6: stale commit | Time-window, exact-commit, ancestor, and diff-aware strategies are policy choices. | `gate --base-commit`; ancestry and changed-path tests. |
| P0-7: ID-only suppressions | Baselines, waivers, and risk acceptance bind semantic Finding fingerprints and expire. | Gate fingerprint tests. |
| P1-1: plan did not verify sources | Plans bind exact trusted Review and accepted Decision hashes, confirmed findings, migration metadata, and completion evidence. | `validate-plan --project`; source/decision/completion tamper tests. |
| P1-2: path escape | Project/profile/review/provider/evidence paths are contained under explicit roots; portfolio registries are the explicit cross-repository authority. | Path-containment tests. |
| P1-3: lexical “latest” selection | Latest selection uses `performed_at`, then stable ID/file tie-breakers; explicit review paths remain available. | Gate selection tests. |

## Architecture capability

| Review requirement | Version 0.3 capability |
| --- | --- |
| Quality-attribute-first assessment | Profile 1.1 contains prioritized quality attributes and measurable source/trigger/environment/target/response/measure scenarios. Version 0.3 also separates deterministic repository facts, declared context, and bounded inference. |
| Business, team, stage, scale, cost, and operations | Structured business context records team count, ownership, distributed-systems experience, on-call, change frequency, scale, latency, availability, consistency, offline, deployment, budget, deadlines, and stack/migration constraints. |
| Architecture styles and fit/avoid guidance | Ten Markdown/frontmatter packs contain 205 sourced entries spanning foundations, domains, decision guides, styles, patterns, technologies, references, migrations, anti-patterns, and case studies. The 128 YAML entries remain read-only compatibility data. |
| Domain and stack depth | Nineteen core/domain Rule Packs cover project, AI agent, mobile, portfolio, frontend, backend, data, real-time, SaaS, identity, trading, IoT, search, media, test, plugin, local-first, desktop, and cloud-native boundaries. |
| Organization knowledge | Repositories can version schema-validated packs under `.architecture/rules/`; duplicate/shadowing IDs and review-kind mismatches fail. |
| AI Agent architecture | Dedicated Skill and Rule Pack cover agent/workflow boundaries, context necessity and assembly, compression fidelity, stable ordering and cache boundaries, memory, retrieval provenance, tool authority, injection, privacy, side effects, recovery, routing, version-bound behavior evidence, cost, technology evolution, and human control. |
| Mobile/client architecture | Dedicated Skill and Rule Pack cover server-first/cache/local-first decisions, local data, sync, conflicts, migrations, background work, notifications, privacy, and lifecycle. |
| Portfolio/system-of-systems architecture | Dedicated Skill, portfolio registry/schema, shared capability catalog, technology catalog, dependency map, cross-repository commit bindings, and portfolio gate. |

## Solution and evolution

| Review requirement | Version 0.3 capability |
| --- | --- |
| Separate solution authority | `architecture-solution-advisor` and Architecture Decision artifact sit between verified findings and remediation. The maintainer-only curator cannot select a project architecture. |
| Multiple candidates and keep-current | A decision requires at least three options, including keep-current; selected and nonselected options record reasons. |
| Hard elimination and proportionality | Decision guides encode rejection rules for unjustified microservices, workflow, event sourcing, local-first, and multi-agent designs. |
| Complete trade-off analysis | Every option records quality effects, business/team/evolution fit, complexity, operations, migration, reversibility, cost, maturity, lock-in, and an eleven-dimension scorecard. |
| Sourced framework choice | Schema 1.2 Decisions bind the task selection plus exact per-entry Markdown versions and hashes. |
| Reversible migration | Plans record slices, prerequisites, compatibility, data migration, deployment, observability, rollback, stop conditions, and acceptance criteria. |
| Completion rather than recommendation | Completed plan items must bind repository-relative evidence files and SHA-256 values for every declared acceptance evidence type. |

## Deterministic evidence and gates

Seventeen Evidence Provider contracts cover architecture, code-quality,
contract, test, runtime, security, and supply-chain evidence, including Ruff,
ESLint, Clippy, golangci-lint, SwiftLint, and Detekt. The adapter does not invoke
a shell. It hashes the real executable and configuration, filters environment
variables, enforces timeout/exit policy, captures outputs, and validates JSON,
SARIF 2.1.0, or JUnit before recording a pass.

The gate is cumulative:

1. Contract Gate validates artifacts, hashes, required reviews, coverage,
   roles, separation, and provider bindings.
2. Finding Gate evaluates verified severity/confidence/lifecycle plus
   fingerprint-bound baseline, waiver, and risk acceptance.
3. Change Gate evaluates commit ancestry/diff, evidence impact, critical/public
   contract changes, decision requirements, freshness, and signatures.
4. Release Gate requires configured test/security/supply-chain evidence,
   authorized accepted decisions, and complete remediation evidence.

GitHub integration is supplied as a template with a Check status, SARIF upload,
job summary, and updatable pull-request comment. `review-diff` compares finding
and coverage evolution. The scheduled freshness workflow validates knowledge
and opens or updates a maintenance issue rather than silently rewriting
decision knowledge.

Language-specific quality checks remain optional, project-owned Evidence
Providers. Catalog detection or executable availability never authorizes
installation or establishes an architecture pass; missing tools remain explicit
coverage gaps until the user approves a pinned project or toolchain change.

## Evaluation and open-source assurance

| Review requirement | Version 0.3 evidence |
| --- | --- |
| Skill routing | 45 direct, indirect, incomplete, negative, and edge cases across the stable entry and eight focused workflow Skills. Separate corpora cover selection, decisions, false positives, and artifact tampering. |
| Finding/solution adversarial cases | Ten code fixtures cover benign SQLite, healthy modular monolith, conflicting/shared writers, queue/workflow proportionality, client ownership, documentation contradiction, prompt authority, and single-agent sufficiency. |
| Evidence validity | The scorer independently resolves fixture path, line range, and exact excerpt; a model validity claim is ignored. |
| Stability and cost | The harness starts an independent command for each repetition and reports finding/severity stability, duration, and optional token/cost usage. |
| Runtime compatibility | CI covers Python 3.11 and 3.14 on Ubuntu, macOS, and Windows, plus Python 3.12 and 3.13 on Ubuntu. |
| Supply chain | Exact hashed locks, dependency vulnerability audit, explicit license allow/deny inventory, deterministic ZIP/checksum, license-complete SPDX SBOM, and GitHub provenance/SBOM attestations. |

## Honest external-evidence boundary

The repository does not publish a fabricated model-quality score. The corpus,
runner, repeated-trial schema, and scorer are executable, but a result becomes
evidence only after an identified model, Codex surface, Skill version, tool
availability, and date actually run it. External provider tools are similarly
configured and run by the audited repository; the plugin does not bundle every
language ecosystem.

The original report explicitly placed a large hosted dashboard, a self-built
MCP server, automatic repository discovery, automatic product-code mutation,
automatic risk approval, and enterprise multi-tenant SaaS in the deferred
category. They remain deliberate non-goals, not undocumented missing core
capabilities. A local/GitHub governance view, SARIF, PR summary, Review Diff,
organization Rule Packs, signed artifacts, scheduled knowledge freshness, and
multi-repository Portfolio review cover the recommended productization surface
without adding a hosted control plane.
