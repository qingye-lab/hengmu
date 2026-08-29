---
id: decision.agent-evaluation-design
kind: decision-guide
version: 1.0.0
status: active
maturity: standard
domains:
- ai-agent
- decision-process
triggers:
- agent-evaluation
quality_attributes:
- testability
- reliability
related:
- domain.ai-agent
last_reviewed: '2026-08-29'
review_after_days: 90
source_policy: stable-principles-plus-official-docs
sources:
- title: OpenAI agent evals guide
  url: https://platform.openai.com/docs/guides/agent-evals
  authority: official
  supports:
  - EVAL-DESIGN
  - EVAL-TRACE
- title: tau-bench a benchmark for tool-agent-user interaction in real-world domains
  url: https://arxiv.org/abs/2406.12045
  authority: research
  supports:
  - EVAL-OUTCOME
  - EVAL-REPEAT
- title: AgentDojo a dynamic environment to evaluate prompt injection attacks and defenses for LLM agents
  url: https://arxiv.org/abs/2406.13352
  authority: research
  supports:
  - EVAL-ADVERSARIAL
- title: AgentBench evaluating LLMs as agents
  url: https://arxiv.org/abs/2308.03688
  authority: research
  supports:
  - EVAL-BOUNDARY
---

# Agent Evaluation Design

## Problem and intent

Choose evaluation evidence that distinguishes path and trace defects from
repeatable task outcomes without turning one benchmark result into a universal
model-quality claim.

## Mechanism

Use trace or path inspection to diagnose where an agent diverged, and repeated
outcome trials to estimate whether the complete system solves a versioned case.
Bind every result to model, surface, harness, prompt, fixture, policy,
repetitions, scorer, runtime, and immutable hashes.

## Fit when

An agent, tool workflow, or model-mediated decision needs release evidence,
regression diagnosis, security testing, or comparison across controlled
conditions.

## Avoid when

The behavior is deterministic and can be proven with ordinary tests, the
ground truth is not reviewable, or the proposed sample cannot support the claim
being made.

## Required capabilities

Versioned cases, reviewed ground truth, isolated conditions, complete identity
binding, repeated trials, explicit failure classes, null-safe usage accounting,
scorer validation, execution-log hashes, and reproducibility are required.

## Benefits

Separating diagnostic traces from repeated outcomes makes failures actionable
while keeping aggregate quality claims bounded to the evaluated system and
conditions.

## Costs and liabilities

Representative fixtures, human review, repetitions, model variance, tool
availability, scorer error, token cost, latency, and benchmark contamination
make evaluations expensive and time-bound.

## Failure modes

Failures include single-trial conclusions, unbound model or harness identity,
changing ground truth after results, counting missing usage as zero, selecting
only successful traces, or transferring scores to another surface or runtime.

## Alternatives

Use deterministic unit or integration tests, schema validation, property-based
tests, security review, production shadow observation, or human acceptance when
agent-behavior trials do not answer the actual decision.

## Migration and exit

Start with a small canary that exercises the harness and failure taxonomy,
freeze inputs and hashes, then expand repetitions only after infrastructure
errors are zero. Retain failed logs and supersede evidence with a new bound run
rather than rewriting history.

## Evidence to inspect

Inspect case provenance, ground truth, model and surface identifiers, harness
implementation and configuration hashes, prompt, fixture, tool policy,
repetitions, scorer, runtime, raw trials, failure classes, usage nulls,
confidence intervals, and recomputable logs.

## Evidence that changes the recommendation

Deterministic tests covering the decision, unverifiable ground truth, hidden
prompt or harness changes, infrastructure failures, inadequate repetitions, or
a scorer that disagrees with reviewed examples changes the recommendation.

## Quality trade-offs

Trace inspection provides diagnosis but can overfit visible paths; repeated
outcome evaluation supports bounded rates but costs more and may conceal causal
differences. Use both only when each changes a release or repair decision.

## Volatile facts

Model behavior, hosted surfaces, prompts, tools, harnesses, and benchmark
datasets change. A result applies only to its bound identities and execution
evidence and does not establish transferable model quality or production
fitness.

## Claim map

- EVAL-DESIGN: Evaluation design must match the decision and bind the full system identity.
- EVAL-TRACE: Trace and path evidence diagnoses where behavior diverged but does not by itself estimate success probability.
- EVAL-OUTCOME: Repeated end-to-end trials support bounded outcome estimates for the evaluated cases.
- EVAL-REPEAT: Repetitions and uncertainty reporting are required when behavior is stochastic.
- EVAL-ADVERSARIAL: Security evaluation needs adversarial tasks and explicit attack-success outcomes.
- EVAL-BOUNDARY: Benchmark results remain scoped to the evaluated environments, tasks, models, and agent implementations.
