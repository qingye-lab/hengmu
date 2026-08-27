#!/usr/bin/env python3
"""Validate Rule Pack, critical-flow, and selected-knowledge review coverage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

from architecture_tool import ArchitectureError, validate_file, validate_review
from artifact_types import ProfileArtifact, ReviewArtifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument(
        "--allow-candidates",
        action="store_true",
        help="Validate a candidate handoff; verified reviews are required by default.",
    )
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    review_path = args.review.expanduser()
    if not review_path.is_absolute():
        review_path = root / review_path
    try:
        profile = cast(
            ProfileArtifact,
            validate_file(
                root / ".architecture" / "profile.yaml",
                "project-profile.schema.json",
            ),
        )
        review = cast(
            ReviewArtifact,
            validate_review(
                review_path.resolve(),
                rule_pack_ids=profile["project"]["rule_packs"],
                strict_trust=True,
                repository_root=root,
            ),
        )
        if (
            not args.allow_candidates
            and review["review"]["verification_state"] != "verified"
        ):
            raise ArchitectureError(
                f"{review_path} is a candidate review; verified coverage is required"
            )
        if not review["coverage_complete"]:
            raise ArchitectureError(f"{review_path} declares incomplete coverage")
    except (ArchitectureError, OSError) as exc:
        print(f"Coverage validation failed: {exc}", file=sys.stderr)
        return 2
    not_assessed = [
        item["rule_id"]
        for item in review["coverage"]
        if item["status"] == "not_assessed"
    ]
    print(
        "Coverage validation passed: "
        f"{len(review['coverage'])} rules; "
        f"{len(review.get('critical_flow_coverage', []))} critical flows; "
        f"{len(review.get('selected_knowledge', []))} knowledge entries; "
        f"{len(not_assessed)} rules not assessed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
