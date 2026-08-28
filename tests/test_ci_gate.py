from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "evaluate_ci_gate.py"
SPEC = importlib.util.spec_from_file_location("evaluate_ci_gate", SCRIPT)
assert SPEC and SPEC.loader
evaluate_ci_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluate_ci_gate)


class CiGateTests(unittest.TestCase):
    def test_event_and_result_table(self) -> None:
        cases = (
            ("pull_request", "success", "success", True),
            ("pull_request", "success", "skipped", False),
            ("pull_request", "success", "failure", False),
            ("pull_request", "success", "cancelled", False),
            ("push", "success", "skipped", True),
            ("push", "success", "success", True),
            ("push", "success", "failure", False),
            ("push", "success", "cancelled", False),
        )
        for event, quality, dependency, expected in cases:
            with self.subTest(
                event=event,
                quality=quality,
                dependency=dependency,
            ):
                passed, _ = evaluate_ci_gate.evaluate(event, quality, dependency)
                self.assertEqual(passed, expected)

    def test_workflow_has_exact_eight_lanes_and_release_uses_summary_gate(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        matrix = workflow["jobs"]["quality"]["strategy"]["matrix"]
        base = {
            (operating_system, python)
            for operating_system in matrix["os"]
            for python in matrix["python-version"]
        }
        included = {(item["os"], item["python-version"]) for item in matrix["include"]}
        self.assertEqual(
            base | included,
            {
                ("ubuntu-latest", "3.11"),
                ("ubuntu-latest", "3.12"),
                ("ubuntu-latest", "3.13"),
                ("ubuntu-latest", "3.14"),
                ("macos-latest", "3.11"),
                ("macos-latest", "3.14"),
                ("windows-latest", "3.11"),
                ("windows-latest", "3.14"),
            },
        )
        summary = workflow["jobs"]["quality-gate"]
        self.assertEqual(summary["if"], "always()")
        self.assertEqual(summary["needs"], ["quality", "dependency-review"])
        release = workflow["jobs"]["release"]
        self.assertEqual(release["needs"], ["quality-gate"])
        self.assertEqual(
            release["if"],
            "${{ !cancelled() && needs.quality-gate.result == 'success' "
            "&& startsWith(github.ref, 'refs/tags/v') }}",
        )

    def test_release_guard_truth_table(self) -> None:
        cases = (
            (False, "success", "refs/tags/v1.1.1", True),
            (False, "failure", "refs/tags/v1.1.1", False),
            (False, "cancelled", "refs/tags/v1.1.1", False),
            (False, "skipped", "refs/tags/v1.1.1", False),
            (True, "success", "refs/tags/v1.1.1", False),
            (False, "success", "refs/heads/main", False),
            (False, "success", "refs/tags/1.1.1", False),
        )
        for workflow_cancelled, gate_result, ref, expected in cases:
            with self.subTest(
                workflow_cancelled=workflow_cancelled,
                gate_result=gate_result,
                ref=ref,
            ):
                actual = (
                    not workflow_cancelled
                    and gate_result == "success"
                    and ref.startswith("refs/tags/v")
                )
                self.assertEqual(actual, expected)

    def test_main_reports_table_outcomes(self) -> None:
        cases = (
            ("push", "success", "skipped", 0, "passed"),
            ("pull_request", "success", "failure", 2, "failed"),
        )
        for event, quality, dependency, expected, message in cases:
            with self.subTest(event=event, dependency=dependency):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    patch.object(
                        sys,
                        "argv",
                        [
                            str(SCRIPT),
                            "--event",
                            event,
                            "--quality",
                            quality,
                            "--dependency-review",
                            dependency,
                        ],
                    ),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(evaluate_ci_gate.main(), expected)
                self.assertIn(message, (stdout.getvalue() + stderr.getvalue()).lower())


if __name__ == "__main__":
    unittest.main()
