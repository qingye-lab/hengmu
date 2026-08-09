# Decision artifact workflow

## 1. Choose the source and design mode

Use exactly one source context:

| Mode | Required source | Finding rule | Output |
| --- | --- | --- | --- |
| Remediation | Verified Review | Only confirmed unresolved Finding IDs | Decision readable through 1.2 |
| Legacy open Greenfield | Approved Design Brief 1.0 | Empty Finding list | Decision 1.3 |
| Current open target | Approved Design Brief 1.1 with `design_mode: open` | Empty Finding and constraint lists | Decision 1.4 |
| Current constrained target | Approved Design Brief 1.1 with `design_mode: constrained` | Empty Finding list; bind every Brief constraint | Decision 1.4 |

Do not create a synthetic Review for Greenfield work. Validate the Brief before
reasoning:

```bash
python3 ../../resources/scripts/architecture_tool.py validate-design-brief \
  <repo>/.architecture/architecture-design-brief.yaml --project <repo>
```

The distributed template is a draft. An approved Brief must bind authorized
`approved_by` identities, repository-relative SHA-256 approval evidence, and
one detached SSH signature per approver. Signature identities must verify
against the project's `artifact_signatures` policy.

For a constrained Brief, confirm that every required, preferred, and prohibited
constraint has an ID, kind, disposition, target, scope, accountable authority,
rationale, and review trigger. Architecture-style, pattern, and technology
constraints also bind a Knowledge ID. The Brief's constraints are decision
inputs, not independent proof.

## 2. Select and bind Knowledge

Set one stable `<run-id>` for the decision. Create a bounded selection under
the configured review input directory and validate its compact context before
reading selected Markdown entries. Never reuse or overwrite selection bytes
bound to an earlier Decision:

```bash
python3 ../../resources/scripts/architecture_tool.py select-knowledge \
  --facts <repository-facts.yaml> \
  --profile <profile.yaml> \
  --task "<decision problem>" \
  --skill architecture-solution-advisor \
  --output <repo>/.architecture/reviews/inputs/<run-id>-decision-knowledge-selection.yaml \
  --context-output <repo>/.architecture/reviews/inputs/<run-id>-decision-knowledge-context.yaml

python3 ../../resources/scripts/architecture_tool.py validate-knowledge-context \
  <repo>/.architecture/reviews/inputs/<run-id>-decision-knowledge-context.yaml \
  --selection <repo>/.architecture/reviews/inputs/<run-id>-decision-knowledge-selection.yaml \
  --facts <repository-facts.yaml> \
  --profile <profile.yaml>
```

Use `--kind-budget KIND=LIMIT` only when the decision needs a tighter cap. Use
`--maintainer` only for auditable curation. Bind every cited style, pattern,
technology, reference architecture, and migration entry by ID, version, and
SHA-256. Knowledge describes capabilities and mechanisms; it does not prove
project fit or constraint feasibility.

For remediation, bind the verified Review and selection:

```bash
python3 ../../resources/scripts/architecture_tool.py decision-bindings \
  --project <repo> --review <verified-review.yaml> \
  --knowledge-selection <decision-knowledge-selection.yaml>
```

For either Greenfield mode, bind the Design Brief instead:

```bash
python3 ../../resources/scripts/architecture_tool.py decision-bindings \
  --project <repo> --design-brief <architecture-design-brief.yaml> \
  --knowledge-selection <decision-knowledge-selection.yaml>
```

## 3. Challenge constraints before option scoring

Record each constraint assessment explicitly:

- challenge required constraints for conflicting requirements, authority,
  feasibility, and hidden migration or operating consequences;
- generate only variants that satisfy every surviving required constraint;
- allow preferred constraints to lose when evidence shows a better quality,
  compatibility, cost, or safety outcome;
- hard-eliminate prohibited options and preserve the reason outside the scorecard.

Do not let a constraint input become a fact, finding, or proof merely because it
uses “must”, appears in a Profile, or is repeated by Knowledge. Mark unresolved
conflicts as unknowns and stop if no compliant variant survives.

## 4. Compare and write the target

Restate measurable quality scenarios and compare keep-current/local correction,
the smallest compatible structural improvement, and a materially viable
alternative when supported. The target architecture section of Decision 1.4
must bind:

- runtime units and responsibility;
- deployment units, environment, rollout, and accountable owner;
- authoritative data owners and lifecycle;
- interfaces, consumers, compatibility, and evolution;
- trust boundaries, identities, permissions, and untrusted inputs;
- critical flows, failure, recovery, and measures;
- operations, observability, capacity, backup/recovery, and on-call;
- assessment of every required, preferred, and prohibited constraint; and
- the selected Knowledge snapshot.

Do not claim an architecture is compliant without a surviving constraint
assessment for every declared constraint. A satisfied assessment must bind the
same Knowledge ID as the Brief and concrete `target_refs`; technology
constraints must point to runtime units that actually use the technology.
Do not use technology-evolution as a
shortcut for ordinary design or constraint handling.

## 5. Evolution assessment gate

Use this gate only for an explicit emerging-technology, upgrade, or replacement
question. It remains a lens inside Remediation or Greenfield, never a third
source mode. Before selection, bind a companion Markdown assessment containing:

| Check | Minimum record | Stop condition |
| --- | --- | --- |
| Baseline | owner, current measure, local correction, do-nothing consequence | no observed baseline |
| Gap | scenario, current value, target, method, threshold, evidence | novelty or hypothetical scale only |
| Volatile claims | official publisher/URL, scope, access date, freshness, bound capture | current official evidence unavailable |
| Compatibility | consumers, public/persisted contracts, data, mixed versions, duration, cost | affected contract unknown |
| Operations | owner, team skills, support, observability, failure, security, operational fit, cost | material fit untested |
| Exit | portability, lock-in, rollback point, data recovery, irreversible gate | rollback/exit cannot be tested |
| Shadow/pilot | bounded cohort, success/stop criteria, measures, owner | applicable shadow or pilot not run |
| Revisit | metric/event, threshold, owner, date/cadence, reopening evidence | trigger vague or unowned |

Keep-current or evidence-only is the valid disposition when the packet is
incomplete. Treat the measurable revisit triggers as part of the decision, not
as optional prose. Never pin versions from memory. A current official capability
claim still does not prove project fit.

## 6. Validate authority and compatibility

Write YAML and Markdown under `.architecture/reviews/`, or the portfolio review
directory. Use the matching schema and keep `decision.status: proposed` until
the authorized decision maker accepts it:

```bash
python3 ../../resources/scripts/architecture_tool.py validate-decision \
  <decision.yaml> --project <repo> \
  --design-brief <architecture-design-brief.yaml>
```

Use `--review <verified-review.yaml>` for remediation. Validation must check the
exact Brief or Review bytes, Knowledge selection, target architecture sections,
constraint assessments, source findings where applicable, and all evidence
hashes. Acceptance is a separate authority transition; the router and advisor
must not perform it.
