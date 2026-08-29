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
  - MCP-AUTH
  - MCP-ANNOTATIONS
- title: Model Context Protocol versioning and compatibility revision 2026-07-28
  url: https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
  authority: standard
  supports:
  - MCP-WIRE
  - MCP-DISCOVERY
- title: Model Context Protocol server discovery revision 2026-07-28
  url: https://modelcontextprotocol.io/specification/2026-07-28/server/discover
  authority: standard
  supports:
  - MCP-DISCOVERY
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

For revision 2026-07-28, carry protocol version, client identity, and client
capabilities in every request's `_meta`; Streamable HTTP mirrors the version in
`MCP-Protocol-Version` and rejects a header/body mismatch. A client may call
`server/discover` to obtain supported versions, capabilities, and server
identity, or invoke another RPC and handle `UnsupportedProtocolVersionError`.
Keep the host responsible for consent and policy instead of treating
self-reported server metadata or model output as authority.

## Fit when

An application needs an interoperable boundary for independently owned context
or tools and can operate per-request version and capability metadata, consent,
authorization, and server trust explicitly.

## Avoid when

A local typed function or fixed service API already provides the required
boundary, or the deployment cannot mediate server identity, user consent, tool
effects, and untrusted content.

## Required capabilities

Per-request revision and capability metadata, server identity, transport security,
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

Introduce one read-only server behind an allowlist, bind the requested revision
and capability metadata in evidence, then expand authority only after
negative-path tests. Use `initialize` only when an explicit dual-era client or
server falls back to legacy revision 2025-11-25 or earlier. Preserve a direct
adapter until the MCP boundary can be removed without changing the user
contract.

## Evidence to inspect

Inspect the revision, client identity, and capabilities carried in each
request's `_meta`; for HTTP, inspect the matching `MCP-Protocol-Version`
header. Inspect any optional `server/discover` result, transport, server
identity, authorization mode, consent surface, tool schemas, annotation
handling, token audience, audit trail, modern-version errors, and any explicit
legacy fallback path.

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

- MCP-WIRE: Revision 2026-07-28 carries protocol version, client identity, and capabilities as per-request metadata, with the protocol version mirrored in the Streamable HTTP header.
- MCP-DISCOVERY: Servers implement `server/discover`, but clients may instead invoke another RPC and handle a version error; there is no modern initialization handshake.
- MCP-AUTH: Authorization support does not transfer consent, token-audience, or least-privilege responsibility away from the host and deployment.
- MCP-ANNOTATIONS: Tool annotations are untrusted metadata and cannot authorize an operation.
