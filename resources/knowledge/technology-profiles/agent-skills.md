---
id: technology.agent-skills
kind: technology-profile
version: 1.0.0
status: active
maturity: standard
domains:
- plugin-platform
triggers:
- skill.md
- allowed-tools
- progressive-disclosure
quality_attributes:
- interoperability
- maintainability
related:
- domain.plugin-platform
last_reviewed: '2026-08-29'
review_after_days: 90
source_policy: official-docs-required
sources:
- title: Agent Skills specification
  url: https://agentskills.io/specification
  authority: standard
  supports:
  - SKILL-FORMAT
  - SKILL-DISCLOSURE
  - SKILL-TOOLS
- title: Agent Skills specification repository snapshot 69ef37e
  url: https://github.com/agentskills/agentskills/tree/69ef37e9424c0a7ea9dd2293b559e43ec8176379
  authority: maintainer
  supports:
  - SKILL-SNAPSHOT
dynamic_facts: true
version_range: Specification repository snapshot 69ef37e9424c0a7ea9dd2293b559e43ec8176379; the specification has no SemVer release contract.
---

# Agent Skills

## Problem and intent

Package discoverable instructions and supporting resources in a portable
directory contract so compatible agents can load focused procedures only when
their described goal applies.

## Mechanism

Use a `SKILL.md` file with constrained frontmatter and Markdown instructions,
plus optional scripts, references, and assets. Let the host discover metadata
first and progressively disclose the full skill and referenced resources.

## Fit when

A reusable agent workflow needs portable discovery, bounded instructions, and
co-located deterministic resources across compatible hosts.

## Avoid when

The behavior is an application feature, a security policy, an authorization
boundary, or a single local instruction that does not need portable discovery
and packaging.

## Required capabilities

Deterministic packaging, valid frontmatter, collision-resistant descriptions,
bounded resource references, source review, version or snapshot binding,
host-specific activation tests, and separate tool authorization are required.

## Benefits

The directory convention supports reusable goal-oriented workflows and
progressive disclosure without requiring one universal application runtime.

## Costs and liabilities

Activation overlap, stale instructions, context growth, host differences,
untrusted bundled scripts, source provenance, and resource-path portability
remain package-owner responsibilities.

## Failure modes

Failures include overlapping descriptions, loading broad instructions for an
unrelated request, path escape, executing unreviewed scripts, assuming optional
frontmatter is enforced, or treating package origin as trusted identity.

## Alternatives

Use repository guidance, an application command, a typed tool, an MCP server,
or ordinary documentation when portable agent workflow discovery is not the
actual requirement.

## Migration and exit

Package one focused workflow, validate it on each named host, preserve the
previous invocation path during adoption, and remove the Skill when its goal is
owned more reliably by deterministic application behavior.

## Evidence to inspect

Inspect the exact repository snapshot, `SKILL.md` frontmatter and description,
referenced files, package inventory, script hashes, host discovery behavior,
activation and negative cases, tool permissions, and provenance.

## Evidence that changes the recommendation

A deterministic product feature meeting the same goal, overlapping activation,
unreviewed executable content, unsupported host discovery, or missing source
provenance changes the recommendation.

## Quality trade-offs

Portability and progressive disclosure trade against divergent host behavior,
activation ambiguity, additional package review, and the lack of a universal
execution or security policy.

## Volatile facts

The specification has no bound SemVer release. This entry binds snapshot
`69ef37e9424c0a7ea9dd2293b559e43ec8176379`; `allowed-tools` is experimental
and does not guarantee enforcement, sandboxing, signing, authentication, or
authorization.

## Claim map

- SKILL-FORMAT: Agent Skills use a directory with `SKILL.md` and constrained metadata plus optional resources.
- SKILL-DISCLOSURE: Hosts can discover metadata before loading the full instructions and referenced resources.
- SKILL-TOOLS: `allowed-tools` is experimental metadata and is not a security enforcement guarantee.
- SKILL-SNAPSHOT: The entry binds the exact maintainer repository snapshot rather than inventing a specification SemVer.
