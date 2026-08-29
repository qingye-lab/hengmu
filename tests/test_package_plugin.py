from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "package_plugin.py"
SPEC = importlib.util.spec_from_file_location("package_plugin", SCRIPT_PATH)
assert SPEC and SPEC.loader
package_plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package_plugin)
SMOKE_SCRIPT_PATH = ROOT / "scripts" / "smoke_test_package.py"
SMOKE_SPEC = importlib.util.spec_from_file_location(
    "smoke_test_package", SMOKE_SCRIPT_PATH
)
assert SMOKE_SPEC and SMOKE_SPEC.loader
smoke_test_package = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(smoke_test_package)


class PackagePluginTests(unittest.TestCase):
    def test_archive_is_reproducible_and_runtime_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            first, first_checksum = package_plugin.build_package(
                ROOT,
                temp_root / "first",
            )
            second, _ = package_plugin.build_package(
                ROOT,
                temp_root / "second",
            )

            self.assertEqual(first.read_bytes(), second.read_bytes())
            digest = hashlib.sha256(first.read_bytes()).hexdigest()
            self.assertEqual(
                first_checksum.read_text(encoding="utf-8"),
                f"{digest}  {first.name}\n",
            )

            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                timestamps = {item.date_time for item in archive.infolist()}

            self.assertEqual(names, sorted(names))
            self.assertIn(".codex-plugin/plugin.json", names)
            self.assertNotIn("plugin.json", names)
            self.assertIn("resources/scripts/architecture_tool.py", names)
            self.assertIn("resources/selector-source.json", names)
            self.assertIn("resources/templates/knowledge-context.yaml", names)
            self.assertIn("skills/project-architecture-audit/SKILL.md", names)
            self.assertIn("LICENSE", names)
            self.assertIn("NOTICE", names)
            self.assertIn("requirements.txt", names)
            self.assertIn("requirements-runtime.lock", names)
            self.assertEqual(timestamps, {package_plugin.FIXED_ZIP_TIME})
            self.assertFalse(any(name.startswith(".architecture/") for name in names))
            self.assertFalse(any(name.startswith("scripts/") for name in names))
            self.assertFalse(any(name.startswith("tests/") for name in names))
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any(name.endswith(".pyc") for name in names))
            smoke_test_package.smoke_test(first, "codex")

    def test_agent_plugins_archive_is_portable_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            first, first_checksum = package_plugin.build_package(
                ROOT,
                temp_root / "first",
                "agent-plugins",
            )
            second, _ = package_plugin.build_package(
                ROOT,
                temp_root / "second",
                "agent-plugins",
            )

            self.assertEqual(first.read_bytes(), second.read_bytes())
            digest = hashlib.sha256(first.read_bytes()).hexdigest()
            self.assertEqual(
                first_checksum.read_text(encoding="utf-8"),
                f"{digest}  {first.name}\n",
            )
            self.assertEqual(first.name, "hengmu-1.3.0-agent-plugins.zip")

            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                manifest = json.loads(archive.read("plugin.json"))
                timestamps = {item.date_time for item in archive.infolist()}

            self.assertEqual(names, sorted(names))
            self.assertEqual(
                manifest["$schema"],
                package_plugin.AGENT_PLUGINS_SCHEMA,
            )
            self.assertEqual(manifest["name"], "hengmu")
            self.assertEqual(
                manifest,
                json.loads((ROOT / "plugin.json").read_text(encoding="utf-8")),
            )
            self.assertIn("agent-plugins", manifest["keywords"])
            self.assertIn("cursor", manifest["keywords"])
            self.assertNotIn("for Codex", manifest["description"])
            self.assertNotIn("skills", manifest)
            self.assertNotIn("interface", manifest)
            self.assertIn(".codex-plugin/plugin.json", names)
            self.assertIn("skills/hengmu/SKILL.md", names)
            self.assertFalse(
                any(name.endswith("/agents/openai.yaml") for name in names)
            )
            self.assertEqual(timestamps, {package_plugin.FIXED_ZIP_TIME})
            smoke_test_package.smoke_test(first)

    def test_agent_plugins_smoke_rejects_missing_provenance_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            archive, _ = package_plugin.build_package(
                ROOT,
                temp_root / "dist",
                "agent-plugins",
            )
            broken = temp_root / "broken.zip"
            with (
                zipfile.ZipFile(archive) as source,
                zipfile.ZipFile(broken, "w") as target,
            ):
                for info in source.infolist():
                    if info.filename != ".codex-plugin/plugin.json":
                        target.writestr(info, source.read(info))

            with self.assertRaisesRegex(
                smoke_test_package.SmokeTestError,
                r"missing required runtime paths: \.codex-plugin/plugin\.json",
            ):
                smoke_test_package.smoke_test(broken)

    def test_agent_plugins_smoke_rejects_unsafe_zip_entries(self) -> None:
        cases: tuple[tuple[str, zipfile.ZipInfo | str, str], ...] = (
            ("duplicate", "plugin.json", "duplicate entry names"),
            (
                "backslash",
                self._raw_name_info(r"unsafe\entry"),
                "uses a backslash",
            ),
            ("windows-drive-relative", "C:unsafe", "Unsafe archive entry"),
            ("windows-drive-absolute", "C:/unsafe", "Unsafe archive entry"),
            ("absolute", "/unsafe", "Unsafe archive entry"),
            ("traversal", "../unsafe", "Unsafe archive entry"),
            (
                "symlink",
                self._symlink_info("unsafe-link"),
                "contains a symlink",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            for name, unsafe_entry, expected in cases:
                with self.subTest(name=name):
                    archive_path = temp_root / f"{name}.zip"
                    with (
                        warnings.catch_warnings(),
                        zipfile.ZipFile(archive_path, "w") as archive,
                    ):
                        warnings.simplefilter("ignore", UserWarning)
                        for required in smoke_test_package.REQUIRED_RUNTIME_PATHS:
                            archive.writestr(required, b"{}")
                        archive.writestr(unsafe_entry, b"target")
                    destination = temp_root / f"extract-{name}"
                    destination.mkdir()
                    windows_normalization = (
                        mock.patch.object(zipfile.os, "sep", "\\")
                        if name == "backslash"
                        else contextlib.nullcontext()
                    )
                    with (
                        windows_normalization,
                        self.assertRaisesRegex(
                            smoke_test_package.SmokeTestError,
                            expected,
                        ),
                    ):
                        smoke_test_package.safe_extract(archive_path, destination)

    @staticmethod
    def _symlink_info(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        return info

    @staticmethod
    def _raw_name_info(name: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(name)
        # ZipInfo normalizes platform separators in ``filename`` during
        # construction. Restore the archive spelling so this fixture contains
        # the unsafe bytes even when the test itself runs on Windows.
        info.filename = name
        info.orig_filename = name
        return info

    def test_agent_plugins_manifest_projection_rejects_identity_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
            manifest["version"] = "9.9.9"
            (temp_root / "plugin.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                package_plugin.PackageError,
                "shared identity does not match",
            ):
                package_plugin.load_portable_source_manifest(
                    temp_root,
                    package_plugin.load_codex_manifest(ROOT),
                )

    def test_agent_plugins_manifest_owns_portable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
            manifest["description"] = "Updated host-neutral architecture workflows."
            (temp_root / "plugin.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )

            loaded = package_plugin.load_portable_source_manifest(
                temp_root,
                package_plugin.load_codex_manifest(ROOT),
            )
            self.assertEqual(loaded["description"], manifest["description"])

    def test_package_smoke_times_out_stalled_command(self) -> None:
        with self.assertRaisesRegex(
            smoke_test_package.SmokeTestError,
            "timed out after",
        ):
            smoke_test_package.run_step(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                ROOT,
                timeout_seconds=0.05,
            )


if __name__ == "__main__":
    unittest.main()
