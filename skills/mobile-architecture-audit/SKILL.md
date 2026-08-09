---
name: mobile-architecture-audit
description: Specialized architecture audit for mobile applications, especially iOS and SwiftUI. Use when assessing local persistence, offline behavior, synchronization and conflicts, migrations, background execution, notifications, networking and caches, state ownership, privacy permissions, battery efficiency, or excessive client-side business logic. Extends rather than replaces the general project architecture audit.
---

# Audit mobile architecture

Assess correctness across application lifecycle transitions, unreliable networks, local persistence, and operating-system constraints.

## Load the contract

Read these files completely:

- `../../resources/references/review-contract.md`
- `../../resources/references/knowledge-contract.md`
- `../../resources/references/mobile-rules.md`
- `../../resources/rules/mobile-core.yaml`
- `../../resources/knowledge/manifest.yaml`

Load the project profile, constraints, and critical flows. Pair with `project-architecture-audit` for backend, shared contracts, or full-product scope.

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
     --task "<current mobile audit request>" \
     --skill mobile-architecture-audit \
     --output <repo>/.architecture/reviews/inputs/<run-id>-knowledge-selection.yaml \
     --context-output <repo>/.architecture/reviews/inputs/<run-id>-knowledge-context.yaml
   python3 ../../resources/scripts/architecture_tool.py validate-knowledge-context \
     <repo>/.architecture/reviews/inputs/<run-id>-knowledge-context.yaml \
     --selection <repo>/.architecture/reviews/inputs/<run-id>-knowledge-selection.yaml \
     --facts <repo>/.architecture/reviews/inputs/<run-id>-repository-facts.yaml \
     --profile <repo>/.architecture/reviews/inputs/<run-id>-profile.yaml
   ```

   Read the compact context index and every Markdown path it selects only after
   validation succeeds; reserve the full exclusion ledger for scripts, Reviews,
   and Gates. Do not load unrelated packs.
3. Map state ownership across views, domain logic, repositories, local stores, caches, remote APIs, extensions, widgets, and background tasks.
4. Trace the critical flows through cold launch, foreground/background transitions, offline mode, retries, cancellation, and process termination.
5. Trace schema and data migrations, including downgrade assumptions, partial failure, backup/restore, and store corruption handling.
6. Trace synchronization identities, ordering, conflict policy, tombstones, idempotency, and eventual consistency.
7. Inspect notification scheduling, authorization changes, timezone and calendar behavior, deduplication, cancellation, and reconciliation.
8. Inspect network caching, stale data policy, connectivity assumptions, request cancellation, and error recovery.
9. Inspect SwiftUI or equivalent state lifetimes, actor/thread isolation, observation boundaries, and test seams.
10. Inspect privacy manifests, permission purpose, sensitive storage, logs, analytics, and data deletion.
11. Assess battery, background execution, and resource pressure against actual product requirements.

Do not flag local-first architecture, SQLite/Core Data/SwiftData, singletons, or client-side logic without proving a violated product invariant.

## Verification handoff and output

Apply the candidate evidence requirements in `review-contract.md`. Leave every
finding at `verification.status: candidate`.

Write persistent artifacts under `.architecture/reviews/` using kind `mobile`:

- `<timestamp>-mobile-candidates.yaml`;

Start machine-readable output from `../../resources/templates/review.yaml` and set `review.kind` to `mobile`.

Use Review schema 1.2. Bind the exact per-run repository-facts, Profile, and
mobile knowledge-selection paths and hashes, preserve fact/inference
boundaries, enumerate critical-flow coverage, and validate with:

```bash
python3 ../../resources/scripts/architecture_tool.py validate-review \
  <review.yaml> --project <repo>
python3 ../../resources/scripts/architecture_tool.py validate-coverage \
  --project <repo> --review <review.yaml> --allow-candidates
```

Hand off architecture, candidate strengths and risks, lifecycle and
critical-flow impact, coverage, counter-evidence, and limitations. Use
`$architecture-finding-verifier` for confirmed conclusions and the final
report. Do not prescribe fixes.
