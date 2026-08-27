from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import yaml

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "run_behavior_benchmark.py"
SPEC = importlib.util.spec_from_file_location("run_behavior_benchmark", SCRIPT_PATH)
assert SPEC and SPEC.loader
run_behavior_benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_behavior_benchmark)


class BehaviorBenchmarkTests(unittest.TestCase):
    def test_tree_manifest_uses_portable_case_sensitive_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            readme = fixture / "README.md"
            source = fixture / "catalog.py"
            readme.write_text("fixture\n", encoding="utf-8")
            source.write_text("VALUE = 1\n", encoding="utf-8")
            expected_records = [
                {
                    "path": "README.md",
                    "sha256": hashlib.sha256(readme.read_bytes()).hexdigest(),
                },
                {
                    "path": "catalog.py",
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
            ]
            expected = hashlib.sha256(
                run_behavior_benchmark.canonical_json(expected_records).encode()
            ).hexdigest()

            self.assertEqual(
                run_behavior_benchmark.tree_manifest(fixture),
                (expected, 2),
            )

    def test_context_manifest_schema_keeps_base_without_skill_content(self) -> None:
        manifest_path = ROOT / "benchmarks" / "ablation" / "context-manifest.yaml"
        schema_path = (
            ROOT / "resources" / "schemas" / "benchmark-context-manifest.schema.json"
        )
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(manifest)
        tampered["treatments"][0]["skill_body"] = [
            "skills/project-architecture-audit/SKILL.md"
        ]
        with self.assertRaisesRegex(ValueError, "is invalid"):
            run_behavior_benchmark.validate(tampered, schema, manifest_path)

    def test_ablation_manifest_requires_one_comparable_triplet_per_skill(self) -> None:
        manifest = yaml.safe_load(
            (ROOT / "benchmarks" / "ablation" / "context-manifest.yaml").read_text(
                encoding="utf-8"
            )
        )
        corpus = yaml.safe_load(
            (ROOT / "benchmarks" / "ground-truth.yaml").read_text(encoding="utf-8")
        )
        skills = {case["skill"] for case in corpus["cases"]}

        duplicate = copy.deepcopy(manifest)
        duplicate["treatments"].append(copy.deepcopy(duplicate["treatments"][0]))
        with self.assertRaisesRegex(ValueError, "repeats treatment"):
            run_behavior_benchmark.validate_ablation_treatments(
                duplicate,
                skills=skills,
            )

        incomplete = copy.deepcopy(manifest)
        incomplete["treatments"] = incomplete["treatments"][1:]
        with self.assertRaisesRegex(ValueError, "exactly one Base/Full/Compressed"):
            run_behavior_benchmark.validate_ablation_treatments(
                incomplete,
                skills=skills,
            )

    def test_provenance_marks_declared_context_assets_as_relevant_inputs(self) -> None:
        python_version = subprocess.run(
            [sys.executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        surface = python_version.stdout.strip() or python_version.stderr.strip()
        corpus_path = ROOT / "benchmarks" / "ground-truth.yaml"
        corpus = yaml.safe_load(corpus_path.read_text(encoding="utf-8"))
        observed_calls: list[tuple[str, ...]] = []
        original_git_output = run_behavior_benchmark.git_output

        def recording_git_output(root: Path, *args: str) -> str:
            observed_calls.append(args)
            return original_git_output(root, *args)

        run_behavior_benchmark.git_output = recording_git_output
        try:
            run_behavior_benchmark.collect_provenance(
                root=ROOT,
                corpus_path=corpus_path,
                corpus=corpus,
                command=[sys.executable, "-c", "pass"],
                skill_version="1.1.0",
                model="test-model",
                surface=surface,
                declared_runtimes=[sys.executable],
                context_manifest_path=(
                    ROOT / "benchmarks" / "ablation" / "context-manifest.yaml"
                ),
                context_budget={
                    "inputs": [
                        {
                            "path": (
                                "benchmarks/ablation/skills/"
                                "project-architecture-audit.md"
                            )
                        }
                    ]
                },
            )
        finally:
            run_behavior_benchmark.git_output = original_git_output

        status_call = next(args for args in observed_calls if args[0] == "status")
        self.assertIn(
            "benchmarks/ablation/skills/project-architecture-audit.md",
            status_call,
        )

    def test_runner_executes_every_fixture_without_ground_truth_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run.yaml"
            python_version = subprocess.run(
                [sys.executable, "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
            python_surface = (
                python_version.stdout.strip() or python_version.stderr.strip()
            )
            args = Namespace(
                root=ROOT,
                ground_truth=ROOT / "benchmarks" / "ground-truth.yaml",
                output=output,
                model="test-model",
                surface=python_surface,
                skill_version="1.1.0",
                runtime_executables=[sys.executable],
                timeout=10,
                command=[
                    sys.executable,
                    "-c",
                    (
                        "import json; "
                        "print(json.dumps({'observed_findings': [], "
                        "'observed_recommendations': [], "
                        "'observed_decision': {"
                        "'selected_option': 'test-option', "
                        "'compared_tradeoffs': [], "
                        "'knowledge_ids': [], "
                        "'rejected_options': [], "
                        "'migration_slices': []}}))"
                    ),
                    str(ROOT / "scripts" / "codex_benchmark_adapter.py"),
                    "--condition={condition}",
                    "--context-manifest={context_manifest}",
                    "{skill}",
                    "{fixture}",
                    "{prompt}",
                ],
            )
            result = run_behavior_benchmark.run_benchmark(args)
            log_path = output.with_suffix(".log.jsonl")
            self.assertEqual(len(result["cases"]), 10)
            self.assertEqual(result["schema_version"], "1.5")
            self.assertEqual(result["benchmark"]["model"], "test-model")
            self.assertEqual(result["benchmark"]["condition"], "full")
            context_budget = result["benchmark"]["context_budget"]
            self.assertEqual(
                context_budget["metric_kind"],
                "declared-context-proxy-v1",
            )
            self.assertGreater(context_budget["skill_body_chars"], 0)
            self.assertGreater(context_budget["knowledge_chars"], 0)
            provenance = result["benchmark"]["provenance"]
            self.assertEqual(provenance["command_template"], args.command)
            self.assertEqual(provenance["model_request"], "test-model")
            self.assertEqual(
                {item["role"] for item in provenance["runtime_executables"]},
                {"command", "model"},
            )
            self.assertIn(
                "plugin-manifest",
                {item["role"] for item in provenance["inputs"]},
            )
            self.assertIn(
                "context-manifest",
                {item["role"] for item in provenance["inputs"]},
            )
            self.assertEqual(provenance["execution_log"]["records"], 10)
            self.assertEqual(
                provenance["execution_log"]["sha256"],
                hashlib.sha256(log_path.read_bytes()).hexdigest(),
            )
            log_records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(log_records), 10)
            self.assertTrue(
                all(
                    "execution" in trial and trial["execution"]["command"]
                    for case in result["cases"]
                    for trial in case["trials"]
                )
            )
            self.assertTrue(
                all(case["observed_findings"] == [] for case in result["cases"])
            )
            output.write_text(
                yaml.safe_dump(result, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            if provenance["source"]["dirty"]:
                # The runner correctly marks a developer's modified benchmark
                # harness as non-release evidence. CI reruns the full score
                # assertion from a clean checkout.
                return
            score = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "resources" / "scripts" / "architecture_tool.py"),
                    "benchmark-score",
                    "--ground-truth",
                    str(ROOT / "benchmarks" / "ground-truth.yaml"),
                    "--run",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(score.returncode, 0, score.stderr)
            score_payload = json.loads(score.stdout)
            self.assertTrue(score_payload["provenance"]["valid"])
            self.assertTrue(
                score_payload["provenance"]["runtime_verification"][
                    "current_host_match"
                ]
            )

            original_version = provenance["runtime_executables"][0]["version_output"]
            provenance["runtime_executables"][0]["version_output"] = "tampered"
            output.write_text(
                yaml.safe_dump(result, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            runtime_tampered = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "resources" / "scripts" / "architecture_tool.py"),
                    "benchmark-score",
                    "--ground-truth",
                    str(ROOT / "benchmarks" / "ground-truth.yaml"),
                    "--run",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(runtime_tampered.returncode, 2)
            self.assertIn(
                "recorded runtime version hash mismatch",
                runtime_tampered.stderr,
            )
            provenance["runtime_executables"][0]["version_output"] = original_version
            output.write_text(
                yaml.safe_dump(result, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

            log_path.write_text(
                log_path.read_text(encoding="utf-8") + "{}\n",
                encoding="utf-8",
            )
            tampered = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "resources" / "scripts" / "architecture_tool.py"),
                    "benchmark-score",
                    "--ground-truth",
                    str(ROOT / "benchmarks" / "ground-truth.yaml"),
                    "--run",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(tampered.returncode, 2)
            self.assertIn("execution log hash mismatch", tampered.stderr)

    def test_archived_runtime_verification_binds_git_artifacts(self) -> None:
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        run_path = ROOT / "benchmarks" / "results" / "gpt-5.6-terra.yaml"
        process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "resources" / "scripts" / "architecture_tool.py"),
                "benchmark-score",
                "--ground-truth",
                str(ROOT / "benchmarks" / "ground-truth.yaml"),
                "--run",
                str(run_path),
                "--runtime-verification",
                "archived",
                "--artifact-commit",
                head,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        score = json.loads(process.stdout)
        provenance = score["provenance"]
        self.assertTrue(provenance["valid"])
        self.assertEqual(
            provenance["archive_binding"]["run_path"],
            "benchmarks/results/gpt-5.6-terra.yaml",
        )
        self.assertEqual(
            provenance["archive_binding"]["execution_log_path"],
            "benchmarks/results/gpt-5.6-terra.log.jsonl",
        )

        with tempfile.TemporaryDirectory() as temporary:
            tampered = Path(temporary) / run_path.name
            tampered.write_bytes(run_path.read_bytes() + b"\n")
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "resources" / "scripts" / "architecture_tool.py"),
                    "benchmark-score",
                    "--ground-truth",
                    str(ROOT / "benchmarks" / "ground-truth.yaml"),
                    "--run",
                    str(tampered),
                    "--runtime-verification",
                    "archived",
                    "--artifact-commit",
                    head,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn(
                "Archived benchmark run must be inside the repository",
                rejected.stderr,
            )

    def test_failed_trial_preserves_a_hash_only_execution_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "failed.yaml"
            python_version = subprocess.run(
                [sys.executable, "--version"],
                check=True,
                capture_output=True,
                text=True,
            )
            args = Namespace(
                root=ROOT,
                ground_truth=ROOT / "benchmarks" / "ground-truth.yaml",
                output=output,
                model="test-model",
                surface=(
                    python_version.stdout.strip() or python_version.stderr.strip()
                ),
                skill_version="1.1.0",
                runtime_executables=[sys.executable],
                timeout=10,
                repetitions=1,
                command=[
                    sys.executable,
                    "-c",
                    "raise SystemExit(7)",
                    "{condition}",
                    "{context_manifest}",
                ],
            )
            with self.assertRaisesRegex(RuntimeError, "trial 1 failed"):
                run_behavior_benchmark.run_benchmark(args)
            record = json.loads(
                output.with_suffix(".log.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(record["exit_code"], 7)
            self.assertIsNone(record["observation"])
            self.assertEqual(
                set(record),
                {
                    "schema_version",
                    "case_id",
                    "trial_index",
                    "duration_seconds",
                    "exit_code",
                    "command",
                    "command_sha256",
                    "stdout_sha256",
                    "stderr_sha256",
                    "observation",
                },
            )

    def test_command_placeholders_are_argument_safe(self) -> None:
        fixture = Path("/tmp/fixture with spaces")
        rendered = run_behavior_benchmark.render_command(
            ["agent", "--prompt", "{prompt}", "--repo", "{fixture}"],
            skill="project-architecture-audit",
            fixture=fixture,
            prompt="Audit; do not execute this punctuation.",
        )
        self.assertEqual(rendered[2], "Audit; do not execute this punctuation.")
        self.assertEqual(rendered[4], str(fixture))

    def test_non_full_condition_requires_treatment_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run.yaml"
            args = Namespace(
                root=ROOT,
                ground_truth=ROOT / "benchmarks" / "ground-truth.yaml",
                output=output,
                model="test-model",
                surface="test-surface",
                skill_version="1.1.0",
                runtime_executables=[],
                timeout=10,
                repetitions=1,
                condition="base",
                command=[sys.executable, "-c", "print('{}')"],
            )
            with self.assertRaisesRegex(
                ValueError,
                r"require a \{condition\} command placeholder",
            ):
                run_behavior_benchmark.run_benchmark(args)

    def test_context_budget_rejects_tampered_declared_input(self) -> None:
        corpus = run_behavior_benchmark.load_yaml(
            ROOT / "benchmarks" / "ground-truth.yaml"
        )
        manifest_path, manifest = run_behavior_benchmark.load_context_manifest(
            ROOT,
            ROOT / "benchmarks" / "ablation" / "context-manifest.yaml",
        )
        budget = run_behavior_benchmark.collect_context_budget(
            root=ROOT,
            manifest_path=manifest_path,
            manifest=manifest,
            condition="compressed",
            corpus=corpus,
        )
        budget["skill_body_chars"] += 1
        with self.assertRaisesRegex(ValueError, "Context budget total is stale"):
            run_behavior_benchmark.validate_context_budget(budget, root=ROOT)

    def test_ablation_treatments_share_workflow_knowledge_without_outcome_leakage(
        self,
    ) -> None:
        manifest_path, manifest = run_behavior_benchmark.load_context_manifest(
            ROOT,
            ROOT / "benchmarks" / "ablation" / "context-manifest.yaml",
        )
        del manifest_path
        corpus = run_behavior_benchmark.load_yaml(
            ROOT / "benchmarks" / "ground-truth.yaml"
        )
        skills = {case["skill"] for case in corpus["cases"]}
        asset_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "benchmarks" / "ablation").rglob("*.md")
        ).lower()
        for case in corpus["cases"]:
            self.assertNotIn(case["id"], asset_text)
        for skill in skills:
            base = run_behavior_benchmark.treatment_for(
                manifest,
                condition="base",
                skill=skill,
            )
            full = run_behavior_benchmark.treatment_for(
                manifest,
                condition="full",
                skill=skill,
            )
            compressed = run_behavior_benchmark.treatment_for(
                manifest,
                condition="compressed",
                skill=skill,
            )
            self.assertEqual(base["knowledge_basis"], "none")
            self.assertEqual(base["knowledge"], [])
            self.assertEqual(full["knowledge_basis"], "workflow-required")
            self.assertEqual(compressed["knowledge_basis"], "workflow-required")
            self.assertEqual(full["knowledge"], compressed["knowledge"])

    def test_solution_treatment_contains_declared_required_knowledge(self) -> None:
        _, manifest = run_behavior_benchmark.load_context_manifest(
            ROOT,
            ROOT / "benchmarks" / "ablation" / "context-manifest.yaml",
        )
        corpus = run_behavior_benchmark.load_yaml(
            ROOT / "benchmarks" / "ground-truth.yaml"
        )
        treatment = run_behavior_benchmark.treatment_for(
            manifest,
            condition="full",
            skill="architecture-solution-advisor",
        )
        supplied_ids = set()
        for relative in treatment["knowledge"]:
            payload = (ROOT / relative).read_text(encoding="utf-8")
            supplied_ids.add(
                next(
                    line.split(":", 1)[1].strip()
                    for line in payload.splitlines()
                    if line.startswith("id:")
                )
            )
        required_ids = {
            knowledge_id
            for case in corpus["cases"]
            for knowledge_id in case.get("expected_decision", {}).get(
                "required_knowledge_ids", []
            )
        }
        self.assertLessEqual(required_ids, supplied_ids)

    def test_fixture_evidence_is_resolved_not_self_asserted(self) -> None:
        fixture = ROOT / "benchmarks" / "fixtures" / "account-balance-updates"
        valid = [
            {
                "path": "store.py",
                "line_start": 1,
                "line_end": 3,
                "excerpt": "connection.write_balance(account_id, balance + amount)",
            }
        ]
        self.assertTrue(run_behavior_benchmark.evidence_is_valid(fixture, valid))
        invalid = [dict(valid[0], excerpt="a line that is not in the fixture")]
        self.assertFalse(run_behavior_benchmark.evidence_is_valid(fixture, invalid))

    def test_fixture_inputs_do_not_disclose_expected_outcomes(self) -> None:
        corpus = run_behavior_benchmark.load_yaml(
            ROOT / "benchmarks" / "ground-truth.yaml"
        )
        banned_path_terms = {
            "benign",
            "conflict",
            "healthy",
            "injected",
            "missing",
            "sufficient",
        }
        banned_content = {
            "expected behavior:",
            "expected decision:",
            "do not recommend",
        }
        for case in corpus["cases"]:
            fixture = ROOT / case["fixture"]
            self.assertTrue(fixture.is_dir(), case["id"])
            self.assertTrue(
                banned_path_terms.isdisjoint(fixture.name.split("-")),
                fixture.name,
            )
            for path in fixture.rglob("*"):
                if not path.is_file():
                    continue
                content = path.read_text(encoding="utf-8").lower()
                for phrase in banned_content:
                    self.assertNotIn(phrase, content, str(path))


if __name__ == "__main__":
    unittest.main()
