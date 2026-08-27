"""Internal types for schema-validated architecture artifacts.

JSON Schema remains authoritative. Callers must only use these TypedDicts after
the corresponding schema validator has accepted an untrusted mapping.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class FindingVerification(TypedDict):
    status: str
    rationale: str


class Finding(TypedDict):
    id: str
    rule_id: str
    status: str
    evidence: list[dict[str, Any]]
    impact: dict[str, Any]
    verification: NotRequired[FindingVerification]
    fingerprint: NotRequired[str]


class ReviewIdentity(TypedDict):
    id: str
    kind: str
    subject: dict[str, Any]
    performed_at: str
    scope: list[str]
    reviewers: list[str]
    verification_state: NotRequired[str]
    commit: NotRequired[str]
    rule_packs: NotRequired[list[dict[str, Any]]]


class ReviewArtifact(TypedDict):
    schema_version: str
    review: ReviewIdentity
    summary: dict[str, Any]
    coverage: list[dict[str, Any]]
    findings: list[Finding]
    evidence_sources: list[dict[str, Any]]
    limitations: list[str]
    coverage_complete: NotRequired[bool]
    selected_knowledge: NotRequired[list[dict[str, Any]]]
    critical_flow_coverage: NotRequired[list[dict[str, Any]]]


class ProjectIdentity(TypedDict):
    id: str
    rule_packs: list[str]
    review_requirements: list[dict[str, Any]]
    critical_flows_file: str
    repository_facts: NotRequired[dict[str, Any]]


class ProfileArtifact(TypedDict):
    schema_version: str
    project: ProjectIdentity


class GatePolicyArtifact(TypedDict):
    schema_version: str
    stages: dict[str, Any]
    release_requirements: dict[str, Any]


class DecisionIdentity(TypedDict):
    id: str
    knowledge_selection_path: NotRequired[str]


class DecisionArtifact(TypedDict):
    schema_version: str
    decision: DecisionIdentity


class PlanIdentity(TypedDict):
    id: str


class PlanArtifact(TypedDict):
    schema_version: str
    plan: PlanIdentity


class KnowledgeSelectionInputs(TypedDict):
    facts_sha256: str
    profile_sha256: NotRequired[str]


class KnowledgeSelectionArtifact(TypedDict):
    schema_version: str
    selection: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    inputs: KnowledgeSelectionInputs


class EvidenceRunIdentity(TypedDict):
    id: str
    provider_id: str
    trust: str
    commit: str


class EvidenceRunArtifact(TypedDict):
    schema_version: str
    run: EvidenceRunIdentity
    result: dict[str, Any]
