# Compatibility

## Supported runtime boundary

The portable CLI supports CPython 3.11–3.13 with the exact packages and hashes
in `requirements-runtime.lock`. CI runs repository validation, tests, lint,
formatting, packaging, and checksum verification on:

| Operating system | Python 3.11 | Python 3.13 |
| --- | --- | --- |
| Ubuntu | CI | CI |
| macOS | CI | CI |
| Windows | CI | CI |

`requirements.txt` contains supported dependency ranges. The lock is the
reproducible installation boundary used by CI and release packaging.

## Plugin surfaces

The repository validates:

- both plugin manifests and runtime layout with schema-aligned repository
  validation;
- every Skill's metadata, references, and activation boundaries;
- routing metadata and five activation/boundary cases per Skill;
- deterministic archive contents independent of local marketplace state; and
- the extracted portable audit path through Knowledge Selection.

Packaging has two explicit targets:

- `codex`: the native Codex package with `.codex-plugin/plugin.json` and
  Codex-specific `agents/openai.yaml` metadata;
- `agent-plugins`: the portable package with a root `plugin.json` and the
  standard `skills/` layout. It contains the same runtime CLI and Skill
  instructions. It retains the complete hidden Codex manifest, including its
  Codex-only `interface` fields, solely as inert provenance data required by
  the shared selector; Codex-specific `skills/*/agents/openai.yaml` files are
  excluded.

The portable package targets the [Agent Plugins v1 specification](https://agent-plugins.org/specification):
the root manifest and `skills/` location are portable, while installation,
permissions, invocation syntax, UI, and marketplace behavior remain client
specific. The runtime still requires CPython 3.11–3.13 and the locked
dependencies described above. See [host compatibility](host-compatibility.md)
for the precise capability and lifecycle boundary.

## Host interoperability boundary

| Host or surface | Package to use | Evidence status | Boundary |
| --- | --- | --- | --- |
| Codex | `codex` | Native manifest, Codex UI metadata, Skill/repository validation, deterministic packaging, extracted Knowledge Selection, and CI/release workflow are covered by this repository | A current installed Codex UI/CLI smoke test is release-time evidence and is not represented as a permanent universal guarantee |
| Cursor | `agent-plugins` | Cursor documents that conformant Agent Plugins load without changes; Hengmu's portable manifest/layout and archive tests are covered | No Hengmu-specific installed Cursor smoke test is currently recorded; Cursor-specific rules, agents, commands, hooks, variables, and marketplace behavior are not included |
| VS Code / GitHub Copilot CLI | `agent-plugins` | Both clients document Agent Plugins 1.0 discovery, direct Git or local-path installation, and Skill invocation; Hengmu's portable manifest/layout and archive tests are covered | No Hengmu-specific installed VS Code or Copilot CLI smoke test is currently recorded; client-specific agents, hooks, commands, and marketplace policy are not included |
| Kiro | Agent Skills projection from `agent-plugins` | Kiro documents workspace Skills under `.kiro/skills/`; Hengmu's package contains conformant Skills | Kiro does not consume Hengmu's root manifest on this path. Install all nine Skills together with shared `resources/`; no Hengmu-specific installed Kiro smoke test is currently recorded |
| Other Agent Plugins clients | `agent-plugins` | Portable package is generated and tested against the standard layout | Use only when the host documents Agent Plugins or Agent Skills support; this repository does not claim host-specific support without a reproducible report |

These installation paths do not claim identical behavior across Codex, Cursor,
VS Code, GitHub Copilot, Kiro, or another IDE. A host may accept the portable
package or Skills while exposing different controls, permissions, Skill
activation, or marketplace workflows. The portable archive currently contains
Skills only; it has no `mcp.json` and therefore makes no MCP-server support claim.
Follow the versioned, host-specific instructions in the repository README and
do not import only the `hengmu` router into a Skills-only client.

When adding a host-specific claim, record the client version, operating system,
package format, installation path, Skill/prompt, observed result, and date in
release evidence or a sanitized compatibility report. Treat that evidence as
time-bound: host updates can change behavior without changing Hengmu's package.

Automated CI cannot launch every Codex desktop, CLI, or ChatGPT plugin surface.
Before a release, maintainers should install the built ZIP or local marketplace
entry in at least one current Codex surface and, when claiming external-host
support, in that host as well. Record the application version, surface,
operating system, package format, and observed routing result. This manual
evidence is time-bound and must not be represented as universal compatibility.

## Artifact compatibility and the 1.0 release

- Schema `1.0` is readable history in 0.3.
- Trusted schema `1.1` remains enforceable for 0.2 compatibility.
- Brief schema `1.0` remains the readable legacy open Greenfield contract. Brief
  schema `1.1` makes the current mode explicit: `open` has no architecture
  constraints, while `constrained` records required, preferred, and prohibited
  inputs. It is new in the single 1.0 release.
- Architecture Decision artifacts through `1.3` remain parseable and
  migratable (including
  remediation `1.1`/`1.2` and legacy open Greenfield `1.3`). Decision schema
  `1.4` is the current Brief 1.1 target contract for both open and constrained
  modes; it binds the Brief, target architecture, constraint assessments,
  source evidence, and Knowledge.
- Remediation Plan artifacts through `1.2` remain readable. Plan schema `1.3`
  adds the accepted Greenfield target path: it binds Brief/Decision directly,
  keeps Finding lists empty, and maps work to target units, flows, and
  constraints.
- Knowledge selection schema `1.1` adds context priority and preserves `1.0`
  selection readability.
- Aggregate Portfolio Reviews continue to use the trusted `1.1` portfolio
  contract in 0.3; per-project facts and selections are hash-bound evidence.
- The 128 YAML knowledge entries remain read-only compatibility data. New
  Decisions use the 205 Markdown entries registered by the ten-pack manifest.
- Repository-local Rule Packs are supported under `.architecture/rules/` and
  must use Rule Pack schema `1.1`; organization packs cannot shadow bundled IDs.
- Evidence Provider commands are project configuration, not portable defaults.
  A provider is ready only when its executable and project markers exist on
  the current operating system.
- JSON, SARIF 2.1.0, and JUnit XML provider outputs receive structural
  validation. Text output is captured and hashed but remains lower-assurance
  evidence.
- CLI success, policy failure, and invalid-input exit codes remain `0`, `1`,
  and `2`.
- Public Skill names are compatibility contracts.
- The public surface contains the stable `hengmu` routing entry and eight
  focused workflow Skills. Existing focused names remain directly invocable.
  The Knowledge Curator lives under `maintainer/skills/` and is not an
  end-user plugin workflow.
- The new Brief `1.1`, Decision `1.4`, and Plan `1.3` target-design contracts
  ship together in Hengmu `1.0.0`; legacy artifact paths remain parseable and
  migratable. No new target-design contract is released partially.
- Breaking schema, CLI, or Skill-name changes require a major release after
  `1.0.0`; during `0.x`, they require explicit migration guidance and a minor
  release at minimum. The stable router and all eight focused Skill names do not
  change in 1.0.

### Coexistence and migration

Old readers may continue to consume Brief 1.0 and artifacts through Decision
1.3/Plan 1.2. A 1.0 reader must preserve those paths and must not silently
reinterpret a legacy open Brief as a constrained Brief. To use constraints,
create a new Brief 1.1, challenge and assess every constraint, create a Decision
1.4, and obtain acceptance; do not mutate the old Brief or promote its prose.

Parseability and migration support do not preserve an artifact's former trust
status. A historical chain may be read without satisfying current Knowledge,
source-identity, freshness, or independent-verification policy. Before an old
artifact becomes accepted input to a current Gate, rerun the applicable
independent verification and bind current provenance; never promote a legacy
verification status by migration alone.

For accepted Greenfield work, create a new Plan 1.3 bound to the exact Brief and
Decision. For remediation, retain the existing Review/Finding bindings and Plan
1.2 path. Rollback is artifact-level: keep the old accepted chain, reject or
supersede the new proposal, and remove no legacy artifact. See
[migrating to 1.0](migrating-to-1.0.md) for the operational sequence.

See [the 0.4 migration guide](migrating-to-0.4.md) and
[the 0.3 migration guide](migrating-to-0.3.md). The
[0.2 migration guide](migrating-to-0.2.md) remains available for older
artifacts.
