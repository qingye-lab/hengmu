---
id: technology.model-context-protocol
kind: technology-profile
version: 1.0.0
status: active
maturity: standard
domains:
- ai-agent
triggers:
- mcp
quality_attributes:
- interoperability
- security
related:
- domain.ai-agent
last_reviewed: '2026-08-29'
review_after_days: 45
source_policy: official-docs-required
sources:
- title: Model Context Protocol specification revision 2026-07-28
  url: https://modelcontextprotocol.io/specification/2026-07-28
  authority: standard
  supports:
  - MCP-WIRE
  - MCP-AUTH
  - MCP-ANNOTATIONS
- title: Model Context Protocol security best practices
  url: https://modelcontextprotocol.io/specification/2026-07-28/basic/security_best_practices
  authority: standard
  supports:
  - MCP-AUTH
dynamic_facts: true
version_range: Protocol revision 2026-07-28 only; verify the current official revision and negotiated capabilities before use.
---

# Model Context Protocol

## Problem and intent

Use a versioned client-host-server protocol to expose contextual resources,
prompts, and callable tools without embedding every integration into the model
application.

## Mechanism

Bind initialization, capability negotiation, JSON-RPC messages, transport,
lifecycles, and authorization decisions to the protocol revision implemented by
each participant. Keep the host responsible for consent and policy instead of
treating server metadata or model output as authority.

## Fit when

An application needs an interoperable boundary for independently owned context
or tools and can operate version negotiation, consent, authorization, and
server trust explicitly.

## Avoid when

A local typed function or fixed service API already provides the required
boundary, or the deployment cannot mediate server identity, user consent, tool
effects, and untrusted content.

## Required capabilities

Protocol-revision negotiation, server identity, transport security,
least-privilege authorization where authorization is used, explicit consent,
schema validation, timeouts, audit records, and revocation are required.

## Benefits

The protocol separates interoperable discovery and invocation from a specific
model vendor while preserving explicit resource, prompt, and tool contracts.

## Costs and liabilities

Hosts inherit lifecycle, consent, authorization, transport, compatibility,
prompt-injection, confused-deputy, and tool-side-effect responsibilities that
the wire protocol does not remove.

## Failure modes

Failures include trusting tool annotations as security facts, forwarding
tokens to the wrong server, accepting capability drift, exposing excess user
context, or allowing model-selected calls to bypass deterministic policy.

## Alternatives

Use a direct application API, a typed in-process adapter, OpenAPI-bound tools,
or a fixed workflow when interoperability and independent server discovery are
not required.

## Migration and exit

Introduce one read-only server behind an allowlist, bind the negotiated
revision and capabilities in evidence, then expand authority only after
negative-path tests. Preserve a direct adapter until the MCP boundary can be
removed without changing the user contract.

## Evidence to inspect

Inspect the implemented protocol revision, initialization exchange,
capabilities, transport, server identity, authorization mode, consent surface,
tool schemas, annotation handling, token audience, audit trail, and tested
failure paths.

## Evidence that changes the recommendation

A simpler owned API meeting the same interoperability scenario, an unverified
server, missing consent or token audience controls, or a protocol-revision
mismatch changes the recommendation.

## Quality trade-offs

Interoperability and integration speed trade against a larger trust boundary,
more compatibility states, additional latency, and host-owned security and
operational work.

## Volatile facts

The bound revision is 2026-07-28. Authorization is an optional protocol
capability rather than a universal deployment guarantee, and annotations are
untrusted hints. Recheck the official revision and security guidance before a
current decision.

## Claim map

- MCP-WIRE: The 2026-07-28 specification defines the bound protocol lifecycle and capability negotiation.
- MCP-AUTH: Authorization support does not transfer consent, token-audience, or least-privilege responsibility away from the host and deployment.
- MCP-ANNOTATIONS: Tool annotations are untrusted metadata and cannot authorize an operation.
