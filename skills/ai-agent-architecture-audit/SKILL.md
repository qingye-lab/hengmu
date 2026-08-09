---
name: ai-agent-architecture-audit
description: Specialized architecture audit for AI-agent systems and AI-enabled products. Use for agent runtimes, tool-using workflows, memory systems, RAG, MCP integrations, long-running tasks, model routing, evidence tracking, human approval, evaluations, or systems exposed to prompt injection and model uncertainty. Extends rather than replaces the general project architecture audit.
---

# Audit AI-agent architecture

Assess whether probabilistic model behavior is safely bounded by deterministic product and operational controls.

## Load the contract

Read these files completely:

- `../../resources/references/review-contract.md`
- `../../resources/references/knowledge-contract.md`
- `../../resources/references/ai-agent-rules.md`
- `../../resources/rules/ai-agent-core.yaml`
- `../../resources/knowledge/manifest.yaml`

Load the project's profile, constraints, and critical flows. Use `project-architecture-audit` as well when the request covers the whole product rather than only AI-specific boundaries.

## Workflow

1. Require a valid project Profile before the specialized audit. When
   `.architecture/profile.yaml` is missing and persistence is allowed, run
   `architecture_tool.py prepare-project-audit --repo <repo>`, then validate
   the project. When the user explicitly requests read-only work, do not create
   governance; report the missing Profile and stop with a
   `project-architecture-audit` bootstrap handoff.
2. Set one stable `<run-id>` for the audit and preserve every generated input
   under `.architecture/reviews/inputs/`; never reuse or overwrite a prior
   Review's evidence chain. Inspect current facts, build a current Profile from
   the declared Profile, then select Knowledge:

   ```bash
   python3 ../../resources/scripts/architecture_tool.py inspect-repository \
     --repo <repo> \
     --output <repo>/.architecture/reviews/inputs/<run-id>-repository-facts.yaml
   python3 ../../resources/scripts/architecture_tool.py build-profile \
     --facts <repo>/.architecture/reviews/inputs/<run-id>-repository-facts.yaml \
     --declared <repo>/.architecture/profile.yaml \
     --output <repo>/.architecture/reviews/inputs/<run-id>-profile.yaml
   python3 ../../resources/scripts/architecture_tool.py select-knowledge \
     --facts <repo>/.architecture/reviews/inputs/<run-id>-repository-facts.yaml \
     --profile <repo>/.architecture/reviews/inputs/<run-id>-profile.yaml \
     --task "<current AI-agent audit request>" \
     --skill ai-agent-architecture-audit \
     --output <repo>/.architecture/reviews/inputs/<run-id>-knowledge-selection.yaml \
     --context-output <repo>/.architecture/reviews/inputs/<run-id>-knowledge-context.yaml
   python3 ../../resources/scripts/architecture_tool.py validate-knowledge-context \
     <repo>/.architecture/reviews/inputs/<run-id>-knowledge-context.yaml \
     --selection <repo>/.architecture/reviews/inputs/<run-id>-knowledge-selection.yaml \
     --facts <repo>/.architecture/reviews/inputs/<run-id>-repository-facts.yaml \
     --profile <repo>/.architecture/reviews/inputs/<run-id>-profile.yaml
   ```

   Read the compact context projection only after validation succeeds; reserve
   the full exclusion ledger for scripts, Reviews, and Gates. Do not load every
   selected Markdown entry by default. Open a complete selected entry only
   after verifying its recorded hash and only when a candidate-driving claim,
   ambiguity, volatile fact, or explicit trade-off depends on its source
   detail. Do not load unrelated packs.
3. Draw the control path from user intent through orchestration, model calls, retrieval, tools, persisted state, human approval, and side effects.
4. Separate deterministic services and policy enforcement from model judgment.
5. Identify trust boundaries for user content, retrieved content, prompts, tools, credentials, memory, and model providers.
6. Inventory every context source and record its owner, purpose, necessity, authority, scope, freshness, sensitivity, transformation, retention, and disposal. Require an explicit reason for every included source and evidence for every omitted source that could affect a critical flow.
7. Trace context and memory lifecycles: creation, scoping, provenance, retention, mutation, retrieval, deletion, and recovery. Inspect budget, truncation, summarization, ranking, and fallback behavior; require preservation or explicit rejection of authority, provenance, and recency.
8. Compare context ordering across retries, tasks, tenants, and releases. Inspect cache keys, cache boundaries, invalidation, and reuse so ordering changes or volatile data cannot silently cross an authorization or provenance boundary.
9. Classify stable policy, contracts, and instructions separately from volatile user input, retrieval results, task state, and provider responses. Prevent volatile or sensitive material from entering stable context or reusable caches.
10. Minimize sensitive and personal data independently at prompt, retrieval, memory, and trace boundaries. Inspect field-level allowlists, redaction, purpose limitation, scope, retention, deletion, and reference-versus-raw-content choices.
11. Trace long-running task state, idempotency, retries, cancellation, checkpoints, resumption, and duplicate side effects.
12. Inspect model routing, timeouts, fallbacks, degraded modes, cost budgets, latency budgets, and failure visibility.
13. Bind behavior evidence to exact model/runtime, prompt, tool policy and schema, retriever/index/ranking configuration, context treatment, evaluation data, environment, timestamp, and hashes where available.
14. For any new or upgraded agent runtime, protocol, model, or framework, compare adopt, retain, and reject decisions against the current baseline using critical-flow evidence for quality, compatibility, security, operations, cost, ownership, rollout, and rollback.
15. Inspect evidence capture, source attribution, evaluation coverage, production feedback, and human confirmation boundaries.
16. Assess every applicable AI rule and record explicit `not_applicable` or `not_assessed` states.

For each finding or strength, bind the assessment to at least one concrete affected critical flow and prove the complete trigger-to-impact or control-to-outcome path. State findings as architecture invariants and affected capabilities; record technology names only as versioned evidence. A prompt containing untrusted text is not by itself a prompt-injection vulnerability; show how it can cross a policy or tool boundary.

## Verification handoff and output

Apply the candidate evidence requirements in `review-contract.md`. Leave every
finding at `verification.status: candidate`.
Do not turn this candidate handoff into a verified conclusion; preserve the
candidate/verification boundary and hand off confirmation to the independent
verifier.

Write persistent artifacts under `.architecture/reviews/` using kind `ai-agent`:

- `<timestamp>-ai-agent-candidates.yaml`;

Start machine-readable output from `../../resources/templates/review.yaml` and set `review.kind` to `ai-agent`.

Use Review schema 1.2. Bind the exact per-run repository-facts, Profile, and AI
knowledge-selection paths and hashes, preserve fact/inference boundaries,
enumerate critical-flow coverage, and validate with:

```bash
python3 ../../resources/scripts/architecture_tool.py validate-review \
  <review.yaml> --project <repo>
python3 ../../resources/scripts/architecture_tool.py validate-coverage \
  --project <repo> --review <review.yaml> --allow-candidates
```

Hand off architecture, candidate strengths and risks, critical-flow impact,
coverage, counter-evidence, and limitations. Use
`$architecture-finding-verifier` for confirmed conclusions and the final
report. Do not prescribe fixes.
