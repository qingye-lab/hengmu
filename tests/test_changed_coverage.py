from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_changed_coverage.py"
SPEC = importlib.util.spec_from_file_location("check_changed_coverage", SCRIPT)
assert SPEC and SPEC.loader
check_changed_coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_changed_coverage)


class ChangedCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Coverage Test")
        self.git("config", "user.email", "coverage@example.invalid")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str) -> str:
        return self.git_at(self.root, *arguments)

    def git_at(self, root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def test_uses_merge_base_when_base_branch_advances(self) -> None:
        (self.root / "base.py").write_text("BASE = 1\n", encoding="utf-8")
        base = self.commit("base")
        self.git("switch", "-qc", "feature")
        feature = self.root / "scripts" / "feature.py"
        feature.parent.mkdir()
        feature.write_text("FEATURE = 1\n", encoding="utf-8")
        self.commit("feature")
        self.git("switch", "-q", "main")
        (self.root / "main.txt").write_text("advanced\n", encoding="utf-8")
        advanced_base = self.commit("advance base")
        self.git("switch", "-q", "feature")

        merge_base = check_changed_coverage.resolve_merge_base(
            self.root,
            advanced_base,
        )
        self.assertEqual(merge_base, base)
        self.assertEqual(
            check_changed_coverage.changed_python_paths(self.root, merge_base),
            ("scripts/feature.py",),
        )

    def test_changed_python_scope_is_table_driven(self) -> None:
        cases = (
            (
                "new missing file",
                {"README.md": "base\n"},
                {
                    "README.md": "base\n",
                    "scripts/new_check.py": "CHECK = True\n",
                },
                ("scripts/new_check.py",),
            ),
            (
                "rename",
                {"resources/scripts/old_name.py": "VALUE = 1\n"},
                {"resources/scripts/new_name.py": "VALUE = 1\n"},
                ("resources/scripts/new_name.py",),
            ),
            (
                "pure deletion",
                {"scripts/deleted.py": "VALUE = 1\n"},
                {},
                (),
            ),
            (
                "no Python diff",
                {"README.md": "base\n"},
                {"README.md": "changed\n"},
                (),
            ),
            (
                "Python outside coverage scope",
                {"tests/test_example.py": "VALUE = 1\n"},
                {"tests/test_example.py": "VALUE = 2\n"},
                (),
            ),
        )
        for index, (name, before, after, expected) in enumerate(cases):
            with self.subTest(name=name):
                root = self.root / f"case-{index}"
                root.mkdir()
                self.git_at(root, "init", "-q", "-b", "main")
                self.git_at(root, "config", "user.name", "Coverage Test")
                self.git_at(
                    root,
                    "config",
                    "user.email",
                    "coverage@example.invalid",
                )
                for relative, content in before.items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                self.git_at(root, "add", "-A")
                self.git_at(root, "commit", "-qm", "base")
                base = self.git_at(root, "rev-parse", "HEAD")
                for relative in set(before) - set(after):
                    (root / relative).unlink()
                for relative, content in after.items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")
                self.git_at(root, "add", "-A")
                self.git_at(root, "commit", "-qm", "change")
                self.assertEqual(
                    check_changed_coverage.changed_python_paths(root, base),
                    expected,
                )

    def test_missing_changed_file_in_coverage_fails_closed(self) -> None:
        coverage_xml = self.root / "coverage.xml"
        coverage_xml.write_text(
            "<coverage><packages><package><classes>"
            '<class filename="resources/scripts/runtime.py" />'
            "</classes></package></packages></coverage>",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            check_changed_coverage.ChangedCoverageError,
            "scripts/new_check.py",
        ):
            check_changed_coverage.require_changed_paths_in_coverage(
                self.root,
                coverage_xml,
                ("resources/scripts/runtime.py", "scripts/new_check.py"),
            )

    def test_pr_main_rejects_missing_coverage_before_diff_cover(self) -> None:
        path = self.root / "scripts" / "new_check.py"
        path.parent.mkdir()
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        base = self.commit("base")
        path.write_text("CHECK = True\n", encoding="utf-8")
        self.commit("add check")
        coverage_xml = self.root / "coverage.xml"
        coverage_xml.write_text("<coverage />", encoding="utf-8")
        stderr = io.StringIO()
        with (
            patch.dict(
                os.environ,
                {"GITHUB_EVENT_NAME": "pull_request"},
                clear=False,
            ),
            patch.object(
                sys,
                "argv",
                [
                    str(SCRIPT),
                    "--root",
                    str(self.root),
                    "--coverage",
                    str(coverage_xml),
                    "--base-sha",
                    base,
                ],
            ),
            patch.object(check_changed_coverage, "run_diff_cover") as diff_cover,
            redirect_stderr(stderr),
        ):
            self.assertEqual(check_changed_coverage.main(), 2)
        diff_cover.assert_not_called()
        self.assertIn("missing from coverage XML", stderr.getvalue())

    def test_non_pr_event_skips_without_git_or_coverage_inputs(self) -> None:
        with (
            patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push"}, clear=False),
            patch.object(sys, "argv", [str(SCRIPT)]),
        ):
            self.assertEqual(check_changed_coverage.main(), 0)

    def test_invalid_base_sha_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            check_changed_coverage.ChangedCoverageError,
            "full commit hash",
        ):
            check_changed_coverage.resolve_merge_base(self.root, "main")


if __name__ == "__main__":
    unittest.main()
