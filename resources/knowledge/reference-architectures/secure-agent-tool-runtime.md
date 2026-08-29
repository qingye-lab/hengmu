---
id: reference.secure-agent-tool-runtime
kind: reference-architecture
version: 1.0.0
status: active
maturity: standard
domains:
- ai-agent
triggers:
- tool-sandbox
- complete-mediation
quality_attributes:
- security
- reliability
related:
- domain.ai-agent
last_reviewed: '2026-08-29'
review_after_days: 90
source_policy: stable-principles-plus-official-docs
sources:
- title: OWASP AI Agent Security Cheat Sheet
  url: https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html
  authority: official
  supports:
  - RUNTIME-AUTHORITY
  - RUNTIME-MEDIATION
  - RUNTIME-SANDBOX
- title: AgentDojo a dynamic environment to evaluate prompt injection attacks and defenses for LLM agents
  url: https://arxiv.org/abs/2406.13352
  authority: research
  supports:
  - RUNTIME-INJECTION
- title: ToolEmu identifying the risks of LM agents with an LM-emulated sandbox
  url: https://arxiv.org/abs/2309.15817
  authority: research
  supports:
  - RUNTIME-TOOLS
---

# Secure Agent Tool Runtime

## Problem and intent

Execute model-requested tools while keeping identity, authorization, data
access, side effects, isolation, approval, and audit under deterministic control
outside the model.

## Mechanism

Route every tool request through a complete mediation point that resolves the
authenticated principal, validates typed arguments, checks least-privilege
policy and current state, obtains human approval for governed effects, executes
inside an appropriate sandbox, and records tamper-evident results.

## Components and responsibilities

- The model proposes a typed action but never grants authority.
- The orchestrator binds task state, limits, idempotency, cancellation, and retries.
- The policy enforcement point authenticates the principal and authorizes every call.
- The approval service confirms governed side effects without delegating approval to prompt text.
- The sandbox limits filesystem, network, process, secret, time, and resource access.
- Tool adapters validate inputs and outputs and enforce service-specific invariants.
- The audit pipeline binds request, policy, approval, execution, output, and hashes.

## Data flow

Untrusted user, retrieved, and tool content enters the model context; the model
emits only a proposal. The orchestrator normalizes it, policy evaluates the
principal and current resource, approval is obtained when required, the sandbox
executes the allowed adapter, and validated output returns with an audit record.

## Fit when

An agent can read sensitive data, call external services, execute code, mutate
state, spend resources, or trigger effects whose authority cannot safely be
represented by model judgment.

## Avoid when

The workflow is read-only and deterministic, no tool execution is needed, or a
fixed application endpoint already provides the complete policy and isolation
boundary with less complexity.

## Required capabilities

Authenticated identity, complete mediation, least privilege, typed contracts,
current-state authorization, explicit approval, sandbox isolation, secret
brokering, bounded resources, idempotency, cancellation, output validation,
logging, incident response, and adversarial tests are required.

## Benefits

The architecture constrains model uncertainty to proposals while deterministic
components own permission, execution, containment, and evidence.

## Costs and liabilities

Policy administration, sandbox operations, adapter maintenance, approval
latency, secret brokering, audit storage, and residual prompt-injection and
tool-vulnerability risk remain substantial.

## Failure modes

Failures include model-owned authorization, policy checked only at planning
time, confused-deputy calls, prompt-injected approvals, sandbox escape, leaked
secrets, unbounded retries, output injection, and audit records detached from
the actual side effect.

## Alternatives

Use a fixed workflow, a read-only query layer, a single narrow application API,
offline human execution, or no agent when the security and operating model
cannot justify general tool use.

## Migration and exit

Inventory tools and principals, default-deny every capability, introduce
read-only adapters first, test strongest abuse paths, and add reversible effects
behind approval. Remove generalized tools when a narrower deterministic
boundary can satisfy the user outcome.

## Evidence to inspect

Inspect identity and token audience, policy source, every mediation path,
allowlists, argument schemas, approval binding, sandbox configuration, network
and filesystem controls, secrets, retry and cancellation behavior, tool output
validation, audit hashes, alerts, and adversarial execution evidence.

## Evidence that changes the recommendation

A narrower fixed API, a bypass around policy, model-controlled credentials,
missing approval binding, unverifiable sandbox isolation, unbounded resources,
or unaudited side effects changes the recommendation.

## Quality trade-offs

Stronger mediation and isolation reduce autonomy and increase latency,
operational cost, and adapter work. Broader tools improve flexibility while
increasing blast radius, verification burden, and recovery complexity.

## Volatile facts

Threats, tool APIs, sandbox mechanisms, model behavior, and security guidance
change. This reference is a bounded design pattern, not proof of completeness,
security certification, regulatory compliance, or absence of exploitable paths.

## Claim map

- RUNTIME-AUTHORITY: The model proposes actions and never serves as the authorization authority.
- RUNTIME-MEDIATION: Every tool call requires deterministic complete mediation and least-privilege policy.
- RUNTIME-SANDBOX: Tool execution needs isolation and explicit resource boundaries proportional to its effects.
- RUNTIME-INJECTION: Untrusted instructions can steer tool-using agents, so context must not confer authority.
- RUNTIME-TOOLS: Tool risks require scenario-based evidence rather than capability descriptions alone.
