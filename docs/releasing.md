# Releasing

1. Update `CHANGELOG.md`, `.codex-plugin/plugin.json`, and root `plugin.json`.
   Shared identity fields must remain aligned; the portable description and
   keywords remain host-neutral.
2. Regenerate `requirements-runtime.lock` and `requirements-dev.lock` with
   `pip-compile --generate-hashes` when dependency ranges change.
3. Run the full gate from `AGENTS.md`, including `validate-knowledge`,
   `scripts/audit_licenses.py`, and `pip-audit`.
4. Confirm the selector-source and latest reviewed implementation commits are
   reachable from the release commit:

   ```bash
   python3 resources/scripts/architecture_tool.py validate-history-anchors .
   ```

   Merge source-anchored governance pull requests with **Merge Commit**.
   Squash or rebase merging discards the reviewed ancestry and will fail CI
   on `main` and the release workflow.
5. Validate all ten Markdown Knowledge Packs and confirm no entry is stale:

   ```bash
   python3 resources/scripts/validate_knowledge.py
   ```

6. Confirm the 45 routing cases and separate selection, decision,
   false-positive, and artifact-validity corpora parse and pass their
   deterministic tests.
7. Run the repository's architecture gate through `release`; preserve the
   trusted Review, accepted Decision, completed Plan, and passed provider
   evidence used by that result.
8. Build both supported package contracts:

   ```bash
   python3 scripts/package_plugin.py --format codex --output-dir dist
   python3 scripts/package_plugin.py --format agent-plugins --output-dir dist
   ```

   The Codex package remains `hengmu-<version>.zip`; the portable package is
   `hengmu-<version>-agent-plugins.zip`.

9. Confirm both archives contain only runtime files:

   ```bash
   unzip -l dist/hengmu-<version>.zip
   unzip -l dist/hengmu-<version>-agent-plugins.zip
   ```

10. Exercise both extracted runtimes through Knowledge Selection:

   ```bash
   python3 scripts/smoke_test_package.py \
     --format codex \
     --archive dist/hengmu-<version>.zip
   python3 scripts/smoke_test_package.py \
     --format agent-plugins \
     --archive dist/hengmu-<version>-agent-plugins.zip
   ```

11. Verify the checksums on any supported platform:

   ```bash
   python3 scripts/verify_checksum.py \
     dist/*.zip.sha256
   ```

12. Generate and inspect both SPDX SBOMs:

   ```bash
   python3 scripts/generate_sbom.py \
     --archive dist/*.zip \
     --output-dir dist
   ```

13. Confirm every dependency package in both SPDX documents has a declared
   license and exactly matches `resources/supply-chain/runtime-licenses.json`.
14. Complete one current Codex installation smoke test. For every named
   Agent Plugins host support claim, also test that current client. Record the
   client, version, operating system, installation path, routed Skill, and
   observed result as described in [host compatibility](host-compatibility.md).
15. If a behavioral quality claim is planned, preserve three trials per case
   from at least two identified models, with surface, exact plugin version, and
   scorer output.
16. Confirm migration evidence never preserves a legacy verified label as
    current 1.2 verification.
17. Before the first v1.1 tag, a repository administrator must enable GitHub
    immutable releases. Read the setting back with the authenticated GET
    endpoint and require `enabled: true`:

    ```bash
    gh api \
      -H "X-GitHub-Api-Version: 2026-03-10" \
      repos/qingye-lab/hengmu/immutable-releases
    ```

    The publication script's `publish` mode performs this same read-only
    preflight as its first remote administration check. A disabled setting
    (including `404`), malformed response, or authentication/API/CLI error
    blocks publication with no Release mutation. Neither mode enables the
    setting; retain the administrator action and GET readback as release
    evidence. See GitHub's
    [immutable-release repository endpoints](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository).
18. Create a signed or annotated `v<version>` tag.
19. Push the tag. The release workflow re-runs validation, tests, lint,
   formatting, dependency audit, deterministic packaging, checksum, and SBOM
   generation. It creates GitHub provenance and SBOM attestations, creates or
   resumes a draft containing exactly the two ZIPs, two checksums, and two SPDX
   SBOMs, and verifies their remote digests. This `prepare` phase does not check
   the repository administration setting and does not publish.
20. From an authenticated administrator environment, with the same six verified
    files in `dist`, run the separate publication phase:

    ```bash
    python3 scripts/publish_release.py publish \
      --repository qingye-lab/hengmu \
      --tag v<version> \
      --dist dist
    ```

    The command first reads back immutable-release enablement, then accepts only
    a complete exact draft. It never creates, uploads, or replaces an asset. It
    publishes once, requires the resulting Release to report `draft: false` and
    `immutable: true` with the exact inventory, and verifies the Release and all
    six assets with at most five attempts separated by ten seconds. Repeating
    `publish` against an immutable published Release is read-only.

After publication, verify both artifact digest and attestation:

```bash
gh attestation verify \
  dist/hengmu-<version>.zip \
  --repo qingye-lab/hengmu
gh attestation verify \
  dist/hengmu-<version>-agent-plugins.zip \
  --repo qingye-lab/hengmu
python3 scripts/verify_checksum.py \
  dist/*.zip.sha256
```

Do not publish from an uncommitted working tree or manually replace a release
asset without issuing a new version. If post-publication verification fails,
fix the source or workflow and issue a patch release; never mutate the published
Release. GitHub attestations establish build
provenance; they do not prove the source or workflow is vulnerability-free.
