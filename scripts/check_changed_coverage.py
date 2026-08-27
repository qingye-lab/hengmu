#!/usr/bin/env python3
"""Enforce changed-line coverage against a pull request merge base."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
SCOPED_PATHS = (
    ":(glob)resources/scripts/**/*.py",
    ":(glob)scripts/**/*.py",
)


class ChangedCoverageError(RuntimeError):
    """The changed-line coverage preconditions or check failed."""


def git(root: Path, *arguments: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise ChangedCoverageError(process.stderr.strip() or "Git command failed")
    return process.stdout.strip()


def resolve_merge_base(root: Path, base_sha: str) -> str:
    if COMMIT_RE.fullmatch(base_sha) is None:
        raise ChangedCoverageError("Pull request base SHA must be a full commit hash")
    return git(root, "merge-base", "HEAD", base_sha)


def changed_python_paths(root: Path, merge_base: str) -> tuple[str, ...]:
    output = git(
        root,
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{merge_base}...HEAD",
        "--",
        *SCOPED_PATHS,
    )
    return tuple(line for line in output.splitlines() if line.endswith(".py"))


def coverage_xml_paths(root: Path, coverage_xml: Path) -> set[str]:
    try:
        document = ElementTree.parse(coverage_xml)
    except ElementTree.ParseError as exc:
        raise ChangedCoverageError(f"Coverage XML is malformed: {exc}") from exc
    paths: set[str] = set()
    for record in document.findall(".//class"):
        filename = record.get("filename")
        if not filename:
            raise ChangedCoverageError("Coverage XML contains a class without a file")
        normalized = filename.replace("\\", "/")
        candidate = Path(normalized)
        if candidate.is_absolute():
            try:
                normalized = candidate.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
        else:
            normalized = candidate.as_posix().removeprefix("./")
        paths.add(normalized)
    return paths


def require_changed_paths_in_coverage(
    root: Path,
    coverage_xml: Path,
    changed_paths: tuple[str, ...],
) -> None:
    covered = coverage_xml_paths(root, coverage_xml)
    missing = sorted(set(changed_paths) - covered)
    if missing:
        raise ChangedCoverageError(
            "Changed Python paths are missing from coverage XML: " + ", ".join(missing)
        )


def run_diff_cover(
    root: Path,
    coverage_xml: Path,
    merge_base: str,
    *,
    fail_under: float,
) -> None:
    executable = shutil.which("diff-cover")
    if executable is None:
        raise ChangedCoverageError("diff-cover executable is unavailable")
    process = subprocess.run(
        [
            executable,
            str(coverage_xml),
            "--compare-branch",
            merge_base,
            "--fail-under",
            str(fail_under),
        ],
        cwd=root,
        check=False,
    )
    if process.returncode != 0:
        raise ChangedCoverageError(
            f"Changed-line coverage failed with exit code {process.returncode}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--coverage", type=Path, default=Path("coverage.xml"))
    parser.add_argument("--base-sha", default=os.environ.get("PR_BASE_SHA"))
    parser.add_argument("--fail-under", type=float, default=90.0)
    args = parser.parse_args()
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        print("Changed-line coverage skipped: not a pull request event.")
        return 0
    try:
        if args.base_sha is None:
            raise ChangedCoverageError("PR_BASE_SHA is required for pull requests")
        root = args.root.resolve()
        coverage_xml = args.coverage
        if not coverage_xml.is_absolute():
            coverage_xml = root / coverage_xml
        if not coverage_xml.is_file():
            raise ChangedCoverageError(f"Coverage XML is missing: {coverage_xml}")
        merge_base = resolve_merge_base(root, args.base_sha)
        paths = changed_python_paths(root, merge_base)
        if not paths:
            print("Changed-line coverage skipped: no non-deleted Python changes.")
            return 0
        require_changed_paths_in_coverage(root, coverage_xml, paths)
        run_diff_cover(
            root,
            coverage_xml,
            merge_base,
            fail_under=args.fail_under,
        )
    except (ChangedCoverageError, OSError) as exc:
        print(f"Changed-line coverage failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"Changed-line coverage passed for {len(paths)} Python path(s) "
        f"against merge base {merge_base}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
