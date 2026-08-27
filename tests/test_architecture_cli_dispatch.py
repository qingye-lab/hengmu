from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "resources" / "scripts" / "architecture_tool.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("architecture_cli_dispatch", SCRIPT)
assert SPEC and SPEC.loader
architecture_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(architecture_tool)


class ArchitectureCliDispatchTests(unittest.TestCase):
    def test_evidence_provider_status_dispatches_and_prints_json(self) -> None:
        args = Namespace(command="evidence-providers", project=".")
        stdout = io.StringIO()
        with (
            patch.object(
                architecture_tool,
                "evidence_provider_status",
                return_value=[{"id": "provider-1", "status": "ready"}],
            ) as status,
            redirect_stdout(stdout),
        ):
            self.assertEqual(architecture_tool.run(args), 0)
        status.assert_called_once_with(Path())
        self.assertEqual(json.loads(stdout.getvalue())[0]["status"], "ready")

    def test_verify_evidence_dispatches_validated_review(self) -> None:
        args = Namespace(
            command="verify-evidence",
            review="review.yaml",
            repo="project",
        )
        review = {"review": {"id": "review-1"}}
        stdout = io.StringIO()
        with (
            patch.object(
                architecture_tool,
                "validate_review",
                return_value=review,
            ) as validate,
            patch.object(
                architecture_tool,
                "verify_review_evidence",
                return_value={"status": "pass"},
            ) as verify,
            redirect_stdout(stdout),
        ):
            self.assertEqual(architecture_tool.run(args), 0)
        validate.assert_called_once_with(Path("review.yaml").resolve())
        verify.assert_called_once_with(review, Path("project"))
        self.assertEqual(json.loads(stdout.getvalue()), {"status": "pass"})

    def test_decision_bindings_uses_the_validated_review_after_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary).resolve()
            (project / ".architecture").mkdir()
            review_path = project / "review.yaml"
            selection_path = project / "selection.yaml"
            review_path.write_text("review: review-1\n", encoding="utf-8")
            selection_path.write_text("selection: selection-1\n", encoding="utf-8")
            args = Namespace(
                command="decision-bindings",
                project=str(project),
                review="review.yaml",
                design_brief=None,
                knowledge_selection=None,
            )
            profile = {"project": {"rule_packs": ["project-core"]}}
            review = {
                "schema_version": "1.2",
                "review": {"id": "review-1"},
                "knowledge_selection": {"path": "selection.yaml"},
            }
            selection = {
                "selection": [
                    {"id": "foundation.test", "version": "1.0.0", "sha256": "a" * 64}
                ]
            }
            stdout = io.StringIO()
            with (
                patch.object(
                    architecture_tool,
                    "validate_file",
                    return_value=profile,
                ),
                patch.object(
                    architecture_tool,
                    "validate_review",
                    return_value=review,
                ) as validate_review,
                patch.object(
                    architecture_tool,
                    "validate_knowledge_selection_artifact",
                    return_value=selection,
                ),
                redirect_stdout(stdout),
            ):
                self.assertEqual(architecture_tool.run(args), 0)

            validate_review.assert_called_once_with(
                review_path,
                rule_pack_ids=["project-core"],
                strict_trust=True,
                repository_root=project,
                require_current_selection=True,
            )
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["source_review"], "review-1")
            self.assertEqual(result["knowledge_selection_path"], "selection.yaml")


if __name__ == "__main__":
    unittest.main()
