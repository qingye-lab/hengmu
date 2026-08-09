# Gate release publication on the full quality matrix

- Status: accepted
- Date: 2026-08-09
- Owners: repository maintainers
- Scope: GitHub CI, release publication, package evidence, and branch policy
- Supersedes: none
- Superseded by: none

## Context

The v1.0.2 tag triggered an Ubuntu-only Release workflow in parallel with the
six-job CI quality matrix. Both Windows jobs failed the same ZIP safety
regression while the independent Release workflow completed and published
assets. The package digests and attestations were valid, but the publication
path did not enforce the accepted requirement that the supported matrix pass
before release.

## Evidence

- GitHub CI run 31261631968 failed on Windows with Python 3.11 and 3.13.
- GitHub Release run 31261631962 succeeded for the same
  `f3103a5fe4bac90b87518e626fdfd979470a8d94` commit.
- The Windows failure occurred because `ZipInfo.filename` had already
  normalized the raw backslash entry retained by `ZipInfo.orig_filename`.
- The existing v1.0.2 Decision requires the six-platform matrix and package
  checks before portable support is treated as released.

## Decision

Make the Release workflow reusable and call it only from the tag CI run after
the complete quality matrix succeeds. Keep release-stage validation and package
rebuilding inside the called workflow as defense in depth. Validate the
dependency structurally in repository tests and require the hosted CI check in
the main-branch repository ruleset.

Validate original archive entry names before any platform-specific
normalization. Smoke test both native and portable archives through the same
deterministic Knowledge Selection path.

## Alternatives considered

- Keep independent CI and Release workflows and rely on maintainers to inspect
  CI first — rejected because tag events race and the requirement is not
  machine-enforced.
- Duplicate the full six-platform matrix inside Release — rejected because it
  increases cost and creates two quality definitions.
- Remove Windows from the supported matrix — rejected because the public
  runtime contract supports Windows and the defect is repairable at the archive
  validation boundary.

## Consequences

A tag with any failed quality job remains unpublished. Release duration includes
the matrix plus the existing release-stage rebuild. The native and portable
archives receive equivalent extracted-runtime evidence without claiming real
IDE activation.

## Verification

- Run the ZIP regression and supply-chain workflow-contract tests.
- Run the full local repository gate and both package smoke tests.
- Observe all supported GitHub matrix jobs succeed before the publish job.
- Verify release assets, checksums, SBOMs, and attestations after publication.

## Revisit when

- GitHub changes reusable-workflow permission or tag-context semantics;
- the supported OS/Python matrix changes;
- release orchestration moves to another platform; or
- package formats require materially different runtime verification.
