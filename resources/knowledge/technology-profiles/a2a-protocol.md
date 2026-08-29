---
id: technology.a2a-protocol
kind: technology-profile
version: 1.0.0
status: active
maturity: standard
domains:
- ai-agent
triggers:
- a2a
quality_attributes:
- interoperability
- reliability
related:
- domain.ai-agent
last_reviewed: '2026-08-29'
review_after_days: 90
source_policy: official-docs-required
sources:
- title: Agent2Agent Protocol specification v1.0.1
  url: https://a2a-protocol.org/latest/specification/
  authority: standard
  supports:
  - A2A-WIRE
  - A2A-TASK
  - A2A-SECURITY
- title: Agent2Agent Protocol 1.0 announcement
  url: https://a2a-protocol.org/latest/announcing-1.0/
  authority: official
  supports:
  - A2A-WIRE
dynamic_facts: true
version_range: Wire major 1.0 with current specification patch v1.0.1; verify the official patch and declared capabilities before use.
---

# Agent2Agent Protocol

## Problem and intent

Exchange tasks, messages, artifacts, status, and capability metadata between
independently implemented agents without sharing their internal orchestration.

## Mechanism

Use the A2A 1.0 wire contract and Agent Cards to discover capabilities, then
manage task identifiers, state transitions, messages, artifacts, streaming,
and push notifications at an explicitly owned handoff boundary.

## Fit when

Separate administrative or product boundaries need interoperable agent task
handoff and can assign ownership for identity, authorization, task state,
delivery, cancellation, retries, and recovery.

## Avoid when

The participants are modules in one process, a deterministic queue or API is
sufficient, or no component owns the authoritative task and side-effect state
across the handoff.

## Required capabilities

Authenticated peer identity, Agent Card validation, capability negotiation,
task ownership, idempotency, bounded retries, cancellation, artifact
validation, notification verification, auditability, and recovery are required.

## Benefits

A versioned wire boundary can decouple collaborating agents and preserve task
and artifact semantics without exposing private planning or memory.

## Costs and liabilities

Distributed ownership introduces discovery trust, state reconciliation,
duplicate delivery, partial failure, confused-deputy, compatibility, latency,
and cross-organization operational costs.

## Failure modes

Failures include ambiguous task ownership, accepting an untrusted Agent Card,
duplicated side effects after retries, lost cancellation, stale status,
unverified push notifications, and artifacts detached from their task.

## Alternatives

Use an internal function, a typed service API, a durable workflow, or a message
queue with application-owned task semantics when open agent interoperability is
not required.

## Migration and exit

Pilot a read-only or reversible task class, bind the wire version and Agent
Card, exercise retry and cancellation, and retain the prior handoff adapter
until ownership and recovery evidence support removal.

## Evidence to inspect

Inspect the negotiated A2A version, Agent Card source and signature or trust
path, authentication, authorization, task owner, transition log, retry keys,
cancellation semantics, artifact hashes, notification verification, and
recovery tests.

## Evidence that changes the recommendation

A single-owner API satisfying the same boundary, an unverifiable Agent Card,
unclear task authority, non-idempotent effects, or incompatible wire semantics
changes the recommendation.

## Quality trade-offs

Cross-agent interoperability trades against distributed-state complexity,
larger attack surface, more operational ownership, and potentially higher
latency and cost.

## Volatile facts

This entry binds wire major 1.0 and the current v1.0.1 specification patch.
Announcement language is not evidence of production fitness, security, or
interoperability for a particular pair of implementations.

## Claim map

- A2A-WIRE: The current official specification is v1.0.1 within wire major 1.0.
- A2A-TASK: Task state, messages, artifacts, streaming, and notifications require an explicit authoritative owner.
- A2A-SECURITY: Protocol conformance does not prove peer trust, authorization correctness, idempotency, or recovery.
