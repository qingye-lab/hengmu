#!/usr/bin/env python3
"""Exercise the packaged Agent Plugins runtime after safe extraction."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
COMMAND_TIMEOUT_SECONDS = 120
COMMON_RUNTIME_PATHS = (
    ".codex-plugin/plugin.json",
    "resources/scripts/architecture_tool.py",
    "resources/scripts/inspect_repository.py",
    "resources/scripts/build_project_profile.py",
    "resources/scripts/select_knowledge.py",
)
REQUIRED_RUNTIME_PATHS_BY_FORMAT = {
    "agent-plugins": ("plugin.json", *COMMON_RUNTIME_PATHS),
    "codex": COMMON_RUNTIME_PATHS,
}
# Retain the public constant used by existing callers for the portable format.
REQUIRED_RUNTIME_PATHS = REQUIRED_RUNTIME_PATHS_BY_FORMAT["agent-plugins"]


class SmokeTestError(RuntimeError):
    """An archive is unsafe, incomplete, or unable to run its primary path."""


def resolve_archive(pattern: str, package_format: str) -> Path:
    """Resolve one archive path while supporting quoted release globs."""

    pattern_path = Path(pattern).expanduser()
    if pattern_path.is_absolute():
        search_root = Path(pattern_path.anchor)
        search_pattern = str(pattern_path.relative_to(search_root))
    else:
        search_root = Path.cwd()
        search_pattern = str(pattern_path)
    matches = sorted(path.resolve() for path in search_root.glob(search_pattern))
    if package_format == "codex":
        matches = [
            path for path in matches if not path.name.endswith("-agent-plugins.zip")
        ]
    else:
        matches = [path for path in matches if path.name.endswith("-agent-plugins.zip")]
    if len(matches) != 1:
        raise SmokeTestError(
            f"Expected exactly one archive for {pattern!r}; found {len(matches)}"
        )
    archive = matches[0]
    if not archive.is_file():
        raise SmokeTestError(f"Archive is not a file: {archive}")
    return archive


def safe_extract(
    archive_path: Path,
    destination: Path,
    required_runtime_paths: tuple[str, ...] = REQUIRED_RUNTIME_PATHS,
) -> None:
    """Extract regular ZIP entries without allowing traversal or symlinks."""

    with zipfile.ZipFile(archive_path) as archive:
        archive_names = archive.namelist()
        names = set(archive_names)
        if len(names) != len(archive_names):
            raise SmokeTestError("Archive contains duplicate entry names")
        missing = sorted(set(required_runtime_paths) - names)
        if missing:
            raise SmokeTestError(
                "Archive is missing required runtime paths: " + ", ".join(missing)
            )
        for info in archive.infolist():
            raw_name = info.orig_filename
            if "\\" in raw_name:
                raise SmokeTestError(f"Archive entry uses a backslash: {raw_name}")
            if raw_name != info.filename:
                raise SmokeTestError(
                    f"Archive entry changed during name normalization: {raw_name!r}"
                )
            relative = PurePosixPath(raw_name)
            windows_relative = PureWindowsPath(raw_name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or windows_relative.drive
                or windows_relative.is_absolute()
            ):
                raise SmokeTestError(f"Unsafe archive entry: {raw_name}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SmokeTestError(f"Archive contains a symlink: {raw_name}")
            target = destination.joinpath(*relative.parts)
            try:
                target.resolve().relative_to(destination.resolve())
            except ValueError as exc:
                raise SmokeTestError(f"Unsafe archive entry: {raw_name}") from exc
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))


def run_step(
    command: list[str],
    cwd: Path,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> None:
    """Run one packaged CLI step and retain diagnostics on failure."""

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeTestError(
            f"Packaged command timed out after {timeout_seconds:g}s: "
            + " ".join(command)
        ) from exc
    if result.returncode:
        detail = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        raise SmokeTestError(
            f"Packaged command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{detail}" if detail else "")
        )


def load_manifest(path: Path, label: str) -> dict[str, object]:
    """Load one package manifest as a JSON object."""

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SmokeTestError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SmokeTestError(f"{label} must be a JSON object")
    return manifest


def smoke_test(
    archive_path: Path,
    package_format: str = "agent-plugins",
) -> None:
    """Run preparation, fact inspection, profiling, and Knowledge Selection."""

    try:
        required_runtime_paths = REQUIRED_RUNTIME_PATHS_BY_FORMAT[package_format]
    except KeyError as exc:
        raise SmokeTestError(f"Unsupported package format: {package_format}") from exc
    archive_path = archive_path.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="hengmu-package-smoke-") as temporary:
        temporary_root = Path(temporary)
        unpacked = temporary_root / "plugin"
        target = temporary_root / "target"
        unpacked.mkdir()
        target.mkdir()
        safe_extract(archive_path, unpacked, required_runtime_paths)

        native_manifest = load_manifest(
            unpacked / ".codex-plugin" / "plugin.json",
            "Codex manifest",
        )
        if native_manifest.get("name") != "hengmu":
            raise SmokeTestError("Codex manifest has the wrong plugin name")
        if package_format == "agent-plugins":
            manifest = load_manifest(unpacked / "plugin.json", "Portable manifest")
            if manifest.get("$schema") != AGENT_PLUGINS_SCHEMA:
                raise SmokeTestError(
                    "Portable manifest has the wrong Agent Plugins schema"
                )
            if manifest.get("name") != native_manifest.get("name") or manifest.get(
                "version"
            ) != native_manifest.get("version"):
                raise SmokeTestError(
                    "Portable and Codex manifests have inconsistent identity"
                )

        (target / "README.md").write_text("# Package smoke target\n", encoding="utf-8")
        architecture = target / ".architecture"
        facts = architecture / "repository-facts.yaml"
        profile = architecture / "profile.yaml"
        selection = architecture / "knowledge-selection.yaml"
        context = architecture / "knowledge-context.yaml"

        python = sys.executable
        scripts = unpacked / "resources" / "scripts"
        commands = (
            [
                python,
                str(scripts / "architecture_tool.py"),
                "prepare-project-audit",
                "--repo",
                str(target),
            ],
            [
                python,
                str(scripts / "inspect_repository.py"),
                "--repo",
                str(target),
                "--output",
                str(facts),
                "--force",
            ],
            [
                python,
                str(scripts / "build_project_profile.py"),
                "--facts",
                str(facts),
                "--output",
                str(profile),
                "--force",
            ],
            [
                python,
                str(scripts / "select_knowledge.py"),
                "--facts",
                str(facts),
                "--profile",
                str(profile),
                "--task",
                "audit this repository",
                "--skill",
                "project-architecture-audit",
                "--output",
                str(selection),
                "--context-output",
                str(context),
                "--force",
            ],
        )
        for command in commands:
            run_step(command, unpacked)
        for artifact in (facts, profile, selection, context):
            if not artifact.is_file():
                raise SmokeTestError(
                    f"Packaged workflow did not create {artifact.name}"
                )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test one extracted Agent Plugins release archive."
    )
    parser.add_argument(
        "--archive",
        required=True,
        help="Archive path or quoted glob that must resolve to exactly one ZIP.",
    )
    parser.add_argument(
        "--format",
        choices=sorted(REQUIRED_RUNTIME_PATHS_BY_FORMAT),
        default="agent-plugins",
        help="Package contract to validate (default: agent-plugins).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        archive = resolve_archive(args.archive, args.format)
        smoke_test(archive, args.format)
    except (OSError, zipfile.BadZipFile, SmokeTestError) as exc:
        print(f"Package smoke test failed: {exc}")
        raise SystemExit(2) from exc
    print(f"Package smoke test passed: {archive}")


if __name__ == "__main__":
    main()
