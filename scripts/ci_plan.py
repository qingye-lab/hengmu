#!/usr/bin/env python3
"""Select the CI quality matrix and docs-only status for a workflow event."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys

FULL_LANES: tuple[tuple[str, str], ...] = (
    ("ubuntu-latest", "3.11"),
    ("ubuntu-latest", "3.12"),
    ("ubuntu-latest", "3.13"),
    ("ubuntu-latest", "3.14"),
    ("macos-latest", "3.11"),
    ("macos-latest", "3.14"),
    ("windows-latest", "3.11"),
    ("windows-latest", "3.14"),
)
# Pull requests keep the lowest and highest Python plus 3.13, which hosts the
# coverage, mypy and history-anchor steps.
PULL_REQUEST_LANES: tuple[tuple[str, str], ...] = (
    ("ubuntu-latest", "3.11"),
    ("ubuntu-latest", "3.13"),
    ("ubuntu-latest", "3.14"),
)
# Only files that no validator, test or package consumes may skip the matrix.
DOCS_ONLY_PATTERNS: tuple[str, ...] = (
    "README.md",
    "README.zh-CN.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "SECURITY.md",
    "SUPPORT.md",
)


def build_matrix(event: str) -> dict[str, list[dict[str, str]]]:
    lanes = PULL_REQUEST_LANES if event == "pull_request" else FULL_LANES
    return {"include": [{"os": o, "python-version": p} for o, p in lanes]}


def is_docs_only(event: str, changed: list[str]) -> bool:
    if event != "pull_request" or not changed:
        return False
    return all(
        any(fnmatch.fnmatchcase(path, pattern) for pattern in DOCS_ONLY_PATTERNS)
        for path in changed
    )


def changed_files(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base")
    parser.add_argument("--head")
    args = parser.parse_args()
    changed: list[str] = []
    if args.event == "pull_request" and args.base and args.head:
        changed = changed_files(args.base, args.head)
    docs_only = is_docs_only(args.event, changed)
    outputs = {
        "matrix": json.dumps(build_matrix(args.event), separators=(",", ":")),
        "docs_only": str(docs_only).lower(),
    }
    lines = [f"{key}={value}" for key, value in outputs.items()]
    print("\n".join(lines))
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
