# Architecture constraints

These constraints define the real decision boundary for this repository.
Maintainers review them with every release that changes a public contract.

## Product and business

- The stable `hengmu` entry name, the eight focused workflow Skill names, and
  their distinct user goals are compatibility contracts. The entry may route
  but cannot merge or weaken workflow authority. Owner: maintainers. Source:
  plugin manifest, README, and the accepted entry-point decision. Changing them
  requires a documented migration and an appropriate SemVer release.
- Architecture diagnosis, independent verification, remediation planning, and
  deterministic gating remain separate workflows. Architecture solution
  selection sits between verification and remediation; knowledge curation
  remains a maintainer-only workflow separate from product decisions. Owner: maintainers. Source:
  accepted repository layout decision. This boundary is fixed unless a new
  architecture decision supersedes it.

## Platform and compatibility

- Runtime scripts support Python 3.11 through 3.14 with only PyYAML and
  jsonschema. Owner: maintainers. Source: CI boundary matrix and requirements.
  Review each minor release.
- The plugin keeps Skills as direct children of `skills/` and shared runtime
  contracts under root `resources/`. Native Codex and Agent Plugins packages
  expose the same workflow and CLI outcomes through separate identity
  manifests. Owner: maintainers. Source: package validation and the accepted
  cross-host equivalence decision. Review when either packaging contract
  changes.
- Host lifecycle, permission, rules, and steering integrations remain explicit
  opt-in adapters. They cannot silently install dependencies, broaden
  permissions, or change the portable Skill/CLI contract. The current product
  makes no SessionStart, dangerous-operation interception, or automatic
  completion-gate claim.
- Artifact schemas and CLI exit codes are public contracts. Schema `1.0`
  remains readable; trusted `1.1` remains compatible; new project artifacts
  use facts/selection-bound Review schema `1.2`; Knowledge selections use
  source-anchored Runtime Manifest schema `1.4`; Greenfield Decisions may use
  additive schema `1.3` bound to a Design Brief. Schema `1.3` selections remain
  read-only historical records but cannot enter a new trusted chain when their
  runtime differs. Compatible additions are preferred; incompatible changes
  require migration guidance.
- Generated knowledge is draft-only. Golden active entries require reviewed
  curation provenance, named option or operating-model depth, and source claim
  mappings. Owner: maintainers. Source: the knowledge-entry schema and
  validator.

## Security, privacy, and compliance

- Runtime behavior requires no network, credentials, telemetry, or external
  service. Explicitly enabled Evidence Provider commands are project-owned
  subprocesses and may have their own access; the governance runtime never
  supplies network or credentials implicitly. Adding access to the runtime
  itself requires a separate security and privacy review.
- Candidate model findings cannot block a build. Only schema-valid verified
  findings with complete Rule Pack coverage, provenance, evidence bindings,
  and authorized verification may enter the deterministic quality gate.
- Verification floors are proportional to severity and authority. The
  repository requires critical V3, high V2, medium/low V1, and V4 for release
  or persistent risk acceptance.
- Repository inspection may report observable technology and artifact facts
  but must never emit suitability, severity, or remediation conclusions.
- Initialization may create `.architecture/` or `.architecture-portfolio/`
  only at an explicit target and must refuse to overwrite existing state.
- Bundled Rule Packs and repository-local organization Rule Packs are separate
  namespaces under one validator. Local packs cannot shadow bundled IDs or
  cross a review-kind boundary.

## Operations, cost, and team

- Release artifacts contain only an explicit runtime allowlist and carry a
  reproducible SHA-256 checksum. Exact dependency locks, license policy, SPDX
  package declarations, and GitHub attestations are release requirements.
- The repository is maintained through reviewable source, schemas, tests,
  evaluation cases, and decision records; undocumented maintainer-only steps
  are not release prerequisites.

## Explicit non-goals

- This project does not implement an MCP server, hosted service, architecture
  dashboard, or automatic repository discovery.
- It records but never autonomously approves risk acceptance or waivers, and
  never modifies audited product code on a user's behalf.
