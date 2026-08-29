from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import yaml

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_ROOT = ROOT / "resources" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

from build_project_profile import build_profile  # noqa: E402
from inspect_repository import InspectionError, inspect_repository  # noqa: E402
from knowledge_model import (  # noqa: E402
    KnowledgeError,
    validate_knowledge_tree,
    validate_markdown_entry,
)
from select_knowledge import SelectionError, select_knowledge  # noqa: E402


class TargetArchitectureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_empty_facts(self) -> Path:
        facts_path = self.root / "empty-repository-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1.1",
                    "repository": {
                        "root": ".",
                        "commit": "unknown",
                        "dirty": False,
                        "scanned_at": "2026-08-29T00:00:00+00:00",
                        "scope": ["."],
                    },
                    "languages": [],
                    "frameworks": [],
                    "storage": [],
                    "interfaces": [],
                    "infrastructure": [],
                    "artifacts": {
                        "manifests": [],
                        "migrations": [],
                        "api_definitions": [],
                        "ci": [],
                        "deployments": [],
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return facts_path

    def test_inspector_collects_only_bounded_observations(self) -> None:
        (self.root / "app.py").write_text(
            "from fastapi import FastAPI\napp = FastAPI()\n",
            encoding="utf-8",
        )
        (self.root / "requirements.txt").write_text(
            "fastapi==1.0\npsycopg==3.0\n",
            encoding="utf-8",
        )
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )

        self.assertEqual(
            {item["id"] for item in facts["frameworks"]},
            {"fastapi"},
        )
        self.assertEqual(
            {item["id"] for item in facts["storage"]},
            {"postgresql"},
        )
        serialized = json.dumps(facts, sort_keys=True).lower()
        self.assertEqual(facts["schema_version"], "1.1")
        self.assertTrue(
            all(
                item["role"] in {"runtime", "production"}
                for field in ("languages", "frameworks", "storage")
                for item in facts[field]
            )
        )
        self.assertNotIn("recommendation", serialized)
        self.assertNotIn("finding", serialized)
        self.assertNotIn("severity", serialized)

    def test_inspector_rejects_scope_escape(self) -> None:
        with self.assertRaisesRegex(InspectionError, "escapes repository root"):
            inspect_repository(self.root, scope_values=["../outside"])

    def test_profile_resolves_nested_architecture_facts_from_repository_root(
        self,
    ) -> None:
        inputs = self.root / ".architecture" / "reviews" / "inputs"
        inputs.mkdir(parents=True)
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 30, tzinfo=UTC),
        )
        facts_path = inputs / "current-repository-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(facts, sort_keys=False),
            encoding="utf-8",
        )

        profile = build_profile(facts_path)

        self.assertEqual(profile["project"]["name"], self.root.name)
        self.assertEqual(
            profile["project"]["repository_facts"]["path"],
            ".architecture/reviews/inputs/current-repository-facts.yaml",
        )
        self.assertIn(
            ".architecture/reviews/inputs/current-repository-facts.yaml",
            profile["project"]["profile_sources"]["detected"],
        )

    def test_fixture_only_swift_is_observable_but_does_not_infer_mobile(
        self,
    ) -> None:
        fixture = self.root / "benchmarks" / "fixtures" / "mobile-editor"
        fixture.mkdir(parents=True)
        (fixture / "Repository.swift").write_text(
            "struct Repository {}\n",
            encoding="utf-8",
        )
        fixture_facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        swift = [item for item in fixture_facts["languages"] if item["id"] == "swift"]
        self.assertEqual(
            swift,
            [
                {
                    "id": "swift",
                    "role": "benchmark-fixture",
                    "evidence": ["benchmarks/fixtures/mobile-editor/Repository.swift"],
                }
            ],
        )
        facts_path = self.root / "fixture-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(fixture_facts, sort_keys=False),
            encoding="utf-8",
        )
        fixture_profile = build_profile(facts_path)
        self.assertEqual(
            fixture_profile["project"]["required_knowledge_domains"],
            [],
        )
        self.assertEqual(
            fixture_profile["project"]["required_reviews"],
            ["project-architecture"],
        )
        self.assertEqual(
            fixture_profile["project"]["rule_packs"],
            ["project-core"],
        )
        fixture_selection = select_knowledge(
            facts_path,
            profile_path=None,
            task="Review the repository architecture.",
            skill="project-architecture-audit",
            maximum_entries=16,
        )
        self.assertNotIn(
            "domain.mobile",
            {item["id"] for item in fixture_selection["selection"]},
        )

        (self.root / "Application.swift").write_text(
            "struct Application {}\n",
            encoding="utf-8",
        )
        mixed_facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        self.assertEqual(
            {
                item["role"]
                for item in mixed_facts["languages"]
                if item["id"] == "swift"
            },
            {"production", "benchmark-fixture"},
        )
        mixed_path = self.root / "mixed-facts.yaml"
        mixed_path.write_text(
            yaml.safe_dump(mixed_facts, sort_keys=False),
            encoding="utf-8",
        )
        mixed_profile = build_profile(mixed_path)
        self.assertIn(
            "mobile",
            mixed_profile["project"]["required_knowledge_domains"],
        )

    def test_noncontributing_roles_remain_observable_and_legacy_facts_contribute(
        self,
    ) -> None:
        example = self.root / "examples"
        example.mkdir()
        (example / "package.json").write_text(
            json.dumps({"dependencies": {"react": "1.0.0"}}),
            encoding="utf-8",
        )
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        self.assertEqual(
            next(item for item in facts["frameworks"] if item["id"] == "react")["role"],
            "example",
        )
        facts_path = self.root / "role-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(facts, sort_keys=False),
            encoding="utf-8",
        )
        profile = build_profile(facts_path)
        self.assertNotIn(
            "frontend",
            profile["project"]["required_knowledge_domains"],
        )
        selection = select_knowledge(
            facts_path,
            profile_path=None,
            task="Review a bounded architecture.",
            skill="unregistered-test-skill",
            maximum_entries=8,
        )
        self.assertNotIn(
            "technology.react",
            {item["id"] for item in selection["selection"]},
        )

        legacy = copy.deepcopy(facts)
        legacy["schema_version"] = "1.0"
        legacy_path = self.root / "legacy-facts.yaml"
        legacy_path.write_text(
            yaml.safe_dump(legacy, sort_keys=False),
            encoding="utf-8",
        )
        legacy_profile = build_profile(legacy_path)
        self.assertIn(
            "frontend",
            legacy_profile["project"]["required_knowledge_domains"],
        )

    def test_all_noncontributing_file_roles_do_not_infer_product_domains(
        self,
    ) -> None:
        role_paths = {
            "test": "tests/Repository.swift",
            "benchmark-fixture": "benchmarks/fixtures/sample/Repository.swift",
            "example": "examples/Repository.swift",
            "documentation": "docs/Repository.swift",
            "generated": "generated_client.swift",
        }
        for relative in role_paths.values():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("struct Repository {}\n", encoding="utf-8")
        # Generated and vendor trees are deliberately pruned before traversal.
        # Their files cannot become product facts merely because they are large
        # or contain a familiar framework name.
        for relative in (
            "generated/Repository.swift",
            "vendor/Repository.swift",
            "third_party/Repository.swift",
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("struct Repository {}\n", encoding="utf-8")

        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        swift_roles = {
            item["role"] for item in facts["languages"] if item["id"] == "swift"
        }
        self.assertEqual(swift_roles, set(role_paths))
        observed_paths = {
            path for item in facts["languages"] for path in item["evidence"]
        }
        self.assertTrue(
            observed_paths.isdisjoint(
                {
                    "generated/Repository.swift",
                    "vendor/Repository.swift",
                    "third_party/Repository.swift",
                }
            )
        )

        facts_path = self.root / "noncontributing-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(facts, sort_keys=False),
            encoding="utf-8",
        )
        profile = build_profile(facts_path)
        self.assertEqual(
            profile["project"]["required_knowledge_domains"],
            [],
        )
        selection = select_knowledge(
            facts_path,
            profile_path=None,
            task="Review only product architecture facts.",
            skill="unregistered-test-skill",
            maximum_entries=8,
        )
        self.assertNotIn(
            "domain.mobile",
            {item["id"] for item in selection["selection"]},
        )

    def test_generated_role_has_deterministic_precedence_over_example_path(
        self,
    ) -> None:
        path = self.root / "examples" / "generated_client.swift"
        path.parent.mkdir()
        path.write_text("struct Client {}\n", encoding="utf-8")
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        self.assertEqual(
            facts["languages"],
            [
                {
                    "id": "swift",
                    "role": "generated",
                    "evidence": ["examples/generated_client.swift"],
                }
            ],
        )

    def test_development_only_dependencies_do_not_create_product_facts(self) -> None:
        (self.root / "package.json").write_text(
            json.dumps(
                {
                    "devDependencies": {
                        "react": "1.0.0",
                        "vite": "1.0.0",
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.root / "requirements-dev.txt").write_text(
            "fastapi==1.0\n",
            encoding="utf-8",
        )
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        self.assertEqual(
            facts["frameworks"],
            [
                {
                    "id": "fastapi",
                    "category": "backend",
                    "role": "test",
                    "evidence": ["requirements-dev.txt"],
                }
            ],
        )
        self.assertEqual(facts["storage"], [])
        self.assertIn("requirements-dev.txt", facts["artifacts"]["manifests"])
        facts_path = self.root / "development-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(facts, sort_keys=False),
            encoding="utf-8",
        )
        profile = build_profile(facts_path)
        self.assertNotIn(
            "backend-api",
            profile["project"]["required_knowledge_domains"],
        )

    def test_static_setup_py_runtime_dependencies_remain_product_facts(self) -> None:
        (self.root / "setup.py").write_text(
            """from setuptools import setup

RUNTIME_REQUIREMENTS = ["fastapi>=0.110", "psycopg[binary]>=3"]
setup(name="example", install_requires=RUNTIME_REQUIREMENTS)
""",
            encoding="utf-8",
        )
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        self.assertIn(
            {
                "id": "fastapi",
                "category": "backend",
                "role": "production",
                "evidence": ["setup.py"],
            },
            facts["frameworks"],
        )
        self.assertIn(
            {
                "id": "postgresql",
                "role": "production",
                "evidence": ["setup.py"],
            },
            facts["storage"],
        )

    def test_python_dependency_names_are_exact_not_substrings(self) -> None:
        (self.root / "requirements.txt").write_text(
            "\n".join(
                [
                    "nextcloud-client==1.0",
                    "agentscope>=0.2",
                    "pgvector~=0.3",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )

        self.assertEqual(facts["frameworks"], [])
        self.assertEqual(facts["storage"], [])

        (self.root / "requirements.txt").write_text(
            "\n".join(
                [
                    "fastapi>=0.110",
                    "openai-agents>=0.2",
                    "psycopg[binary]>=3",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        exact = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        self.assertEqual(
            {item["id"] for item in exact["frameworks"]},
            {"fastapi", "openai-agents-sdk"},
        )
        self.assertEqual(
            {item["id"] for item in exact["storage"]},
            {"postgresql"},
        )

    def test_selector_uses_facts_and_respects_negative_scope(self) -> None:
        (self.root / "package.json").write_text(
            json.dumps(
                {
                    "dependencies": {
                        "react": "1.0.0",
                        "vite": "1.0.0",
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.root / "requirements.txt").write_text(
            "fastapi==1.0\npsycopg==3.0\n",
            encoding="utf-8",
        )
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        facts_path = self.root / "repository-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(facts, sort_keys=False),
            encoding="utf-8",
        )

        selection = select_knowledge(
            facts_path,
            profile_path=None,
            task=(
                "Review React, FastAPI, and PostgreSQL boundaries without "
                "Kafka, Kubernetes, iOS, Event Sourcing, or multi-agent design."
            ),
            skill="project-architecture-audit",
            maximum_entries=24,
        )
        selected = {item["id"] for item in selection["selection"]}

        self.assertTrue(
            {
                "technology.react",
                "technology.fastapi",
                "technology.postgresql",
                "domain.web-frontend",
                "domain.backend-api",
            }.issubset(selected)
        )
        self.assertTrue(
            {
                "technology.apache-kafka",
                "technology.kubernetes",
                "domain.mobile",
                "decision.single-agent-vs-multi-agent",
                "pattern.cqrs-event-sourcing",
            }.isdisjoint(selected)
        )
        self.assertEqual(
            len(selection["selection"]) + len(selection["excluded"]),
            211,
        )
        priorities = {item["id"]: item["priority"] for item in selection["selection"]}
        self.assertEqual(
            priorities["foundation.evidence-reasoning"],
            "required",
        )
        self.assertEqual(priorities["technology.fastapi"], "recommended")
        self.assertEqual(selection["schema_version"], "1.4")
        self.assertTrue(
            all({"kind", "maturity"}.issubset(item) for item in selection["selection"])
        )

    def test_selector_enforces_per_kind_budgets_and_advisor_golden_policy(
        self,
    ) -> None:
        (self.root / "package.json").write_text(
            json.dumps({"dependencies": {"react": "1.0.0"}}),
            encoding="utf-8",
        )
        (self.root / "requirements.txt").write_text(
            "fastapi==1.0\n",
            encoding="utf-8",
        )
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        facts_path = self.root / "repository-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(facts, sort_keys=False),
            encoding="utf-8",
        )
        selection = select_knowledge(
            facts_path,
            profile_path=None,
            task="Compare React and FastAPI architecture options.",
            skill="architecture-solution-advisor",
            maximum_entries=16,
            includes=["style.modular-monolith"],
            kind_budgets={
                "foundation": 6,
                "domain": 2,
                "technology-profile": 2,
            },
        )
        selected = {item["id"]: item for item in selection["selection"]}

        self.assertEqual(selected["technology.fastapi"]["maturity"], "golden")
        self.assertEqual(selected["technology.react"]["maturity"], "standard")
        self.assertIn(
            "Non-Golden exception: detected technology has no declared Golden "
            "replacement.",
            selected["technology.react"]["reasons"],
        )
        self.assertEqual(
            selected["style.modular-monolith"]["maturity"],
            "standard",
        )
        self.assertIn(
            "Non-Golden exception: explicit caller include.",
            selected["style.modular-monolith"]["reasons"],
        )
        for record in selected.values():
            if record["maturity"] != "golden":
                self.assertTrue(
                    any(
                        reason.startswith("Non-Golden exception:")
                        for reason in record["reasons"]
                    )
                )
        for kind, budget in selection["budget"]["per_kind"].items():
            self.assertLessEqual(
                budget["selected_entries"],
                budget["maximum_entries"],
                kind,
            )

        with self.assertRaisesRegex(
            SelectionError,
            "foundation=4 is below 5 mandatory entries",
        ):
            select_knowledge(
                facts_path,
                profile_path=None,
                task="Compare a bounded architecture.",
                skill="architecture-solution-advisor",
                maximum_entries=16,
                kind_budgets={"foundation": 4},
            )
        with self.assertRaisesRegex(
            SelectionError,
            "Explicit exclusions remove mandatory knowledge",
        ):
            select_knowledge(
                facts_path,
                profile_path=None,
                task="Compare a bounded architecture.",
                skill="architecture-solution-advisor",
                maximum_entries=16,
                excludes=["foundation.tradeoff-analysis"],
            )

        no_exception = select_knowledge(
            facts_path,
            profile_path=None,
            task="Compare modular monolith architecture options.",
            skill="architecture-solution-advisor",
            maximum_entries=16,
        )
        self.assertNotIn(
            "style.modular-monolith",
            {item["id"] for item in no_exception["selection"]},
        )
        maintainer = select_knowledge(
            facts_path,
            profile_path=None,
            task="Compare modular monolith architecture options.",
            skill="architecture-solution-advisor",
            maximum_entries=16,
            maintainer_mode=True,
        )
        maintainer_entries = {item["id"]: item for item in maintainer["selection"]}
        self.assertIn("style.modular-monolith", maintainer_entries)
        self.assertIn(
            "Non-Golden exception: maintainer mode.",
            maintainer_entries["style.modular-monolith"]["reasons"],
        )
        self.assertTrue(maintainer["inputs"]["maintainer_mode"])

    def test_ai_knowledge_2026_positive_selection_is_exact_and_explainable(
        self,
    ) -> None:
        facts_path = self.write_empty_facts()
        cases = (
            (
                "Review the mcp boundary.",
                "unregistered-test-skill",
                "technology.model-context-protocol",
                "technology-profile",
                "Task matches trigger(s): mcp",
            ),
            (
                "Review the a2a handoff.",
                "unregistered-test-skill",
                "technology.a2a-protocol",
                "technology-profile",
                "Task matches trigger(s): a2a",
            ),
            (
                "Review progressive-disclosure and skill.md boundaries.",
                "unregistered-test-skill",
                "technology.agent-skills",
                "technology-profile",
                "Task matches trigger(s): progressive-disclosure, skill.md",
            ),
            (
                "Review genai trace fields.",
                "unregistered-test-skill",
                "technology.opentelemetry-genai",
                "technology-profile",
                "Task matches trigger(s): genai",
            ),
            (
                "Design an agent-evaluation corpus.",
                "unregistered-test-skill",
                "decision.agent-evaluation-design",
                "decision-guide",
                "Task matches trigger(s): agent-evaluation",
            ),
            (
                "Review tool-sandbox complete-mediation controls.",
                "unregistered-test-skill",
                "reference.secure-agent-tool-runtime",
                "reference-architecture",
                "Task matches trigger(s): complete-mediation, tool-sandbox",
            ),
        )

        for task, skill, expected_id, expected_kind, reason in cases:
            with self.subTest(expected_id=expected_id):
                selection = select_knowledge(
                    facts_path,
                    profile_path=None,
                    task=task,
                    skill=skill,
                    maximum_entries=12,
                )
                selected = {item["id"]: item for item in selection["selection"]}
                self.assertIn(expected_id, selected)
                self.assertEqual(selected[expected_id]["kind"], expected_kind)
                self.assertEqual(selected[expected_id]["maturity"], "standard")
                self.assertEqual(selected[expected_id]["priority"], "recommended")
                self.assertIn(reason, selected[expected_id]["reasons"])

    def test_ai_knowledge_2026_avoids_generic_and_negated_requests(self) -> None:
        facts_path = self.write_empty_facts()
        cases = (
            (
                "Review human and team skills development.",
                {"technology.agent-skills"},
            ),
            (
                "Review ordinary OpenTelemetry service tracing.",
                {"technology.opentelemetry-genai"},
            ),
            (
                "Review a generic agent runtime.",
                {"reference.secure-agent-tool-runtime"},
            ),
            (
                "Review generic software tests.",
                {"decision.agent-evaluation-design"},
            ),
            (
                "Review the system without MCP, A2A, Agent Skills, or GenAI telemetry.",
                {
                    "technology.model-context-protocol",
                    "technology.a2a-protocol",
                    "technology.agent-skills",
                    "technology.opentelemetry-genai",
                },
            ),
        )

        for task, forbidden_ids in cases:
            with self.subTest(task=task):
                selection = select_knowledge(
                    facts_path,
                    profile_path=None,
                    task=task,
                    skill="unregistered-test-skill",
                    maximum_entries=12,
                )
                selected = {item["id"] for item in selection["selection"]}
                self.assertTrue(forbidden_ids.isdisjoint(selected))

        mcp = select_knowledge(
            facts_path,
            profile_path=None,
            task="Review the mcp boundary.",
            skill="unregistered-test-skill",
            maximum_entries=12,
        )
        a2a = select_knowledge(
            facts_path,
            profile_path=None,
            task="Review the a2a handoff.",
            skill="unregistered-test-skill",
            maximum_entries=12,
        )
        self.assertNotIn(
            "technology.a2a-protocol",
            {item["id"] for item in mcp["selection"]},
        )
        self.assertNotIn(
            "technology.model-context-protocol",
            {item["id"] for item in a2a["selection"]},
        )

    def test_ai_knowledge_2026_budget_is_deterministic_and_accounted(self) -> None:
        facts_path = self.write_empty_facts()
        selection = select_knowledge(
            facts_path,
            profile_path=None,
            task="Review mcp, a2a, skill.md, and genai boundaries.",
            skill="unregistered-test-skill",
            maximum_entries=12,
            kind_budgets={"technology-profile": 3},
        )
        selected = {item["id"] for item in selection["selection"]}
        self.assertEqual(
            selected
            & {
                "technology.model-context-protocol",
                "technology.a2a-protocol",
                "technology.agent-skills",
                "technology.opentelemetry-genai",
            },
            {
                "technology.a2a-protocol",
                "technology.agent-skills",
                "technology.model-context-protocol",
            },
        )
        excluded = {item["id"]: item["reason"] for item in selection["excluded"]}
        self.assertEqual(
            excluded["technology.opentelemetry-genai"],
            "Relevant but outside the configured technology-profile context budget.",
        )
        self.assertEqual(selection["budget"]["selected_entries"], len(selected))
        self.assertEqual(
            selection["budget"]["per_kind"]["technology-profile"],
            {"maximum_entries": 3, "selected_entries": 3},
        )

    def test_ai_knowledge_2026_advisor_requires_an_exact_standard_exception(
        self,
    ) -> None:
        facts_path = self.write_empty_facts()
        discretionary = select_knowledge(
            facts_path,
            profile_path=None,
            task="Compare mcp architecture options.",
            skill="architecture-solution-advisor",
            maximum_entries=16,
        )
        self.assertNotIn(
            "technology.model-context-protocol",
            {item["id"] for item in discretionary["selection"]},
        )
        excluded = {item["id"]: item["reason"] for item in discretionary["excluded"]}
        self.assertEqual(
            excluded["technology.model-context-protocol"],
            "Architecture solution advisor defaults to Golden discretionary "
            "knowledge; this standard entry has no explicit exception.",
        )

        explicit = select_knowledge(
            facts_path,
            profile_path=None,
            task="Compare mcp architecture options.",
            skill="architecture-solution-advisor",
            maximum_entries=16,
            includes=["technology.model-context-protocol"],
        )
        selected = {item["id"]: item for item in explicit["selection"]}
        self.assertEqual(
            selected["technology.model-context-protocol"]["priority"],
            "required",
        )
        self.assertIn(
            "Non-Golden exception: explicit caller include.",
            selected["technology.model-context-protocol"]["reasons"],
        )

    def test_selector_uses_decision_intent_to_disambiguate_local_runtime(
        self,
    ) -> None:
        facts = inspect_repository(
            self.root,
            scanned_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        facts_path = self.root / "repository-facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(facts, sort_keys=False),
            encoding="utf-8",
        )

        plugin_runtime = select_knowledge(
            facts_path,
            profile_path=None,
            task="Preserve the local-first plugin runtime.",
            skill="architecture-solution-advisor",
            maximum_entries=16,
            decision_intents=["plugin-runtime-topology"],
        )
        selected = {item["id"]: item for item in plugin_runtime["selection"]}
        self.assertIn("style.plugin-architecture", selected)
        self.assertEqual(
            selected["style.plugin-architecture"]["priority"],
            "required",
        )
        self.assertIn(
            "Non-Golden exception: exact decision-intent match.",
            selected["style.plugin-architecture"]["reasons"],
        )
        self.assertTrue(
            {
                "decision.local-first-vs-server-first",
                "decision.optimistic-vs-pessimistic-update",
                "decision.state-management",
            }.isdisjoint(selected)
        )

        data_authority = select_knowledge(
            facts_path,
            profile_path=None,
            task="Choose local-first data authority and conflict behavior.",
            skill="architecture-solution-advisor",
            maximum_entries=16,
            decision_intents=["data-authority-topology"],
        )
        self.assertIn(
            "decision.local-first-vs-server-first",
            {item["id"] for item in data_authority["selection"]},
        )
        with self.assertRaisesRegex(SelectionError, "Unknown decision intents"):
            select_knowledge(
                facts_path,
                profile_path=None,
                task="Compare an unknown architecture decision.",
                skill="architecture-solution-advisor",
                maximum_entries=16,
                decision_intents=["unknown-topology"],
            )

    def test_selector_uses_canonical_profile_domains_and_avoids_generic_reference(
        self,
    ) -> None:
        selection = select_knowledge(
            ROOT / ".architecture" / "repository-facts.yaml",
            profile_path=ROOT / ".architecture" / "profile.yaml",
            task="Review this plugin's architecture knowledge quality and contracts.",
            skill="project-architecture-audit",
            maximum_entries=24,
        )
        selected = {item["id"]: item["priority"] for item in selection["selection"]}

        self.assertEqual(selected["domain.plugin-platform"], "required")
        self.assertEqual(
            selected["domain.test-automation-platform"],
            "required",
        )
        self.assertNotIn("domain.backend-api", selected)
        self.assertNotIn("domain.data-platform", selected)
        self.assertNotIn("domain.cloud-native-platform", selected)
        self.assertNotIn("reference.multi-tenant-knowledge-base", selected)

    def test_selector_expands_one_hop_as_optional_without_displacing_required(
        self,
    ) -> None:
        selection = select_knowledge(
            ROOT / ".architecture" / "repository-facts.yaml",
            profile_path=None,
            task="Review a bounded decision.",
            skill="unregistered-test-skill",
            maximum_entries=3,
            includes=["decision.cache-strategy"],
        )
        selected = {item["id"]: item["priority"] for item in selection["selection"]}

        self.assertEqual(selected["decision.cache-strategy"], "required")
        self.assertEqual(selected["pattern.materialized-view"], "optional")
        self.assertLessEqual(len(selected), 3)

    def test_selector_one_hop_respects_kind_budgets_and_is_deterministic(
        self,
    ) -> None:
        facts_path = self.root / "facts.yaml"
        facts_path.write_text(
            yaml.safe_dump(
                {
                    "schema_version": "1.1",
                    "repository": {
                        "root": ".",
                        "commit": "unknown",
                        "dirty": False,
                        "scanned_at": "2026-07-29T00:00:00+00:00",
                        "scope": ["."],
                    },
                    "languages": [],
                    "frameworks": [],
                    "storage": [],
                    "interfaces": [],
                    "infrastructure": [],
                    "artifacts": {
                        "manifests": [],
                        "migrations": [],
                        "api_definitions": [],
                        "ci": [],
                        "deployments": [],
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        first = select_knowledge(
            facts_path,
            profile_path=None,
            task="Compare a bounded architecture.",
            skill="unregistered-test-skill",
            maximum_entries=8,
            includes=["technology.fastapi"],
            kind_budgets={"domain": 0},
        )
        second = select_knowledge(
            facts_path,
            profile_path=None,
            task="Compare a bounded architecture.",
            skill="unregistered-test-skill",
            maximum_entries=8,
            includes=["technology.fastapi"],
            kind_budgets={"domain": 0},
        )
        self.assertEqual(first, second)
        self.assertNotIn(
            "domain.backend-api",
            {item["id"] for item in first["selection"]},
        )
        excluded = {item["id"]: item["reason"] for item in first["excluded"]}
        self.assertEqual(
            excluded["domain.backend-api"],
            "Relevant but outside the configured domain context budget.",
        )

    def test_selector_does_not_route_generic_knowledge_token_to_reference(
        self,
    ) -> None:
        selection = select_knowledge(
            ROOT / ".architecture" / "repository-facts.yaml",
            profile_path=None,
            task="Review knowledge quality.",
            skill="unregistered-test-skill",
            maximum_entries=8,
        )
        selected = {item["id"] for item in selection["selection"]}

        self.assertNotIn("reference.multi-tenant-knowledge-base", selected)

    def test_knowledge_tree_has_all_target_packs_and_entries(self) -> None:
        manifest, entries = validate_knowledge_tree(
            ROOT / "resources" / "knowledge",
            schema_root=ROOT / "resources" / "schemas",
            today=date(2026, 8, 29),
        )

        self.assertEqual(len(manifest["packs"]), 10)
        self.assertEqual(len(entries), 211)
        self.assertEqual(
            manifest["_validated_counts"],
            {
                "foundations": 22,
                "domains": 17,
                "decision-guides": 24,
                "architecture-styles": 17,
                "patterns": 30,
                "technology-profiles": 51,
                "reference-architectures": 16,
                "migration-guides": 18,
                "anti-patterns": 10,
                "case-studies": 6,
            },
        )
        self.assertTrue(all(manifest["_validated_counts"].values()))

    def test_ai_knowledge_2026_freshness_boundaries_are_exact(self) -> None:
        cases = (
            (
                ROOT
                / "resources"
                / "knowledge"
                / "technology-profiles"
                / "model-context-protocol.md",
                "technology-profile",
                date(2026, 10, 13),
                date(2026, 10, 14),
            ),
            (
                ROOT
                / "resources"
                / "knowledge"
                / "technology-profiles"
                / "a2a-protocol.md",
                "technology-profile",
                date(2026, 11, 27),
                date(2026, 11, 28),
            ),
        )
        for path, expected_kind, last_current, first_stale in cases:
            with self.subTest(path=path.name):
                validate_markdown_entry(
                    path,
                    schema_root=ROOT / "resources" / "schemas",
                    expected_kind=expected_kind,
                    today=last_current,
                )
                with self.assertRaisesRegex(KnowledgeError, "is stale by 1 day"):
                    validate_markdown_entry(
                        path,
                        schema_root=ROOT / "resources" / "schemas",
                        expected_kind=expected_kind,
                        today=first_stale,
                    )

    def test_knowledge_validator_rejects_shallow_and_stale_entries(self) -> None:
        source = (
            ROOT / "resources" / "knowledge" / "foundations" / "quality-attributes.md"
        )
        shallow = self.root / "shallow.md"
        shallow.write_text(
            re.sub(
                r"(?ms)^## Mechanism\n.*?(?=^## )",
                "## Mechanism\n\nx\n\n",
                source.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(KnowledgeError, "too shallow"):
            validate_markdown_entry(
                shallow,
                schema_root=ROOT / "resources" / "schemas",
                expected_kind="foundation",
                today=date(2026, 7, 29),
            )

        stale = self.root / "stale.md"
        stale.write_text(
            source.read_text(encoding="utf-8").replace(
                "last_reviewed: '2026-07-28'",
                "last_reviewed: '2020-01-01'",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(KnowledgeError, "is stale"):
            validate_markdown_entry(
                stale,
                schema_root=ROOT / "resources" / "schemas",
                expected_kind="foundation",
                today=date(2026, 7, 29),
            )

    def test_knowledge_tree_rejects_unknown_relations(self) -> None:
        knowledge_root = self.root / "knowledge"
        manifest = yaml.safe_load(
            (ROOT / "resources" / "knowledge" / "manifest.yaml").read_text(
                encoding="utf-8"
            )
        )
        for pack in manifest["packs"]:
            pack["required"] = pack["id"] == "foundations"
            (knowledge_root / pack["path"]).mkdir(parents=True)
        (knowledge_root / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False),
            encoding="utf-8",
        )
        source = (
            ROOT / "resources" / "knowledge" / "foundations" / "quality-attributes.md"
        )
        entry = source.read_text(encoding="utf-8").replace(
            "related: []",
            "related:\n- foundation.does-not-exist",
        )
        (knowledge_root / "foundations" / "quality-attributes.md").write_text(
            entry,
            encoding="utf-8",
        )

        with self.assertRaisesRegex(KnowledgeError, "unknown related IDs"):
            validate_knowledge_tree(
                knowledge_root,
                schema_root=ROOT / "resources" / "schemas",
                today=date(2026, 7, 29),
            )

    def test_decision_snapshot_rejects_stale_knowledge_hash(self) -> None:
        import architecture_tool

        _, entries = validate_knowledge_tree(
            ROOT / "resources" / "knowledge",
            schema_root=ROOT / "resources" / "schemas",
            today=date(2026, 8, 29),
        )
        decision = architecture_tool.load_yaml(
            ROOT / "resources" / "templates" / "architecture-decision.yaml"
        )
        decision["knowledge_snapshot"] = [
            {
                "id": entry_id,
                "version": entries[entry_id].metadata["version"],
                "sha256": entries[entry_id].sha256,
            }
            for entry_id in (
                "style.modular-monolith",
                "style.microservices",
                "pattern.idempotency-key",
                "technology.fastapi",
                "technology.postgresql",
                "technology.rabbitmq",
            )
        ]
        brief_path = ROOT / "resources" / "templates" / "architecture-design-brief.yaml"
        decision["decision"]["source_context_sha256"] = architecture_tool.file_sha256(
            brief_path
        )
        decision_path = self.root / "decision.yaml"
        decision_path.write_text(
            yaml.safe_dump(decision, sort_keys=False),
            encoding="utf-8",
        )
        approved_brief = architecture_tool.load_yaml(brief_path)
        approved_brief["brief"]["status"] = "approved"
        with patch.object(
            architecture_tool,
            "validate_design_brief",
            return_value=approved_brief,
        ):
            architecture_tool.validate_decision(
                decision_path,
                design_brief_path=brief_path,
            )

        tampered = copy.deepcopy(decision)
        tampered["knowledge_snapshot"][0]["sha256"] = "0" * 64
        decision_path.write_text(
            yaml.safe_dump(tampered, sort_keys=False),
            encoding="utf-8",
        )
        with (
            patch.object(
                architecture_tool,
                "validate_design_brief",
                return_value=approved_brief,
            ),
            self.assertRaisesRegex(
                architecture_tool.ArchitectureError,
                "hash is stale",
            ),
        ):
            architecture_tool.validate_decision(
                decision_path,
                design_brief_path=brief_path,
            )


if __name__ == "__main__":
    unittest.main()
