#!/usr/bin/env python3
"""Fail closed when the CI matrix or event-specific dependency review failed."""

from __future__ import annotations

import argparse
import sys

TERMINAL_RESULTS = {"success", "failure", "cancelled", "skipped"}


def evaluate(event: str, quality: str, dependency_review: str) -> tuple[bool, str]:
    if quality != "success":
        return False, f"quality matrix ended with {quality}"
    if event == "pull_request":
        if dependency_review != "success":
            return (
                False,
                f"pull request dependency review ended with {dependency_review}",
            )
    elif dependency_review not in {"success", "skipped"}:
        return False, f"dependency review ended with {dependency_review}"
    return True, "all required CI results passed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--quality", choices=sorted(TERMINAL_RESULTS), required=True)
    parser.add_argument(
        "--dependency-review",
        choices=sorted(TERMINAL_RESULTS),
        required=True,
    )
    args = parser.parse_args()
    passed, detail = evaluate(args.event, args.quality, args.dependency_review)
    if not passed:
        print(f"Quality gate failed: {detail}", file=sys.stderr)
        return 2
    print(f"Quality gate passed: {detail}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
