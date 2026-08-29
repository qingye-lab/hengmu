from __future__ import annotations

import datetime as dt
import importlib.util
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "resources" / "scripts" / "architecture_tool.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location(
    "architecture_tool_review_execution",
    SCRIPT_PATH,
)
assert SPEC and SPEC.loader
architecture_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(architecture_tool)
import knowledge_model  # noqa: E402


class ReviewExecutionRuntimeTests(unittest.TestCase):
    def test_historical_decision_remains_readable_after_knowledge_evolves(
        self,
    ) -> None:
        decision_path = (
            ROOT / ".architecture" / "reviews" / "2026-07-29-architecture-decision.yaml"
        )
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "knowledge foundation.evolutionary-architecture hash is stale",
        ):
            architecture_tool.validate_decision(decision_path)
        architecture_tool.validate_decision(
            decision_path,
            repository_root=ROOT,
            allow_unverifiable_historical=True,
        )

    def test_knowledge_cache_is_content_bound_and_revalidates_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            knowledge_root = root / "knowledge"
            schema_root = root / "schemas"
            shutil.copytree(ROOT / "resources" / "knowledge", knowledge_root)
            shutil.copytree(ROOT / "resources" / "schemas", schema_root)
            target = next(knowledge_root.rglob("*.md"))
            real_validator = knowledge_model.validate_markdown_entry
            with patch.object(
                knowledge_model,
                "validate_markdown_entry",
                wraps=real_validator,
            ) as mocked:
                knowledge_model.validate_knowledge_tree(
                    knowledge_root,
                    schema_root=schema_root,
                    today=dt.date(2026, 8, 29),
                )
                first_count = mocked.call_count
                knowledge_model.validate_knowledge_tree(
                    knowledge_root,
                    schema_root=schema_root,
                    today=dt.date(2026, 8, 29),
                )
                self.assertEqual(mocked.call_count, first_count)
                target.write_bytes(target.read_bytes() + b"\n")
                knowledge_model.validate_knowledge_tree(
                    knowledge_root,
                    schema_root=schema_root,
                    today=dt.date(2026, 8, 29),
                )
                self.assertGreater(mocked.call_count, first_count)

    def test_schema_validator_cache_is_content_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            schema_path = root / "knowledge-manifest.schema.json"
            shutil.copy(
                ROOT / "resources" / "schemas" / schema_path.name,
                schema_path,
            )
            manifest_path = ROOT / "resources" / "knowledge" / "manifest.yaml"
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            knowledge_model._SCHEMA_VALIDATOR_CACHE.clear()

            knowledge_model._validate_schema(
                manifest,
                schema_path,
                manifest_path,
            )
            self.assertEqual(len(knowledge_model._SCHEMA_VALIDATOR_CACHE), 1)
            first_validator = next(
                iter(knowledge_model._SCHEMA_VALIDATOR_CACHE.values())
            )
            knowledge_model._validate_schema(
                manifest,
                schema_path,
                manifest_path,
            )
            self.assertIs(
                next(iter(knowledge_model._SCHEMA_VALIDATOR_CACHE.values())),
                first_validator,
            )

            schema_path.write_bytes(schema_path.read_bytes() + b"\n")
            knowledge_model._validate_schema(
                manifest,
                schema_path,
                manifest_path,
            )
            self.assertEqual(len(knowledge_model._SCHEMA_VALIDATOR_CACHE), 2)

    def test_sparse_provider_config_is_valid_and_status_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = Namespace(
                repo=str(root),
                name="Sparse Provider Project",
                project_id="sparse-provider-project",
                types=["service"],
                lifecycle="active",
                criticality="medium",
                owners=["test-owner"],
                qualities=["recoverability"],
                reviews=["project-architecture"],
                rule_packs=["project-core"],
                data_classification="internal",
                infer_profile=False,
            )
            target = architecture_tool.init_project(args)
            config_path = target / "evidence-providers.yaml"
            config = architecture_tool.load_yaml(config_path)
            config["providers"] = [config["providers"][0]]
            config_path.write_text(
                yaml.safe_dump(config, sort_keys=False),
                encoding="utf-8",
            )
            _, configured = architecture_tool.validate_evidence_provider_config(
                config_path
            )
            self.assertEqual(set(configured), {config["providers"][0]["id"]})
            status = architecture_tool.evidence_provider_status(root)
            self.assertEqual(
                len(status),
                len(architecture_tool.load_evidence_provider_catalog()[1]),
            )
            unconfigured = next(item for item in status if not item["configured"])
            self.assertEqual(unconfigured["configuration_status"], "unconfigured")
            self.assertFalse(unconfigured["ready"])
            self.assertEqual(unconfigured["readiness_reason"], "not configured")
            self.assertIn(
                "explicit user approval",
                unconfigured["missing_tool_guidance"],
            )
            ruff = next(item for item in status if item["id"] == "ruff")
            self.assertIn("explicit user approval", ruff["missing_tool_guidance"])
            with self.assertRaisesRegex(
                architecture_tool.ArchitectureError,
                "not configured",
            ):
                architecture_tool.run_evidence_provider(root, unconfigured["id"])

    def test_execution_plan_keeps_prior_coverage_context_only(self) -> None:
        from test_architecture_tool import ArchitectureToolTests

        fixture = ArchitectureToolTests("runTest")
        fixture.setUp()
        try:
            fixture.init_project()
            review_path = fixture.write_review()
            subprocess = __import__("subprocess")
            subprocess.run(
                ["git", "-C", str(fixture.root), "add", ".architecture/reviews"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(fixture.root),
                    "commit",
                    "-qm",
                    "Record review artifacts",
                ],
                check=True,
            )
            base_commit = architecture_tool.load_yaml(review_path)["review"]["commit"]
            critical_flows = fixture.root / ".architecture" / "critical-flows.md"
            critical_flows.write_text(
                critical_flows.read_text(encoding="utf-8")
                + "\nReview-planning classification fixture.\n",
                encoding="utf-8",
            )
            public_contract = fixture.root / "resources" / "schemas" / "contract.json"
            public_contract.parent.mkdir(parents=True)
            public_contract.write_text("{}\n", encoding="utf-8")
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(fixture.root),
                    "add",
                    ".architecture/critical-flows.md",
                    "resources/schemas/contract.json",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(fixture.root),
                    "commit",
                    "-qm",
                    "Record classified changes",
                ],
                check=True,
            )
            result = architecture_tool.plan_review_execution(
                fixture.root,
                review_path,
                base_commit=base_commit,
                scope=["."],
            )
            self.assertFalse(result["execution"]["architecture_quality_inferred"])
            self.assertTrue(result["prior_verified_review"]["sha256"])
            self.assertTrue(
                all(
                    item["action"] == "reassess"
                    for item in result["execution"]["rules"]
                )
            )
            self.assertTrue(
                all(
                    item["reuse"] in {"context-only", "none"}
                    for item in result["execution"]["critical_flows"]
                )
            )
            self.assertNotIn("passed", yaml.safe_dump(result))
            self.assertEqual(
                result["changed_paths"],
                sorted(
                    architecture_tool.git_changed_paths(
                        fixture.root,
                        base_commit,
                        architecture_tool.current_git_commit(fixture.root),
                    )
                ),
            )
            self.assertEqual(
                result["impact"],
                {
                    key: bool(result["impact_paths"][key])
                    for key in architecture_tool.REVIEW_EXECUTION_IMPACTS
                },
            )
            self.assertTrue(result["impact"]["critical"])
            self.assertTrue(result["impact"]["public_contract"])
            self.assertIn(
                ".architecture/critical-flows.md",
                result["impact_paths"]["critical"],
            )
            self.assertIn(
                "resources/schemas/contract.json",
                result["impact_paths"]["public_contract"],
            )
            with self.assertRaisesRegex(
                architecture_tool.ArchitectureError,
                "scope excludes changed paths",
            ):
                architecture_tool.plan_review_execution(
                    fixture.root,
                    review_path,
                    base_commit=base_commit,
                    scope=["resources"],
                )
        finally:
            fixture.tearDown()

    def test_knowledge_cache_returns_isolated_snapshots(self) -> None:
        manifest, entries = knowledge_model.validate_knowledge_tree(
            ROOT / "resources" / "knowledge",
            schema_root=ROOT / "resources" / "schemas",
            today=dt.date(2026, 8, 29),
        )
        manifest["packs"].clear()
        first_entry = next(iter(entries.values()))
        first_entry.metadata["domains"].clear()

        fresh_manifest, fresh_entries = knowledge_model.validate_knowledge_tree(
            ROOT / "resources" / "knowledge",
            schema_root=ROOT / "resources" / "schemas",
            today=dt.date(2026, 8, 29),
        )
        self.assertTrue(fresh_manifest["packs"])
        self.assertTrue(fresh_entries[first_entry.id].metadata["domains"])


if __name__ == "__main__":
    unittest.main()
