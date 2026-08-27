# Hengmu quality-baseline architecture verification

## Outcome

Independent V2 verification accepts the candidate Review with zero findings.
The Ruff declaration and hash lock now agree on 0.16.4, the owning invariant
has a regression test, and one bounded Ubuntu Python 3.12 lane extends the CI
quality matrix from six to seven jobs. Release authority, runtime dependencies,
public contracts, and package contents are unchanged.

## Subject and bound inputs

- Subject: Hengmu (`hengmu`), project scope `.`.
- Reviewed commit: `bd576e45c253691a6bb362c99cc5e6315a04cdc5`.
- Verification: independent agent, level V2, run
  `hengmu-quality-baseline-verifier-20260827-1050`.
- Candidate:
  `.architecture/reviews/2026-08-27-quality-baseline-project-candidates.yaml`,
  SHA-256
  `60f7a7ec3245ee7ca1da9d1342a0a830a6b40a14144821f89d55799b0988af4c`.
- Repository facts:
  `.architecture/reviews/inputs/2026-08-27-quality-baseline-repository-facts.yaml`,
  SHA-256
  `8b0c5294c3015b1e65ce79569edc8fc80760a430a933cf978d4741564426a9cd`.
- Profile:
  `.architecture/reviews/inputs/2026-08-27-quality-baseline-profile.yaml`,
  SHA-256
  `55eae34e43bf43cbe28ffeeaffb29d207d84dce32c6dd99e2bb9c165d9569456`.
- Knowledge Selection:
  `.architecture/reviews/inputs/2026-08-27-quality-baseline-knowledge-selection.yaml`,
  SHA-256
  `7434b173624b587fe79a3e8f44ff31d522108ed9985cf6e9a7478b8c8e89771f`.
- Deterministic test evidence:
  `.architecture/evidence/test-results-20260827t104124691195z-bd576e45c253.yaml`,
  SHA-256
  `c2b7a702d3feb1cc11b3149dddb930a298d8430b050b0697675f3f4ef0e77d02`.
- Rule Packs: `project-core`, `plugin-platform`, and
  `test-automation-platform`, all version 1.0.0 and hash-matched.
- Knowledge: nine selected entries; every ID, version, and source hash matches
  the Selection and compact context.

## Root cause and scoped repair

The development dependency declaration required Ruff 0.16.3 or newer while
the exact lock still resolved Ruff 0.16.0. CI installed the lock, so the
declared lower bound was not an effective control. The supply-chain tests did
not bind the declared Ruff range to the locked version, allowing the drift to
survive. Separately, the public Python compatibility range includes 3.12, but
CI sampled only 3.11 and 3.13.

The repair changes only four files:

- `requirements-dev.txt` and `requirements-dev.lock` now agree on Ruff 0.16.4.
- `tests/test_supply_chain.py` rejects a Ruff lock below the declared range and
  binds the intended CI matrix shape.
- `.github/workflows/ci.yml` adds Ubuntu/Python 3.12 without multiplying the
  complete three-OS matrix.

The independent verifier confirmed that workflow permissions, the reusable
release workflow, `requirements-runtime.lock`, public Skills, schemas, CLI,
plugin manifests, and package allowlists did not change.

## Skeptical verification

The verifier attempted to disprove the zero-finding conclusion by inspecting
the complete diff, all candidate bindings, the Evidence Provider run, and the
adjacent release and package boundaries. The adverse hypotheses did not hold:

- The effective CI matrix contains exactly seven jobs: the existing three
  operating systems at Python 3.11 and 3.13, plus Ubuntu/Python 3.12.
- The tag release still depends on the complete `quality` job and retains its
  isolated permissions.
- The Ruff lower bound and exact lock agree, and the new test fails if they
  drift again.
- All 19 unique source anchors resolve to the reviewed commit.
- The Evidence Provider records the same clean commit before and after
  execution; all 337 dependency-closure entries match.
- Its JUnit output is structurally valid and records 162 passing tests plus 11
  passing subtests, with no failures, errors, skips, or stderr output.
- Independent focused execution of the two new assertions passes.
- The isolated local environment reports Python 3.12.13, Ruff 0.16.4, and a
  clean `pip check`.

## Coverage

Coverage is complete: 31 rules appear exactly once, with 28 assessed and three
explicitly not applicable. All six declared critical flows are assessed, and
all nine selected Knowledge entries are hash-bound. There are no confirmed,
rejected, or needs-evidence findings.

The six assessed flows are:

1. Plugin discovery and Skill execution.
2. Finding verification and policy enforcement.
3. Architecture Knowledge and behavior evaluation.
4. Greenfield architecture decision.
5. Safe project initialization.
6. Deterministic release packaging.

## Residual boundaries

- This is V2 independent-agent verification. No human verifier participated,
  and no V3, V4, or V5 claim is made.
- The branch has not been pushed, so the complete hosted GitHub Actions matrix
  remains external and unverified.
- The Evidence Provider run does not bind the Python interpreter version or
  executable hash. The Python 3.12.13 observation is local corroboration, not a
  reconstructable property of the run artifact.
- This approval covers only the Ruff lock and bounded Python 3.12 CI repair.
  It does not claim completion of later iteration-plan work such as mypy,
  coverage, CODEOWNERS, or broader dependency-audit policy.

Validation results:

- `validate-review`: passed.
- `validate-coverage`: passed — 31 rules, 6 critical flows, 9 Knowledge entries.
- `verify-evidence`: passed with `[]`.
- `validate-evidence-run --require-passed`: passed.
- Signature verification was not required for a zero-finding V2 Review.

Counts: raw 0; confirmed 0; rejected 0; needs-evidence 0.
