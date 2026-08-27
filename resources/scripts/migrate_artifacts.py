#!/usr/bin/env python3
"""Migrate a legacy Review to a 1.2 candidate without fabricating verification."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, cast

import yaml
from architecture_tool import (
    ArchitectureError,
    canonical_sha256,
    expected_rules,
    file_sha256,
    finding_fingerprint,
    git_is_clean,
    load_rule_packs,
    local_rule_pack_roots,
    require_within_root,
    slugify,
    validate_file,
    validate_review,
)
from artifact_types import (
    KnowledgeSelectionArtifact,
    ProfileArtifact,
    ReviewArtifact,
)

WORKFLOW_BY_KIND = {
    "project": "project-architecture",
    "ai-agent": "ai-agent-architecture",
    "mobile": "mobile-architecture",
    "portfolio": "portfolio-architecture",
}


class MigrationError(RuntimeError):
    """Unsafe legacy artifact migration."""


def critical_flow_ids(path: Path) -> list[str]:
    headings = re.findall(
        r"^## (?!Flow template\s*$)(.+?)\s*$",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    return [slugify(heading) for heading in headings]


def migrate_review(
    source_path: Path,
    *,
    project_root: Path,
    facts_path: Path,
    selection_path: Path,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    source_path = require_within_root(root, source_path, "legacy review")
    facts_path = require_within_root(root, facts_path, "repository facts")
    selection_path = require_within_root(
        root,
        selection_path,
        "knowledge selection",
    )
    source = cast(ReviewArtifact, validate_review(source_path))
    if source["schema_version"] == "1.2":
        raise MigrationError(f"{source_path} already uses Review schema 1.2")
    profile_path = root / ".architecture" / "profile.yaml"
    profile = cast(
        ProfileArtifact,
        validate_file(profile_path, "project-profile.schema.json"),
    )
    facts = validate_file(facts_path, "repository-facts.schema.json")
    selection = cast(
        KnowledgeSelectionArtifact,
        validate_file(
            selection_path,
            "knowledge-selection.schema.json",
        ),
    )
    if selection["inputs"]["facts_sha256"] != file_sha256(facts_path):
        raise MigrationError("Knowledge selection is bound to different facts")
    if selection["inputs"].get("profile_sha256") != file_sha256(profile_path):
        raise MigrationError("Knowledge selection is bound to a different profile")

    declared_ids = [item["id"] for item in source["review"].get("rule_packs", [])]
    pack_ids = declared_ids or next(
        requirement["rule_packs"]
        for requirement in profile["project"]["review_requirements"]
        if requirement["kind"] == source["review"]["kind"]
    )
    packs = load_rule_packs(pack_ids, local_rule_pack_roots(root))
    rules = expected_rules(packs)
    rule_versions = {
        rule["id"]: record["payload"]["version"]
        for record in packs.values()
        for rule in record["payload"]["rules"]
    }
    rule_qualities = {
        rule["id"]: rule["quality_attributes"]
        for record in packs.values()
        for rule in record["payload"]["rules"]
    }
    source_findings = {item["id"]: item for item in source["findings"]}
    migrated_findings: list[dict[str, Any]] = []
    for finding in source_findings.values():
        if finding["rule_id"] not in rule_versions:
            raise MigrationError(
                f"Finding {finding['id']} references rule "
                f"{finding['rule_id']} outside the effective Rule Packs"
            )
        migrated: dict[str, Any] = dict(finding)
        migrated["verification"] = {
            "status": "candidate",
            "rationale": (
                "Migrated from a legacy artifact; independent 1.2 verification "
                "and candidate hash binding are required."
            ),
        }
        migrated["status"] = finding["status"]
        migrated["evidence_level"] = (
            "E4"
            if any(item["type"] == "runtime" for item in migrated["evidence"])
            else "E2"
        )
        migrated["evidence_fingerprint"] = canonical_sha256(migrated["evidence"])
        migrated["fact_inference_boundary"] = {
            "facts": [item["observation"] for item in migrated["evidence"]],
            "inferences": [
                migrated["impact"]["failure_mode"],
                migrated["impact"]["blast_radius"],
            ],
            "unknowns": ["Legacy verification has not been re-established under 1.2."],
        }
        migrated["applicability"] = {
            "profile_conditions": [
                "Applies only while the cited invariant and project profile "
                "remain current."
            ]
        }
        migrated["source_candidate_ids"] = [source["review"]["id"]]
        migrated["rule_pack_version"] = rule_versions[migrated["rule_id"]]
        migrated["staleness_state"] = (
            "current"
            if source["review"].get("commit") == facts["repository"]["commit"]
            else "stale"
        )
        migrated.setdefault(
            "quality_attribute_impacts",
            rule_qualities[migrated["rule_id"]],
        )
        migrated.setdefault("decision_references", [])
        migrated.pop("fingerprint", None)
        migrated["fingerprint"] = finding_fingerprint(
            profile["project"]["id"],
            migrated,
        )
        migrated_findings.append(migrated)

    source_coverage = {item["rule_id"]: item for item in source["coverage"]}
    coverage: list[dict[str, Any]] = []
    for rule_id in sorted(rules):
        if rule_id in source_coverage:
            item = dict(source_coverage[rule_id])
            if item["status"] == "assessed":
                item["status"] = "not_assessed"
                item["reason"] = (
                    "Legacy coverage had no independently resolvable per-rule "
                    "evidence binding."
                )
        else:
            item = {
                "rule_id": rule_id,
                "status": "not_assessed",
                "finding_ids": [],
                "reason": "Rule was added or selected after the legacy review.",
            }
        coverage.append(item)

    selected_knowledge = [
        {
            "id": item["id"],
            "version": item["version"],
            "sha256": item["sha256"],
        }
        for item in selection["selection"]
    ]
    scope_manifest = source["review"].get(
        "scope_manifest",
        source["review"]["scope"],
    )
    migrated = {
        "schema_version": "1.2",
        "review": {
            "id": source["review"]["id"] + "-migrated",
            "kind": source["review"]["kind"],
            "workflow": source["review"].get(
                "workflow",
                WORKFLOW_BY_KIND[source["review"]["kind"]],
            ),
            "subject": source["review"]["subject"],
            "performed_at": source["review"]["performed_at"],
            "commit": facts["repository"]["commit"],
            "scope": source["review"]["scope"],
            "verification_state": "candidates",
            "reviewers": source["review"]["reviewers"],
            "profile": ".architecture/profile.yaml",
            "repository_identity": profile["project"]["id"],
            "profile_sha256": file_sha256(profile_path),
            "dirty_tree": not git_is_clean(root),
            "rule_packs": [
                {
                    "id": pack_id,
                    "version": record["payload"]["version"],
                    "sha256": file_sha256(record["path"]),
                }
                for pack_id, record in sorted(packs.items())
            ],
            "scope_manifest": scope_manifest,
        },
        "summary": {
            "architecture": source["summary"]["architecture"],
            "raw_findings": len(migrated_findings),
            "confirmed": 0,
            "rejected": 0,
            "needs_evidence": 0,
        },
        "coverage": coverage,
        "findings": migrated_findings,
        "evidence_sources": source["evidence_sources"],
        "limitations": sorted(
            set(source["limitations"])
            | {
                "Legacy conclusions were deliberately downgraded to candidates.",
                "Independent 1.2 verification is required before enforcement.",
            }
        ),
        "coverage_complete": all(item["status"] != "not_assessed" for item in coverage),
        "repository_facts": {
            "path": facts_path.relative_to(root).as_posix(),
            "sha256": file_sha256(facts_path),
        },
        "knowledge_selection": {
            "path": selection_path.relative_to(root).as_posix(),
            "sha256": file_sha256(selection_path),
        },
        "selected_knowledge": selected_knowledge,
        "critical_flow_coverage": [
            {
                "id": flow_id,
                "status": "not_assessed",
                "reason": "Legacy review did not carry critical-flow coverage.",
            }
            for flow_id in critical_flow_ids(
                root / profile["project"]["critical_flows_file"]
            )
        ],
        "unknowns": [
            "Legacy verification identities and trust cannot be migrated.",
            "Critical-flow coverage requires a new audit pass.",
        ],
    }
    validation_path = _write_temporary_for_validation(root, migrated)
    try:
        validate_review(
            validation_path,
            rule_pack_ids=pack_ids,
            strict_trust=True,
            repository_root=root,
            allow_unverifiable_historical=True,
        )
    finally:
        validation_path.unlink(missing_ok=True)
    return migrated


def _validation_path(root: Path) -> Path:
    return root / ".architecture" / "reviews" / ".migration-validation.yaml"


def _write_temporary_for_validation(
    root: Path,
    payload: dict[str, Any],
) -> Path:
    path = _validation_path(root)
    if path.exists():
        raise MigrationError(f"Temporary validation path already exists: {path}")
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--knowledge-selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    review_path = args.review if args.review.is_absolute() else root / args.review
    facts_path = args.facts if args.facts.is_absolute() else root / args.facts
    selection_path = (
        args.knowledge_selection
        if args.knowledge_selection.is_absolute()
        else root / args.knowledge_selection
    )
    output = args.output if args.output.is_absolute() else root / args.output
    try:
        if output.exists():
            raise MigrationError(f"Refusing to overwrite existing output: {output}")
        migrated = migrate_review(
            review_path,
            project_root=root,
            facts_path=facts_path,
            selection_path=selection_path,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            yaml.safe_dump(migrated, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except (ArchitectureError, MigrationError, OSError, yaml.YAMLError) as exc:
        print(f"Review migration failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"Migrated candidate review written: {output}. "
        "Independent verification is still required."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
