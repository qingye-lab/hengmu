from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "codex_benchmark_adapter.py"
SPEC = importlib.util.spec_from_file_location("codex_benchmark_adapter", SCRIPT_PATH)
assert SPEC and SPEC.loader
codex_benchmark_adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex_benchmark_adapter)


class CodexBenchmarkAdapterTests(unittest.TestCase):
    def test_cli_metadata_keeps_current_codex_model_identity_unavailable(
        self,
    ) -> None:
        actual, source, usage = codex_benchmark_adapter.parse_cli_metadata(
            "\n".join(
                [
                    json.dumps({"type": "thread.started", "model": "actual-model"}),
                    json.dumps(
                        {
                            "type": "turn.completed",
                            "usage": {"input_tokens": 11, "output_tokens": 7},
                        }
                    ),
                ]
            ),
            "requested-model",
        )
        self.assertEqual((actual, source), (None, "unavailable"))
        self.assertEqual(usage["input_tokens"], 11)
        self.assertIsNone(usage["cost_usd"])

    def test_cli_metadata_rejects_hostile_nested_models_and_banners(self) -> None:
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "mcp_tool_call",
                            "model": "hostile-nested-model",
                            "result": {"actual_model": "hostile-tool-result"},
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 3, "output_tokens": 2},
                        "metadata": {"model_id": "hostile-metadata-model"},
                    }
                ),
                "model: hostile-banner-model",
            ]
        )
        actual, source, usage = codex_benchmark_adapter.parse_cli_metadata(
            stdout,
            "actual model: hostile-stderr-banner",
        )
        self.assertEqual((actual, source), (None, "unavailable"))
        self.assertEqual(usage["input_tokens"], 3)

    def test_command_envelope_owns_metadata_and_exposes_no_raw_errors(self) -> None:
        observation = {
            "observed_findings": [],
            "observed_recommendations": [],
            "observed_decision": {
                "selected_option": "not-applicable",
                "compared_tradeoffs": [],
                "knowledge_ids": [],
                "rejected_options": [],
                "migration_slices": [],
            },
        }
        envelope = codex_benchmark_adapter.command_envelope(
            requested_model="requested-model",
            status="completed",
            actual_model="actual-model",
            actual_model_source="cli-json",
            correction_count=0,
            usage=None,
            observation=observation,
        )
        self.assertEqual(
            envelope["adapter"]["name"], codex_benchmark_adapter.ADAPTER_NAME
        )
        self.assertEqual(envelope["retries"]["generic"], 0)
        self.assertFalse(envelope["model_fallback"])
        self.assertNotIn("error", envelope)

    def test_allowed_rules_come_from_machine_rule_packs(self) -> None:
        rule_ids = codex_benchmark_adapter.allowed_rule_ids(ROOT)
        self.assertIn("PROJECT.IDEMPOTENCY.001", rule_ids)
        self.assertIn("AI.TOOL.002", rule_ids)
        self.assertNotIn("pattern.idempotency-key", rule_ids)

    def test_prompt_tradeoff_vocabulary_matches_observation_schema(self) -> None:
        schema = json.loads(
            (
                ROOT / "resources" / "schemas" / "benchmark-observation.schema.json"
            ).read_text(encoding="utf-8")
        )
        schema_tradeoffs = schema["properties"]["observed_decision"]["properties"][
            "compared_tradeoffs"
        ]["items"]["enum"]
        self.assertEqual(
            list(codex_benchmark_adapter.CANONICAL_TRADEOFFS),
            schema_tradeoffs,
        )

    def test_evidence_validation_requires_a_verbatim_contiguous_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            (fixture / "sample.py").write_text(
                "def run():\n    return True\n",
                encoding="utf-8",
            )
            valid = {
                "observed_findings": [
                    {
                        "rule_id": "PROJECT.TEST.001",
                        "evidence": [
                            {
                                "path": "sample.py",
                                "line_start": 2,
                                "line_end": 2,
                                "excerpt": "    return True",
                            }
                        ],
                    }
                ]
            }
            self.assertEqual(
                codex_benchmark_adapter.evidence_errors(valid, fixture),
                [],
            )
            valid["observed_findings"][0]["evidence"][0]["excerpt"] = "..."
            self.assertTrue(
                codex_benchmark_adapter.evidence_errors(valid, fixture),
            )

    def test_solution_prompt_uses_canonical_option_and_tradeoff_ids(self) -> None:
        prompt = codex_benchmark_adapter.build_prompt(
            skill_path=ROOT / "skills" / "architecture-solution-advisor" / "SKILL.md",
            knowledge_root=ROOT / "resources" / "knowledge",
            fixture=ROOT / "benchmarks" / "fixtures" / "render-job-processing",
            task="Choose a proportional architecture.",
        )
        self.assertIn("style.web-queue-worker becomes web-queue-worker", prompt)
        self.assertIn("delivery-semantics", prompt)
        self.assertIn("never combine dimensions", prompt)

    def test_base_prompt_does_not_disclose_skill_or_knowledge_locations(self) -> None:
        skill_path = ROOT / "skills" / "project-architecture-audit" / "SKILL.md"
        knowledge_root = ROOT / "resources" / "knowledge"
        prompt = codex_benchmark_adapter.build_prompt(
            skill_path=skill_path,
            knowledge_root=knowledge_root,
            fixture=ROOT / "benchmarks" / "fixtures" / "desktop-sqlite-catalog",
            task="Audit only directly proved risks.",
            condition="base",
        )
        self.assertNotIn(str(skill_path), prompt)
        self.assertNotIn(str(knowledge_root), prompt)
        self.assertNotIn("Read and follow the Skill", prompt)

    def test_compressed_treatment_uses_only_manifest_declared_inputs(self) -> None:
        manifest = codex_benchmark_adapter.load_context_manifest(
            ROOT,
            ROOT / "benchmarks" / "ablation" / "context-manifest.yaml",
        )
        treatment = codex_benchmark_adapter.treatment_for(
            manifest,
            condition="compressed",
            skill="architecture-solution-advisor",
        )
        compact = codex_benchmark_adapter.resolve_treatment_paths(
            ROOT,
            treatment,
            "skill_body",
        )
        knowledge = codex_benchmark_adapter.resolve_treatment_paths(
            ROOT,
            treatment,
            "knowledge",
        )
        prompt = codex_benchmark_adapter.build_prompt(
            skill_path=ROOT / "skills" / "architecture-solution-advisor" / "SKILL.md",
            knowledge_root=ROOT / "resources" / "knowledge",
            fixture=ROOT / "benchmarks" / "fixtures" / "render-job-processing",
            task="Select a proportional architecture.",
            condition="compressed",
            compact_skill_paths=compact,
            knowledge_paths=knowledge,
        )
        self.assertIn(str(compact[0]), prompt)
        self.assertIn(str(knowledge[0]), prompt)
        self.assertNotIn(
            str(ROOT / "skills" / "architecture-solution-advisor" / "SKILL.md"),
            prompt,
        )

    def test_treatment_resolution_rejects_exact_and_default_ambiguity(self) -> None:
        manifest = codex_benchmark_adapter.load_context_manifest(
            ROOT,
            ROOT / "benchmarks" / "ablation" / "context-manifest.yaml",
        )
        exact_ambiguous = copy.deepcopy(manifest)
        duplicate_exact = copy.deepcopy(exact_ambiguous["treatments"][-1])
        duplicate_exact["tool_descriptions"] = []
        exact_ambiguous["treatments"].append(duplicate_exact)
        with self.assertRaisesRegex(ValueError, "multiple exact"):
            codex_benchmark_adapter.treatment_for(
                exact_ambiguous,
                condition="compressed",
                skill="ai-agent-architecture-audit",
                case_id="evaluation-report-pipeline",
            )

        default_ambiguous = copy.deepcopy(manifest)
        default = next(
            treatment
            for treatment in default_ambiguous["treatments"]
            if treatment["condition"] == "base"
            and treatment["skill"] == "project-architecture-audit"
            and "case_id" not in treatment
        )
        duplicate_default = copy.deepcopy(default)
        duplicate_default["tool_descriptions"] = []
        default_ambiguous["treatments"].append(duplicate_default)
        with self.assertRaisesRegex(ValueError, "multiple default"):
            codex_benchmark_adapter.treatment_for(
                default_ambiguous,
                condition="base",
                skill="project-architecture-audit",
                case_id="benign-large-sqlite",
            )

    def test_full_treatment_uses_declared_references_and_shared_knowledge(self) -> None:
        manifest = codex_benchmark_adapter.load_context_manifest(
            ROOT,
            ROOT / "benchmarks" / "ablation" / "context-manifest.yaml",
        )
        full = codex_benchmark_adapter.treatment_for(
            manifest,
            condition="full",
            skill="architecture-solution-advisor",
        )
        compressed = codex_benchmark_adapter.treatment_for(
            manifest,
            condition="compressed",
            skill="architecture-solution-advisor",
        )
        references = codex_benchmark_adapter.resolve_treatment_paths(
            ROOT,
            full,
            "references",
        )
        knowledge = codex_benchmark_adapter.resolve_treatment_paths(
            ROOT,
            full,
            "knowledge",
        )
        prompt = codex_benchmark_adapter.build_prompt(
            skill_path=ROOT / "skills" / "architecture-solution-advisor" / "SKILL.md",
            knowledge_root=ROOT / "resources" / "knowledge",
            fixture=ROOT / "benchmarks" / "fixtures" / "render-job-processing",
            task="Select a proportional architecture.",
            condition="full",
            reference_paths=references,
            knowledge_paths=knowledge,
        )
        self.assertEqual(full["knowledge"], compressed["knowledge"])
        self.assertIn(str(references[0]), prompt)
        self.assertIn(str(knowledge[0]), prompt)
        self.assertNotIn("The architecture knowledge catalog is read-only at", prompt)

    def test_missing_codex_emits_structured_tool_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stream = io.StringIO()
            argv = [
                str(SCRIPT_PATH),
                "--root",
                str(ROOT),
                "--model",
                "requested-model",
                "--skill",
                "ai-agent-architecture-audit",
                "--fixture",
                str(ROOT / "benchmarks" / "fixtures" / "protocol-session-host"),
                "--prompt",
                "Audit the bounded fixture without inferring expected outcomes.",
                "--case-id",
                "protocol-session-host",
                "--codex",
                "definitely-not-codex",
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(stream):
                self.assertEqual(codex_benchmark_adapter.main(), 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["failure"]["class"], "tool-error")
            self.assertIsNone(payload["actual_model"])
            self.assertNotIn(str(temporary), stream.getvalue())

    def test_evidence_correction_exhaustion_is_structured(self) -> None:
        invalid = {
            "observed_findings": [
                {
                    "rule_id": "AI.STATE.001",
                    "severity": "high",
                    "evidence": [
                        {
                            "path": "host.py",
                            "line_start": 1,
                            "line_end": 1,
                            "excerpt": "not present",
                        }
                    ],
                }
            ],
            "observed_recommendations": [],
            "observed_decision": {
                "selected_option": "not-applicable",
                "compared_tradeoffs": [],
                "knowledge_ids": [],
                "rejected_options": [],
                "migration_slices": [],
            },
        }
        argv = [
            str(SCRIPT_PATH),
            "--root",
            str(ROOT),
            "--model",
            "requested-model",
            "--skill",
            "ai-agent-architecture-audit",
            "--fixture",
            str(ROOT / "benchmarks" / "fixtures" / "protocol-session-host"),
            "--prompt",
            "Audit the bounded fixture without inferring expected outcomes.",
            "--case-id",
            "protocol-session-host",
        ]
        stream = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.object(
                codex_benchmark_adapter.shutil, "which", return_value=sys.executable
            ),
            patch.object(
                codex_benchmark_adapter,
                "execute_codex",
                side_effect=[
                    (invalid, "requested-model", "cli-json", None),
                    (invalid, "requested-model", "cli-json", None),
                ],
            ),
            redirect_stdout(stream),
        ):
            self.assertEqual(codex_benchmark_adapter.main(), 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["failure"]["class"], "schema-invalid")
        self.assertEqual(payload["retries"]["evidence_correction_count"], 1)
        self.assertNotIn("not present", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
