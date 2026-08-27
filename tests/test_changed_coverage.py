from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
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
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
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
        (self.root / "feature.py").write_text("FEATURE = 1\n", encoding="utf-8")
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
            ("feature.py",),
        )

    def test_deletion_is_skipped_but_python_rename_is_checked(self) -> None:
        (self.root / "deleted.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "renamed.py").write_text("VALUE = 2\n", encoding="utf-8")
        base = self.commit("base")
        (self.root / "deleted.py").unlink()
        self.git("mv", "renamed.py", "new_name.py")
        self.commit("delete and rename")

        paths = check_changed_coverage.changed_python_paths(self.root, base)
        self.assertNotIn("deleted.py", paths)
        self.assertIn("new_name.py", paths)

    def test_no_python_diff_is_empty(self) -> None:
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        base = self.commit("base")
        (self.root / "README.md").write_text("changed\n", encoding="utf-8")
        self.commit("documentation only")

        self.assertEqual(
            check_changed_coverage.changed_python_paths(self.root, base),
            (),
        )

    def test_pure_python_deletion_is_empty(self) -> None:
        path = self.root / "deleted.py"
        path.write_text("VALUE = 1\n", encoding="utf-8")
        base = self.commit("base")
        path.unlink()
        self.commit("delete Python source")

        self.assertEqual(
            check_changed_coverage.changed_python_paths(self.root, base),
            (),
        )

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
