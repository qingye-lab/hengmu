---
id: technology.opentelemetry-genai
kind: technology-profile
version: 1.0.0
status: active
maturity: standard
domains:
- ai-agent
- observability
triggers:
- genai
quality_attributes:
- observability
- security
related:
- technology.opentelemetry
- domain.ai-agent
last_reviewed: '2026-08-29'
review_after_days: 45
source_policy: official-docs-required
sources:
- title: OpenTelemetry GenAI semantic conventions snapshot 67dff02
  url: https://github.com/open-telemetry/semantic-conventions-genai/tree/67dff024110be5bd9f318006e733f4078e0f4c97
  authority: maintainer
  supports:
  - OTEL-SNAPSHOT
- title: OpenTelemetry GenAI model manifest snapshot 67dff02
  url: https://github.com/open-telemetry/semantic-conventions-genai/blob/67dff024110be5bd9f318006e733f4078e0f4c97/model/manifest.yaml
  authority: maintainer
  supports:
  - OTEL-MATURITY
- title: OpenTelemetry GenAI attributes registry snapshot 67dff02
  url: https://github.com/open-telemetry/semantic-conventions-genai/blob/67dff024110be5bd9f318006e733f4078e0f4c97/docs/registry/attributes/gen-ai.md
  authority: maintainer
  supports:
  - OTEL-ATTRIBUTES
- title: OpenTelemetry GenAI span conventions snapshot 67dff02
  url: https://github.com/open-telemetry/semantic-conventions-genai/blob/67dff024110be5bd9f318006e733f4078e0f4c97/docs/gen-ai/gen-ai-spans.md
  authority: maintainer
  supports:
  - OTEL-SENSITIVE
dynamic_facts: true
version_range: Semantic conventions snapshot 67dff024110be5bd9f318006e733f4078e0f4c97, schema gen-ai-dev/1.42.0-dev, Development maturity.
---

# OpenTelemetry GenAI Semantic Conventions

## Problem and intent

Represent generative-AI requests, responses, operations, agents, tools, and
usage with interoperable telemetry attributes and events rather than unrelated
vendor-specific field names.

## Mechanism

Instrument the actual model and agent boundaries with the bound OpenTelemetry
GenAI semantic-convention snapshot, record the schema and instrumentation
identity, and apply explicit collection, sampling, redaction, and retention
policy before exporting any content-bearing fields.

## Fit when

Teams already operate OpenTelemetry and need cross-component GenAI traces for
latency, usage, error, path, or evaluation diagnosis with an owned privacy and
observability policy.

## Avoid when

Ordinary service tracing is sufficient, no owner will interpret the signals,
or prompts, completions, tool arguments, or user identifiers cannot be safely
collected and governed.

## Required capabilities

Pinned schema and instrumentation versions, trace correlation, explicit opt-in
for sensitive content, redaction, access control, sampling, retention,
cardinality budgets, exporter security, and deletion handling are required.

## Benefits

Consistent semantic names can improve correlation and comparison across model,
agent, and tool boundaries while retaining the existing OpenTelemetry pipeline.

## Costs and liabilities

Development-status semantics may change, and content capture creates privacy,
security, storage, cardinality, retention, and vendor-backend compatibility
costs.

## Failure modes

Failures include emitting prompt or completion content by default, mixing
schema revisions, exposing tool arguments, treating missing usage as zero,
unbounded cardinality, or claiming trace completeness from partial spans.

## Alternatives

Use ordinary OpenTelemetry service spans, privacy-preserving aggregate metrics,
provider-native logs, or evaluation-specific execution records when GenAI
semantic interoperability is not required.

## Migration and exit

Begin with content-free operation, latency, error, and usage fields; verify
backend handling and privacy controls before any sensitive opt-in. Preserve a
schema-versioned translation boundary so experimental fields can be removed or
upgraded.

## Evidence to inspect

Inspect the semantic-convention snapshot and schema URL, instrumentation
library, emitted fields and events, content opt-ins, redaction, access policy,
sampling, retention, cardinality, exporter path, missing-value behavior, and
trace coverage tests.

## Evidence that changes the recommendation

Ordinary tracing meeting the diagnostic need, an unowned backend, absent
privacy controls, schema drift, sensitive fields enabled by default, or
unbounded cardinality changes the recommendation.

## Quality trade-offs

Interoperable diagnosis trades against experimental-contract churn, telemetry
cost, privacy exposure, and the risk that detailed traces are mistaken for
complete behavioral evidence.

## Volatile facts

This entry binds snapshot `67dff024110be5bd9f318006e733f4078e0f4c97`
and schema `gen-ai-dev/1.42.0-dev`, both at Development maturity. Sensitive
content is opt-in. The conventions do not provide a privacy, retention,
authorization, or trace-completeness guarantee.

## Claim map

- OTEL-ATTRIBUTES: The bound registry defines GenAI semantic attributes for interoperable instrumentation.
- OTEL-SENSITIVE: Content-bearing telemetry requires explicit opt-in and separate privacy controls.
- OTEL-SNAPSHOT: The exact repository snapshot and development schema identify the volatile contract.
- OTEL-MATURITY: Development maturity means fields can change and must not be treated as a stable universal contract.
