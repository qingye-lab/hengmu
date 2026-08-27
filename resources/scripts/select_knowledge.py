#!/usr/bin/env python3
"""Select relevant architecture knowledge with deterministic, explainable rules."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

import yaml
from build_project_profile import fact_ids
from jsonschema import Draft202012Validator, FormatChecker
from knowledge_model import KnowledgeEntry, validate_knowledge_tree

RESOURCE_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = RESOURCE_ROOT.parent
KNOWLEDGE_ROOT = RESOURCE_ROOT / "knowledge"
SCHEMA_ROOT = RESOURCE_ROOT / "schemas"
PLUGIN_MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
SELECTOR_SOURCE_PATH = RESOURCE_ROOT / "selector-source.json"
SELECTOR_IMPLEMENTATION_PATHS = (
    "requirements-runtime.lock",
    "resources/scripts/select_knowledge.py",
    "resources/scripts/build_project_profile.py",
    "resources/scripts/knowledge_model.py",
    "resources/schemas/repository-facts.schema.json",
    "resources/schemas/project-profile.schema.json",
    "resources/schemas/knowledge-entry.schema.json",
    "resources/schemas/knowledge-manifest.schema.json",
    "resources/schemas/knowledge-context.schema.json",
    "resources/schemas/knowledge-selection.schema.json",
    "resources/schemas/selector-source.schema.json",
)
CANONICAL_DOMAIN_ID_MAP = {
    "ai-agent": "domain.ai-agent",
    "backend-api": "domain.backend-api",
    "cloud-native-platform": "domain.cloud-native-platform",
    "data-platform": "domain.data-platform",
    "identity": "domain.identity",
    "mobile": "domain.mobile",
    "plugin-platform": "domain.plugin-platform",
    "real-time-system": "domain.real-time-system",
    "test-automation-platform": "domain.test-automation-platform",
    "web-frontend": "domain.web-frontend",
}
# Entry metadata and pre-1.1 profiles used these short domain names.  They are
# normalized only for compatibility; quality names such as reliability or
# security are deliberately not coerced into unrelated product domains.
LEGACY_DOMAIN_ALIASES = {
    "data": "data-platform",
    "delivery": "cloud-native-platform",
    "frontend": "web-frontend",
    "testing": "test-automation-platform",
}
SKILL_REQUIRED = {
    "project-architecture-audit": (
        "foundation.quality-attributes",
        "foundation.evidence-reasoning",
        "foundation.proportional-design",
        "foundation.system-boundaries",
        "foundation.data-ownership",
    ),
    "architecture-solution-advisor": (
        "foundation.quality-attributes",
        "foundation.tradeoff-analysis",
        "foundation.proportional-design",
        "foundation.technology-selection",
        "foundation.evolutionary-architecture",
    ),
    "architecture-finding-verifier": (
        "foundation.evidence-reasoning",
        "foundation.quality-attributes",
    ),
    "architecture-remediation-planner": (
        "foundation.evolutionary-architecture",
        "foundation.tradeoff-analysis",
    ),
    "ai-agent-architecture-audit": (
        "foundation.evidence-reasoning",
        "domain.ai-agent",
        "decision.workflow-vs-agent",
        "decision.single-agent-vs-multi-agent",
    ),
    "mobile-architecture-audit": (
        "foundation.evidence-reasoning",
        "domain.mobile",
        "decision.local-first-vs-server-first",
    ),
    "portfolio-architecture-audit": (
        "foundation.system-boundaries",
        "foundation.technology-selection",
        "anti-pattern.premature-generic-platform",
    ),
}
TECHNOLOGY_ALIASES = {
    "apache-kafka": "technology.apache-kafka",
    "openai-agents-sdk": "technology.openai-agents-sdk",
    "postgresql": "technology.postgresql",
    "redis-valkey": "technology.redis",
}
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
SELECTION_THRESHOLD = 20
GENERIC_TRIGGER_TOKENS = {
    "architecture",
    "data",
    "design",
    "deterministic",
    "knowledge",
    "platform",
    "runtime",
    "service",
    "system",
}
KNOWLEDGE_KINDS = (
    "foundation",
    "domain",
    "decision-guide",
    "architecture-style",
    "pattern",
    "technology-profile",
    "reference-architecture",
    "migration-guide",
    "anti-pattern",
    "case-study",
)
DEFAULT_KIND_BUDGETS = {
    "foundation": 6,
    "domain": 10,
    "decision-guide": 3,
    "architecture-style": 2,
    "pattern": 2,
    "technology-profile": 3,
    "reference-architecture": 1,
    "migration-guide": 1,
    "anti-pattern": 1,
    "case-study": 1,
}
SELECTION_CONTRACT_VERSION = "1.1"
SELECTION_POLICY_VERSION = "1.0"
KNOWLEDGE_CONTEXT_SCHEMA_VERSION = "1.1"
KNOWLEDGE_CONTEXT_DISCLOSURE_ORDER = [
    "operational-kernel",
    "project-context",
    "run-context",
    "source-evidence",
]
KNOWLEDGE_CONTEXT_FULL_SOURCE_REQUIRED_FOR = [
    "candidate-driving-claim",
    "ambiguity",
    "volatile-fact",
    "explicit-source-review",
]
DECISION_INTENT_ENTRIES = {
    "data-authority-topology": ("decision.local-first-vs-server-first",),
    "plugin-runtime-topology": (
        "domain.plugin-platform",
        "style.plugin-architecture",
    ),
}
AMBIGUOUS_TRIGGER_INTENTS = {
    "local-first": {"data-authority-topology"},
}


class SelectionError(RuntimeError):
    """Invalid knowledge selection input or result."""


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SelectionError(f"Missing YAML file: {path}") from exc
    except yaml.YAMLError as exc:
        raise SelectionError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SelectionError(f"Expected YAML mapping in {path}")
    return value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_tree_manifest(root: Path) -> list[dict[str, str]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": file_sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def knowledge_tree_sha256() -> str:
    """Hash every bundled Knowledge file, not only parsed entry metadata."""
    return canonical_sha256(file_tree_manifest(KNOWLEDGE_ROOT))


def selector_runtime_source() -> dict[str, str]:
    try:
        source = json.loads(SELECTOR_SOURCE_PATH.read_text(encoding="utf-8"))
        schema = json.loads(
            (SCHEMA_ROOT / "selector-source.schema.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise SelectionError(
            f"Missing Selector source contract: {exc.filename}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SelectionError(f"Invalid Selector source contract: {exc}") from exc
    errors = list(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(source)
    )
    if errors:
        raise SelectionError(f"Invalid Selector source contract: {errors[0].message}")
    plugin = json.loads(PLUGIN_MANIFEST_PATH.read_text(encoding="utf-8"))
    if (
        source["repository"] != plugin["repository"]
        or source["plugin_version"] != plugin["version"]
    ):
        raise SelectionError(
            "Selector source repository/version does not match plugin.json"
        )
    return cast(dict[str, str], source)


def selector_implementation_inputs() -> list[dict[str, str]]:
    return [
        {
            "path": relative_path,
            "sha256": file_sha256(PLUGIN_ROOT / relative_path),
        }
        for relative_path in SELECTOR_IMPLEMENTATION_PATHS
    ]


def selector_provenance(
    entries: dict[str, KnowledgeEntry] | None = None,
) -> dict[str, Any]:
    # The validated entries parameter keeps this public helper compatible with
    # 1.3 callers. The 1.1 contract hashes raw Knowledge bytes so catalog and
    # manifest changes cannot hide behind unchanged parsed metadata.
    del entries
    source = selector_runtime_source()
    implementation_inputs = selector_implementation_inputs()
    return {
        "contract_version": SELECTION_CONTRACT_VERSION,
        "source": {
            "repository": source["repository"],
            "commit": source["commit"],
            "plugin_version": source["plugin_version"],
            "plugin_manifest_sha256": file_sha256(PLUGIN_MANIFEST_PATH),
        },
        "implementation_inputs": implementation_inputs,
        "implementation_bundle_sha256": canonical_sha256(implementation_inputs),
        "knowledge_manifest_sha256": file_sha256(KNOWLEDGE_ROOT / "manifest.yaml"),
        "knowledge_tree_sha256": knowledge_tree_sha256(),
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "replay_mode": "creation-time-lock",
    }


def selection_result_sha256(selection: dict[str, Any]) -> str:
    return canonical_sha256(
        {key: value for key, value in selection.items() if key != "result_sha256"}
    )


def knowledge_context(
    selection: dict[str, Any],
    *,
    selection_lock_sha256: str,
) -> dict[str, Any]:
    context = {
        "schema_version": KNOWLEDGE_CONTEXT_SCHEMA_VERSION,
        "selection_lock_sha256": selection_lock_sha256,
        "selection_result_sha256": selection.get(
            "result_sha256",
            selection_result_sha256(selection),
        ),
        "disclosure": {
            "order": list(KNOWLEDGE_CONTEXT_DISCLOSURE_ORDER),
            "knowledge_mode": "validated-compact-projection",
            "source_binding": "selection-entry-sha256",
            "full_source_required_for": list(
                KNOWLEDGE_CONTEXT_FULL_SOURCE_REQUIRED_FOR
            ),
        },
        "selected": [
            {
                "id": item["id"],
                "path": item["path"],
                "sha256": item["sha256"],
                "priority": item["priority"],
                "reasons": list(item["reasons"]),
            }
            for item in selection["selection"]
        ],
    }
    schema = json.loads(
        (SCHEMA_ROOT / "knowledge-context.schema.json").read_text(encoding="utf-8")
    )
    errors = list(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(context)
    )
    if errors:
        raise SelectionError(
            f"Generated Knowledge context is invalid: {errors[0].message}"
        )
    return context


def normalized_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9.+-]+", value.lower())
        if len(token) > 1 and token not in STOP_WORDS
    }


def parse_kind_budget(value: str) -> tuple[str, int]:
    kind, separator, raw_limit = value.partition("=")
    if not separator or kind not in KNOWLEDGE_KINDS:
        raise argparse.ArgumentTypeError(
            "--kind-budget must be KIND=LIMIT with KIND one of: "
            + ", ".join(KNOWLEDGE_KINDS)
        )
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--kind-budget limit must be an integer: {value}"
        ) from exc
    if limit < 0:
        raise argparse.ArgumentTypeError(
            f"--kind-budget limit cannot be negative: {value}"
        )
    return kind, limit


def canonical_domain(value: str) -> str:
    return LEGACY_DOMAIN_ALIASES.get(value, value)


def select_knowledge(
    facts_path: Path,
    *,
    profile_path: Path | None,
    task: str,
    skill: str,
    maximum_entries: int,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    kind_budgets: dict[str, int] | None = None,
    maintainer_mode: bool = False,
    decision_intents: list[str] | None = None,
) -> dict[str, Any]:
    if maximum_entries < 1:
        raise SelectionError("maximum_entries must be positive")
    configured_kind_budgets = dict(DEFAULT_KIND_BUDGETS)
    for kind, limit in (kind_budgets or {}).items():
        if kind not in KNOWLEDGE_KINDS:
            raise SelectionError(f"Unknown knowledge kind budget: {kind}")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise SelectionError(
                f"Knowledge kind budget for {kind} must be a non-negative integer"
            )
        configured_kind_budgets[kind] = limit
    facts_path = facts_path.expanduser().resolve()
    facts = load_yaml(facts_path)
    facts_schema = json.loads(
        (SCHEMA_ROOT / "repository-facts.schema.json").read_text(encoding="utf-8")
    )
    facts_errors = list(Draft202012Validator(facts_schema).iter_errors(facts))
    if facts_errors:
        raise SelectionError(
            f"{facts_path} is not valid repository facts: {facts_errors[0].message}"
        )
    profile: dict[str, Any] | None = None
    if profile_path is not None:
        profile_path = profile_path.expanduser().resolve()
        profile = load_yaml(profile_path)
        profile_schema = json.loads(
            (SCHEMA_ROOT / "project-profile.schema.json").read_text(encoding="utf-8")
        )
        profile_errors = list(
            Draft202012Validator(
                profile_schema,
                format_checker=FormatChecker(),
            ).iter_errors(profile)
        )
        if profile_errors:
            raise SelectionError(
                f"{profile_path} is not a valid profile: {profile_errors[0].message}"
            )
    _, entries = validate_knowledge_tree(
        KNOWLEDGE_ROOT,
        schema_root=SCHEMA_ROOT,
    )
    includes = includes or []
    excludes = excludes or []
    decision_intents = sorted(set(decision_intents or []))
    unknown_intents = sorted(set(decision_intents) - set(DECISION_INTENT_ENTRIES))
    if unknown_intents:
        raise SelectionError("Unknown decision intents: " + ", ".join(unknown_intents))
    unknown_includes = sorted(set(includes) - set(entries))
    unknown_excludes = sorted(set(excludes) - set(entries))
    if unknown_includes or unknown_excludes:
        raise SelectionError(
            "Unknown knowledge IDs: " + ", ".join(unknown_includes + unknown_excludes)
        )
    overlap = sorted(set(includes) & set(excludes))
    if overlap:
        raise SelectionError(
            "Knowledge IDs cannot be both included and excluded: " + ", ".join(overlap)
        )
    scores: dict[str, int] = dict.fromkeys(entries, 0)
    reasons: dict[str, set[str]] = {entry_id: set() for entry_id in entries}
    priorities: dict[str, str] = dict.fromkeys(entries, "recommended")
    direct_relevance: dict[str, set[str]] = {entry_id: set() for entry_id in entries}

    def add(
        entry_id: str,
        score: int,
        reason: str,
        *,
        priority: str = "recommended",
        relevance: str | None = None,
    ) -> None:
        if entry_id not in entries or entry_id in excludes:
            return
        scores[entry_id] += score
        reasons[entry_id].add(reason)
        if relevance is not None:
            direct_relevance[entry_id].add(relevance)
        priority_rank = {"optional": 0, "recommended": 1, "required": 2}
        if priority_rank[priority] > priority_rank[priorities[entry_id]]:
            priorities[entry_id] = priority

    for entry_id in SKILL_REQUIRED.get(skill, ()):
        add(
            entry_id,
            100,
            f"Required foundation or lens for {skill}.",
            priority="required",
            relevance=f"skill:{skill}",
        )
    for entry_id in includes:
        add(
            entry_id,
            1000,
            "Explicit caller include.",
            priority="required",
            relevance=f"include:{entry_id}",
        )
    for intent in decision_intents:
        for entry_id in DECISION_INTENT_ENTRIES[intent]:
            add(
                entry_id,
                80,
                f"Decision intent matches: {intent}.",
                priority="required",
                relevance=f"intent:{intent}",
            )

    profile_domains: set[str] = set()
    if profile is not None:
        profile_domains.update(
            canonical_domain(str(item))
            for item in profile["project"].get("required_knowledge_domains", [])
        )
    project_domains = set(profile_domains)
    frameworks = fact_ids(facts, "frameworks")
    storage = fact_ids(facts, "storage")
    infrastructure = fact_ids(facts, "infrastructure")
    languages = fact_ids(facts, "languages")
    if frameworks & {"react", "nextjs", "vue", "astro", "vite"}:
        project_domains.add("web-frontend")
    if frameworks & {
        "aspnet-core",
        "django",
        "fastapi",
        "nestjs",
        "spring-boot",
    }:
        project_domains.add("backend-api")
    if frameworks & {
        "langgraph",
        "microsoft-agent-framework",
        "openai-agents-sdk",
    }:
        project_domains.add("ai-agent")
    if storage:
        project_domains.add("data-platform")
    if languages & {"dart", "kotlin", "swift"}:
        project_domains.add("mobile")
    if infrastructure & {
        "kubernetes",
    }:
        project_domains.add("cloud-native-platform")
    for domain in sorted(project_domains):
        mapped = CANONICAL_DOMAIN_ID_MAP.get(domain)
        if mapped is not None:
            add(
                mapped,
                80,
                f"Project profile or detected facts require {domain}.",
                priority="required" if domain in profile_domains else "recommended",
                relevance=f"domain:{domain}",
            )

    for fact_id in sorted(frameworks | storage | infrastructure):
        technology_id = TECHNOLOGY_ALIASES.get(
            fact_id,
            f"technology.{fact_id}",
        )
        add(
            technology_id,
            90,
            f"Repository facts detect {fact_id}.",
            relevance=f"fact:{fact_id}",
        )

    task_tokens = normalized_tokens(task)
    negated_tokens: set[str] = set()
    for match in re.finditer(
        r"\b(?:without|exclude|excluding|avoid|avoiding|do not|don't|not)\b"
        r"(?P<tail>[^.;\n]{0,120})",
        task.lower(),
    ):
        negated_tokens.update(normalized_tokens(match.group("tail")))
    for entry_id, knowledge_entry in entries.items():
        if entry_id in excludes:
            continue
        trigger_tokens = {
            token
            for trigger in knowledge_entry.metadata["triggers"]
            for token in normalized_tokens(str(trigger))
        }
        matched = sorted((task_tokens & trigger_tokens) - negated_tokens)
        matched = [
            token
            for token in matched
            if token not in AMBIGUOUS_TRIGGER_INTENTS
            or bool(set(decision_intents) & AMBIGUOUS_TRIGGER_INTENTS[token])
        ]
        entry_domains = {
            canonical_domain(str(domain))
            for domain in knowledge_entry.metadata["domains"]
        }
        matched_domains = sorted(entry_domains & project_domains)
        distinctive_matches = set(matched) - GENERIC_TRIGGER_TOKENS
        reference_match = (
            knowledge_entry.metadata["kind"] != "reference-architecture"
            or (
                bool(matched_domains)
                and (len(distinctive_matches) >= 1 or len(set(matched)) >= 2)
            )
            or len(distinctive_matches) >= 2
        )
        if (
            matched
            and reference_match
            and (matched_domains or len(matched) >= 2 or distinctive_matches)
        ):
            add(
                entry_id,
                25 + min(len(matched), 4),
                "Task matches trigger(s): " + ", ".join(matched),
            )
            direct_relevance[entry_id].update(f"task:{token}" for token in matched)
        if matched_domains:
            add(
                entry_id,
                6,
                "Entry domain matches project: " + ", ".join(matched_domains),
            )
            direct_relevance[entry_id].update(
                f"domain:{domain}" for domain in matched_domains
            )

    candidates = [
        (entry_id, score)
        for entry_id, score in scores.items()
        if score >= SELECTION_THRESHOLD and entry_id not in excludes
    ]
    candidates.sort(key=lambda item: (-item[1], item[0]))
    profile_required = {
        CANONICAL_DOMAIN_ID_MAP[domain]
        for domain in profile_domains
        if domain in CANONICAL_DOMAIN_ID_MAP
    }
    mandatory = {
        entry_id
        for entry_id in (
            *SKILL_REQUIRED.get(skill, ()),
            *includes,
            *profile_required,
            *(
                entry_id
                for intent in decision_intents
                for entry_id in DECISION_INTENT_ENTRIES[intent]
            ),
        )
        if entry_id in entries
    }
    blocked_mandatory = sorted(mandatory & set(excludes))
    if blocked_mandatory:
        raise SelectionError(
            "Explicit exclusions remove mandatory knowledge: "
            + ", ".join(blocked_mandatory)
        )
    if len(mandatory) > maximum_entries:
        raise SelectionError(
            f"Context budget {maximum_entries} is below {len(mandatory)} "
            "mandatory entries"
        )
    mandatory_by_kind: dict[str, int] = dict.fromkeys(KNOWLEDGE_KINDS, 0)
    for entry_id in mandatory:
        mandatory_by_kind[entries[entry_id].metadata["kind"]] += 1
    for kind, count in mandatory_by_kind.items():
        if count > configured_kind_budgets[kind]:
            raise SelectionError(
                f"Knowledge kind budget {kind}={configured_kind_budgets[kind]} "
                f"is below {count} mandatory entries"
            )

    def maturity(entry_id: str) -> str:
        return str(entries[entry_id].metadata.get("maturity", "standard"))

    def has_explicit_golden_replacement(entry_id: str) -> bool:
        """Return only a declared identity replacement, never a fuzzy peer.

        A common broad domain or task trigger is not enough to make a Golden
        entry a substitute for a standard one. A Golden entry may declare an
        exact backwards-compatible replacement through ``legacy_ids``.
        """
        return any(
            maturity(candidate_id) == "golden"
            and entry_id in entries[candidate_id].metadata.get("legacy_ids", [])
            for candidate_id in entries
        )

    def exact_non_golden_exception(entry_id: str) -> str | None:
        entry = entries[entry_id]
        if entry_id in SKILL_REQUIRED.get(skill, ()):
            return "skill-required contract dependency"
        if entry_id in includes:
            return "explicit caller include"
        if any(
            relevance.startswith("intent:") for relevance in direct_relevance[entry_id]
        ):
            return "exact decision-intent match"
        if maintainer_mode:
            return "maintainer mode"
        if entry_id in profile_required and not has_explicit_golden_replacement(
            entry_id
        ):
            return "profile-required domain has no declared Golden replacement"
        if (
            entry.metadata["kind"] == "technology-profile"
            and any(reason.startswith("fact:") for reason in direct_relevance[entry_id])
            and not has_explicit_golden_replacement(entry_id)
        ):
            return "detected technology has no declared Golden replacement"
        return None

    def advisor_allows(entry_id: str) -> bool:
        if skill != "architecture-solution-advisor" or maturity(entry_id) == "golden":
            return True
        exception = exact_non_golden_exception(entry_id)
        if exception is not None:
            reasons[entry_id].add("Non-Golden exception: " + exception + ".")
            return True
        return False

    selected_ids: list[str] = []
    selected_by_kind: dict[str, int] = dict.fromkeys(KNOWLEDGE_KINDS, 0)
    budget_exclusions: dict[str, str] = {}

    def select_if_budgeted(entry_id: str) -> bool:
        kind = str(entries[entry_id].metadata["kind"])
        if len(selected_ids) >= maximum_entries:
            budget_exclusions[entry_id] = (
                "Relevant but outside the configured total context budget."
            )
            return False
        if selected_by_kind[kind] >= configured_kind_budgets[kind]:
            budget_exclusions[entry_id] = (
                f"Relevant but outside the configured {kind} context budget."
            )
            return False
        selected_ids.append(entry_id)
        selected_by_kind[kind] += 1
        return True

    for entry_id in sorted(mandatory, key=lambda item: (-scores[item], item)):
        if not advisor_allows(entry_id):
            raise SelectionError(
                f"Mandatory knowledge {entry_id} lacks an auditable "
                "architecture-solution-advisor exception"
            )
        if not select_if_budgeted(entry_id):
            raise SelectionError(
                f"Cannot satisfy mandatory knowledge budget: {entry_id}"
            )

    for entry_id, _ in candidates:
        if entry_id in mandatory:
            continue
        if not advisor_allows(entry_id):
            budget_exclusions[entry_id] = (
                "Architecture solution advisor defaults to Golden discretionary "
                "knowledge; this standard entry has no explicit exception."
            )
            continue
        select_if_budgeted(entry_id)

    if not selected_ids:
        fallback = "foundation.evidence-reasoning"
        if configured_kind_budgets["foundation"] < 1:
            raise SelectionError(
                "Knowledge kind budget foundation=0 cannot fit the default "
                "evidence discipline entry"
            )
        select_if_budgeted(fallback)
        reasons[fallback].add("Default evidence discipline fallback.")
        priorities[fallback] = "required"

    # Expand one hop only. Related entries are useful context, but never displace
    # explicitly required or normally relevant entries and never bypass excludes.
    seed_ids = list(selected_ids)
    for seed_id in seed_ids:
        if len(selected_ids) >= maximum_entries:
            break
        related_ids = [
            related_id
            for related_id in entries[seed_id].metadata["related"]
            if related_id not in excludes and related_id not in selected_ids
        ]
        for related_id in related_ids:
            direct_relevance[related_id].add(f"relation:{seed_id}")
            if not advisor_allows(related_id):
                budget_exclusions[related_id] = (
                    "Architecture solution advisor defaults to Golden discretionary "
                    "knowledge; this standard related entry has no explicit exception."
                )
                continue
            if not select_if_budgeted(related_id):
                continue
            priorities[related_id] = "optional"
            reasons[related_id].add(f"One-hop relation from {seed_id}.")

    selected: list[dict[str, Any]] = []
    for entry_id in selected_ids:
        entry: KnowledgeEntry = entries[entry_id]
        selected.append(
            {
                "id": entry_id,
                "version": entry.metadata["version"],
                "path": entry.path.relative_to(KNOWLEDGE_ROOT).as_posix(),
                "sha256": entry.sha256,
                "kind": entry.metadata["kind"],
                "maturity": maturity(entry_id),
                "priority": priorities[entry_id],
                "reasons": sorted(reasons[entry_id]),
            }
        )
    excluded_records = []
    for entry_id in sorted(set(entries) - set(selected_ids)):
        if entry_id in excludes:
            reason = "Explicit caller exclusion."
        elif entry_id in budget_exclusions:
            reason = budget_exclusions[entry_id]
        elif scores[entry_id] >= SELECTION_THRESHOLD:
            reason = "Relevant but outside the configured context budget."
        elif scores[entry_id] > 0:
            reason = "Domain-only relevance is below the selection threshold."
        else:
            reason = (
                "No project fact, profile domain, task trigger, or skill rule "
                "selected it."
            )
        excluded_records.append({"id": entry_id, "reason": reason})
    result: dict[str, Any] = {
        "schema_version": "1.4",
        "selection": selected,
        "excluded": excluded_records,
        "inputs": {
            "skill": skill,
            "task": task,
            "facts_sha256": file_sha256(facts_path),
            "maintainer_mode": maintainer_mode,
            "includes": sorted(includes),
            "excludes": sorted(excludes),
            "decision_intents": decision_intents,
            "project_commit": str(facts.get("repository", {}).get("commit", "unknown")),
        },
        "budget": {
            "maximum_entries": maximum_entries,
            "selected_entries": len(selected),
            "per_kind": {
                kind: {
                    "maximum_entries": configured_kind_budgets[kind],
                    "selected_entries": selected_by_kind[kind],
                }
                for kind in KNOWLEDGE_KINDS
            },
        },
    }
    if profile_path is not None:
        result["inputs"]["profile_sha256"] = file_sha256(profile_path)
    result["selector"] = selector_provenance(entries)
    result["result_sha256"] = selection_result_sha256(result)
    schema = json.loads(
        (SCHEMA_ROOT / "knowledge-selection.schema.json").read_text(encoding="utf-8")
    )
    errors = list(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(result)
    )
    if errors:
        raise SelectionError(
            f"Generated knowledge selection is invalid: {errors[0].message}"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--max-entries", type=int, default=24)
    parser.add_argument(
        "--kind-budget",
        action="append",
        default=[],
        type=parse_kind_budget,
        metavar="KIND=LIMIT",
    )
    parser.add_argument(
        "--maintainer",
        action="store_true",
        help=(
            "Allow architecture-solution-advisor to include relevant standard "
            "knowledge with an auditable maintainer-mode exception."
        ),
    )
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument(
        "--decision-intent",
        action="append",
        default=[],
        help=(
            "Bind an explicit semantic decision namespace, such as "
            "plugin-runtime-topology or data-authority-topology."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--context-output",
        type=Path,
        help="Also write the compact model-facing selected-Knowledge sidecar.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        kind_budgets: dict[str, int] = {}
        for kind, limit in args.kind_budget:
            if kind in kind_budgets:
                raise SelectionError(f"Duplicate knowledge kind budget: {kind}")
            kind_budgets[kind] = limit
        result = select_knowledge(
            args.facts,
            profile_path=args.profile,
            task=args.task,
            skill=args.skill,
            maximum_entries=args.max_entries,
            includes=args.include,
            excludes=args.exclude,
            kind_budgets=kind_budgets,
            maintainer_mode=args.maintainer,
            decision_intents=args.decision_intent,
        )
        output = args.output.expanduser().resolve()
        if output.exists() and not args.force:
            raise SelectionError(
                f"Refusing to overwrite existing output without --force: {output}"
            )
        context_output = (
            args.context_output.expanduser().resolve()
            if args.context_output is not None
            else None
        )
        if context_output is not None and context_output.exists() and not args.force:
            raise SelectionError(
                "Refusing to overwrite existing context output without --force: "
                f"{context_output}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            yaml.safe_dump(result, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        if context_output is not None:
            context_output.parent.mkdir(parents=True, exist_ok=True)
            context_output.write_text(
                yaml.safe_dump(
                    knowledge_context(
                        result,
                        selection_lock_sha256=file_sha256(output),
                    ),
                    sort_keys=False,
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
    except (SelectionError, OSError) as exc:
        print(f"Knowledge selection failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"Knowledge selection written: {args.output.resolve()} "
        f"({result['budget']['selected_entries']} entries)"
    )
    if args.context_output is not None:
        print(f"Knowledge context written: {args.context_output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
