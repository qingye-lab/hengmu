from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "resources" / "scripts"))

import knowledge_model  # noqa: E402
from knowledge_model import KnowledgeError, validate_markdown_entry  # noqa: E402

SCHEMA_ROOT = ROOT / "resources" / "schemas"
SOURCE = ROOT / "resources" / "knowledge" / "domains" / "backend-api" / "overview.md"
TODAY = date(2030, 1, 1)


class FreshnessSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(knowledge_model.set_freshness_enforcement, True)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def entry(self, **replacements: str) -> Path:
        text = SOURCE.read_text(encoding="utf-8")
        for old, new in replacements.items():
            self.assertIn(old, text)
            text = text.replace(old, new, 1)
        path = self.dir / "entry.md"
        path.write_text(text, encoding="utf-8")
        return path

    def validate(self, path: Path, **kwargs: bool) -> None:
        validate_markdown_entry(
            path,
            schema_root=SCHEMA_ROOT,
            expected_kind="domain",
            today=TODAY,
            **kwargs,
        )

    def test_default_enforces_freshness(self) -> None:
        self.assertTrue(knowledge_model.freshness_enforced())
        with self.assertRaisesRegex(KnowledgeError, "is stale by"):
            self.validate(self.entry())

    def test_skip_allows_stale_entry(self) -> None:
        self.validate(self.entry(), enforce_freshness=False)

    def test_skip_still_rejects_future_review_date(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        old = next(
            line for line in text.splitlines() if line.startswith("last_reviewed:")
        )
        path = self.entry(**{old: "last_reviewed: '2031-01-01'"})
        with self.assertRaisesRegex(KnowledgeError, "in the future"):
            self.validate(path, enforce_freshness=False)

    def test_skip_still_rejects_non_https_source(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        url = next(
            line.strip() for line in text.splitlines() if "url: https://" in line
        )
        path = self.entry(**{url: url.replace("https://", "http://")})
        with self.assertRaisesRegex(KnowledgeError, "HTTPS"):
            self.validate(path, enforce_freshness=False)

    def test_skip_still_rejects_schema_error(self) -> None:
        path = self.entry(**{"version:": "bogus_field_removed:"})
        with self.assertRaises(KnowledgeError):
            self.validate(path, enforce_freshness=False)

    def test_module_switch_is_a_global_default(self) -> None:
        knowledge_model.set_freshness_enforcement(False)
        self.assertFalse(knowledge_model.freshness_enforced())
        knowledge_model.set_freshness_enforcement(True)
        self.assertTrue(knowledge_model.freshness_enforced())

    def test_tree_cache_distinguishes_modes(self) -> None:
        root = ROOT / "resources" / "knowledge"
        with self.assertRaisesRegex(KnowledgeError, "is stale by"):
            knowledge_model.validate_knowledge_tree(
                root, schema_root=SCHEMA_ROOT, today=TODAY, enforce_freshness=True
            )
        manifest, entries = knowledge_model.validate_knowledge_tree(
            root, schema_root=SCHEMA_ROOT, today=TODAY, enforce_freshness=False
        )
        self.assertTrue(entries)
        self.assertTrue(manifest["packs"])

    def test_architecture_tool_catalog_validation_follows_switch(self) -> None:
        import architecture_tool

        far = date(2040, 1, 1)
        with self.assertRaises(Exception):
            architecture_tool.validate_knowledge(far)
        knowledge_model.set_freshness_enforcement(False)
        result = architecture_tool.validate_knowledge(far)
        self.assertTrue(result["stale"])


if __name__ == "__main__":
    unittest.main()
