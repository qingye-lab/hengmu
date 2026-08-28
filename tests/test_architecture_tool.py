from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import date
from pathlib import Path
from unittest.mock import patch

import yaml

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "resources" / "scripts" / "architecture_tool.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("architecture_tool", SCRIPT_PATH)
assert SPEC and SPEC.loader
architecture_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(architecture_tool)


def as_legacy_remediation_decision(
    decision: dict,
    schema_version: str,
) -> dict:
    """Remove current Greenfield-only fields when exercising a legacy contract."""
    decision["schema_version"] = schema_version
    for field in (
        "decision_kind",
        "architecture_intent",
        "source_context",
        "source_context_sha256",
    ):
        decision["decision"].pop(field, None)
    decision.pop("target_architecture", None)
    decision.pop("hard_eliminations", None)
    for option in decision["options"]:
        option.pop("constraint_assessments", None)
        option["architecture_styles"] = []
        option["patterns"] = []
        option["technologies"] = []
    return decision


def as_legacy_remediation_plan(plan: dict, schema_version: str) -> dict:
    """Remove Greenfield-only source and target fields for legacy plan tests."""
    plan["schema_version"] = schema_version
    for field in ("plan_kind", "source_context", "source_context_sha256"):
        plan["plan"].pop(field, None)
    for item in plan["items"]:
        item.pop("target_bindings", None)
        item["acceptance_evidence_types"] = ["test", "operational"]
    return plan


class ArchitectureToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_repository_rename_preserves_historical_source_identity(self) -> None:
        old = "https://github.com/liyanqing90/codex-architecture-governance.git"
        intermediate = "https://github.com/liyanqing90/hengmu"
        new = "git@github.com:qingye-lab/hengmu.git"
        self.assertEqual(
            architecture_tool.normalize_git_repository(old),
            architecture_tool.normalize_git_repository(new),
        )
        self.assertEqual(
            architecture_tool.normalize_git_repository(intermediate),
            architecture_tool.normalize_git_repository(new),
        )
        self.assertNotEqual(
            architecture_tool.normalize_git_repository(
                "https://github.com/liyanqing90/another-project"
            ),
            architecture_tool.normalize_git_repository(new),
        )
        self.assertTrue(
            architecture_tool.repository_identities_match(
                "codex-architecture-governance",
                "hengmu",
            )
        )
        self.assertFalse(
            architecture_tool.repository_identities_match(
                "another-project",
                "hengmu",
            )
        )

    def project_args(self) -> Namespace:
        return Namespace(
            repo=str(self.root),
            name="Test Project",
            project_id="test-project",
            types=["service"],
            lifecycle="active",
            criticality="high",
            owners=["test-owner"],
            qualities=["recoverability"],
            reviews=["project-architecture"],
            rule_packs=["project-core"],
            data_classification="internal",
        )

    def portfolio_args(self) -> Namespace:
        return Namespace(
            root=str(self.root),
            name="Test Portfolio",
            portfolio_id="test-portfolio",
            owners=["test-owner"],
            review_horizon_months=12,
        )

    def finding(
        self,
        verification: str = "confirmed",
        status: str = "open",
    ) -> dict:
        rationale = (
            "Direct source and caller evidence confirm the path."
            if verification == "confirmed"
            else "The candidate cannot yet be confirmed from current evidence."
        )
        return {
            "id": "TEST-DATA-001",
            "kind": "risk",
            "rule_id": "PROJECT.DATA.001",
            "title": "Conflicting authoritative writers",
            "invariant": "One component owns authoritative writes for this record.",
            "severity": "high",
            "confidence": 0.9,
            "verification": {
                "status": verification,
                "rationale": rationale,
                "verified_by": "test-verifier",
                "verified_at": "2026-07-28T10:00:00+00:00",
            },
            "status": status,
            "evidence": [
                {
                    "type": "source",
                    "location": "src/store.py:42",
                    "symbol": "save",
                    "observation": "Two independent writers update the same record.",
                    "source_commit": "deadbeef",
                }
            ],
            "impact": {
                "affected_components": ["store", "worker"],
                "failure_mode": "Concurrent updates can silently overwrite state.",
                "blast_radius": "All records processed by both writers.",
            },
            "counter_evidence": [],
            "first_seen": "2026-07-28",
            "last_seen": "2026-07-28",
            "found_by": ["architecture-auditor"],
            "tags": ["data-ownership"],
        }

    def review(self, verification: str = "confirmed") -> dict:
        finding_status = "rejected" if verification == "rejected" else "open"
        finding = self.finding(verification=verification, status=finding_status)
        counts = {
            "confirmed": int(verification == "confirmed"),
            "rejected": int(verification == "rejected"),
            "needs_evidence": int(verification == "needs-evidence"),
        }
        return {
            "schema_version": "1.0",
            "review": {
                "id": "2026-07-28-test-project",
                "kind": "project",
                "subject": {
                    "id": "test-project",
                    "name": "Test Project",
                    "repository": str(self.root),
                },
                "performed_at": "2026-07-28T10:00:00+00:00",
                "commit": "deadbeef",
                "scope": ["."],
                "verification_state": "verified",
                "reviewers": ["test-verifier"],
                "profile": ".architecture/profile.yaml",
            },
            "summary": {
                "architecture": "A test service with two writers.",
                "raw_findings": 1,
                **counts,
            },
            "coverage": [
                {
                    "rule_id": "PROJECT.DATA.001",
                    "status": "assessed",
                    "finding_ids": ["TEST-DATA-001"],
                }
            ],
            "findings": [finding],
            "evidence_sources": ["src/store.py"],
            "limitations": [],
        }

    def write_yaml(self, path: Path, value: dict) -> None:
        path.write_text(
            yaml.safe_dump(value, sort_keys=False),
            encoding="utf-8",
        )

    def init_project(self) -> Path:
        target = architecture_tool.init_project(self.project_args())
        policy_path = target / "gate-policy.yaml"
        policy = architecture_tool.load_yaml(policy_path)
        policy["block"].update(
            {
                "freshness_strategy": "time-window",
                "require_clean_tree": False,
                "require_evidence_resolution": False,
            }
        )
        self.write_yaml(policy_path, policy)
        subprocess.run(
            ["git", "init", "-q", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Test User"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "config",
                "user.email",
                "test@example.invalid",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", ".architecture"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Initialize test project"],
            check=True,
        )
        return target

    def write_review(
        self,
        review: dict | None = None,
        *,
        portfolio: bool = False,
    ) -> Path:
        supplied = copy.deepcopy(review or self.review())
        verification = supplied["findings"][0]["verification"]["status"]
        if portfolio:
            config_root = self.root / ".architecture-portfolio"
            profile_path = config_root / "portfolio.yaml"
            pack_id = "portfolio-core"
            subject_id = "test-portfolio"
            verifier_identity = "portfolio-verifier"
            auditor_identity = "portfolio-auditor"
            kind = "portfolio"
            workflow = "portfolio-architecture"
            finding_rule = "PORTFOLIO.DATA.001"
            commits = {"project-a": "deadbeef", "project-b": "cafebabe"}
        else:
            config_root = self.root / ".architecture"
            profile_path = config_root / "profile.yaml"
            pack_id = "project-core"
            subject_id = "test-project"
            verifier_identity = "architecture-verifier"
            auditor_identity = "architecture-auditor"
            kind = "project"
            workflow = "project-architecture"
            finding_rule = "PROJECT.DATA.001"
            commits = None

        reviews = config_root / "reviews"
        supplied["findings"][0]["rule_id"] = finding_rule
        if verification == "rejected":
            supplied["findings"][0]["status"] = "rejected"

        candidate = copy.deepcopy(supplied)
        candidate["schema_version"] = "1.0"
        candidate["review"].update(
            {
                "id": f"2026-07-28-{subject_id}-candidate",
                "kind": kind,
                "subject": {
                    "id": subject_id,
                    "name": "Test Portfolio" if portfolio else "Test Project",
                },
                "verification_state": "candidates",
                "profile": profile_path.relative_to(self.root).as_posix(),
            }
        )
        if portfolio:
            candidate["review"].pop("commit", None)
            candidate["review"]["commits"] = commits
        else:
            candidate["review"]["commit"] = architecture_tool.current_git_commit(
                self.root
            )
        candidate_finding = candidate["findings"][0]
        candidate_finding["found_by"] = [auditor_identity]
        candidate_finding["verification"] = {
            "status": "candidate",
            "rationale": "Awaiting independent verification.",
        }
        candidate_finding["status"] = "open"
        candidate["summary"].update(
            {
                "raw_findings": 1,
                "confirmed": 0,
                "rejected": 0,
                "needs_evidence": 0,
            }
        )
        candidate["coverage"] = [
            {
                "rule_id": finding_rule,
                "status": "assessed",
                "finding_ids": ["TEST-DATA-001"],
            }
        ]
        candidate_path = reviews / f"2026-07-28-{kind}-candidates.yaml"
        self.write_yaml(candidate_path, candidate)
        candidate_hash = architecture_tool.file_sha256(candidate_path)

        pack_path = ROOT / "resources" / "rules" / f"{pack_id}.yaml"
        pack = architecture_tool.load_yaml(pack_path)
        verified = copy.deepcopy(supplied)
        verified["schema_version"] = "1.1"
        verified["review"].update(
            {
                "id": f"2026-07-28-{subject_id}-verified",
                "kind": kind,
                "workflow": workflow,
                "subject": candidate["review"]["subject"],
                "repository_identity": subject_id,
                "profile": profile_path.relative_to(self.root).as_posix(),
                "profile_sha256": architecture_tool.file_sha256(profile_path),
                "dirty_tree": False,
                "rule_packs": [
                    {
                        "id": pack_id,
                        "version": pack["version"],
                        "sha256": architecture_tool.file_sha256(pack_path),
                    }
                ],
                "scope_manifest": ["."],
                "verification_state": "verified",
                "reviewers": [verifier_identity],
                "verification_run": {
                    "id": "test-verification-run",
                    "surface": "pytest",
                    "started_at": "2026-07-28T09:59:00+00:00",
                    "completed_at": "2026-07-28T10:00:00+00:00",
                },
                "source_candidate": {
                    "path": candidate_path.relative_to(self.root).as_posix(),
                    "review_id": candidate["review"]["id"],
                    "sha256": candidate_hash,
                },
            }
        )
        if portfolio:
            verified["review"].pop("commit", None)
            verified["review"]["commits"] = commits
        else:
            verified["review"]["commit"] = architecture_tool.current_git_commit(
                self.root
            )

        verified_finding = verified["findings"][0]
        verified_finding["found_by"] = [auditor_identity]
        verified_finding["verification"] = {
            "status": verification,
            "rationale": supplied["findings"][0]["verification"]["rationale"],
            "verified_by": verifier_identity,
            "verified_at": "2026-07-28T10:00:00+00:00",
            "level": "V2",
            "verifier": {
                "type": "agent",
                "identity": verifier_identity,
                "run_id": "test-verification-run",
            },
            "source_candidate": {
                "review_id": candidate["review"]["id"],
                "sha256": candidate_hash,
            },
        }
        verified_finding["fingerprint"] = architecture_tool.finding_fingerprint(
            subject_id,
            verified_finding,
        )
        verified["summary"].update(
            {
                "raw_findings": 1,
                "confirmed": int(verification == "confirmed"),
                "rejected": int(verification == "rejected"),
                "needs_evidence": int(verification == "needs-evidence"),
            }
        )
        verified["coverage"] = [
            {
                "rule_id": rule["id"],
                "status": "assessed",
                "finding_ids": (
                    ["TEST-DATA-001"] if rule["id"] == finding_rule else []
                ),
            }
            for rule in pack["rules"]
        ]
        verified["coverage_complete"] = True
        path = reviews / f"2026-07-28-{kind}-verified.yaml"
        self.write_yaml(path, verified)
        return path

    def test_init_and_validate_project(self) -> None:
        target = self.init_project()
        self.assertEqual(target, self.root / ".architecture")
        self.assertTrue((target / "runs").is_dir())
        validated = architecture_tool.validate_project(self.root)
        self.assertEqual(len(validated), 8)
        with self.assertRaises(architecture_tool.ArchitectureError):
            architecture_tool.init_project(self.project_args())

    def test_prepare_project_audit_initializes_from_facts_and_is_idempotent(
        self,
    ) -> None:
        (self.root / "pyproject.toml").write_text(
            "[project]\n"
            'name = "prepared-service"\n'
            'version = "0.1.0"\n'
            'dependencies = ["fastapi>=0.100"]\n',
            encoding="utf-8",
        )
        args = Namespace(
            repo=str(self.root),
            name=None,
            project_id=None,
        )

        target, initialized = architecture_tool.prepare_project_audit(args)

        self.assertTrue(initialized)
        self.assertEqual(target, self.root / ".architecture")
        profile = architecture_tool.load_yaml(target / "profile.yaml")
        self.assertIn("service", profile["project"]["type"])
        self.assertIn("backend-api", profile["project"]["rule_packs"])
        self.assertEqual(
            profile["project"]["repository_facts"]["path"],
            ".architecture/repository-facts.yaml",
        )
        original = {
            path.relative_to(target).as_posix(): path.read_bytes()
            for path in target.rglob("*")
            if path.is_file()
        }

        reused, initialized_again = architecture_tool.prepare_project_audit(args)

        self.assertFalse(initialized_again)
        self.assertEqual(reused, target)
        self.assertEqual(
            original,
            {
                path.relative_to(target).as_posix(): path.read_bytes()
                for path in target.rglob("*")
                if path.is_file()
            },
        )

    def test_prepare_project_audit_refuses_partial_existing_control_plane(
        self,
    ) -> None:
        target = self.root / ".architecture"
        target.mkdir()
        marker = target / "user-owned.txt"
        marker.write_text("preserve me\n", encoding="utf-8")
        args = Namespace(
            repo=str(self.root),
            name=None,
            project_id=None,
        )

        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "Missing file",
        ):
            architecture_tool.prepare_project_audit(args)

        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve me\n")

    def test_legacy_review_migration_downgrades_trust_and_binds_inputs(
        self,
    ) -> None:
        config_root = self.init_project()
        critical_flows = config_root / "critical-flows.md"
        critical_flows.write_text(
            "# Critical flows\n\n"
            "## Durable save\n\n"
            "A write must reach its authoritative owner exactly once.\n",
            encoding="utf-8",
        )
        profile_path = config_root / "profile.yaml"
        facts_path = config_root / "repository-facts.yaml"
        selection = architecture_tool.select_knowledge(
            facts_path,
            profile_path=profile_path,
            task="Migrate the project architecture review contract.",
            skill="project-architecture-audit",
            maximum_entries=16,
        )
        selection_path = config_root / "knowledge-selection.yaml"
        self.write_yaml(selection_path, selection)
        review_path = self.write_review()
        migrated_path = config_root / "reviews" / "migrated-candidates.yaml"

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "resources" / "scripts" / "migrate_artifacts.py"),
                "--project",
                str(self.root),
                "--review",
                str(review_path),
                "--facts",
                str(facts_path),
                "--knowledge-selection",
                str(selection_path),
                "--output",
                str(migrated_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        migrated = architecture_tool.load_yaml(migrated_path)
        self.assertEqual(migrated["schema_version"], "1.2")
        self.assertEqual(migrated["review"]["verification_state"], "candidates")
        self.assertTrue(
            all(
                finding["verification"]["status"] == "candidate"
                for finding in migrated["findings"]
            )
        )
        self.assertEqual(migrated["findings"][0]["status"], "open")
        self.assertIn(
            "Legacy conclusions were deliberately downgraded to candidates.",
            migrated["limitations"],
        )
        architecture_tool.validate_review(
            migrated_path,
            rule_pack_ids=["project-core"],
            strict_trust=True,
            repository_root=self.root,
        )

        resolved_source = architecture_tool.load_yaml(review_path)
        resolved_source["review"]["id"] = "test-project-resolved-source"
        resolved_source["findings"][0]["status"] = "resolved"
        resolved_source_path = config_root / "reviews" / "resolved-source.yaml"
        self.write_yaml(resolved_source_path, resolved_source)
        resolved_migrated_path = config_root / "reviews" / "resolved-migrated.yaml"
        resolved_result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "resources" / "scripts" / "migrate_artifacts.py"),
                "--project",
                str(self.root),
                "--review",
                str(resolved_source_path),
                "--facts",
                str(facts_path),
                "--knowledge-selection",
                str(selection_path),
                "--output",
                str(resolved_migrated_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(resolved_result.returncode, 0, resolved_result.stderr)
        resolved_migrated = architecture_tool.load_yaml(resolved_migrated_path)
        self.assertEqual(resolved_migrated["findings"][0]["status"], "resolved")

        overwrite_result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "resources" / "scripts" / "migrate_artifacts.py"),
                "--project",
                str(self.root),
                "--review",
                str(review_path),
                "--facts",
                str(facts_path),
                "--knowledge-selection",
                str(selection_path),
                "--output",
                str(migrated_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(overwrite_result.returncode, 2)
        self.assertIn("Refusing to overwrite", overwrite_result.stderr)

        validation_sentinel = config_root / "reviews" / ".migration-validation.yaml"
        validation_sentinel.write_text("preserve: user-owned\n", encoding="utf-8")
        sentinel_output = config_root / "reviews" / "sentinel-migrated.yaml"
        sentinel_result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "resources" / "scripts" / "migrate_artifacts.py"),
                "--project",
                str(self.root),
                "--review",
                str(review_path),
                "--facts",
                str(facts_path),
                "--knowledge-selection",
                str(selection_path),
                "--output",
                str(sentinel_output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(sentinel_result.returncode, 2)
        self.assertIn(
            "Temporary validation path already exists", sentinel_result.stderr
        )
        self.assertEqual(
            validation_sentinel.read_text(encoding="utf-8"),
            "preserve: user-owned\n",
        )
        self.assertFalse(sentinel_output.exists())

        unsupported_assessment = copy.deepcopy(migrated)
        unsupported_assessment["coverage"][0].update({"status": "assessed"})
        unsupported_assessment["coverage"][0].pop("reason", None)
        unsupported_path = config_root / "reviews" / "unsupported-coverage.yaml"
        self.write_yaml(unsupported_path, unsupported_assessment)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "assessed coverage .* has no evidence",
        ):
            architecture_tool.validate_review(
                unsupported_path,
                rule_pack_ids=["project-core"],
                strict_trust=True,
                repository_root=self.root,
            )

        stale_selection = copy.deepcopy(migrated)
        stale_selection["selected_knowledge"][0]["sha256"] = "0" * 64
        stale_path = config_root / "reviews" / "stale-selection.yaml"
        self.write_yaml(stale_path, stale_selection)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "selected_knowledge does not match",
        ):
            architecture_tool.validate_review(
                stale_path,
                rule_pack_ids=["project-core"],
                strict_trust=True,
                repository_root=self.root,
            )

        missing_flow = copy.deepcopy(migrated)
        missing_flow["critical_flow_coverage"] = []
        missing_flow_path = config_root / "reviews" / "missing-flow.yaml"
        self.write_yaml(missing_flow_path, missing_flow)
        with self.assertRaises(architecture_tool.ArchitectureError):
            architecture_tool.validate_review(
                missing_flow_path,
                rule_pack_ids=["project-core"],
                strict_trust=True,
                repository_root=self.root,
            )

        verified = copy.deepcopy(migrated)
        verified["review"]["id"] = "test-project-12-verified"
        verified["review"]["verification_state"] = "verified"
        verified["review"]["reviewers"] = ["architecture-verifier"]
        verified["review"]["verification_run"] = {
            "id": "verification-12",
            "surface": "pytest",
            "started_at": "2026-07-28T09:59:00+00:00",
            "completed_at": "2026-07-28T10:01:00+00:00",
        }
        verified["review"]["source_candidate"] = {
            "path": migrated_path.relative_to(self.root).as_posix(),
            "review_id": migrated["review"]["id"],
            "sha256": architecture_tool.file_sha256(migrated_path),
        }
        verified["summary"]["confirmed"] = len(verified["findings"])
        for finding in verified["findings"]:
            finding["verification"] = {
                "status": "confirmed",
                "rationale": "Independent test verification confirmed the invariant.",
            }
        verified["critical_flow_coverage"] = [
            {"id": "durable-save", "status": "assessed"}
        ]
        verified_path = config_root / "reviews" / "verified-12.yaml"
        self.write_yaml(verified_path, verified)
        architecture_tool.validate_review(verified_path)

        _, knowledge = architecture_tool.validate_knowledge_tree(
            ROOT / "resources" / "knowledge",
            schema_root=ROOT / "resources" / "schemas",
            today=date(2026, 8, 6),
        )
        decision = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "architecture-decision.yaml"
        )
        as_legacy_remediation_decision(decision, "1.2")
        decision["decision"].update(
            {
                "id": "ADR-TEST-012",
                "source_review": verified["review"]["id"],
                "source_review_sha256": architecture_tool.file_sha256(verified_path),
                "status": "accepted",
                "decision_makers": ["architecture-owner"],
            }
        )
        decision["problem"]["finding_ids"] = ["TEST-DATA-001"]
        decision["knowledge_snapshot"] = [
            {
                "id": entry_id,
                "version": knowledge[entry_id].metadata["version"],
                "sha256": knowledge[entry_id].sha256,
            }
            for entry_id in (
                "style.modular-monolith",
                "pattern.feature-flag",
                "technology.import-linter",
                "migration.layered-monolith-to-modular",
            )
        ]
        decision_path = config_root / "reviews" / "decision-12.yaml"
        self.write_yaml(decision_path, decision)
        architecture_tool.validate_decision(
            decision_path,
            review_path=verified_path,
            require_accepted=True,
        )

        plan = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "remediation-plan.yaml"
        )
        as_legacy_remediation_plan(plan, "1.2")
        plan["plan"]["plan_kind"] = "remediation"
        plan["plan"].update(
            {
                "source_review": verified["review"]["id"],
                "source_review_sha256": architecture_tool.file_sha256(verified_path),
                "source_decision": decision["decision"]["id"],
                "source_decision_sha256": architecture_tool.file_sha256(decision_path),
            }
        )
        plan["items"][0]["finding_ids"] = ["TEST-DATA-001"]
        plan["items"][0]["finding_bindings"] = [
            {
                "id": "TEST-DATA-001",
                "fingerprint": verified["findings"][0]["fingerprint"],
            }
        ]
        plan["items"][0]["knowledge_ids"] = [decision["knowledge_snapshot"][0]["id"]]
        plan_path = config_root / "reviews" / "plan-12.yaml"
        self.write_yaml(plan_path, plan)
        architecture_tool.validate_plan(
            plan_path,
            review_path=verified_path,
            decision_path=decision_path,
        )

        stale_plan = copy.deepcopy(plan)
        stale_plan["items"][0]["finding_bindings"][0]["fingerprint"] = "0" * 64
        stale_plan_path = config_root / "reviews" / "stale-plan-12.yaml"
        self.write_yaml(stale_plan_path, stale_plan)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "stale fingerprint",
        ):
            architecture_tool.validate_plan(
                stale_plan_path,
                review_path=verified_path,
                decision_path=decision_path,
            )

        unknown_knowledge = copy.deepcopy(plan)
        unknown_knowledge["items"][0]["knowledge_ids"] = ["technology.not-selected"]
        unknown_path = config_root / "reviews" / "unknown-knowledge-12.yaml"
        self.write_yaml(unknown_path, unknown_knowledge)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "knowledge absent",
        ):
            architecture_tool.validate_plan(
                unknown_path,
                review_path=verified_path,
                decision_path=decision_path,
            )

    def test_selection_v14_binds_runtime_manifest_and_current_replay(
        self,
    ) -> None:
        config_root = self.init_project()
        facts_path = config_root / "repository-facts.yaml"
        profile_path = config_root / "profile.yaml"
        selection = architecture_tool.select_knowledge(
            facts_path,
            profile_path=profile_path,
            task="Review a bounded project architecture.",
            skill="project-architecture-audit",
            maximum_entries=16,
            kind_budgets={"foundation": 6, "domain": 2},
        )
        selection_path = config_root / "selection-v14.yaml"
        self.write_yaml(selection_path, selection)
        architecture_tool.validate_knowledge_selection_artifact(
            selection_path,
            facts_path=facts_path,
            profile_path=profile_path,
        )
        self.assertEqual(selection["schema_version"], "1.4")
        self.assertEqual(selection["selector"]["contract_version"], "1.1")
        self.assertEqual(
            selection["selector"]["replay_mode"],
            "creation-time-lock",
        )
        self.assertEqual(selection["inputs"]["decision_intents"], [])
        self.assertEqual(
            selection["inputs"]["project_commit"],
            architecture_tool.load_yaml(facts_path)["repository"]["commit"],
        )
        self.assertNotIn("source_commit", selection["inputs"])
        self.assertEqual(
            [item["path"] for item in selection["selector"]["implementation_inputs"]],
            list(architecture_tool.SELECTOR_IMPLEMENTATION_PATHS),
        )
        self.assertNotEqual(
            selection["selector"]["source"]["commit"],
            selection["inputs"]["project_commit"],
        )

        maturity_tampered = copy.deepcopy(selection)
        maturity_tampered["selection"][0]["maturity"] = "golden"
        maturity_tampered["result_sha256"] = architecture_tool.selection_result_sha256(
            maturity_tampered
        )
        maturity_path = config_root / "selection-maturity-tampered.yaml"
        self.write_yaml(maturity_path, maturity_tampered)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "maturity does not match bundled source",
        ):
            architecture_tool.validate_knowledge_selection_artifact(
                maturity_path,
                facts_path=facts_path,
                profile_path=profile_path,
            )

        count_tampered = copy.deepcopy(selection)
        count_tampered["budget"]["selected_entries"] += 1
        count_tampered["result_sha256"] = architecture_tool.selection_result_sha256(
            count_tampered
        )
        count_path = config_root / "selection-count-tampered.yaml"
        self.write_yaml(count_path, count_tampered)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "selected_entries does not match selection",
        ):
            architecture_tool.validate_knowledge_selection_artifact(
                count_path,
                facts_path=facts_path,
                profile_path=profile_path,
            )

        binding_tampered = copy.deepcopy(selection)
        binding_tampered["selector"]["implementation_inputs"][0]["sha256"] = "0" * 64
        binding_path = config_root / "selection-binding-tampered.yaml"
        self.write_yaml(binding_path, binding_tampered)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "result_sha256 does not match",
        ):
            architecture_tool.validate_knowledge_selection_artifact(
                binding_path,
                facts_path=facts_path,
                profile_path=profile_path,
            )

        historical = copy.deepcopy(selection)
        historical["selector"]["implementation_inputs"][0]["sha256"] = "0" * 64
        historical["selector"]["implementation_bundle_sha256"] = (
            architecture_tool.canonical_sha256(
                historical["selector"]["implementation_inputs"]
            )
        )
        historical["selection"][0]["maturity"] = (
            "golden"
            if historical["selection"][0]["maturity"] == "standard"
            else "standard"
        )
        historical["result_sha256"] = architecture_tool.selection_result_sha256(
            historical
        )
        historical_path = config_root / "selection-historical.yaml"
        self.write_yaml(historical_path, historical)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "historical input hash does not match",
        ):
            architecture_tool.validate_knowledge_selection_artifact(
                historical_path,
                facts_path=facts_path,
                profile_path=profile_path,
            )
        readable_historical = architecture_tool.validate_knowledge_selection_artifact(
            historical_path,
            facts_path=facts_path,
            profile_path=profile_path,
            require_trusted_runtime=False,
        )
        self.assertNotEqual(
            readable_historical["selection"][0]["maturity"],
            selection["selection"][0]["maturity"],
        )

        legacy = copy.deepcopy(selection)
        legacy["schema_version"] = "1.1"
        legacy.pop("selector")
        legacy.pop("result_sha256")
        legacy["inputs"].pop("decision_intents")
        legacy["inputs"].pop("project_commit")
        legacy_path = config_root / "selection-legacy.yaml"
        self.write_yaml(legacy_path, legacy)
        self.assertEqual(
            architecture_tool.validate_knowledge_selection_artifact(legacy_path)[
                "schema_version"
            ],
            "1.1",
        )

    def test_selection_v14_verifies_archived_runtime_without_executing_it(
        self,
    ) -> None:
        config_root = self.init_project()
        facts_path = config_root / "repository-facts.yaml"
        profile_path = config_root / "profile.yaml"
        selection = architecture_tool.select_knowledge(
            facts_path,
            profile_path=profile_path,
            task="Verify an archived Selector Runtime Manifest.",
            skill="project-architecture-audit",
            maximum_entries=16,
            kind_budgets={"foundation": 6, "domain": 2},
        )

        source_root = self.root / "selector-source"
        for relative_path in (
            *architecture_tool.SELECTOR_IMPLEMENTATION_PATHS,
            ".codex-plugin/plugin.json",
        ):
            source = ROOT / relative_path
            destination = source_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        shutil.copytree(
            ROOT / "resources" / "knowledge",
            source_root / "resources" / "knowledge",
        )
        subprocess.run(["git", "init", "-q", str(source_root)], check=True)
        subprocess.run(
            ["git", "-C", str(source_root), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "config",
                "user.email",
                "test@example.com",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "remote",
                "add",
                "origin",
                selection["selector"]["source"]["repository"],
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(source_root), "add", "."],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(source_root), "commit", "-qm", "runtime"],
            check=True,
        )
        commit = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        selection["selector"]["source"]["commit"] = commit
        selection["result_sha256"] = architecture_tool.selection_result_sha256(
            selection
        )
        selection_path = config_root / "selection-archived.yaml"
        self.write_yaml(selection_path, selection)
        with patch.dict(
            "os.environ",
            {"CAG_SELECTOR_SOURCE_ROOT": str(source_root)},
        ):
            validated = architecture_tool.validate_knowledge_selection_artifact(
                selection_path,
                facts_path=facts_path,
                profile_path=profile_path,
            )
            with self.assertRaisesRegex(
                architecture_tool.ArchitectureError,
                "uses an archived Selector Runtime lock",
            ):
                architecture_tool.validate_knowledge_selection_artifact(
                    selection_path,
                    facts_path=facts_path,
                    profile_path=profile_path,
                    require_current_runtime=True,
                )
        self.assertEqual(validated["selector"]["source"]["commit"], commit)

    def test_select_knowledge_cli_passes_decision_intent(self) -> None:
        config_root = self.init_project()
        output = config_root / "decision-intent-selection.yaml"
        context_output = config_root / "decision-intent-context.yaml"
        args = architecture_tool.build_parser().parse_args(
            [
                "select-knowledge",
                "--facts",
                str(config_root / "repository-facts.yaml"),
                "--profile",
                str(config_root / "profile.yaml"),
                "--task",
                "Preserve the local-first plugin runtime.",
                "--skill",
                "architecture-solution-advisor",
                "--decision-intent",
                "plugin-runtime-topology",
                "--max-entries",
                "16",
                "--output",
                str(output),
                "--context-output",
                str(context_output),
            ]
        )

        self.assertEqual(architecture_tool.run(args), 0)
        selection = architecture_tool.load_yaml(output)
        self.assertEqual(
            selection["inputs"]["decision_intents"],
            ["plugin-runtime-topology"],
        )
        selected = {item["id"] for item in selection["selection"]}
        self.assertIn("style.plugin-architecture", selected)
        self.assertNotIn("decision.local-first-vs-server-first", selected)
        context = architecture_tool.load_yaml(context_output)
        self.assertEqual(
            context["selection_lock_sha256"],
            architecture_tool.file_sha256(output),
        )
        self.assertEqual(
            {item["id"] for item in context["selected"]},
            selected,
        )
        self.assertNotIn("excluded", context)
        architecture_tool.validate_knowledge_context_artifact(
            context_output,
            output,
            facts_path=config_root / "repository-facts.yaml",
            profile_path=config_root / "profile.yaml",
        )

        context_mutations = {
            "selection lock": lambda value: value.update(
                {"selection_lock_sha256": "0" * 64}
            ),
            "selection result": lambda value: value.update(
                {"selection_result_sha256": "0" * 64}
            ),
            "selected projection": lambda value: value["selected"][0].update(
                {"priority": "optional"}
            ),
            "selected order": lambda value: value["selected"].reverse(),
        }
        for label, mutate in context_mutations.items():
            with self.subTest(label=label):
                tampered = copy.deepcopy(context)
                mutate(tampered)
                self.write_yaml(context_output, tampered)
                with self.assertRaises(architecture_tool.ArchitectureError):
                    architecture_tool.validate_knowledge_context_artifact(
                        context_output,
                        output,
                        facts_path=config_root / "repository-facts.yaml",
                        profile_path=config_root / "profile.yaml",
                    )
        self.write_yaml(context_output, context)

        validate_context_args = architecture_tool.build_parser().parse_args(
            [
                "validate-knowledge-context",
                str(context_output),
                "--selection",
                str(output),
                "--facts",
                str(config_root / "repository-facts.yaml"),
                "--profile",
                str(config_root / "profile.yaml"),
            ]
        )
        self.assertEqual(architecture_tool.run(validate_context_args), 0)

    def test_history_anchors_require_reachable_selector_and_review_commits(
        self,
    ) -> None:
        config_root = self.init_project()
        manifest_path = self.root / ".codex-plugin" / "plugin.json"
        manifest_path.parent.mkdir()
        manifest_path.write_text(
            json.dumps({"name": "test-plugin", "version": "0.4.2"}),
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", str(manifest_path)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Add plugin manifest"],
            check=True,
        )
        anchor = architecture_tool.current_git_commit(self.root)
        selector_source_path = self.root / "resources" / "selector-source.json"
        selector_source_path.parent.mkdir(parents=True)
        selector_source_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "repository": (
                        "https://github.com/example/architecture-governance"
                    ),
                    "commit": anchor,
                    "plugin_version": "0.4.2",
                }
            ),
            encoding="utf-8",
        )
        review = self.review()
        review["review"]["commit"] = anchor
        review_path = config_root / "reviews" / "history-verified.yaml"
        self.write_yaml(review_path, review)
        subprocess.run(
            ["git", "-C", str(self.root), "add", "."],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Add history anchors"],
            check=True,
        )

        result = architecture_tool.validate_history_anchors(
            self.root,
            review_path,
        )
        self.assertEqual(result["selector_source"]["commit"], anchor)
        self.assertEqual(result["reviewed_implementation"]["commit"], anchor)

        selector_source = json.loads(selector_source_path.read_text(encoding="utf-8"))
        selector_source["plugin_version"] = "1.0.0"
        selector_source_path.write_text(
            json.dumps(selector_source),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "plugin version does not match",
        ):
            architecture_tool.validate_history_anchors(self.root, review_path)
        selector_source["plugin_version"] = "0.4.2"
        selector_source_path.write_text(
            json.dumps(selector_source),
            encoding="utf-8",
        )

        detached_branch = "unmerged-anchor"
        subprocess.run(
            ["git", "-C", str(self.root), "switch", "-qc", detached_branch, anchor],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "commit",
                "--allow-empty",
                "-qm",
                "Unmerged anchor",
            ],
            check=True,
        )
        unmerged = architecture_tool.current_git_commit(self.root)
        subprocess.run(
            ["git", "-C", str(self.root), "switch", "-q", "-"],
            check=True,
        )
        selector_source = json.loads(selector_source_path.read_text(encoding="utf-8"))
        selector_source["commit"] = unmerged
        selector_source_path.write_text(
            json.dumps(selector_source),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "is not an ancestor of HEAD",
        ):
            architecture_tool.validate_history_anchors(
                self.root,
                review_path,
            )

    def test_governance_run_is_informational_and_path_contained(self) -> None:
        config_root = self.init_project()
        run = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "governance-run-manifest.yaml"
        )
        run["run"].update(
            {
                "id": "GOV-RUN-TEST-001",
                "source": {
                    "repository": ".",
                    "commit": architecture_tool.current_git_commit(self.root),
                    "scope": ["."],
                },
                "tools_used": [
                    {
                        "id": "architecture-tool",
                        "path": "resources/scripts/architecture_tool.py",
                        "sha256": "a" * 64,
                    }
                ],
            }
        )
        runs = config_root / "runs"
        run_path = runs / "governance-run.yaml"
        self.write_yaml(run_path, run)
        validated = architecture_tool.validate_governance_run(
            run_path,
            project_root=self.root,
        )
        self.assertEqual(validated["run"]["trust"], "informational-only")

        backwards = copy.deepcopy(run)
        backwards["run"]["completed_at"] = "1969-12-31T23:59:59+00:00"
        backwards_path = runs / "backwards.yaml"
        self.write_yaml(backwards_path, backwards)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "completes before it starts",
        ):
            architecture_tool.validate_governance_run(
                backwards_path,
                project_root=self.root,
            )

        escaped = copy.deepcopy(run)
        escaped["run"]["tools_used"][0]["path"] = "../outside.py"
        escaped_path = runs / "escaped.yaml"
        self.write_yaml(escaped_path, escaped)
        with self.assertRaises(architecture_tool.ArchitectureError):
            architecture_tool.validate_governance_run(
                escaped_path,
                project_root=self.root,
            )

        # A run record has no Review envelope, so it cannot be smuggled into
        # the deterministic evidence chain by placing it among reviews.
        review_path = config_root / "reviews" / "governance-run.yaml"
        self.write_yaml(review_path, run)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "Unknown YAML artifact",
        ):
            architecture_tool.validate_project(self.root)

    def test_product_mode_is_descriptive_and_cannot_bypass_a_gate(self) -> None:
        config_root = self.init_project()
        review_path = self.write_review()
        policy_path = config_root / "gate-policy.yaml"
        policy = architecture_tool.load_yaml(policy_path)

        governed = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        policy["product_mode"] = "advisory"
        self.write_yaml(policy_path, policy)
        advisory_label = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(advisory_label["status"], governed["status"])
        self.assertEqual(advisory_label["blocking"], governed["blocking"])
        self.assertEqual(
            advisory_label["policy_failures"],
            governed["policy_failures"],
        )

        legacy_policy = copy.deepcopy(policy)
        legacy_policy["schema_version"] = "1.1"
        legacy_policy.pop("product_mode")
        self.write_yaml(policy_path, legacy_policy)
        legacy = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(legacy["status"], governed["status"])
        self.assertEqual(legacy["blocking"], governed["blocking"])
        self.assertEqual(legacy["policy_failures"], governed["policy_failures"])

    def test_project_can_load_a_repository_local_rule_pack(self) -> None:
        config_root = architecture_tool.init_project(self.project_args())
        local_pack_path = config_root / "rules" / "organization-boundary.yaml"
        local_pack = {
            "schema_version": "1.1",
            "id": "organization-boundary",
            "version": "1.0.0",
            "review_kind": "project",
            "rules": [
                {
                    "id": "ORG.BOUNDARY.001",
                    "domain": "organization-boundary",
                    "invariant": (
                        "Organization-owned capabilities expose an explicit "
                        "versioned contract."
                    ),
                    "evidence_requirements": [
                        "Owning team and versioned interface evidence"
                    ],
                }
            ],
        }
        self.write_yaml(local_pack_path, local_pack)
        profile_path = config_root / "profile.yaml"
        profile = architecture_tool.load_yaml(profile_path)
        profile["project"]["rule_packs"].append("organization-boundary")
        profile["project"]["review_requirements"][0]["rule_packs"].append(
            "organization-boundary"
        )
        self.write_yaml(profile_path, profile)

        validated = architecture_tool.validate_project(self.root)

        self.assertIn(profile_path, validated)

    def test_review_diff_reports_finding_and_coverage_changes(self) -> None:
        before = self.review()
        after = copy.deepcopy(before)
        after["review"]["id"] = "2026-07-29-test-project"
        after["review"]["performed_at"] = "2026-07-29T10:00:00+00:00"
        after["findings"][0]["status"] = "planned"
        after["findings"][0]["title"] = "Conflicting writers are scheduled for repair"
        after["coverage"][0]["reason"] = "Reassessed after remediation planning."
        before_path = self.root / "before.yaml"
        after_path = self.root / "after.yaml"
        self.write_yaml(before_path, before)
        self.write_yaml(after_path, after)

        result = architecture_tool.review_diff(before_path, after_path)

        self.assertEqual(result["summary"]["changed"], 1)
        self.assertEqual(result["summary"]["coverage_changed"], 1)
        self.assertEqual(
            result["changed"][0]["changed_fields"],
            ["title", "status"],
        )

    def test_repository_dogfood_configuration_is_valid(self) -> None:
        validated = {
            path.relative_to(ROOT).as_posix()
            for path in architecture_tool.validate_project(ROOT)
        }
        required = {
            ".architecture/profile.yaml",
            ".architecture/gate-policy.yaml",
            ".architecture/baseline.yaml",
            ".architecture/repository-facts.yaml",
            ".architecture/risk-acceptances.yaml",
            ".architecture/evidence-providers.yaml",
            ".architecture/constraints.md",
            ".architecture/critical-flows.md",
        }
        self.assertLessEqual(required, validated)
        self.assertTrue(
            any(path.endswith("-verified.yaml") for path in validated),
        )
        self.assertTrue(
            any(path.endswith("-architecture-decision.yaml") for path in validated),
        )

    def test_evidence_provider_run_binds_and_revalidates_output(self) -> None:
        self.init_project()
        config_path = self.root / ".architecture" / "evidence-providers.yaml"
        config = architecture_tool.load_yaml(config_path)
        provider = next(
            item for item in config["providers"] if item["id"] == "test-results"
        )
        provider.update(
            {
                "enabled": True,
                "command": [
                    sys.executable,
                    "-c",
                    'print(\'<testsuite name="provider" tests="1"/>\')',
                ],
                "allow_without_detection": True,
                "output_source": "stdout",
                "dependency_inputs": [".architecture/evidence-providers.yaml"],
                "cache_mode": "isolated",
            }
        )
        provider.pop("result_path", None)
        self.write_yaml(config_path, config)
        subprocess.run(
            ["git", "-C", str(self.root), "add", str(config_path)],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "commit",
                "-qm",
                "Configure evidence provider",
            ],
            check=True,
        )

        artifact_path, artifact = architecture_tool.run_evidence_provider(
            self.root,
            "test-results",
        )
        self.assertEqual(artifact["result"]["status"], "passed")
        validated = architecture_tool.validate_evidence_run(
            artifact_path,
            self.root,
            require_passed=True,
        )
        self.assertEqual(validated["run"]["provider_id"], "test-results")
        self.assertEqual(validated["schema_version"], "1.2")
        self.assertEqual(
            validated["run"]["dependency_inputs_before"],
            validated["run"]["dependency_inputs_after"],
        )

        mismatched_closure = copy.deepcopy(artifact)
        mismatched_closure["run"]["dependency_inputs_before"][0]["sha256"] = "0" * 64
        self.write_yaml(artifact_path, mismatched_closure)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "stable dependency closure",
        ):
            architecture_tool.validate_evidence_run(
                artifact_path,
                self.root,
                require_passed=True,
            )
        self.write_yaml(artifact_path, artifact)

        legacy_artifact = copy.deepcopy(artifact)
        legacy_artifact["schema_version"] = "1.0"
        legacy_artifact["run"].pop("completed_commit")
        legacy_artifact["run"].pop("dirty_tree_after")
        self.write_yaml(artifact_path, legacy_artifact)
        architecture_tool.validate_evidence_run(artifact_path, self.root)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "lacks post-run repository state",
        ):
            architecture_tool.validate_evidence_run(
                artifact_path,
                self.root,
                require_passed=True,
            )
        self.write_yaml(artifact_path, artifact)

        tampered_artifact = architecture_tool.load_yaml(artifact_path)
        tampered_artifact["result"]["exit_code"] = 1
        self.write_yaml(artifact_path, tampered_artifact)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "status does not match",
        ):
            architecture_tool.validate_evidence_run(
                artifact_path,
                self.root,
            )
        tampered_artifact["result"]["exit_code"] = 0
        self.write_yaml(artifact_path, tampered_artifact)

        stdout_path = self.root / validated["result"]["stdout"]["path"]
        stdout_path.write_bytes(stdout_path.read_bytes() + b"tamper")
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "byte count does not match",
        ):
            architecture_tool.validate_evidence_run(
                artifact_path,
                self.root,
            )

    def test_dirty_provider_run_is_informational_only(self) -> None:
        self.init_project()
        config_path = self.root / ".architecture" / "evidence-providers.yaml"
        config = architecture_tool.load_yaml(config_path)
        provider = next(
            item for item in config["providers"] if item["id"] == "test-results"
        )
        provider.update(
            {
                "enabled": True,
                "command": [
                    sys.executable,
                    "-c",
                    'print(\'<testsuite name="provider" tests="1"/>\')',
                ],
                "allow_without_detection": True,
                "output_source": "stdout",
                "dependency_inputs": [".architecture/evidence-providers.yaml"],
                "cache_mode": "isolated",
            }
        )
        provider.pop("result_path", None)
        self.write_yaml(config_path, config)

        artifact_path, artifact = architecture_tool.run_evidence_provider(
            self.root,
            "test-results",
        )
        self.assertTrue(artifact["run"]["dirty_tree"])
        architecture_tool.validate_evidence_run(artifact_path, self.root)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "dirty working tree",
        ):
            architecture_tool.validate_evidence_run(
                artifact_path,
                self.root,
                require_passed=True,
            )

    def test_provider_worktree_mutation_is_informational_only(self) -> None:
        self.init_project()
        config_path = self.root / ".architecture" / "evidence-providers.yaml"
        config = architecture_tool.load_yaml(config_path)
        provider = next(
            item for item in config["providers"] if item["id"] == "test-results"
        )
        provider.update(
            {
                "enabled": True,
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "target=Path('.architecture')/'constraints.md'; "
                        "target.write_text('changed\\n'); "
                        'print(\'<testsuite name="provider" tests="1"/>\')'
                    ),
                ],
                "allow_without_detection": True,
                "output_source": "stdout",
                "dependency_inputs": [".architecture/evidence-providers.yaml"],
                "cache_mode": "isolated",
            }
        )
        provider.pop("result_path", None)
        self.write_yaml(config_path, config)
        subprocess.run(
            ["git", "-C", str(self.root), "add", str(config_path)],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "commit",
                "-qm",
                "Configure mutating provider fixture",
            ],
            check=True,
        )

        artifact_path, artifact = architecture_tool.run_evidence_provider(
            self.root,
            "test-results",
        )
        self.assertFalse(artifact["run"]["dirty_tree"])
        self.assertTrue(artifact["run"]["dirty_tree_after"])
        architecture_tool.validate_evidence_run(artifact_path, self.root)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "changed the working tree during execution",
        ):
            architecture_tool.validate_evidence_run(
                artifact_path,
                self.root,
                require_passed=True,
            )

    def test_provider_config_rejects_installing_command_runners(self) -> None:
        self.init_project()
        config_path = self.root / ".architecture" / "evidence-providers.yaml"
        config = architecture_tool.load_yaml(config_path)
        config["providers"][0]["command"] = ["npx", "archunit"]
        self.write_yaml(config_path, config)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "forbidden package or shell runner npx",
        ):
            architecture_tool.validate_evidence_provider_config(config_path)

        config["providers"][0]["command"] = [
            "python3",
            "-m",
            "pip",
            "install",
            "archunit",
        ]
        self.write_yaml(config_path, config)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "may install tools",
        ):
            architecture_tool.validate_evidence_provider_config(config_path)

        for command in (
            ["corepack", "pnpm", "dlx", "archunit"],
            ["./run-provider.sh"],
            ["./run-provider.ksh"],
        ):
            config["providers"][0]["command"] = command
            self.write_yaml(config_path, config)
            with self.assertRaisesRegex(
                architecture_tool.ArchitectureError,
                "forbidden package or shell runner",
            ):
                architecture_tool.validate_evidence_provider_config(config_path)

        config["providers"][0]["command"] = ["cargo", "clippy"]
        self.write_yaml(config_path, config)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "must use offline mode",
        ):
            architecture_tool.validate_evidence_provider_config(config_path)

        config["providers"][0]["command"] = ["npm", "ci"]
        self.write_yaml(config_path, config)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "may install or download tools",
        ):
            architecture_tool.validate_evidence_provider_config(config_path)

    def test_provider_config_allows_absolute_python_on_all_platforms(self) -> None:
        self.init_project()
        config_path = self.root / ".architecture" / "evidence-providers.yaml"
        config = architecture_tool.load_yaml(config_path)
        config["providers"][0]["command"] = [sys.executable, "-c", "print('ok')"]
        self.write_yaml(config_path, config)

        architecture_tool.validate_evidence_provider_config(config_path)

    def test_provider_config_rejects_windows_absolute_installers(self) -> None:
        self.init_project()
        config_path = self.root / ".architecture" / "evidence-providers.yaml"
        config = architecture_tool.load_yaml(config_path)
        cases = (
            (
                [r"C:\Python\python.exe", "-m", "pip", "install", "archunit"],
                "may install tools",
            ),
            (
                [r"C:\Python\pip.exe", "install", "archunit"],
                "may install or download tools",
            ),
            (
                [r"C:\Rust\cargo.exe", "install", "archunit"],
                "may install or download tools",
            ),
            (
                [r"C:\Tools\run.bat"],
                "forbidden package or shell runner",
            ),
            (
                [r"C:\msys64\usr\bin\bash.exe", "-c", "echo unsafe"],
                "forbidden package or shell runner",
            ),
        )
        for command, message in cases:
            config["providers"][0]["command"] = command
            self.write_yaml(config_path, config)
            with self.assertRaisesRegex(architecture_tool.ArchitectureError, message):
                architecture_tool.validate_evidence_provider_config(config_path)

    def test_git_utf8_output_preserves_coverage_evidence(self) -> None:
        self.init_project()
        evidence_path = self.root / "unicode-evidence.md"
        evidence_path.write_text("架构证据\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "add", evidence_path.name],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Add UTF-8 evidence"],
            check=True,
        )
        commit = architecture_tool.current_git_commit(self.root)
        blob_sha = architecture_tool.git_output(
            self.root,
            "rev-parse",
            f"{commit}:unicode-evidence.md",
        )
        architecture_tool.validate_coverage_evidence_binding(
            {
                "path": "unicode-evidence.md",
                "commit": commit,
                "blob_sha": blob_sha,
                "line_start": 1,
                "line_end": 1,
            },
            self.root,
            commit,
            "UTF-8 evidence",
        )

    def test_deterministic_provider_requires_dependency_closure(self) -> None:
        self.init_project()
        config_path = self.root / ".architecture" / "evidence-providers.yaml"
        config = architecture_tool.load_yaml(config_path)
        provider = next(
            item for item in config["providers"] if item["id"] == "test-results"
        )
        provider.update(
            {
                "enabled": True,
                "command": [sys.executable, "-c", "print('ok')"],
                "allow_without_detection": True,
                "output_source": "stdout",
            }
        )
        provider.pop("result_path", None)
        self.write_yaml(config_path, config)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "dependency_inputs closure",
        ):
            architecture_tool.validate_evidence_provider_config(config_path)

    def test_provider_executable_rejects_extensionless_shell_wrapper(self) -> None:
        wrapper = self.root / "scripts" / "run-provider"
        wrapper.parent.mkdir()
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "forbidden shell wrapper",
        ):
            architecture_tool.resolve_provider_executable(
                self.root,
                "scripts/run-provider",
            )
        ksh_wrapper = self.root / "scripts" / "run-provider-ksh"
        ksh_wrapper.write_text("#!/bin/ksh\nexit 0\n", encoding="utf-8")
        ksh_wrapper.chmod(0o755)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "forbidden shell wrapper",
        ):
            architecture_tool.resolve_provider_executable(
                self.root,
                "scripts/run-provider-ksh",
            )
        ksh93_wrapper = self.root / "scripts" / "run-provider-ksh93"
        ksh93_wrapper.write_text("#!/bin/ksh93\nexit 0\n", encoding="utf-8")
        ksh93_wrapper.chmod(0o755)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "forbidden shell wrapper",
        ):
            architecture_tool.resolve_provider_executable(
                self.root,
                "scripts/run-provider-ksh93",
            )

    def test_provider_exit_zero_with_invalid_structured_output_fails(self) -> None:
        self.init_project()
        config_path = self.root / ".architecture" / "evidence-providers.yaml"
        config = architecture_tool.load_yaml(config_path)
        provider = next(
            item for item in config["providers"] if item["id"] == "dependency-cruiser"
        )
        provider.update(
            {
                "enabled": True,
                "command": [sys.executable, "-c", "print('not json')"],
                "allow_without_detection": True,
                "output_source": "stdout",
                "dependency_inputs": [".architecture/evidence-providers.yaml"],
                "cache_mode": "isolated",
            }
        )
        self.write_yaml(config_path, config)
        subprocess.run(
            ["git", "-C", str(self.root), "add", str(config_path)],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "commit",
                "-qm",
                "Configure invalid evidence fixture",
            ],
            check=True,
        )

        artifact_path, artifact = architecture_tool.run_evidence_provider(
            self.root,
            "dependency-cruiser",
        )
        self.assertEqual(artifact["result"]["status"], "failed")
        self.assertEqual(artifact["result"]["content_validation"], "invalid")
        architecture_tool.validate_evidence_run(artifact_path, self.root)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "provider result is failed",
        ):
            architecture_tool.validate_evidence_run(
                artifact_path,
                self.root,
                require_passed=True,
            )

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is unavailable")
    def test_review_ssh_signature_verifies_against_allowed_signer(self) -> None:
        self.init_project()
        review_path = self.write_review()
        review = architecture_tool.load_yaml(review_path)
        signature_path = review_path.with_suffix(".yaml.sig")
        review["review"]["signature"] = {
            "format": "ssh",
            "identity": "architecture-verifier",
            "namespace": "architecture-governance",
            "path": signature_path.relative_to(self.root).as_posix(),
        }
        self.write_yaml(review_path, review)

        key_path = self.root / "test-signing-key"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(key_path),
            ],
            check=True,
        )
        public_key = key_path.with_suffix(".pub").read_text(encoding="utf-8")
        allowed_signers = self.root / ".architecture" / "allowed_signers"
        allowed_signers.write_text(
            f"architecture-verifier {public_key}",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "sign",
                "-f",
                str(key_path),
                "-n",
                "architecture-governance",
                str(review_path),
            ],
            check=True,
            capture_output=True,
        )
        architecture_tool.verify_review_signature(
            review_path,
            review,
            self.root,
            {
                "allowed_signers_file": ".architecture/allowed_signers",
                "namespace": "architecture-governance",
            },
        )

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen is unavailable")
    def test_greenfield_change_gate_validates_signed_real_chain(self) -> None:
        args = self.project_args()
        args.qualities = ["reliability", "auditability"]
        config_root = architecture_tool.init_project(args)
        policy_path = config_root / "gate-policy.yaml"
        policy = architecture_tool.load_yaml(policy_path)
        policy["block"]["require_clean_tree"] = False
        policy["stages"]["release"] = True
        policy["release_requirements"]["require_no_dirty_tree"] = False
        policy["artifact_signatures"] = {
            "allowed_signers_file": ".architecture/allowed_signers",
            "namespace": "architecture-governance",
        }
        self.write_yaml(policy_path, policy)

        key_path = self.root / "brief-signing-key"
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                str(key_path),
            ],
            check=True,
        )
        public_key = key_path.with_suffix(".pub").read_text(encoding="utf-8")
        (config_root / "allowed_signers").write_text(
            f"architecture-owner {public_key}",
            encoding="utf-8",
        )
        approvals = config_root / "approvals"
        approvals.mkdir()
        approval_record = approvals / "design-brief-approval.md"
        approval_record.write_text(
            "Architecture owner approves this bounded Design Brief.\n",
            encoding="utf-8",
        )
        approval_scope_record = approvals / "design-brief-scope.md"
        approval_scope_record.write_text(
            "Approval covers the constrained target and named quality scenarios.\n",
            encoding="utf-8",
        )
        brief_path = config_root / "architecture-design-brief.yaml"
        brief = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "architecture-design-brief.yaml"
        )
        brief["brief"]["status"] = "approved"
        brief["brief"]["approval"] = {
            "approved_by": ["architecture-owner"],
            "approved_at": "2026-08-06T12:00:00+00:00",
            "authority": "Project architecture decision authority",
            "evidence": [
                {
                    "path": approval_record.relative_to(self.root).as_posix(),
                    "sha256": architecture_tool.file_sha256(approval_record),
                    "description": "Recorded approval rationale and scope.",
                },
                {
                    "path": approval_scope_record.relative_to(self.root).as_posix(),
                    "sha256": architecture_tool.file_sha256(approval_scope_record),
                    "description": "Recorded approval boundary.",
                },
            ],
            "signatures": [
                {
                    "format": "ssh",
                    "identity": "architecture-owner",
                    "namespace": "architecture-governance",
                    "path": brief_path.with_suffix(".yaml.sig")
                    .relative_to(self.root)
                    .as_posix(),
                }
            ],
        }
        self.write_yaml(brief_path, brief)
        subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "sign",
                "-f",
                str(key_path),
                "-n",
                "architecture-governance",
                str(brief_path),
            ],
            check=True,
            capture_output=True,
        )
        architecture_tool.validate_design_brief(
            brief_path,
            repository_root=self.root,
        )
        approval_record.write_text(
            "Tampered approval evidence.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "approval evidence hash does not match",
        ):
            architecture_tool.validate_design_brief(
                brief_path,
                repository_root=self.root,
            )
        approval_record.write_text(
            "Architecture owner approves this bounded Design Brief.\n",
            encoding="utf-8",
        )
        architecture_tool.validate_design_brief(
            brief_path,
            repository_root=self.root,
        )

        selection_path = config_root / "knowledge-selection.yaml"
        selection = architecture_tool.select_knowledge(
            config_root / "repository-facts.yaml",
            profile_path=config_root / "profile.yaml",
            task="Design the approved constrained order target architecture.",
            skill="architecture-solution-advisor",
            maximum_entries=32,
            includes=[
                "style.modular-monolith",
                "style.microservices",
                "pattern.idempotency-key",
                "technology.fastapi",
                "technology.postgresql",
                "technology.rabbitmq",
            ],
        )
        self.write_yaml(selection_path, selection)

        decision = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "architecture-decision.yaml"
        )
        decision["decision"].update(
            {
                "source_context_sha256": architecture_tool.file_sha256(brief_path),
                "decision_makers": ["architecture-owner"],
                "status": "accepted",
                "knowledge_selection_sha256": architecture_tool.file_sha256(
                    selection_path
                ),
            }
        )
        decision["knowledge_snapshot"] = [
            {
                "id": item["id"],
                "version": item["version"],
                "sha256": item["sha256"],
            }
            for item in selection["selection"]
        ]
        decision_path = config_root / "reviews" / "greenfield-decision.yaml"
        self.write_yaml(decision_path, decision)

        missing_plan = architecture_tool.gate_greenfield(
            self.root,
            decision_path,
            mode="change",
        )
        self.assertEqual(missing_plan["status"], "fail")
        self.assertIn("requires an accepted", missing_plan["policy_failures"][0])

        plan = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "remediation-plan.yaml"
        )
        plan["plan"].update(
            {
                "source_context_sha256": architecture_tool.file_sha256(brief_path),
                "source_decision_sha256": architecture_tool.file_sha256(decision_path),
                "status": "accepted",
            }
        )
        plan_path = config_root / "reviews" / "greenfield-plan.yaml"
        self.write_yaml(plan_path, plan)

        result = architecture_tool.gate_greenfield(
            self.root,
            decision_path,
            mode="change",
        )
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["subject_kind"], "greenfield-decision")
        self.assertTrue(
            any("no Git source commit" in warning for warning in result["warnings"])
        )

        policy["block"]["require_clean_tree"] = True
        self.write_yaml(policy_path, policy)
        policy_enforced = architecture_tool.gate_greenfield(
            self.root,
            decision_path,
            mode="change",
        )
        self.assertEqual(policy_enforced["status"], "fail")
        self.assertTrue(
            any(
                "not a git repository" in item
                for item in policy_enforced["policy_failures"]
            )
        )
        policy["block"]["require_clean_tree"] = False
        self.write_yaml(policy_path, policy)

        release = architecture_tool.gate_greenfield(
            self.root,
            decision_path,
            mode="release",
        )
        self.assertEqual(release["status"], "fail")
        self.assertTrue(
            any(
                "complete implementation plan" in item
                for item in release["policy_failures"]
            )
        )
        self.assertTrue(
            any("missing evidence types" in item for item in release["policy_failures"])
        )

        tampered_brief = architecture_tool.load_yaml(brief_path)
        tampered_brief["context"]["objective"] += " Tampered after approval."
        self.write_yaml(brief_path, tampered_brief)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "SSH signature is invalid",
        ):
            architecture_tool.validate_design_brief(
                brief_path,
                repository_root=self.root,
            )

    def test_init_and_validate_empty_portfolio(self) -> None:
        target = architecture_tool.init_portfolio(self.portfolio_args())
        self.assertEqual(target, self.root / ".architecture-portfolio")
        validated = architecture_tool.validate_portfolio(self.root)
        self.assertEqual(len(validated), 7)

    def test_portfolio_gate_blocks_confirmed_high_finding(self) -> None:
        architecture_tool.init_portfolio(self.portfolio_args())
        review_path = self.write_review(portfolio=True)
        result = architecture_tool.gate_portfolio(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["blocking"][0]["id"], "TEST-DATA-001")

    def test_review_rejects_candidate_in_verified_bundle(self) -> None:
        self.init_project()
        path = self.write_review()
        review = architecture_tool.load_yaml(path)
        review["findings"][0]["verification"]["status"] = "candidate"
        review["findings"][0]["verification"]["rationale"] = "Awaiting verification."
        review["summary"]["confirmed"] = 0
        self.write_yaml(path, review)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "still has candidate IDs",
        ):
            architecture_tool.validate_review(path)

    def test_validate_remediation_plan(self) -> None:
        plan_path = self.root / "plan.yaml"
        self.write_yaml(
            plan_path,
            {
                "schema_version": "1.0",
                "plan": {
                    "id": "2026-07-28-test-remediation",
                    "source_review": "2026-07-28-test-project",
                    "generated_at": "2026-07-28T11:00:00+00:00",
                    "scope": ["store"],
                    "status": "draft",
                },
                "items": [
                    {
                        "id": "PLAN-DATA-001",
                        "finding_ids": ["TEST-DATA-001"],
                        "desired_invariant": "One owner performs authoritative writes.",
                        "owner": "test-owner",
                        "recommended_option": {
                            "title": "Route writes through the owner",
                            "rationale": (
                                "It restores one authoritative write boundary."
                            ),
                            "tradeoffs": ["Requires caller migration."],
                        },
                        "alternatives": [],
                        "do_nothing": "Conflicting writes remain possible.",
                        "effort": {
                            "size": "m",
                            "uncertainty": "medium",
                            "assumptions": ["Both callers can migrate."],
                        },
                        "change_risk": "medium",
                        "governed_change": False,
                        "dependencies": [],
                        "sequence": ["Protect current write behavior with tests."],
                        "test_protection": [
                            "Exercise concurrent writes at the owning boundary."
                        ],
                        "rollback": "Keep the previous adapter until acceptance.",
                        "acceptance_criteria": [
                            "All writes pass through the authoritative owner."
                        ],
                    }
                ],
            },
        )
        validated = architecture_tool.validate_plan(plan_path)
        self.assertEqual(validated["items"][0]["id"], "PLAN-DATA-001")

    def test_greenfield_decision_binds_design_brief_without_fake_review(
        self,
    ) -> None:
        config_root = self.init_project()
        brief = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "architecture-design-brief.yaml"
        )
        brief["brief"].update(
            {
                "id": "DESIGN-BRIEF-TEST-001",
                "authors": ["architecture-owner"],
                "status": "approved",
            }
        )
        brief["schema_version"] = "1.0"
        brief.pop("design_mode")
        brief.pop("architecture_constraints")
        brief["quality_scenarios"][0]["attribute"] = "recoverability"
        brief_path = config_root / "architecture-design-brief.yaml"
        self.write_yaml(brief_path, brief)
        architecture_tool.validate_design_brief(brief_path)

        facts_path = config_root / "repository-facts.yaml"
        profile_path = config_root / "profile.yaml"
        selection = architecture_tool.select_knowledge(
            facts_path,
            profile_path=profile_path,
            task="Design the least-complex recoverable service boundary.",
            skill="architecture-solution-advisor",
            maximum_entries=16,
            includes=[
                "style.modular-monolith",
                "pattern.feature-flag",
                "technology.import-linter",
                "migration.layered-monolith-to-modular",
            ],
        )
        selection_path = config_root / "decision-knowledge-selection.yaml"
        self.write_yaml(selection_path, selection)

        decision = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "architecture-decision.yaml"
        )
        decision["schema_version"] = "1.3"
        decision["decision"].pop("architecture_intent", None)
        decision["decision"].pop("source_review", None)
        decision["decision"].pop("source_review_sha256", None)
        decision.pop("target_architecture")
        decision.pop("hard_eliminations", None)
        decision["decision"].update(
            {
                "id": "ADR-GREENFIELD-001",
                "decision_kind": "greenfield",
                "source_context": brief_path.relative_to(self.root).as_posix(),
                "source_context_sha256": architecture_tool.file_sha256(brief_path),
                "knowledge_selection_path": selection_path.relative_to(
                    self.root
                ).as_posix(),
                "knowledge_selection_sha256": architecture_tool.file_sha256(
                    selection_path
                ),
            }
        )
        decision["problem"]["quality_attributes"] = ["recoverability"]
        decision["problem"]["finding_ids"] = []
        for option in decision["options"]:
            option.pop("constraint_assessments", None)
            option["architecture_styles"] = []
            option["patterns"] = []
            option["technologies"] = []
            option["quality_attribute_effects"] = [
                {
                    "attribute": "recoverability",
                    "effect": "improves",
                    "rationale": (
                        "The option has an explicit restart and rollback path."
                    ),
                }
            ]
        decision["knowledge_snapshot"] = [
            {
                "id": item["id"],
                "version": item["version"],
                "sha256": item["sha256"],
            }
            for item in selection["selection"]
        ]
        decision_path = config_root / "reviews" / "greenfield-decision.yaml"
        self.write_yaml(decision_path, decision)

        validated = architecture_tool.validate_decision(
            decision_path,
            design_brief_path=brief_path,
            repository_root=self.root,
        )
        self.assertEqual(
            validated["decision"]["decision_kind"],
            "greenfield",
        )

        stale = copy.deepcopy(decision)
        stale["decision"]["source_context_sha256"] = "0" * 64
        stale_path = config_root / "reviews" / "stale-greenfield-decision.yaml"
        self.write_yaml(stale_path, stale)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "source_context_sha256",
        ):
            architecture_tool.validate_decision(
                stale_path,
                design_brief_path=brief_path,
                repository_root=self.root,
            )

    def test_trusted_plan_requires_accepted_bound_decision(self) -> None:
        self.init_project()
        review_path = self.write_review()
        review = architecture_tool.load_yaml(review_path)
        decision_path = (
            self.root
            / ".architecture"
            / "reviews"
            / "2026-07-28-architecture-decision.yaml"
        )
        decision = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "architecture-decision.yaml"
        )
        as_legacy_remediation_decision(decision, "1.1")
        decision["decision"].pop("knowledge_selection_path")
        decision["decision"].pop("knowledge_selection_sha256")
        decision["problem"].pop("known_facts")
        decision["problem"].pop("unknowns")
        decision.pop("migration")
        decision["problem"]["quality_attributes"] = ["recoverability"]
        for option in decision["options"]:
            option["quality_attribute_effects"] = [
                {
                    "attribute": "recoverability",
                    "effect": "neutral",
                    "rationale": "The remediation preserves recoverability.",
                }
            ]
        decision["decision"].update(
            {
                "id": "ADR-TEST-001",
                "source_review": review["review"]["id"],
                "source_review_sha256": architecture_tool.file_sha256(review_path),
                "decision_makers": ["architecture-owner"],
                "status": "accepted",
            }
        )
        decision["problem"]["finding_ids"] = ["TEST-DATA-001"]
        decision["knowledge_snapshot"] = architecture_tool.decision_knowledge_snapshot()
        self.write_yaml(decision_path, decision)

        plan_path = (
            self.root / ".architecture" / "reviews" / "2026-07-28-remediation-plan.yaml"
        )
        plan = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "remediation-plan.yaml"
        )
        as_legacy_remediation_plan(plan, "1.1")
        plan["items"][0].pop("finding_bindings", None)
        plan["items"][0].pop("knowledge_ids")
        plan["items"][0].pop("assumptions")
        plan["plan"].update(
            {
                "id": "2026-07-28-test-remediation",
                "source_review": review["review"]["id"],
                "source_review_sha256": architecture_tool.file_sha256(review_path),
                "source_decision": decision["decision"]["id"],
                "source_decision_sha256": architecture_tool.file_sha256(decision_path),
            }
        )
        plan["items"][0]["finding_ids"] = ["TEST-DATA-001"]
        self.write_yaml(plan_path, plan)

        validated = architecture_tool.validate_plan(
            plan_path,
            review_path=review_path,
            decision_path=decision_path,
        )
        self.assertEqual(validated["plan"]["source_decision"], "ADR-TEST-001")

        completion_path = self.root / ".architecture" / "evidence" / "completion.txt"
        completion_path.parent.mkdir(parents=True, exist_ok=True)
        completion_path.write_text("tests and operations accepted\n", encoding="utf-8")
        completion_hash = architecture_tool.file_sha256(completion_path)
        plan["plan"]["status"] = "complete"
        plan["items"][0]["completion_evidence"] = [
            {
                "type": evidence_type,
                "location": completion_path.relative_to(self.root).as_posix(),
                "sha256": completion_hash,
                "result": "Required acceptance outcome passed.",
                "observed_at": "2026-07-28T11:30:00+00:00",
            }
            for evidence_type in ("test", "operational")
        ]
        self.write_yaml(plan_path, plan)
        architecture_tool.validate_plan(
            plan_path,
            review_path=review_path,
            decision_path=decision_path,
            repository_root=self.root,
        )
        completion_path.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "completion evidence hash does not match",
        ):
            architecture_tool.validate_plan(
                plan_path,
                review_path=review_path,
                decision_path=decision_path,
                repository_root=self.root,
            )

        plan["plan"]["status"] = "draft"
        plan["items"][0]["completion_evidence"] = []
        plan["plan"]["source_decision_sha256"] = "0" * 64
        self.write_yaml(plan_path, plan)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "source_decision_sha256",
        ):
            architecture_tool.validate_plan(
                plan_path,
                review_path=review_path,
                decision_path=decision_path,
            )

    def test_change_gate_classifies_public_contract_from_base_commit(self) -> None:
        self.init_project()
        review_path = self.write_review(self.review("needs-evidence"))
        subprocess.run(
            ["git", "-C", str(self.root), "add", ".architecture/reviews"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Add trusted review"],
            check=True,
        )
        base_commit = architecture_tool.current_git_commit(self.root)
        (self.root / "openapi.yaml").write_text(
            "openapi: 3.1.0\ninfo: {title: Test, version: 1.0.0}\npaths: {}\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", "openapi.yaml"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Change API contract"],
            check=True,
        )

        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
            mode="change",
            base_commit=base_commit,
        )
        self.assertEqual(result["change_impacts"]["public_contract"], ["openapi.yaml"])
        self.assertTrue(
            any(
                "Public contract changes require" in failure
                for failure in result["policy_failures"]
            )
        )

    def test_base_change_accepts_review_before_governance_only_commits(self) -> None:
        config_root = self.init_project()
        policy_path = config_root / "gate-policy.yaml"
        policy = architecture_tool.load_yaml(policy_path)
        policy["block"]["freshness_strategy"] = "diff-aware"
        policy["change_requirements"]["critical_paths"] = ["critical.py"]
        self.write_yaml(policy_path, policy)
        subprocess.run(
            ["git", "-C", str(self.root), "add", str(policy_path)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Set change policy"],
            check=True,
        )
        base_commit = architecture_tool.current_git_commit(self.root)

        (self.root / "critical.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "add", "critical.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Change critical path"],
            check=True,
        )
        review_path = self.write_review(self.review("needs-evidence"))
        subprocess.run(
            ["git", "-C", str(self.root), "add", ".architecture/reviews"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Record review"],
            check=True,
        )
        (self.root / "governance-note.md").write_text(
            "Review record follow-up.\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", "governance-note.md"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Document review"],
            check=True,
        )

        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
            mode="change",
            base_commit=base_commit,
        )

        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["change_impacts"]["critical"], ["critical.py"])
        self.assertFalse(
            any(
                "changed after the selected review" in failure
                for failure in result["policy_failures"]
            )
        )
        (self.root / "critical.py").write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.root), "add", "critical.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Change after review"],
            check=True,
        )
        stale_result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
            mode="change",
            base_commit=base_commit,
        )
        self.assertTrue(
            any(
                "changed after the selected review" in failure
                for failure in stale_result["policy_failures"]
            )
        )
        scoped_review = architecture_tool.load_yaml(review_path)
        scoped_review["review"]["scope_manifest"] = ["critical.py"]
        self.write_yaml(review_path, scoped_review)
        unscoped_result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
            mode="change",
            base_commit=base_commit,
        )
        self.assertTrue(
            any(
                "outside the selected review scope" in failure
                for failure in unscoped_result["policy_failures"]
            ),
            unscoped_result["policy_failures"],
        )

    def test_keep_current_decision_covers_compatible_migration(self) -> None:
        config_root = self.init_project()
        policy_path = config_root / "gate-policy.yaml"
        policy = architecture_tool.load_yaml(policy_path)
        policy["change_requirements"].update(
            {
                "critical_paths": [],
                "public_contract_paths": [],
                "migration_paths": ["migration.yaml"],
                "security_paths": [],
            }
        )
        self.write_yaml(policy_path, policy)
        subprocess.run(
            ["git", "-C", str(self.root), "add", str(policy_path)],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Set migration policy"],
            check=True,
        )
        base_commit = architecture_tool.current_git_commit(self.root)

        (self.root / "migration.yaml").write_text(
            "schema_version: '1.1'\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", "migration.yaml"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Add migration"],
            check=True,
        )
        review_path = self.write_review(self.review("needs-evidence"))
        review = architecture_tool.load_yaml(review_path)

        decision = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "architecture-decision.yaml"
        )
        as_legacy_remediation_decision(decision, "1.1")
        decision["decision"].pop("knowledge_selection_path")
        decision["decision"].pop("knowledge_selection_sha256")
        decision["decision"].update(
            {
                "id": "ADR-TEST-MIGRATION",
                "source_review": review["review"]["id"],
                "source_review_sha256": architecture_tool.file_sha256(review_path),
                "decision_makers": ["architecture-owner"],
                "status": "accepted",
            }
        )
        decision["problem"]["quality_attributes"] = ["recoverability"]
        decision["problem"]["finding_ids"] = []
        decision["selected_option"] = "keep-current"
        for option in decision["options"]:
            option["rejected_reasons"] = (
                []
                if option["id"] == "keep-current"
                else ["Keep-current is sufficient for this compatible migration."]
            )
        decision["migration"]["affected_paths"] = ["migration.yaml"]
        decision["knowledge_snapshot"] = architecture_tool.decision_knowledge_snapshot()
        for option in decision["options"]:
            option["quality_attribute_effects"] = [
                {
                    "attribute": "recoverability",
                    "effect": "neutral",
                    "rationale": (
                        "The compatible migration retains current recovery behavior."
                    ),
                }
            ]
        decision_path = config_root / "reviews" / "2026-07-28-migration-decision.yaml"
        self.write_yaml(decision_path, decision)
        subprocess.run(
            ["git", "-C", str(self.root), "add", ".architecture/reviews"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Record migration review"],
            check=True,
        )

        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
            mode="change",
            base_commit=base_commit,
        )

        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["change_impacts"]["migration"], ["migration.yaml"])
        self.assertFalse(
            any(
                "Migration changes require" in failure
                for failure in result["policy_failures"]
            )
        )
        decision["migration"]["affected_paths"] = ["another-migration.yaml"]
        self.write_yaml(decision_path, decision)
        uncovered = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
            mode="change",
            base_commit=base_commit,
        )
        self.assertTrue(
            any(
                "Migration changes require" in failure
                for failure in uncovered["policy_failures"]
            )
        )

        decision["migration"]["affected_paths"] = ["migration.yaml"]
        decision["selected_option"] = "low-complexity-option"
        decision["options"][0]["rejected_reasons"] = [
            "The bounded structural option is now selected."
        ]
        decision["options"][1]["rejected_reasons"] = []
        self.write_yaml(decision_path, decision)
        non_keep_current = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
            mode="change",
            base_commit=base_commit,
        )
        self.assertTrue(
            any(
                "Migration changes require" in failure
                for failure in non_keep_current["policy_failures"]
            )
        )

    def test_contract_gate_requires_every_profile_review_workflow(self) -> None:
        self.init_project()
        profile_path = self.root / ".architecture" / "profile.yaml"
        profile = architecture_tool.load_yaml(profile_path)
        profile["project"]["required_reviews"].append("ai-agent-architecture")
        profile["project"]["review_requirements"].append(
            {
                "id": "ai-agent-architecture",
                "kind": "ai-agent",
                "rule_packs": ["ai-agent-core"],
            }
        )
        profile["project"]["rule_packs"].append("ai-agent-core")
        self.write_yaml(profile_path, profile)
        review_path = self.write_review(self.review("needs-evidence"))

        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
            mode="contract",
        )
        self.assertEqual(result["status"], "fail")
        self.assertTrue(
            any(
                "Required review ai-agent-architecture" in failure
                for failure in result["policy_failures"]
            )
        )

    def test_contract_gate_ignores_untrusted_historical_review_candidate(
        self,
    ) -> None:
        config_root = self.init_project()
        current_review_path = self.write_review(self.review("needs-evidence"))
        historical_review = architecture_tool.load_yaml(current_review_path)
        historical_review["review"]["id"] = "2026-07-27-untrusted-historical"
        historical_review["review"]["performed_at"] = "2026-07-27T10:00:00+00:00"
        historical_review["review"]["profile_sha256"] = "0" * 64
        historical_path = config_root / "reviews" / "000-historical-invalid.yaml"
        self.write_yaml(historical_path, historical_review)
        profile = architecture_tool.load_yaml(config_root / "profile.yaml")

        completed = architecture_tool.completed_required_reviews(
            self.root,
            config_root,
            profile,
            head=architecture_tool.current_git_commit(self.root),
            freshness_strategy="time-window",
            evaluation_date=date(2026, 7, 28),
            max_review_age_days=30,
        )

        self.assertEqual(
            Path(completed["project-architecture"]),
            current_review_path,
        )

        current_review_path.unlink()
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "has no trusted artifact",
        ):
            architecture_tool.completed_required_reviews(
                self.root,
                config_root,
                profile,
                head=architecture_tool.current_git_commit(self.root),
                freshness_strategy="time-window",
                evaluation_date=date(2026, 7, 28),
                max_review_age_days=30,
            )

    def test_policy_enforces_configured_role_separation(self) -> None:
        self.init_project()
        review_path = self.write_review(self.review("needs-evidence"))
        policy_path = self.root / ".architecture" / "gate-policy.yaml"
        policy = architecture_tool.load_yaml(policy_path)
        policy["roles"]["verifiers"] = ["architecture-auditor"]
        self.write_yaml(policy_path, policy)

        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "requires separate auditors and verifiers",
        ):
            architecture_tool.gate_project(
                self.root,
                review_path,
                today=date(2026, 7, 28),
            )

    def test_v4_requires_deterministic_tool_evidence_cited_by_finding(self) -> None:
        self.init_project()
        review_path = self.write_review()
        review = architecture_tool.load_yaml(review_path)
        verification = review["findings"][0]["verification"]
        verification["level"] = "V4"
        verification["verifier"]["type"] = "human"
        self.write_yaml(review_path, review)

        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "cited by the Finding",
        ):
            architecture_tool.validate_review(
                review_path,
                rule_pack_ids=["project-core"],
                strict_trust=True,
                repository_root=self.root,
            )

    def test_confirmed_high_finding_blocks(self) -> None:
        self.init_project()
        review_path = self.write_review()
        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(
            [finding["id"] for finding in result["blocking"]],
            ["TEST-DATA-001"],
        )

    def test_active_baseline_suppresses_confirmed_finding(self) -> None:
        self.init_project()
        review_path = self.write_review()
        baseline_path = self.root / ".architecture" / "baseline.yaml"
        review = architecture_tool.load_yaml(review_path)
        self.write_yaml(
            baseline_path,
            {
                "schema_version": "1.1",
                "findings": [
                    {
                        "id": "TEST-DATA-001",
                        "finding_fingerprint": review["findings"][0]["fingerprint"],
                        "reason": "Accepted migration baseline.",
                        "owner": "test-owner",
                        "recorded_on": "2026-07-28",
                        "expires_on": "2026-08-28",
                    }
                ],
            },
        )
        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["baselined"], ["TEST-DATA-001"])

    def test_needs_evidence_warns_but_does_not_block(self) -> None:
        self.init_project()
        review_path = self.write_review(self.review("needs-evidence"))
        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["unverified"], ["TEST-DATA-001"])
        self.assertTrue(result["warnings"])

    def test_expired_waiver_does_not_suppress(self) -> None:
        self.init_project()
        review_path = self.write_review()
        policy_path = self.root / ".architecture" / "gate-policy.yaml"
        policy = architecture_tool.load_yaml(policy_path)
        review = architecture_tool.load_yaml(review_path)
        policy["waivers"] = [
            {
                "finding_id": "TEST-DATA-001",
                "finding_fingerprint": review["findings"][0]["fingerprint"],
                "reason": "Temporary migration exception.",
                "owner": "test-owner",
                "expires_on": "2026-07-27",
                "approved_by": "policy-owner",
            }
        ]
        self.write_yaml(policy_path, policy)
        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["expired_waivers"], ["TEST-DATA-001"])

    def test_trusted_review_requires_complete_rule_coverage(self) -> None:
        self.init_project()
        review_path = self.write_review()
        review = architecture_tool.load_yaml(review_path)
        review["coverage"].pop()
        self.write_yaml(review_path, review)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "coverage is missing loaded rules",
        ):
            architecture_tool.validate_review(
                review_path,
                rule_pack_ids=["project-core"],
                strict_trust=True,
                repository_root=self.root,
            )

    def test_verified_finding_semantics_must_match_candidate(self) -> None:
        self.init_project()
        review_path = self.write_review()
        review = architecture_tool.load_yaml(review_path)
        finding = review["findings"][0]
        finding["invariant"] = (
            "A different invariant was introduced only after candidate review."
        )
        finding["fingerprint"] = architecture_tool.finding_fingerprint(
            "test-project",
            finding,
        )
        self.write_yaml(review_path, review)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "semantics differ from source candidate",
        ):
            architecture_tool.validate_review(
                review_path,
                rule_pack_ids=["project-core"],
                strict_trust=True,
                repository_root=self.root,
            )

    def test_review_bindings_reject_path_escape(self) -> None:
        self.init_project()
        outside = self.root.parent / "outside-candidate.yaml"
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "escapes configured root",
        ):
            architecture_tool.review_bindings(self.root, outside)

    def test_accepted_risk_requires_authorized_two_party_registry(self) -> None:
        self.init_project()
        review_path = self.write_review()
        policy_path = self.root / ".architecture" / "gate-policy.yaml"
        policy = architecture_tool.load_yaml(policy_path)
        policy["block"]["verification_levels"]["accepted_risk"] = "V2"
        self.write_yaml(policy_path, policy)
        review = architecture_tool.load_yaml(review_path)
        review["findings"][0]["status"] = "accepted-risk"
        self.write_yaml(review_path, review)
        acceptance_path = self.root / ".architecture" / "risk-acceptances.yaml"
        self.write_yaml(
            acceptance_path,
            {
                "schema_version": "1.1",
                "acceptances": [
                    {
                        "finding_id": "TEST-DATA-001",
                        "finding_fingerprint": review["findings"][0]["fingerprint"],
                        "accepted_by": "risk-owner",
                        "approved_by": "policy-owner",
                        "reason": "Migration is scheduled with bounded exposure.",
                        "compensating_controls": [
                            "Monitor conflicting writes and stop on detection."
                        ],
                        "accepted_at": "2026-07-28T10:00:00+00:00",
                        "expires_on": "2026-08-28",
                    }
                ],
            },
        )
        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["accepted_risks"], ["TEST-DATA-001"])

        registry = architecture_tool.load_yaml(acceptance_path)
        registry["acceptances"][0]["approved_by"] = "risk-owner"
        self.write_yaml(acceptance_path, registry)
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "separate accepter and approver",
        ):
            architecture_tool.validate_risk_acceptances(acceptance_path)

    def test_tiered_policy_requires_v2_for_high_findings(self) -> None:
        self.init_project()
        review_path = self.write_review()
        review = architecture_tool.load_yaml(review_path)
        review["findings"][0]["verification"]["level"] = "V1"
        self.write_yaml(review_path, review)

        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )

        self.assertEqual(result["status"], "fail")
        self.assertIn(
            "verification level V1 is below required V2",
            result["blocking"][0]["reason"],
        )

    def test_future_risk_acceptance_does_not_suppress(self) -> None:
        self.init_project()
        review_path = self.write_review()
        review = architecture_tool.load_yaml(review_path)
        review["findings"][0]["status"] = "accepted-risk"
        self.write_yaml(review_path, review)
        self.write_yaml(
            self.root / ".architecture" / "risk-acceptances.yaml",
            {
                "schema_version": "1.1",
                "acceptances": [
                    {
                        "finding_id": "TEST-DATA-001",
                        "finding_fingerprint": review["findings"][0]["fingerprint"],
                        "accepted_by": "risk-owner",
                        "approved_by": "policy-owner",
                        "reason": "Approval is deliberately scheduled for tomorrow.",
                        "compensating_controls": ["Block writes until approval."],
                        "accepted_at": "2026-07-29T00:00:00+00:00",
                        "expires_on": "2026-08-29",
                    }
                ],
            },
        )
        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["pending_acceptances"], ["TEST-DATA-001"])

    def test_git_evidence_resolves_excerpt_inside_bound_lines(self) -> None:
        self.init_project()
        source = self.root / "store.py"
        source.write_text(
            "def save(record):\n    return writer.update(record)\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", "store.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "Add evidence source"],
            check=True,
        )
        commit = architecture_tool.current_git_commit(self.root)
        review = self.review()
        evidence = review["findings"][0]["evidence"][0]
        evidence.update(
            {
                "location": "store.py:2",
                "path": "store.py",
                "commit": commit,
                "line_start": 2,
                "line_end": 2,
                "excerpt": "writer.update(record)",
                "excerpt_sha256": architecture_tool.sha256_bytes(
                    b"writer.update(record)"
                ),
            }
        )
        result = architecture_tool.verify_review_evidence(review, self.root)
        self.assertEqual(result[0]["status"], "resolved")

        evidence["excerpt"] = "writer.delete(record)"
        evidence["excerpt_sha256"] = architecture_tool.sha256_bytes(
            b"writer.delete(record)"
        )
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "absent from the bound source range",
        ):
            architecture_tool.verify_review_evidence(review, self.root)

    def test_mismatched_fingerprint_does_not_suppress(self) -> None:
        self.init_project()
        review_path = self.write_review()
        baseline_path = self.root / ".architecture" / "baseline.yaml"
        self.write_yaml(
            baseline_path,
            {
                "schema_version": "1.1",
                "findings": [
                    {
                        "id": "TEST-DATA-001",
                        "finding_fingerprint": "0" * 64,
                        "reason": "Stale baseline for a different risk.",
                        "owner": "test-owner",
                        "recorded_on": "2026-07-28",
                        "expires_on": "2026-08-28",
                    }
                ],
            },
        )
        result = architecture_tool.gate_project(
            self.root,
            review_path,
            today=date(2026, 7, 28),
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["baselined"], [])

    def test_gate_result_converts_to_sarif(self) -> None:
        self.init_project()
        result = architecture_tool.gate_project(
            self.root,
            self.write_review(),
            today=date(2026, 7, 28),
        )
        sarif = architecture_tool.gate_result_to_sarif(result)
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(
            sarif["runs"][0]["results"][0]["ruleId"],
            "TEST-DATA-001",
        )

    def test_empty_benchmark_run_scores_zero_positive_precision(self) -> None:
        truth = architecture_tool.load_yaml(ROOT / "benchmarks" / "ground-truth.yaml")
        expected_positive = sum(
            finding["present"]
            for case in truth["cases"]
            for finding in case["expected_findings"]
        )
        result = architecture_tool.score_benchmark(
            ROOT / "benchmarks" / "ground-truth.yaml",
            ROOT / "benchmarks" / "run-template.yaml",
        )
        self.assertEqual(result["precision"], 0.0)
        self.assertEqual(result["recall"], 0.0)
        self.assertEqual(result["false_negative"], expected_positive)
        self.assertEqual(result["usage_trials"], 0)
        self.assertIsNone(result["input_tokens"])
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(result["tool_call_trials"], 0)
        self.assertIsNone(result["tool_calls"])

    def test_benchmark_ablation_contract_rejects_ambiguous_treatments(self) -> None:
        manifest = architecture_tool.load_yaml(
            ROOT / "benchmarks" / "ablation" / "context-manifest.yaml"
        )
        truth = architecture_tool.load_yaml(ROOT / "benchmarks" / "ground-truth.yaml")
        skills = {case["skill"] for case in truth["cases"]}

        duplicate = copy.deepcopy(manifest)
        duplicate["treatments"].append(copy.deepcopy(duplicate["treatments"][0]))
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "repeats treatment",
        ):
            architecture_tool.validate_benchmark_ablation_contract(
                duplicate,
                skills=skills,
            )

        incomplete = copy.deepcopy(manifest)
        incomplete["treatments"] = incomplete["treatments"][1:]
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError,
            "exactly one Base/Full/Compressed",
        ):
            architecture_tool.validate_benchmark_ablation_contract(
                incomplete,
                skills=skills,
            )

    def test_benchmark_scores_repeated_trial_stability(self) -> None:
        run = architecture_tool.load_yaml(ROOT / "benchmarks" / "run-template.yaml")
        run["benchmark"]["repetitions"] = 2
        for case in run["cases"]:
            case["trials"] = [
                {
                    "index": index,
                    "duration_seconds": 0.1,
                    "observed_findings": [],
                    "observed_recommendations": [],
                }
                for index in (1, 2)
            ]
        run["cases"][0]["trials"][0]["usage"] = {"tool_calls": 2}
        run_path = self.root / "benchmark-run.yaml"
        self.write_yaml(run_path, run)
        result = architecture_tool.score_benchmark(
            ROOT / "benchmarks" / "ground-truth.yaml",
            run_path,
        )
        truth = architecture_tool.load_yaml(ROOT / "benchmarks" / "ground-truth.yaml")
        expected_positive = sum(
            finding["present"]
            for case in truth["cases"]
            for finding in case["expected_findings"]
        )
        self.assertEqual(result["trials"], 20)
        self.assertEqual(result["false_negative"], expected_positive * 2)
        self.assertEqual(result["finding_stability"], 1.0)
        self.assertAlmostEqual(result["mean_duration_seconds"], 0.1)
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual(result["tool_call_trials"], 1)
        self.assertIsNone(result["input_tokens"])

    def test_benchmark_scores_solution_decision_quality(self) -> None:
        truth = architecture_tool.load_yaml(ROOT / "benchmarks" / "ground-truth.yaml")
        run = architecture_tool.load_yaml(ROOT / "benchmarks" / "run-template.yaml")
        expected = {
            case["id"]: case["expected_decision"]
            for case in truth["cases"]
            if "expected_decision" in case
        }
        for case in run["cases"]:
            decision = expected.get(case["id"])
            if decision is None:
                continue
            case["observed_decision"] = {
                "selected_option": decision["selected_option"],
                "compared_tradeoffs": decision["required_tradeoffs"],
                "knowledge_ids": decision["required_knowledge_ids"],
                "rejected_options": [
                    {
                        "id": f"rejected-{index}",
                        "reason": (
                            "Current evidence does not justify this added complexity."
                        ),
                    }
                    for index in range(
                        1,
                        decision["minimum_rejected_options"] + 1,
                    )
                ],
                "migration_slices": [
                    (
                        "Validate the bounded option behind a reversible "
                        "compatibility seam."
                    )
                    for _ in range(decision["minimum_migration_slices"])
                ],
            }
        run_path = self.root / "solution-benchmark-run.yaml"
        self.write_yaml(run_path, run)
        result = architecture_tool.score_benchmark(
            ROOT / "benchmarks" / "ground-truth.yaml",
            run_path,
        )

        self.assertEqual(result["recommendation_accuracy"], 1.0)
        self.assertEqual(result["overdesign_rate"], 0.0)
        self.assertEqual(result["tradeoff_coverage"], 1.0)
        self.assertEqual(result["knowledge_citation_validity"], 1.0)
        self.assertEqual(result["required_knowledge_coverage"], 1.0)
        self.assertEqual(result["rejection_explanation_coverage"], 1.0)
        self.assertEqual(result["migration_actionability"], 1.0)

    def test_cli_json_preserves_policy_exit_code(self) -> None:
        self.init_project()
        review_path = self.write_review()
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "gate",
                "--project",
                str(self.root),
                "--review",
                str(review_path),
                "--json",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 1, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "fail")

    def test_cli_reports_version(self) -> None:
        process = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--version"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "architecture_tool.py 1.1.1")

    def test_benchmark_score_cli_can_preserve_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "score.json"
            process = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "benchmark-score",
                    "--ground-truth",
                    str(ROOT / "benchmarks" / "ground-truth.yaml"),
                    "--run",
                    str(ROOT / "benchmarks" / "run-template.yaml"),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                json.loads(process.stdout),
            )


if __name__ == "__main__":
    unittest.main()
