# Skill evaluation

The evaluation strategy separates static repository contracts from behavioral
Codex forward tests.

## Static gate

`python3 scripts/validate_repository.py` verifies:

- plugin identity and Semantic Versioning;
- the stable `hengmu` entry plus the exact eight focused workflow Skill names;
- frontmatter, folder naming, descriptions, line budgets, and UI metadata;
- local Markdown links;
- JSON Schema validity and parseable YAML templates;
- one direct, indirect, incomplete, negative, and edge case per public Skill;
- parseable routing, knowledge-selection, decision-quality, false-positive,
  and artifact-validity corpora;
- absence of placeholders and symlinks from runtime directories; packaging
  excludes caches and development artifacts.

Static validation proves structure and coverage. It does not prove that a model
will select the right Skill or produce a high-quality review.

## Behavioral forward tests

Use `evals/cases.yaml` as the source corpus.

The 0.3 evaluation sets have separate responsibilities:

| Corpus | Proves |
| --- | --- |
| `routing.yaml` | Exact public Skill activation and negative boundaries |
| `knowledge-selection.yaml` | Relevant inclusion, important exclusion, reasons, budget, runtime anchoring, and compact-context projection |
| `decision-quality.yaml` | Quality-first comparison and prohibited shortcuts |
| `false-positive.yaml` | Leads are not promoted without an invariant and failure path |
| `artifact-validity.yaml` | Hash, fingerprint, coverage, and migration tampering fails |

Context execution checks also verify that the compact Knowledge sidecar is an
exact ordered projection bound to the full Selection lock, that progressive
disclosure starts with the stable operational kernel, and that source Markdown
is required for candidate-driving claims or ambiguity. The full Selection and
source hashes remain reproducibility inputs even when they are omitted from
model-visible context.

For each case:

1. start a clean Codex task;
2. provide only the Skill path and the case prompt;
3. do not reveal the expected activation or outcome;
4. use a disposable repository or read-only fixture;
5. capture the selected Skill, questions, artifacts, and validation results;
6. compare behavior with the case expectation;
7. classify failure as activation, instruction, tool, environment, or
   unsupported-scope failure.

Do not reuse artifacts between cases. A Skill that succeeds only after seeing
the expected answer has not passed a forward test.

Fixture directory names, titles, and content must remain outcome-neutral.
Repository tests reject phrases such as `Expected behavior`, `Expected
decision`, and `do not recommend` inside model-visible fixtures. Case IDs may
remain descriptive in the hidden ground-truth artifact because the runner
never places those IDs in the model command.

## Acceptance

A release candidate should:

- activate on every direct and indirect case;
- ask or stop correctly on incomplete cases;
- avoid activation on negative cases;
- preserve evidence, scope, and non-inference boundaries on edge cases;
- produce machine-readable artifacts that pass the bundled validator.

Record model and Codex surface when publishing behavioral results. Treat them
as time-bound evidence, not a permanent guarantee.

## Adversarial architecture benchmark

`benchmarks/ground-truth.yaml` is a separate behavior corpus. Its ten fixtures
cover false-positive resistance, modular-monolith proportionality, real data
ownership conflicts, queue versus durable workflow proportionality, mobile
client/server ownership, documentation/code contradictions, shared-database
coupling, and injected tool authority.

The ground truth records expected rule IDs and severity plus forbidden
over-design recommendations. Solution Advisor cases also record the expected
option, required trade-offs and knowledge, over-design options, rejected-option
depth, and migration-slice depth. A run must use the same case IDs and record
the model, Codex surface, Skill version, and run time. Score it with:

```bash
python3 resources/scripts/architecture_tool.py benchmark-score \
  --ground-truth benchmarks/ground-truth.yaml \
  --run benchmark-run.yaml \
  --output benchmark-score.json
```

Metrics are:

- Finding precision and recall by rule ID;
- severity agreement on true positives;
- evidence validity recomputed from fixture-contained file/line/excerpt
  references;
- hits on fixture-specific forbidden recommendations;
- finding and severity stability across repeated independent trials;
- recommendation accuracy and selected-option stability;
- over-design rate and required trade-off coverage;
- validity and relevance of cited knowledge IDs;
- rejected-option explanation coverage and migration actionability;
- mean duration and optional token/cost/tool-call usage.

The score reports `usage_trials` plus separate field-observation counts, and
uses JSON `null` for token, cost, or tool-call totals when the model surface
did not supply that field. Missing telemetry is never represented as zero
consumption.

An empty run has zero precision when expected positives exist. It is not a
successful baseline. `benchmarks/run-template.yaml` only proves schema and
scorer operation; it is not a model result.

The harness remains command-agnostic. A bundled adapter invokes Codex in a
read-only fixture with a strict observation schema:

```bash
python3 scripts/run_behavior_benchmark.py \
  --model MODEL --surface SURFACE --runtime-executable codex --repetitions 3 \
  --output benchmark-run.yaml -- \
  python3 scripts/codex_benchmark_adapter.py \
    --model MODEL --skill '{skill}' \
    --fixture '{fixture}' --prompt '{prompt}' \
    --condition '{condition}' \
    --context-manifest '{context_manifest}'
```

The command must emit JSON with `observed_findings` and
`observed_recommendations`; Solution Advisor cases also require
`observed_decision`. Every observed Finding supplies repository-relative
`path`, `line_start`, `line_end`, and exact `excerpt` evidence. The harness and
scorer independently resolve these references inside the fixture; a
caller-supplied validity assertion is not trusted. Use a clean task per case
and never include the ground-truth expectations in the model prompt. Each
repetition launches a new command process; the harness records every trial
rather than averaging model output before scoring. For schema 1.4+ runs it also
writes a sibling JSONL execution log and binds its hash to the result. Each
trial records the exact rendered argument vector plus hashes of the command,
stdout, stderr, normalized observation, and exact log record. Run-level
provenance binds the clean source commit, execution environment, dependency
lock, schemas, Ground Truth, Knowledge manifest, plugin manifest and Skill
version, fixture trees, runner/adapter bytes, reconstructible command template,
and command/model runtime executable and version fingerprints.

## Context ablation

Schema `1.5` adds a controlled three-treatment benchmark configured by
`benchmarks/ablation/context-manifest.yaml`:

| Condition | Model-visible architecture context |
| --- | --- |
| `base` | No Skill, Reference, or Knowledge content; only the shared tool description and fixture. |
| `full` | The public Skill, its manifest-declared References, and workflow-required Knowledge. |
| `compressed` | A compact workflow-specific instruction and the same workflow-required Knowledge as `full`. |

This is an end-to-end package ablation, not a claim that only prompt wording
changed. Full and Compressed share their Knowledge inputs per Skill; the
manifest must never choose those inputs from fixture outcomes or hidden Ground
Truth. Run each condition separately from the same clean commit and preserve
three YAML/JSONL/score artifact sets:

```bash
python3 scripts/run_behavior_benchmark.py \
  --model MODEL --surface SURFACE --runtime-executable codex --repetitions 3 \
  --condition full \
  --context-manifest benchmarks/ablation/context-manifest.yaml \
  --output benchmarks/results/full-run.yaml -- \
  python3 scripts/codex_benchmark_adapter.py \
    --model MODEL --skill '{skill}' --fixture '{fixture}' --prompt '{prompt}' \
    --condition '{condition}' --context-manifest '{context_manifest}'
```

Replace `full` in the condition and output path with `base` and `compressed`,
then score each run independently. Compare precision, recall, evidence
validity, recommendation accuracy, over-design, trade-off and Knowledge
coverage, duration, stability, and any telemetry the surface actually emits.
When recording a governance-run manifest, telemetry may include declared
context characters and hashes by stage, input/output tokens, cache
read/write/hit values, tool calls, source paths/bytes, and stage durations.
These fields are optional and informational; unavailable values stay absent or
`null`, never zero by assumption, and never become Gate evidence.

Every 1.5 run also contains `context_budget`, a reproducible **declared-input
proxy**. It records corpus-union Unicode code points for Skill metadata/body,
References, Knowledge, and tool descriptions, plus fixture-tree bytes and
source hashes. It is not real token usage, a cost estimate, a per-trial value,
or proof that the model consumed all listed context. It is distinct from
runtime telemetry. Do not state a context or cost improvement until an actual
A/B/C run has been preserved and scored.

The bundled Codex adapter constrains Finding IDs to the bundled machine Rule
Packs and solution trade-offs to a documented atomic vocabulary. It performs
at most one evidence-only correction call when the first response contains an
escaped path, non-contiguous excerpt, or non-verbatim line citation. The
correction receives the prior response and deterministic validation errors,
never ground truth or expected findings. A second invalid response fails the
trial instead of being repaired or scored as valid.

For release evidence, run at least two identified models with three fresh
trials per case. Preserve each run YAML, sibling `*.log.jsonl`, and score JSON.
The scorer resolves every provenance hash against the recorded source commit
and rejects dirty relevant inputs. It also re-resolves the recorded runtime
executables and version commands on the scoring host. A failed or interrupted
run leaves a hash-only execution-log record and must not be rewritten as a
passing model result.

Strict scoring is the default and fails when the current host does not match
the recorded command/model runtimes. Cross-platform reviewers may instead use
`--runtime-verification archived --artifact-commit COMMIT`. Archived mode
requires the exact run YAML and JSONL log bytes to exist in that Git commit,
keeps all source/log/command checks, and reports current-host runtime mismatch
without pretending the original runtime was replayed.

## Release evidence

A release may state behavioral results only when the run artifact is preserved
with an identified model/surface and the exact plugin version. Until then, the
repository claims corpus and harness coverage, not model quality.

A deterministic release report may cover repository contracts, selector cases,
and artifact tamper tests without claiming model behavior. A model-quality
report still requires an actual external run.

Version 1.0.3 satisfies that evidence condition with two models, three trials
per case, preserved run/score artifacts, and an explicit limitations section
in `benchmarks/reports/1.0.3-model-behavior.md`. The historical 0.4.0 report
remains available at `benchmarks/reports/0.4.0-model-behavior.md`.
