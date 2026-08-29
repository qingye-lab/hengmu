from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_ROOT = ROOT / "resources" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import architecture_tool  # noqa: E402


def _constraint(constraint_id: str, disposition: str) -> dict[str, str]:
    return {
        "id": constraint_id,
        "kind": "technology",
        "disposition": disposition,
        "target": f"Target for {constraint_id}",
        "knowledge_id": (
            "technology.redis"
            if disposition == "prohibited"
            else "technology.postgresql"
        ),
    }


def _brief(*constraints: dict[str, str], status: str = "approved") -> dict:
    return {
        "schema_version": "1.1",
        "brief": {"status": status},
        "design_mode": "constrained",
        "architecture_constraints": list(constraints),
        "quality_scenarios": [{"id": "QS-001"}],
        "critical_flows": [{"id": "checkout-flow"}],
        "decision_questions": [{"id": "DQ-001"}],
        "boundaries": {"data_owners": ["Finance owns payment records."]},
    }


def _decision(
    brief: dict,
    *,
    selected_status: dict[str, str] | None = None,
    status: str = "proposed",
) -> dict:
    constraints = brief.get("architecture_constraints", [])
    selected_status = selected_status or {
        item["id"]: "satisfied" for item in constraints
    }

    def option(option_id: str) -> dict:
        return {
            "id": option_id,
            "complexity_tier": "keep-current" if option_id == "keep-current" else "low",
            "architecture_styles": [],
            "patterns": [],
            "technologies": ["technology.postgresql"],
            "quality_attribute_effects": [],
            "rejected_reasons": [] if option_id == "selected" else ["Not selected."],
            "constraint_assessments": [
                {
                    "constraint_id": item["id"],
                    "knowledge_id": item["knowledge_id"],
                    "status": (
                        selected_status[item["id"]]
                        if option_id == "selected"
                        else "satisfied"
                    ),
                    "rationale": "The option assessment is explicit and bounded.",
                    **(
                        {"target_refs": ["checkout"]}
                        if option_id == "selected"
                        and selected_status[item["id"]] == "satisfied"
                        and item["disposition"] != "prohibited"
                        else {}
                    ),
                }
                for item in constraints
            ],
        }

    return {
        "schema_version": "1.4",
        "decision": {
            "id": "ADR-TARGET-001",
            "status": status,
            "architecture_intent": "target-architecture",
        },
        "problem": {
            "quality_attributes": [],
            "constraints": [],
            "finding_ids": [],
            "assumptions": [],
        },
        "knowledge_snapshot": [],
        "selected_option": "selected",
        "options": [option("keep-current"), option("selected"), option("alternative")],
        "hard_eliminations": [],
        "target_architecture": {
            "option_id": "selected",
            "runtime_units": [
                {
                    "id": "checkout",
                    "responsibility": "Accept checkout requests.",
                    "owner": "order-team",
                    "deployment_unit": "order-deployment",
                    "technologies": ["technology.postgresql"],
                },
                {
                    "id": "payments",
                    "responsibility": "Authorize and record payments.",
                    "owner": "order-team",
                    "deployment_unit": "order-deployment",
                    "technologies": ["technology.postgresql"],
                },
            ],
            "deployment_units": [
                {
                    "id": "order-deployment",
                    "environment": "production",
                    "owner": "order-team",
                    "rollout": "Rolling application rollout.",
                    "mixed_version": "Adjacent versions remain compatible.",
                    "on_call": "order-team",
                }
            ],
            "data_ownership": [
                {
                    "id": "payment-data",
                    "data": "payment records",
                    "owner": "payments",
                    "store": "PostgreSQL",
                    "lifecycle": "Retain records for the required audit period.",
                    "consistency": "Payment writes are transactionally consistent.",
                    "recovery": "Restore from verified backups and replay events.",
                }
            ],
            "interfaces": [
                {
                    "id": "checkout-payments",
                    "from": "checkout",
                    "to": "payments",
                    "contract": "Authorize a checkout payment.",
                    "mode": "sync",
                    "compatibility": "Additive request changes remain compatible.",
                    "evolution": "Remove fields only after consumer migration.",
                }
            ],
            "external_systems": [],
            "trust_boundaries": [
                {
                    "id": "public-boundary",
                    "description": "Public clients enter the order application.",
                    "runtime_units": ["checkout"],
                    "identities": ["customer"],
                    "permissions": ["submit checkout"],
                    "secrets": [],
                    "untrusted_inputs": ["checkout request"],
                    "controls": ["authentication and validation"],
                }
            ],
            "critical_flow_bindings": [
                {
                    "flow_id": "checkout-flow",
                    "runtime_units": ["checkout", "payments"],
                    "failure_outcome": (
                        "Checkout remains recoverable without duplicates."
                    ),
                    "recovery": "Retry by idempotency key and reconcile payment state.",
                    "measure": "Duplicate authorization count remains zero.",
                }
            ],
            "operational_model": {
                "deployment": "Roll out one compatible application unit.",
                "observability": "Trace checkout and payment state transitions.",
                "recovery": "Replay durable payment state after restoration.",
                "capacity": "Load-test the declared checkout threshold.",
                "backup_restore": "Verify PostgreSQL backup restoration regularly.",
                "on_call": "order-team",
                "incident_controls": ["halt rollout on duplicate authorization"],
            },
        },
    }


class ConstrainedTargetArchitectureTests(unittest.TestCase):
    def test_cli_reports_integrated_tool_version(self) -> None:
        script = (
            Path(__file__).parents[1] / "resources" / "scripts" / "architecture_tool.py"
        )
        process = subprocess.run(
            [sys.executable, str(script), "--version"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stdout.strip(), "architecture_tool.py 1.3.0")

    def test_brief_11_constraint_ids_and_open_cardinality(self) -> None:
        duplicate = _brief(
            _constraint("same-id", "required"), _constraint("same-id", "preferred")
        )
        with (
            patch.object(architecture_tool, "validate_file", return_value=duplicate),
            self.assertRaisesRegex(
                architecture_tool.ArchitectureError, "constraint ID"
            ),
        ):
            architecture_tool.validate_design_brief(Path("brief.yaml"))

        open_brief = _brief(_constraint("open-id", "preferred"))
        open_brief["design_mode"] = "open"
        with (
            patch.object(architecture_tool, "validate_file", return_value=open_brief),
            self.assertRaisesRegex(architecture_tool.ArchitectureError, "open"),
        ):
            architecture_tool.validate_design_brief(Path("brief.yaml"))

    def test_draft_greenfield_brief_is_rejected(self) -> None:
        brief = _brief(_constraint("required-id", "required"), status="draft")
        decision = _decision(brief)
        decision["decision"]["decision_kind"] = "greenfield"
        for option in decision["options"]:
            option["technologies"] = []
        with tempfile.TemporaryDirectory() as directory:
            brief_path = Path(directory) / "brief.yaml"
            brief_path.write_text("draft brief\n", encoding="utf-8")
            decision["decision"]["source_context"] = "brief.yaml"
            decision["decision"]["source_context_sha256"] = (
                architecture_tool.file_sha256(brief_path)
            )
            with (
                patch.object(architecture_tool, "validate_file", return_value=decision),
                patch.object(
                    architecture_tool, "validate_design_brief", return_value=brief
                ),
                patch.object(
                    architecture_tool,
                    "validate_knowledge_tree",
                    return_value=(None, {}),
                ),
                self.assertRaisesRegex(architecture_tool.ArchitectureError, "approved"),
            ):
                architecture_tool.validate_decision(
                    Path(directory) / "decision.yaml",
                    design_brief_path=brief_path,
                )

    def test_decision_14_fails_closed_at_cli_validation_boundary(self) -> None:
        brief = _brief(_constraint("required-id", "required"))
        malformed = _decision(brief)
        malformed["decision"].pop("architecture_intent")
        malformed["decision"].pop("decision_kind", None)
        with (
            patch.object(architecture_tool, "validate_file", return_value=malformed),
            self.assertRaisesRegex(
                architecture_tool.ArchitectureError, "decision_kind"
            ),
        ):
            architecture_tool.validate_decision(Path("decision.yaml"))

        malformed = _decision(brief)
        malformed["decision"]["decision_kind"] = "greenfield"
        malformed["decision"]["source_context"] = "brief.yaml"
        malformed["decision"]["source_context_sha256"] = "0" * 64
        malformed["problem"] = {"finding_ids": ["FAKE-FINDING-001"]}
        with (
            patch.object(architecture_tool, "validate_file", return_value=malformed),
            self.assertRaisesRegex(architecture_tool.ArchitectureError, "Finding IDs"),
        ):
            architecture_tool.validate_decision(Path("decision.yaml"))

    def test_current_brief_and_decision_versions_are_bound_both_ways(self) -> None:
        current_brief = _brief(_constraint("required-id", "required"))
        legacy_decision = _decision(current_brief)
        legacy_decision["schema_version"] = "1.3"
        legacy_decision["decision"].update(
            {
                "decision_kind": "greenfield",
                "source_context": "brief.yaml",
            }
        )
        legacy_decision["decision"].pop("architecture_intent")
        for option in legacy_decision["options"]:
            option["technologies"] = []

        legacy_brief = copy.deepcopy(current_brief)
        legacy_brief["schema_version"] = "1.0"
        legacy_brief.pop("design_mode")
        legacy_brief.pop("architecture_constraints")
        current_decision = _decision(current_brief)
        current_decision["decision"].update(
            {
                "decision_kind": "greenfield",
                "source_context": "brief.yaml",
            }
        )
        for option in current_decision["options"]:
            option["technologies"] = []

        for decision, brief, expected in (
            (legacy_decision, current_brief, "requires a Decision 1.4"),
            (current_decision, legacy_brief, "requires a schema 1.1"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                brief_path = Path(directory) / "brief.yaml"
                brief_path.write_text("approved brief\n", encoding="utf-8")
                decision["decision"]["source_context_sha256"] = (
                    architecture_tool.file_sha256(brief_path)
                )
                with (
                    patch.object(
                        architecture_tool, "validate_file", return_value=decision
                    ),
                    patch.object(
                        architecture_tool,
                        "validate_design_brief",
                        return_value=brief,
                    ),
                    patch.object(
                        architecture_tool,
                        "validate_knowledge_tree",
                        return_value=(None, {}),
                    ),
                    self.assertRaisesRegex(
                        architecture_tool.ArchitectureError, expected
                    ),
                ):
                    architecture_tool.validate_decision(
                        Path(directory) / "decision.yaml",
                        design_brief_path=brief_path,
                    )

    def test_decision_14_keeps_knowledge_technology_id_validation(self) -> None:
        brief = _brief(_constraint("required-id", "required"))
        decision = _decision(brief)
        decision["decision"].update(
            {
                "decision_kind": "greenfield",
                "source_context": "brief.yaml",
                "source_context_sha256": "0" * 64,
            }
        )
        decision["knowledge_snapshot"] = []
        with tempfile.TemporaryDirectory() as directory:
            brief_path = Path(directory) / "brief.yaml"
            brief_path.write_text("approved brief\n", encoding="utf-8")
            decision["decision"]["source_context_sha256"] = (
                architecture_tool.file_sha256(brief_path)
            )
            with (
                patch.object(architecture_tool, "validate_file", return_value=decision),
                patch.object(
                    architecture_tool, "validate_design_brief", return_value=brief
                ),
                patch.object(
                    architecture_tool,
                    "validate_knowledge_tree",
                    return_value=(None, {}),
                ),
                self.assertRaisesRegex(
                    architecture_tool.ArchitectureError, "unknown technologies"
                ),
            ):
                architecture_tool.validate_decision(
                    Path(directory) / "decision.yaml",
                    design_brief_path=brief_path,
                )

    def test_required_and_prohibited_assessments_are_semantic(self) -> None:
        brief = _brief(
            _constraint("must-use", "required"),
            _constraint("must-not-use", "prohibited"),
        )
        decision = _decision(
            brief,
            selected_status={"must-use": "violated", "must-not-use": "satisfied"},
        )
        with self.assertRaisesRegex(architecture_tool.ArchitectureError, "required"):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), decision, brief
            )

        accepted_unknown = _decision(
            brief,
            selected_status={"must-use": "unknown", "must-not-use": "satisfied"},
            status="accepted",
        )
        with self.assertRaisesRegex(architecture_tool.ArchitectureError, "unknown"):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), accepted_unknown, brief
            )

        preferred_brief = _brief(_constraint("nice-to-have", "preferred"))
        preferred_unknown = _decision(
            preferred_brief,
            selected_status={"nice-to-have": "unknown"},
            status="accepted",
        )
        with self.assertRaisesRegex(architecture_tool.ArchitectureError, "preferred"):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), preferred_unknown, preferred_brief
            )

        prohibited_unknown = _decision(
            brief,
            selected_status={"must-use": "satisfied", "must-not-use": "unknown"},
        )
        with self.assertRaisesRegex(architecture_tool.ArchitectureError, "prohibited"):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), prohibited_unknown, brief
            )

        rejected_unknown = _decision(brief)
        rejected_unknown["options"][2]["constraint_assessments"][1]["status"] = (
            "unknown"
        )
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError, "hard-eliminated"
        ):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), rejected_unknown, brief
            )
        rejected_unknown["hard_eliminations"] = [
            {
                "option_id": "alternative",
                "rule": "prohibited-constraint",
                "evidence": ["The prohibited target remains unresolved."],
            }
        ]
        architecture_tool._validate_greenfield_target_architecture(
            Path("decision.yaml"), rejected_unknown, brief
        )

    def test_target_references_and_technology_ids_are_bounded(self) -> None:
        brief = _brief(_constraint("must-use", "required"))
        decision = _decision(brief)
        architecture_tool._validate_greenfield_target_architecture(
            Path("decision.yaml"), decision, brief
        )

        bad_interface = copy.deepcopy(decision)
        bad_interface["target_architecture"]["interfaces"][0]["to"] = "missing-unit"
        with self.assertRaisesRegex(architecture_tool.ArchitectureError, "runtime IDs"):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), bad_interface, brief
            )

        bad_technology = copy.deepcopy(decision)
        bad_technology["options"][0]["technologies"] = ["technology.postgresql"]
        bad_technology["target_architecture"]["runtime_units"][0]["technologies"] = [
            "technology.redis"
        ]
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError, "technologies"
        ):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), bad_technology, brief
            )

        bad_owner = copy.deepcopy(decision)
        bad_owner["target_architecture"]["data_ownership"][0]["owner"] = "not-a-unit"
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError, "runtime unit"
        ):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), bad_owner, brief
            )

        bad_boundary = copy.deepcopy(decision)
        bad_boundary["target_architecture"]["trust_boundaries"][0]["runtime_units"] = [
            "missing-unit"
        ]
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError, "trust boundaries"
        ):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), bad_boundary, brief
            )

        mismatched_brief = _brief(_constraint("must-use-fastapi", "required"))
        mismatched_brief["architecture_constraints"][0]["knowledge_id"] = (
            "technology.fastapi"
        )
        mismatched = _decision(mismatched_brief)
        for option in mismatched["options"]:
            option["constraint_assessments"][0]["knowledge_id"] = "technology.fastapi"
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError, "omits technology.fastapi"
        ):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), mismatched, mismatched_brief
            )

    def test_approved_brief_requires_bound_authorization_evidence(self) -> None:
        brief = _brief(_constraint("must-use", "required"))
        with (
            patch.object(architecture_tool, "validate_file", return_value=brief),
            self.assertRaisesRegex(architecture_tool.ArchitectureError, "no approval"),
        ):
            architecture_tool.validate_design_brief(Path("brief.yaml"))

    def test_structured_target_references_and_plan_bindings_are_exact(self) -> None:
        brief = _brief(_constraint("must-use", "required"))
        decision = _decision(brief)

        architecture_tool._validate_greenfield_target_architecture(
            Path("decision.yaml"), decision, brief
        )
        plan = {
            "items": [
                {
                    "target_bindings": {
                        "runtime_units": ["checkout", "payments"],
                        "deployment_units": ["order-deployment"],
                        "data_owners": ["payment-data"],
                        "interfaces": ["checkout-payments"],
                        "trust_boundaries": ["public-boundary"],
                        "technologies": ["technology.postgresql"],
                        "critical_flows": ["checkout-flow"],
                        "constraints": ["must-use"],
                    }
                }
            ]
        }
        architecture_tool._validate_greenfield_plan_bindings(
            Path("plan.yaml"), plan, decision, brief
        )

        bad_deployment = copy.deepcopy(decision)
        bad_deployment["target_architecture"]["runtime_units"][0]["deployment_unit"] = (
            "missing-deployment"
        )
        with self.assertRaisesRegex(architecture_tool.ArchitectureError, "deployment"):
            architecture_tool._validate_greenfield_target_architecture(
                Path("decision.yaml"), bad_deployment, brief
            )

        bad_binding = copy.deepcopy(plan)
        bad_binding["items"][0]["target_bindings"]["interfaces"] = ["missing-interface"]
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError, "unknown target IDs"
        ):
            architecture_tool._validate_greenfield_plan_bindings(
                Path("plan.yaml"), bad_binding, decision, brief
            )

        missing_coverage = copy.deepcopy(plan)
        missing_coverage["items"][0]["target_bindings"]["runtime_units"] = ["payments"]
        with self.assertRaisesRegex(
            architecture_tool.ArchitectureError, "does not cover target runtime_units"
        ):
            architecture_tool._validate_greenfield_plan_bindings(
                Path("plan.yaml"), missing_coverage, decision, brief
            )

    def test_greenfield_13_target_is_optional_for_decision_but_required_by_plan(
        self,
    ) -> None:
        brief = _brief(status="approved")
        brief["schema_version"] = "1.0"
        brief.pop("design_mode")
        brief.pop("architecture_constraints")
        brief["quality_scenarios"][0]["attribute"] = "recoverability"
        decision = _decision(brief)
        decision["schema_version"] = "1.3"
        decision.pop("target_architecture")
        decision["decision"].update(
            {
                "decision_kind": "greenfield",
                "source_context": "brief.yaml",
            }
        )
        decision["decision"]["source_context_sha256"] = "0" * 64
        for option in decision["options"]:
            option["technologies"] = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief_path = root / "brief.yaml"
            decision_path = root / "decision.yaml"
            brief_path.write_text("approved open brief\n", encoding="utf-8")
            decision["decision"]["source_context_sha256"] = (
                architecture_tool.file_sha256(brief_path)
            )
            with (
                patch.object(architecture_tool, "validate_file", return_value=decision),
                patch.object(
                    architecture_tool, "validate_design_brief", return_value=brief
                ),
                patch.object(
                    architecture_tool,
                    "validate_knowledge_tree",
                    return_value=(None, {}),
                ),
            ):
                architecture_tool.validate_decision(
                    decision_path,
                    design_brief_path=brief_path,
                )

            decision_path.write_text("accepted decision\n", encoding="utf-8")
            plan = {
                "schema_version": "1.3",
                "plan": {
                    "plan_kind": "greenfield-implementation",
                    "source_context": "brief.yaml",
                    "source_context_sha256": architecture_tool.file_sha256(brief_path),
                    "source_decision": "ADR-TARGET-001",
                    "source_decision_sha256": architecture_tool.file_sha256(
                        decision_path
                    ),
                },
                "items": [{"id": "PLAN-001", "target_bindings": {}}],
            }
            plan_path = root / "plan.yaml"
            plan_path.write_text("plan\n", encoding="utf-8")
            decision_without_target = copy.deepcopy(decision)
            with (
                patch.object(architecture_tool, "validate_file", return_value=plan),
                patch.object(
                    architecture_tool, "validate_design_brief", return_value=brief
                ),
                patch.object(
                    architecture_tool,
                    "validate_decision",
                    return_value=decision_without_target,
                ),
                self.assertRaisesRegex(
                    architecture_tool.ArchitectureError, "target_architecture"
                ),
            ):
                architecture_tool.validate_plan(
                    plan_path,
                    decision_path=decision_path,
                    design_brief_path=brief_path,
                    repository_root=root,
                )

    def test_greenfield_plan_binds_decision_and_brief_hashes(self) -> None:
        brief = _brief(_constraint("must-use", "required"))
        decision = _decision(brief, status="accepted")
        decision["decision"]["decision_kind"] = "greenfield"
        plan = {
            "schema_version": "1.3",
            "plan": {
                "plan_kind": "greenfield-implementation",
                "source_context": "brief.yaml",
                "source_decision": "ADR-TARGET-001",
                "source_decision_sha256": "decision-hash",
                "status": "draft",
            },
            "items": [
                {
                    "id": "PLAN-TARGET-001",
                    "target_bindings": {
                        "runtime_units": ["checkout", "payments"],
                        "deployment_units": ["order-deployment"],
                        "data_owners": ["payment-data"],
                        "interfaces": ["checkout-payments"],
                        "trust_boundaries": ["public-boundary"],
                        "technologies": ["technology.postgresql"],
                        "critical_flows": ["checkout-flow"],
                        "constraints": ["must-use"],
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief_path = root / "brief.yaml"
            decision_path = root / "decision.yaml"
            plan_path = root / "plan.yaml"
            brief_path.write_text("approved brief\n", encoding="utf-8")
            decision_path.write_text("accepted decision\n", encoding="utf-8")
            plan_path.write_text("greenfield plan\n", encoding="utf-8")
            plan["plan"]["source_decision_sha256"] = architecture_tool.file_sha256(
                decision_path
            )
            plan["plan"]["source_context_sha256"] = architecture_tool.file_sha256(
                brief_path
            )
            with (
                patch.object(architecture_tool, "validate_file", return_value=plan),
                patch.object(
                    architecture_tool, "validate_design_brief", return_value=brief
                ),
                patch.object(
                    architecture_tool, "validate_decision", return_value=decision
                ),
            ):
                validated = architecture_tool.validate_plan(
                    plan_path,
                    decision_path=decision_path,
                    design_brief_path=brief_path,
                    repository_root=root,
                )
            self.assertEqual(
                validated["plan"]["plan_kind"], "greenfield-implementation"
            )
            plan["plan"]["status"] = "complete"
            with (
                patch.object(architecture_tool, "validate_file", return_value=plan),
                self.assertRaisesRegex(
                    architecture_tool.ArchitectureError,
                    "no completion evidence",
                ),
            ):
                architecture_tool.validate_plan(
                    plan_path,
                    decision_path=decision_path,
                    design_brief_path=brief_path,
                    repository_root=root,
                )
            evidence_path = root / "completion.txt"
            evidence_path.write_text("provider output\n", encoding="utf-8")
            plan["items"][0]["completion_evidence"] = [
                {
                    "type": "test",
                    "location": "completion.txt",
                    "sha256": architecture_tool.file_sha256(evidence_path),
                    "result": "The bounded test passed.",
                    "observed_at": "2026-08-06T12:00:00+00:00",
                    "provider_id": "test-results",
                }
            ]
            with (
                patch.object(architecture_tool, "validate_file", return_value=plan),
                self.assertRaisesRegex(
                    architecture_tool.ArchitectureError,
                    "provider_id and run_id together",
                ),
            ):
                architecture_tool.validate_plan(
                    plan_path,
                    decision_path=decision_path,
                    design_brief_path=brief_path,
                    repository_root=root,
                )
            plan["plan"]["status"] = "draft"
            plan["items"][0].pop("completion_evidence")
            plan["plan"]["source_decision_sha256"] = "0" * 64
            with (
                patch.object(architecture_tool, "validate_file", return_value=plan),
                patch.object(
                    architecture_tool, "validate_design_brief", return_value=brief
                ),
                patch.object(
                    architecture_tool, "validate_decision", return_value=decision
                ),
                self.assertRaisesRegex(
                    architecture_tool.ArchitectureError, "source_decision_sha256"
                ),
            ):
                architecture_tool.validate_plan(
                    plan_path,
                    decision_path=decision_path,
                    design_brief_path=brief_path,
                    repository_root=root,
                )

    def test_legacy_plan_remains_readable_and_snapshot_mismatch_is_detectable(
        self,
    ) -> None:
        legacy = {
            "schema_version": "1.0",
            "plan": {"status": "draft"},
            "items": [{"id": "PLAN-LEGACY-001", "finding_ids": []}],
        }
        with patch.object(architecture_tool, "validate_file", return_value=legacy):
            self.assertEqual(
                architecture_tool.validate_plan(Path("legacy.yaml")), legacy
            )

        self.assertNotEqual(
            architecture_tool._declared_review_snapshot({"commit": "abc"}),
            architecture_tool._declared_review_snapshot({"commit": "def"}),
        )

    def test_review_gate_scope_excludes_unrelated_greenfield_chain(self) -> None:
        greenfield_decision = {
            "schema_version": "1.4",
            "decision": {
                "id": "ADR-GREENFIELD-001",
                "decision_kind": "greenfield",
                "source_context": ".architecture/architecture-design-brief.yaml",
                "status": "accepted",
            },
        }
        greenfield_plan = {
            "schema_version": "1.3",
            "plan": {
                "plan_kind": "greenfield-implementation",
                "source_decision": "ADR-GREENFIELD-001",
                "status": "complete",
            },
        }
        review = {"review": {"id": "REVIEW-REMEDIATION-001"}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_root = root / ".architecture"
            reviews_root = config_root / "reviews"
            reviews_root.mkdir(parents=True)
            brief_path = config_root / "architecture-design-brief.yaml"
            brief_path.write_text("brief\n", encoding="utf-8")
            decision_path = reviews_root / "greenfield-decision.yaml"
            plan_path = reviews_root / "greenfield-plan.yaml"
            decision_path.write_text("decision\n", encoding="utf-8")
            plan_path.write_text("plan\n", encoding="utf-8")

            def payload_for(path: Path) -> dict:
                return (
                    greenfield_decision
                    if path.name == decision_path.name
                    else greenfield_plan
                )

            with (
                patch.object(architecture_tool, "load_yaml", side_effect=payload_for),
                patch.object(architecture_tool, "validate_data"),
                patch.object(
                    architecture_tool,
                    "validate_decision",
                    return_value=greenfield_decision,
                ),
            ):
                decisions, plans = architecture_tool.related_governance_artifacts(
                    config_root,
                    reviews_root / "selected-review.yaml",
                    review,
                )

            self.assertEqual(decisions, [])
            self.assertEqual(plans, [])

    def test_current_greenfield_cli_validation_requires_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "artifact.yaml"
            artifact.write_text("schema_version: '1.4'\n", encoding="utf-8")
            decision_args = architecture_tool.build_parser().parse_args(
                ["validate-decision", str(artifact)]
            )
            with self.assertRaisesRegex(
                architecture_tool.ArchitectureError, "requires --project"
            ):
                architecture_tool.run(decision_args)

            artifact.write_text(
                "schema_version: '1.3'\n"
                "plan:\n"
                "  plan_kind: greenfield-implementation\n",
                encoding="utf-8",
            )
            plan_args = architecture_tool.build_parser().parse_args(
                ["validate-plan", str(artifact)]
            )
            with self.assertRaisesRegex(
                architecture_tool.ArchitectureError, "requires --project"
            ):
                architecture_tool.run(plan_args)

    def test_historical_cli_flag_reaches_decision_and_plan_validators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "artifact.yaml"
            artifact.write_text("schema_version: '1.3'\n", encoding="utf-8")
            for command, validator_name in (
                ("validate-decision", "validate_decision"),
                ("validate-plan", "validate_plan"),
            ):
                args = architecture_tool.build_parser().parse_args(
                    [command, str(artifact), "--historical"]
                )
                with patch.object(architecture_tool, validator_name) as validator:
                    self.assertEqual(architecture_tool.run(args), 0)
                self.assertTrue(
                    validator.call_args.kwargs["allow_unverifiable_historical"]
                )
                self.assertFalse(
                    validator.call_args.kwargs["require_current_selection"]
                )


if __name__ == "__main__":
    unittest.main()
