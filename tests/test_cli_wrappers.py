from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_ROOT = ROOT / "resources" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))


def load_script(name: str):
    path = SCRIPT_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fingerprint_artifact = load_script("fingerprint_artifact")
validate_coverage = load_script("validate_coverage")
validate_knowledge = load_script("validate_knowledge")


def invoke_main(module, arguments: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with (
        patch.object(sys, "argv", [module.__file__, *arguments]),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        return module.main(), stdout.getvalue(), stderr.getvalue()


class CliWrapperTests(unittest.TestCase):
    def write_yaml(self, root: Path, name: str, payload: object) -> Path:
        path = root / name
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
        )
        return path

    def test_fingerprint_recognizes_all_supported_artifact_kinds(self) -> None:
        finding = {
            "id": "F-1",
            "rule_id": "PROJECT.DATA.001",
            "invariant": "One owner writes authoritative state.",
            "severity": "high",
            "evidence": [{"type": "source", "location": "a.py:1"}],
        }
        payloads = {
            "review": {
                "review": {"id": "review-1", "subject": {"id": "subject-1"}},
                "findings": [finding],
            },
            "architecture-decision": {"decision": {"id": "decision-1"}},
            "remediation-plan": {"plan": {"id": "plan-1"}},
            "evidence-run": {"run": {"id": "run-1"}},
            "generic-artifact": {"name": "generic"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for expected_kind, payload in payloads.items():
                path = self.write_yaml(root, f"{expected_kind}.yaml", payload)
                result = fingerprint_artifact.fingerprint(path, None)
                self.assertEqual(result["kind"], expected_kind)
                self.assertRegex(result["file_sha256"], r"^[0-9a-f]{64}$")
                self.assertRegex(result["canonical_sha256"], r"^[0-9a-f]{64}$")

            standalone = self.write_yaml(root, "finding.yaml", finding)
            result = fingerprint_artifact.fingerprint(standalone, "subject-1")
            self.assertEqual(result["kind"], "finding")
            self.assertRegex(result["finding_fingerprint"], r"^[0-9a-f]{64}$")

    def test_fingerprint_main_writes_output_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.write_yaml(root, "artifact.yaml", {"name": "test"})
            output = root / "nested" / "fingerprint.json"
            code, stdout, stderr = invoke_main(
                fingerprint_artifact,
                [str(source), "--output", str(output)],
            )
            self.assertEqual((code, stderr), (0, ""))
            self.assertIn("Artifact fingerprint written", stdout)
            self.assertEqual(json.loads(output.read_text())["kind"], "generic-artifact")

            sentinel = output.read_bytes()
            code, _, stderr = invoke_main(
                fingerprint_artifact,
                [str(source), "--output", str(output)],
            )
            self.assertEqual(code, 2)
            self.assertIn("refusing to overwrite", stderr)
            self.assertEqual(output.read_bytes(), sentinel)

    def test_fingerprint_main_reports_missing_subject_and_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            finding = self.write_yaml(
                root,
                "finding.yaml",
                {"id": "F-1", "rule_id": "R-1", "evidence": []},
            )
            code, _, stderr = invoke_main(fingerprint_artifact, [str(finding)])
            self.assertEqual(code, 2)
            self.assertIn("requires --subject-id", stderr)

            code, _, stderr = invoke_main(
                fingerprint_artifact,
                [str(root / "missing.yaml")],
            )
            self.assertEqual(code, 2)
            self.assertIn("Missing file", stderr)

    def test_validate_coverage_success_candidate_and_incomplete_paths(self) -> None:
        review = {
            "review": {"verification_state": "verified"},
            "coverage_complete": True,
            "coverage": [{"rule_id": "R-1", "status": "assessed"}],
            "critical_flow_coverage": [{"id": "flow-1"}],
            "selected_knowledge": [{"id": "knowledge-1"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = ["--project", str(root), "--review", "review.yaml"]
            with (
                patch.object(
                    validate_coverage,
                    "validate_file",
                    return_value={"project": {"rule_packs": ["project-core"]}},
                ),
                patch.object(validate_coverage, "validate_review", return_value=review),
            ):
                code, stdout, stderr = invoke_main(validate_coverage, arguments)
            self.assertEqual((code, stderr), (0, ""))
            self.assertIn("1 rules; 1 critical flows; 1 knowledge entries", stdout)

            candidate = {**review, "review": {"verification_state": "candidates"}}
            with (
                patch.object(
                    validate_coverage,
                    "validate_file",
                    return_value={"project": {"rule_packs": []}},
                ),
                patch.object(
                    validate_coverage, "validate_review", return_value=candidate
                ),
            ):
                code, _, stderr = invoke_main(validate_coverage, arguments)
                self.assertEqual(code, 2)
                self.assertIn("candidate review", stderr)
                code, stdout, stderr = invoke_main(
                    validate_coverage,
                    [*arguments, "--allow-candidates"],
                )
                self.assertEqual((code, stderr), (0, ""))
                self.assertIn("Coverage validation passed", stdout)

            incomplete = {**review, "coverage_complete": False}
            with (
                patch.object(
                    validate_coverage,
                    "validate_file",
                    return_value={"project": {"rule_packs": []}},
                ),
                patch.object(
                    validate_coverage, "validate_review", return_value=incomplete
                ),
            ):
                code, _, stderr = invoke_main(validate_coverage, arguments)
            self.assertEqual(code, 2)
            self.assertIn("incomplete coverage", stderr)

    def test_validate_knowledge_real_tree_and_invalid_root(self) -> None:
        code, stdout, stderr = invoke_main(
            validate_knowledge,
            ["--today", "2026-08-29"],
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("Knowledge validation passed", stdout)

        with tempfile.TemporaryDirectory() as temporary:
            code, _, stderr = invoke_main(
                validate_knowledge,
                ["--knowledge-root", temporary, "--today", "2026-08-29"],
            )
        self.assertEqual(code, 2)
        self.assertIn("Knowledge validation failed", stderr)


if __name__ == "__main__":
    unittest.main()
