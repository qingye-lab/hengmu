from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import get_type_hints

import yaml
from jsonschema import Draft202012Validator

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "validate_repository.py"
SPEC = importlib.util.spec_from_file_location("validate_repository", SCRIPT_PATH)
assert SPEC and SPEC.loader
validate_repository = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_repository)


class RepositoryContractTests(unittest.TestCase):
    def test_repository_contract(self) -> None:
        errors = validate_repository.validate_repository(ROOT)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_dogfood_python_contract_covers_supported_endpoints(self) -> None:
        profile = (ROOT / ".architecture" / "profile.yaml").read_text(encoding="utf-8")
        constraints = (ROOT / ".architecture" / "constraints.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Python 3.11 through 3.14", profile)
        self.assertIn("Python 3.11 through 3.14", constraints)
        self.assertNotIn("Python 3.11 through 3.13", profile)
        self.assertNotIn("Python 3.11 through 3.13", constraints)

    def test_coverage_collects_both_trees_without_a_combined_floor(self) -> None:
        configuration = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        run = configuration["tool"]["coverage"]["run"]
        report = configuration["tool"]["coverage"]["report"]
        self.assertEqual(run["source"], ["."])
        self.assertTrue(run["relative_files"])
        self.assertEqual(
            report["include"],
            ["resources/scripts/*", "scripts/*"],
        )
        self.assertNotIn("fail_under", report)

    def test_github_action_scalar_forms_are_parsed_and_pinned(self) -> None:
        commit = "a" * 40
        accepted = (
            f"uses: actions/checkout@{commit}",
            f"uses: 'actions/checkout@{commit}'",
            f'uses: "github/codeql-action/init@{commit}" # pinned',
            "uses: ./local/action # repository action",
        )
        for scalar in accepted:
            with (
                self.subTest(scalar=scalar),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                workflow_root = root / ".github" / "workflows"
                workflow_root.mkdir(parents=True)
                (workflow_root / "ci.yml").write_text(
                    f"steps:\n  - {scalar}\n",
                    encoding="utf-8",
                )
                errors: list[str] = []
                validate_repository.validate_github_action_pins(root, errors)
                self.assertEqual(errors, [])

    def test_unsafe_or_unparseable_github_action_scalar_is_rejected(self) -> None:
        commit = "a" * 40
        rejected = (
            ("uses: actions/checkout@v6", "40-character commit SHA"),
            (
                f"uses: third-party/example@{commit}",
                "must use a GitHub-owned action",
            ),
            ("uses: docker://alpine:3.22", "must not use a Docker action"),
            (
                "uses: 'docker://alpine@sha256:" + "b" * 64 + "'",
                "must not use a Docker action",
            ),
            ("uses: ../local/action", "unparseable remote action"),
            (f"uses: actions@{commit}", "unparseable remote action"),
            (f'uses: "actions/checkout@{commit}', "unparseable uses value"),
            (f"uses: [actions/checkout@{commit}]", "unparseable uses value"),
            ("uses:", "unparseable uses value"),
        )
        for scalar, message in rejected:
            with (
                self.subTest(scalar=scalar),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                workflow_root = root / ".github" / "workflows"
                workflow_root.mkdir(parents=True)
                (workflow_root / "ci.yml").write_text(
                    f"steps:\n  - {scalar}\n",
                    encoding="utf-8",
                )
                errors: list[str] = []
                validate_repository.validate_github_action_pins(root, errors)
                self.assertEqual(len(errors), 1)
                self.assertIn(message, errors[0])

    def test_review_evidence_source_type_matches_schema(self) -> None:
        artifact_types_path = ROOT / "resources" / "scripts" / "artifact_types.py"
        spec = importlib.util.spec_from_file_location(
            "artifact_types_contract",
            artifact_types_path,
        )
        assert spec and spec.loader
        artifact_types = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = artifact_types
        spec.loader.exec_module(artifact_types)
        hints = get_type_hints(artifact_types.ReviewArtifact)
        self.assertEqual(hints["evidence_sources"], list[str])

        schema = json.loads(
            (ROOT / "resources" / "schemas" / "review.schema.json").read_text(
                encoding="utf-8"
            )
        )
        evidence_sources = schema["properties"]["evidence_sources"]
        self.assertEqual(evidence_sources["type"], "array")
        self.assertEqual(evidence_sources["items"]["type"], "string")

    def test_knowledge_selection_template_matches_plugin_and_selector(self) -> None:
        template = yaml.safe_load(
            (ROOT / "resources" / "templates" / "knowledge-selection.yaml").read_text(
                encoding="utf-8"
            )
        )
        native = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        portable = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        selector = json.loads(
            (ROOT / "resources" / "selector-source.json").read_text(encoding="utf-8")
        )
        versions = {
            native["version"],
            portable["version"],
            selector["plugin_version"],
            template["selector"]["source"]["plugin_version"],
        }
        self.assertEqual(versions, {"1.1.1"})

        schema = json.loads(
            (
                ROOT / "resources" / "schemas" / "knowledge-selection.schema.json"
            ).read_text(encoding="utf-8")
        )
        errors = sorted(
            Draft202012Validator(schema).iter_errors(template),
            key=lambda error: list(error.path),
        )
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def test_duplicate_json_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text('{"schema_version": "1.0", "schema_version": "1.1"}')
            errors: list[str] = []
            self.assertIsNone(validate_repository.load_json(path, errors))
            self.assertEqual(len(errors), 1)
            self.assertIn("duplicate JSON key 'schema_version'", errors[0])

    def test_plugin_identity_is_independent_of_checkout_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "custom-checkout-name"
            shutil.copytree(ROOT / ".codex-plugin", root / ".codex-plugin")
            errors: list[str] = []
            manifest = validate_repository.validate_manifest(root, errors)
            self.assertIsNotNone(manifest)
            self.assertEqual(errors, [])

    def test_hengmu_entry_routes_every_focused_skill(self) -> None:
        entry = (ROOT / "skills" / "hengmu" / "SKILL.md").read_text(encoding="utf-8")
        focused_skills = set(validate_repository.EXPECTED_SKILLS) - {"hengmu"}
        for skill in focused_skills:
            self.assertIn(f"../{skill}/SKILL.md", entry)

    def test_hengmu_entry_preserves_direct_focused_invocation(self) -> None:
        entry = (ROOT / "skills" / "hengmu" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Keep all eight focused Skill names directly invocable", entry)
        self.assertIn("explicitly invokes a focused Skill", entry)
        self.assertIn("activate this router", entry)

    def test_specialist_workflows_preserve_per_run_inputs(self) -> None:
        for relative in (
            "skills/ai-agent-architecture-audit/SKILL.md",
            "skills/mobile-architecture-audit/SKILL.md",
        ):
            workflow = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("prepare-project-audit --repo <repo>", workflow)
            self.assertIn("explicitly requests read-only", workflow)
            self.assertIn("Set one stable `<run-id>`", workflow)
            self.assertIn(
                ".architecture/reviews/inputs/<run-id>-profile.yaml", workflow
            )
            self.assertIn(
                ".architecture/reviews/inputs/<run-id>-knowledge-selection.yaml",
                workflow,
            )
            self.assertIn("never reuse or overwrite", workflow)

        decision = (
            ROOT
            / "skills"
            / "architecture-solution-advisor"
            / "references"
            / "decision-artifact-workflow.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Set one stable `<run-id>`", decision)
        self.assertIn(
            ".architecture/reviews/inputs/<run-id>-decision-knowledge-selection.yaml",
            decision,
        )
        self.assertIn("Never reuse or overwrite", decision)

    def test_remediation_evals_require_an_accepted_decision(self) -> None:
        payload = yaml.safe_load(
            (ROOT / "evals" / "cases.yaml").read_text(encoding="utf-8")
        )
        cases = {case["id"]: case for case in payload["cases"]}

        for case_id in ("remediation-direct", "remediation-indirect"):
            self.assertIn("accepted", cases[case_id]["prompt"].lower())
        self.assertIn(
            "no architecture decision has been accepted",
            cases["remediation-incomplete"]["prompt"],
        )
        self.assertIn(
            "accepted architecture decision",
            cases["remediation-incomplete"]["expected"]["outcome"],
        )
        self.assertIn(
            "accepted constrained Greenfield", cases["remediation-edge"]["prompt"]
        )
        self.assertIn(
            "no synthetic Findings",
            cases["remediation-edge"]["expected"]["outcome"],
        )

    def test_readmes_document_each_supported_host_installation(self) -> None:
        expectations = {
            "README.md": (
                "## Install in your IDE",
                "### Codex and ChatGPT desktop",
                "### Cursor",
                "### VS Code and GitHub Copilot",
                "### Kiro",
                '"chat.pluginLocations"',
                "/hengmu:hengmu audit this repository",
                "copilot plugin install qingye-lab/hengmu",
                "complete `.codex-plugin/plugin.json`",
                "`skills/*/agents/openai.yaml`",
                'cp -R "$HENGMU_ROOT/resources/." .kiro/resources/',
            ),
            "README.zh-CN.md": (
                "## 在不同 IDE 中安装",
                "### Codex 与 ChatGPT 桌面端",
                "### Cursor",
                "### VS Code 与 GitHub Copilot",
                "### Kiro",
                '"chat.pluginLocations"',
                "/hengmu:hengmu audit this repository",
                "copilot plugin install qingye-lab/hengmu",
                "完整的 `.codex-plugin/plugin.json`",
                "`skills/*/agents/openai.yaml`",
                'cp -R "$HENGMU_ROOT/resources/." .kiro/resources/',
            ),
        }
        for path, required_text in expectations.items():
            readme = (ROOT / path).read_text(encoding="utf-8")
            for text in required_text:
                self.assertIn(text, readme, f"{path} must document {text!r}")


if __name__ == "__main__":
    unittest.main()
