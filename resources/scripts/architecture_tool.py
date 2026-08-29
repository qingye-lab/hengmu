#!/usr/bin/env python3
"""Initialize, validate, and gate architecture review artifacts."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from build_project_profile import ProfileBuildError, build_profile, derive_domains
from inspect_repository import InspectionError, inspect_repository
from knowledge_model import KnowledgeError, validate_knowledge_tree
from select_knowledge import (
    SELECTOR_IMPLEMENTATION_PATHS,
    SelectionError,
    knowledge_context,
    parse_kind_budget,
    select_knowledge,
    selection_result_sha256,
    selector_provenance,
)

try:
    import yaml
    from jsonschema import Draft202012Validator, FormatChecker
except ModuleNotFoundError as exc:  # pragma: no cover - environment failure
    print(
        f"architecture_tool.py requires PyYAML and jsonschema (missing {exc.name}).",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


SHARED_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_ROOT = SHARED_ROOT / "schemas"
TEMPLATE_ROOT = SHARED_ROOT / "templates"
RULE_ROOT = SHARED_ROOT / "rules"
KNOWLEDGE_ROOT = SHARED_ROOT / "knowledge"
EVIDENCE_PROVIDER_ROOT = SHARED_ROOT / "evidence-providers"
SEVERITY_ORDER = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}
VERIFICATION_LEVEL_ORDER = {
    "V0": 0,
    "V1": 1,
    "V2": 2,
    "V3": 3,
    "V4": 4,
    "V5": 5,
}
TOOL_VERSION = "1.1.3"
TRUSTED_POLICY_VERSIONS = {"1.1", "1.2"}
BENCHMARK_TREATMENT_CONDITIONS = ("base", "full", "compressed")
REVIEW_KIND_CORE_PACK = {
    "project": "project-core",
    "ai-agent": "ai-agent-core",
    "mobile": "mobile-core",
    "portfolio": "portfolio-core",
}
REVIEW_WORKFLOW_KIND = {
    "project-architecture": "project",
    "ai-agent-architecture": "ai-agent",
    "mobile-architecture": "mobile",
    "portfolio-architecture": "portfolio",
}
EVOLUTION_ASSESSMENT_HEADINGS = (
    "Current baseline",
    "Measurable gap",
    "Volatile claims",
    "Compatibility and migration",
    "Operational and team fit",
    "Lock-in and exit",
    "Rollback",
    "Shadow or pilot evidence",
    "Revisit triggers",
)
SHELL_INTERPRETERS = frozenset(
    {
        "ash",
        "bash",
        "csh",
        "cmd",
        "cmd.exe",
        "dash",
        "elvish",
        "fish",
        "ksh",
        "ksh93",
        "mksh",
        "nu",
        "oil",
        "osh",
        "pdksh",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "sh",
        "tcsh",
        "xonsh",
        "ysh",
        "zsh",
    }
)


class ArchitectureError(RuntimeError):
    """User-facing input or contract error."""


_SCHEMA_CACHE: dict[tuple[Path, str], dict[str, Any]] = {}
_SCHEMA_VALIDATOR_CACHE: dict[tuple[Path, str], Draft202012Validator] = {}


def highest_verification_level(*levels: str) -> str:
    return max(levels, key=lambda value: VERIFICATION_LEVEL_ORDER[value])


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if len(slug) < 3:
        slug = f"{slug or 'project'}-app"
    return slug[:64].rstrip("-")


def normalize_yaml_scalars(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: normalize_yaml_scalars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_yaml_scalars(item) for item in value]
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArchitectureError(f"Missing file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ArchitectureError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArchitectureError(f"Expected a YAML mapping in {path}")
    normalized = normalize_yaml_scalars(value)
    if not isinstance(normalized, dict) or not all(
        isinstance(key, str) for key in normalized
    ):
        raise ArchitectureError(f"Expected string mapping keys in {path}")
    return cast(dict[str, Any], normalized)


def write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(value, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except FileNotFoundError as exc:
        raise ArchitectureError(f"Missing file: {path}") from exc


def canonical_sha256(value: Any) -> str:
    normalized = normalize_yaml_scalars(value)
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def finding_fingerprint(subject_id: str, finding: dict[str, Any]) -> str:
    evidence_identity = [
        {
            "type": item["type"],
            "repository": item.get("repository", ""),
            "commit": item.get("commit", item.get("source_commit", "")),
            "path": item.get("path", item.get("location", "")),
            "symbol": item.get("symbol", ""),
            "blob_sha": item.get("blob_sha", ""),
        }
        for item in finding["evidence"]
    ]
    return canonical_sha256(
        {
            "subject_id": subject_id,
            "rule_id": finding["rule_id"],
            "invariant": finding["invariant"],
            "severity": finding["severity"],
            "evidence": evidence_identity,
        }
    )


def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_ROOT / name
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise ArchitectureError(f"Missing bundled schema: {path}") from exc
    digest = sha256_bytes(content)
    cache_key = (path, digest)
    cached = _SCHEMA_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        schema = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ArchitectureError(f"Invalid bundled schema {path}: {exc}") from exc
    if not isinstance(schema, dict):
        raise ArchitectureError(f"Invalid bundled schema {path}: expected object")
    _SCHEMA_CACHE[cache_key] = schema
    return schema


def format_validation_path(parts: Any) -> str:
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def validate_data(
    data: dict[str, Any],
    schema_name: str,
    source: Path,
) -> None:
    schema_path = SCHEMA_ROOT / schema_name
    try:
        schema_digest = sha256_bytes(schema_path.read_bytes())
    except FileNotFoundError as exc:
        raise ArchitectureError(f"Missing bundled schema: {schema_path}") from exc
    cache_key = (schema_path, schema_digest)
    validator = _SCHEMA_VALIDATOR_CACHE.get(cache_key)
    if validator is None:
        validator = Draft202012Validator(
            load_schema(schema_name),
            format_checker=FormatChecker(),
        )
        _SCHEMA_VALIDATOR_CACHE[cache_key] = validator
    errors = sorted(validator.iter_errors(data), key=lambda item: list(item.path))
    if errors:
        messages = [
            f"{format_validation_path(error.absolute_path)}: {error.message}"
            for error in errors
        ]
        raise ArchitectureError(
            f"{source} does not match {schema_name}:\n  - " + "\n  - ".join(messages)
        )


def validate_file(path: Path, schema_name: str) -> dict[str, Any]:
    data = load_yaml(path)
    validate_data(data, schema_name, path)
    return data


def parse_timestamp(value: str, field: str) -> datetime:
    """Parse one schema-validated RFC 3339 timestamp with an explicit offset."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchitectureError(f"Invalid timestamp for {field}: {value}") from exc
    if parsed.tzinfo is None:
        raise ArchitectureError(f"Timestamp for {field} must include a timezone")
    return parsed


def validate_governance_run(
    path: Path,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Validate an informational governance run record.

    A run manifest documents the surrounding high-risk workflow. It is never
    a substitute for a Review, an Evidence Provider run, an acceptance, a
    signature, or any other gate input. Consequently this validation checks
    shape, chronology, and path containment, but does not promote declared
    hashes or artifacts into trusted evidence.
    """
    payload = validate_file(path, "governance-run-manifest.schema.json")
    run = payload["run"]
    started_at = parse_timestamp(run["started_at"], "run.started_at")
    completed_at = parse_timestamp(run["completed_at"], "run.completed_at")
    if completed_at < started_at:
        raise ArchitectureError(f"{path} completes before it starts")

    path_records: list[tuple[str, str]] = [
        ("run.source.scope", scoped_path) for scoped_path in run["source"]["scope"]
    ]
    path_records.extend(
        ("run.selected_knowledge", item["path"]) for item in run["selected_knowledge"]
    )
    path_records.extend(("run.tools_used", item["path"]) for item in run["tools_used"])
    path_records.extend(
        ("run.artifacts_written", item["path"]) for item in run["artifacts_written"]
    )
    for field, relative_path in path_records:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts or "\\" in relative_path:
            raise ArchitectureError(
                f"{path} {field} path escapes repository: {relative_path}"
            )
        if project_root is not None:
            require_within_root(
                project_root,
                project_root.resolve() / candidate,
                f"{path}:{field}",
            )

    for field, records in (
        ("run.selected_knowledge", run["selected_knowledge"]),
        ("run.tools_used", run["tools_used"]),
    ):
        ids = [item["id"] for item in records]
        if len(ids) != len(set(ids)):
            raise ArchitectureError(f"{path} repeats an ID in {field}")
        paths = [item["path"] for item in records]
        if len(paths) != len(set(paths)):
            raise ArchitectureError(f"{path} repeats a path in {field}")
    artifact_paths = [item["path"] for item in run["artifacts_written"]]
    if len(artifact_paths) != len(set(artifact_paths)):
        raise ArchitectureError(f"{path} repeats a path in run.artifacts_written")
    return payload


REPOSITORY_RENAMES = {
    "https://github.com/liyanqing90/codex-architecture-governance": (
        "https://github.com/qingye-lab/hengmu"
    ),
    "https://github.com/liyanqing90/hengmu": "https://github.com/qingye-lab/hengmu",
}
REPOSITORY_IDENTITY_RENAMES = {
    "codex-architecture-governance": "hengmu",
}


def normalize_repository_identity(value: str) -> str:
    normalized = value.strip().lower()
    return REPOSITORY_IDENTITY_RENAMES.get(normalized, normalized)


def repository_identities_match(left: str, right: str) -> bool:
    return normalize_repository_identity(left) == normalize_repository_identity(right)


def normalize_git_repository(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.startswith("git@") and ":" in normalized:
        host, path = normalized[4:].split(":", 1)
        normalized = f"https://{host}/{path}"
    elif normalized.startswith("ssh://git@"):
        normalized = "https://" + normalized.removeprefix("ssh://git@")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    normalized = normalized.lower()
    return REPOSITORY_RENAMES.get(normalized, normalized)


def selector_source_git_root(expected_repository: str) -> Path:
    configured = os.environ.get("CAG_SELECTOR_SOURCE_ROOT")
    candidate = (
        Path(configured).expanduser().resolve()
        if configured
        else SHARED_ROOT.parent.resolve()
    )
    process = git_process(candidate, "rev-parse", "--show-toplevel")
    if process.returncode != 0:
        raise ArchitectureError(
            "Selector Runtime Manifest is not current and its historical Git "
            "source is unavailable; set CAG_SELECTOR_SOURCE_ROOT to a clone of "
            f"{expected_repository}"
        )
    root = Path(process.stdout.strip()).resolve()
    origin = git_output(root, "remote", "get-url", "origin")
    if normalize_git_repository(origin) != normalize_git_repository(
        expected_repository
    ):
        raise ArchitectureError(
            "Selector historical source repository does not match the Runtime "
            f"Manifest: {origin}"
        )
    return root


def archived_knowledge_index(
    root: Path,
    commit: str,
) -> dict[str, dict[str, str]]:
    manifest_path = "resources/knowledge/manifest.yaml"
    try:
        manifest = yaml.safe_load(
            git_blob_bytes(root, commit, manifest_path).decode("utf-8")
        )
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ArchitectureError(
            f"Selector historical Knowledge manifest is invalid at {commit}: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("packs"), list):
        raise ArchitectureError(
            f"Selector historical Knowledge manifest is invalid at {commit}"
        )
    index: dict[str, dict[str, str]] = {}
    seen_paths: set[str] = set()
    for pack in manifest["packs"]:
        if not isinstance(pack, dict) or not isinstance(pack.get("path"), str):
            raise ArchitectureError(
                "Selector historical Knowledge manifest has an invalid pack at "
                f"{commit}"
            )
        relative_pack = Path(pack["path"])
        if (
            relative_pack.is_absolute()
            or ".." in relative_pack.parts
            or "\\" in pack["path"]
        ):
            raise ArchitectureError(
                "Selector historical Knowledge pack escapes the Knowledge root: "
                + pack["path"]
            )
        prefix = f"resources/knowledge/{relative_pack.as_posix()}".rstrip("/")
        output = git_output(
            root,
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            prefix,
        )
        paths = [
            item
            for item in output.splitlines()
            if item.endswith(".md") and item.startswith(prefix + "/")
        ]
        for git_path in paths:
            knowledge_path = git_path.removeprefix("resources/knowledge/")
            if knowledge_path in seen_paths:
                raise ArchitectureError(
                    "Selector historical Knowledge manifest repeats path "
                    + knowledge_path
                )
            seen_paths.add(knowledge_path)
            content = git_blob_bytes(root, commit, git_path)
            try:
                lines = content.decode("utf-8").splitlines()
            except UnicodeDecodeError as exc:
                raise ArchitectureError(
                    f"Selector historical Knowledge is not UTF-8: {git_path}"
                ) from exc
            if not lines or lines[0] != "---":
                raise ArchitectureError(
                    f"Selector historical Knowledge has no frontmatter: {git_path}"
                )
            try:
                closing = lines.index("---", 1)
                metadata = yaml.safe_load("\n".join(lines[1:closing]))
            except (ValueError, yaml.YAMLError) as exc:
                raise ArchitectureError(
                    f"Selector historical Knowledge frontmatter is invalid: {git_path}"
                ) from exc
            required = ("id", "version", "kind")
            if not isinstance(metadata, dict) or any(
                not isinstance(metadata.get(field), str) for field in required
            ):
                raise ArchitectureError(
                    f"Selector historical Knowledge metadata is incomplete: {git_path}"
                )
            entry_id = metadata["id"]
            if entry_id in index:
                raise ArchitectureError(
                    f"Selector historical Knowledge repeats ID {entry_id}"
                )
            maturity = metadata.get("maturity", "standard")
            if not isinstance(maturity, str):
                raise ArchitectureError(
                    f"Selector historical Knowledge maturity is invalid: {git_path}"
                )
            index[entry_id] = {
                "version": metadata["version"],
                "path": knowledge_path,
                "sha256": sha256_bytes(content),
                "kind": metadata["kind"],
                "maturity": maturity,
            }
    if not index:
        raise ArchitectureError(
            f"Selector historical Knowledge tree is empty at {commit}"
        )
    return index


def verify_archived_selector_runtime(
    selection: dict[str, Any],
    path: Path,
) -> None:
    selector = selection["selector"]
    if selector["contract_version"] != "1.1":
        raise ArchitectureError(
            f"{path} has an unverifiable legacy Selector Runtime lock"
        )
    source = selector["source"]
    root = selector_source_git_root(source["repository"])
    commit = source["commit"]
    process = git_process(root, "cat-file", "-e", f"{commit}^{{commit}}")
    if process.returncode != 0:
        raise ArchitectureError(
            f"{path} Selector source commit is unreachable: {commit}"
        )

    implementation_inputs = selector["implementation_inputs"]
    paths = [item["path"] for item in implementation_inputs]
    if paths != list(SELECTOR_IMPLEMENTATION_PATHS):
        raise ArchitectureError(
            f"{path} Selector implementation input set is incomplete or reordered"
        )
    if selector["implementation_bundle_sha256"] != canonical_sha256(
        implementation_inputs
    ):
        raise ArchitectureError(
            f"{path} Selector implementation bundle hash does not match its inputs"
        )
    for item in implementation_inputs:
        actual = sha256_bytes(git_blob_bytes(root, commit, item["path"]))
        if actual != item["sha256"]:
            raise ArchitectureError(
                f"{path} Selector historical input hash does not match "
                f"{item['path']} at {commit}"
            )

    plugin_path = ".codex-plugin/plugin.json"
    plugin_bytes = git_blob_bytes(root, commit, plugin_path)
    if sha256_bytes(plugin_bytes) != source["plugin_manifest_sha256"]:
        raise ArchitectureError(
            f"{path} Selector plugin manifest hash does not match {commit}"
        )
    try:
        plugin = json.loads(plugin_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchitectureError(
            f"{path} Selector plugin manifest is invalid at {commit}: {exc}"
        ) from exc
    if plugin.get("version") != source["plugin_version"] or normalize_git_repository(
        str(plugin.get("repository", ""))
    ) != normalize_git_repository(source["repository"]):
        raise ArchitectureError(
            f"{path} Selector plugin identity does not match {commit}"
        )

    knowledge_manifest = git_blob_bytes(
        root,
        commit,
        "resources/knowledge/manifest.yaml",
    )
    if sha256_bytes(knowledge_manifest) != selector["knowledge_manifest_sha256"]:
        raise ArchitectureError(
            f"{path} Selector historical Knowledge manifest hash does not match"
        )
    tree_sha256, _ = git_tree_manifest(
        root,
        commit,
        "resources/knowledge",
    )
    if tree_sha256 != selector["knowledge_tree_sha256"]:
        raise ArchitectureError(
            f"{path} Selector historical Knowledge tree hash does not match"
        )

    historical = archived_knowledge_index(root, commit)
    selected_ids = {item["id"] for item in selection["selection"]}
    excluded_ids = {item["id"] for item in selection["excluded"]}
    if selected_ids | excluded_ids != set(historical):
        raise ArchitectureError(
            f"{path} does not account for the complete historical Knowledge tree"
        )
    for item in selection["selection"]:
        expected = historical.get(item["id"])
        if expected is None:
            raise ArchitectureError(
                f"{path} selects unknown historical Knowledge {item['id']}"
            )
        for field, expected_value in expected.items():
            if item[field] != expected_value:
                raise ArchitectureError(
                    f"{path} historical Knowledge {item['id']} {field} does not "
                    "match its anchored source"
                )


def validate_knowledge_selection_artifact(
    path: Path,
    *,
    facts_path: Path | None = None,
    profile_path: Path | None = None,
    require_trusted_runtime: bool = True,
    require_current_runtime: bool = False,
) -> dict[str, Any]:
    """Validate a selection's source bindings and derived context accounting.

    Schema 1.0/1.1 selections remain readable. Schema 1.2 selections retain
    their creation-time snapshot semantics. Schema 1.3 remains readable but
    cannot establish a trusted historical runtime. Schema 1.4 either replays
    against the exact current Runtime Manifest or verifies every anchored Git
    blob without executing historical code. Archived verification preserves
    historical readability, but callers creating a new trusted chain must set
    ``require_current_runtime`` so the selection is deterministically replayed
    by the current runtime.
    """
    selection = validate_file(path, "knowledge-selection.schema.json")
    if selection["schema_version"] not in {"1.2", "1.3", "1.4"}:
        if require_current_runtime:
            raise ArchitectureError(
                f"{path} schema {selection['schema_version']} has no replayable "
                "Selector Runtime Manifest; regenerate the Knowledge Selection "
                "before creating a new trusted chain"
            )
        return selection
    try:
        _, entries = validate_knowledge_tree(
            KNOWLEDGE_ROOT,
            schema_root=SCHEMA_ROOT,
        )
    except KnowledgeError as exc:
        raise ArchitectureError(str(exc)) from exc
    replay_with_current_runtime = False
    if selection["schema_version"] == "1.2" and require_current_runtime:
        raise ArchitectureError(
            f"{path} schema 1.2 has no replayable Selector Runtime Manifest; "
            "regenerate the Knowledge Selection before creating a new trusted chain"
        )
    if selection["schema_version"] in {"1.3", "1.4"}:
        if selection["result_sha256"] != selection_result_sha256(selection):
            raise ArchitectureError(f"{path} result_sha256 does not match its payload")
        try:
            replay_with_current_runtime = selection["selector"] == selector_provenance(
                entries
            )
        except SelectionError as exc:
            raise ArchitectureError(
                f"Cannot resolve current Selector Runtime Manifest: {exc}"
            ) from exc
        if not replay_with_current_runtime and require_trusted_runtime:
            if selection["schema_version"] != "1.4":
                raise ArchitectureError(
                    f"{path} has an unverifiable legacy Selector Runtime lock"
                )
            verify_archived_selector_runtime(selection, path)
        if not replay_with_current_runtime and require_current_runtime:
            raise ArchitectureError(
                f"{path} uses an archived Selector Runtime lock; regenerate the "
                "Knowledge Selection with the current runtime before creating a "
                "new trusted Review, Decision, Plan, or Gate chain"
            )

    selected_ids = [item["id"] for item in selection["selection"]]
    if len(selected_ids) != len(set(selected_ids)):
        raise ArchitectureError(f"{path} repeats a selected knowledge ID")
    excluded_ids = [item["id"] for item in selection["excluded"]]
    if len(excluded_ids) != len(set(excluded_ids)):
        raise ArchitectureError(f"{path} repeats an excluded knowledge ID")
    overlap = sorted(set(selected_ids) & set(excluded_ids))
    if overlap:
        raise ArchitectureError(
            f"{path} selects and excludes the same knowledge: " + ", ".join(overlap)
        )

    budget = selection["budget"]
    if budget["selected_entries"] != len(selection["selection"]):
        raise ArchitectureError(f"{path} selected_entries does not match selection")
    if budget["selected_entries"] > budget["maximum_entries"]:
        raise ArchitectureError(f"{path} selected entries exceed the total budget")
    selected_by_kind = dict.fromkeys(budget["per_kind"], 0)
    allowed_standard_exceptions = (
        "Non-Golden exception: skill-required contract dependency.",
        "Non-Golden exception: explicit caller include.",
        "Non-Golden exception: maintainer mode.",
        "Non-Golden exception: profile-required domain has no declared Golden "
        "replacement.",
        "Non-Golden exception: detected technology has no declared Golden replacement.",
        "Non-Golden exception: exact decision-intent match.",
    )
    for item in selection["selection"]:
        if replay_with_current_runtime:
            entry = entries.get(item["id"])
            if entry is None:
                raise ArchitectureError(
                    f"{path} selects unknown knowledge {item['id']}"
                )
            expected = {
                "version": entry.metadata["version"],
                "sha256": entry.sha256,
                "path": entry.path.relative_to(KNOWLEDGE_ROOT).as_posix(),
                "kind": entry.metadata["kind"],
                "maturity": entry.metadata.get("maturity", "standard"),
            }
            for field, expected_value in expected.items():
                if item[field] != expected_value:
                    raise ArchitectureError(
                        f"{path} knowledge {item['id']} {field} does not match "
                        "bundled source"
                    )
        selected_by_kind[item["kind"]] += 1
        if (
            replay_with_current_runtime
            and selection["inputs"]["skill"] == "architecture-solution-advisor"
            and item["maturity"] == "standard"
            and not any(
                reason in allowed_standard_exceptions for reason in item["reasons"]
            )
        ):
            raise ArchitectureError(
                f"{path} standard advisor knowledge {item['id']} has no "
                "approved Golden-only exception"
            )
    for kind, actual in selected_by_kind.items():
        configured = budget["per_kind"][kind]
        if configured["selected_entries"] != actual:
            raise ArchitectureError(
                f"{path} {kind} selected count does not match selection"
            )
        if actual > configured["maximum_entries"]:
            raise ArchitectureError(f"{path} {kind} entries exceed the kind budget")

    inputs = selection["inputs"]
    if facts_path is None:
        return selection
    facts_path = facts_path.resolve()
    if inputs["facts_sha256"] != file_sha256(facts_path):
        raise ArchitectureError(
            f"{path} knowledge selection is bound to different facts"
        )
    if selection["schema_version"] in {"1.3", "1.4"}:
        facts = validate_file(facts_path, "repository-facts.schema.json")
        project_commit_field = (
            "project_commit"
            if selection["schema_version"] == "1.4"
            else "source_commit"
        )
        if inputs[project_commit_field] != facts["repository"]["commit"]:
            raise ArchitectureError(
                f"{path} {project_commit_field} does not match repository facts"
            )
    if profile_path is not None:
        profile_path = profile_path.resolve()
        if inputs.get("profile_sha256") != file_sha256(profile_path):
            raise ArchitectureError(
                f"{path} knowledge selection is bound to a different profile"
            )
    elif "profile_sha256" in inputs:
        return selection

    if not replay_with_current_runtime:
        return selection
    kind_budgets = {
        kind: values["maximum_entries"] for kind, values in budget["per_kind"].items()
    }
    try:
        expected_selection = select_knowledge(
            facts_path,
            profile_path=profile_path,
            task=inputs["task"],
            skill=inputs["skill"],
            maximum_entries=budget["maximum_entries"],
            includes=list(inputs["includes"]),
            excludes=list(inputs["excludes"]),
            kind_budgets=kind_budgets,
            maintainer_mode=inputs["maintainer_mode"],
            decision_intents=list(inputs["decision_intents"]),
        )
    except SelectionError as exc:
        raise ArchitectureError(
            f"{path} cannot replay deterministic knowledge selection: {exc}"
        ) from exc
    if canonical_sha256(expected_selection) != canonical_sha256(selection):
        raise ArchitectureError(f"{path} does not match its deterministic inputs")
    return selection


def validate_knowledge_context_artifact(
    path: Path,
    selection_path: Path,
    *,
    facts_path: Path | None = None,
    profile_path: Path | None = None,
    require_trusted_runtime: bool = True,
    require_current_runtime: bool = True,
) -> dict[str, Any]:
    """Validate a compact context as the exact projection of its Selection."""
    context = validate_file(path, "knowledge-context.schema.json")
    selection = validate_knowledge_selection_artifact(
        selection_path,
        facts_path=facts_path,
        profile_path=profile_path,
        require_trusted_runtime=require_trusted_runtime,
        require_current_runtime=require_current_runtime,
    )
    expected_lock = file_sha256(selection_path)
    if context["selection_lock_sha256"] != expected_lock:
        raise ArchitectureError(
            f"{path} selection_lock_sha256 does not match {selection_path}"
        )
    expected_result = selection.get(
        "result_sha256",
        selection_result_sha256(selection),
    )
    if context["selection_result_sha256"] != expected_result:
        raise ArchitectureError(
            f"{path} selection_result_sha256 does not match {selection_path}"
        )
    expected_selected = [
        {
            "id": item["id"],
            "path": item["path"],
            "sha256": item["sha256"],
            "priority": item["priority"],
            "reasons": list(item["reasons"]),
        }
        for item in selection["selection"]
    ]
    if context["selected"] != expected_selected:
        raise ArchitectureError(
            f"{path} selected entries are not the exact ordered projection of "
            f"{selection_path}"
        )
    return context


def git_process(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
    )
    try:
        stdout = process.stdout.decode("utf-8")
        stderr = process.stderr.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchitectureError(
            f"git {' '.join(args)} produced non-UTF-8 output for {root}: {exc}"
        ) from exc
    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout,
        stderr,
    )


def git_output(root: Path, *args: str) -> str:
    process = git_process(root, *args)
    if process.returncode != 0:
        detail = (
            process.stderr.strip() or process.stdout.strip() or "git command failed"
        )
        raise ArchitectureError(f"git {' '.join(args)} failed for {root}: {detail}")
    return process.stdout.strip()


def git_raw_output(root: Path, *args: str) -> str:
    process = git_process(root, *args)
    if process.returncode != 0:
        detail = (
            process.stderr.strip() or process.stdout.strip() or "git command failed"
        )
        raise ArchitectureError(f"git {' '.join(args)} failed for {root}: {detail}")
    return process.stdout


def require_within_root(root: Path, candidate: Path, field: str) -> Path:
    root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ArchitectureError(
            f"{field} escapes configured root {root}: {resolved}"
        ) from exc
    return resolved


def local_rule_pack_roots(repository_root: Path) -> list[Path]:
    root = repository_root.resolve()
    return [
        root / ".architecture" / "rules",
        root / ".architecture-portfolio" / "rules",
    ]


def load_rule_packs(
    rule_pack_ids: list[str],
    additional_roots: list[Path] | None = None,
) -> dict[str, dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {}
    roots = [RULE_ROOT, *(additional_roots or [])]
    for rule_root in roots:
        if not rule_root.exists():
            continue
        if not rule_root.is_dir():
            raise ArchitectureError(f"Rule Pack root is not a directory: {rule_root}")
        for path in sorted(rule_root.glob("*.yaml")):
            payload = validate_file(path, "rule-pack.schema.json")
            pack_id = payload["id"]
            if pack_id in available:
                raise ArchitectureError(
                    f"Duplicate Rule Pack ID {pack_id} in "
                    f"{available[pack_id]['path']} and {path}"
                )
            available[pack_id] = {"path": path, "payload": payload}
    missing = sorted(set(rule_pack_ids) - set(available))
    if missing:
        raise ArchitectureError("Unknown rule packs: " + ", ".join(missing))
    return {pack_id: available[pack_id] for pack_id in rule_pack_ids}


def validate_review_rule_pack_kinds(
    requirements: list[dict[str, Any]],
    rule_packs: dict[str, dict[str, Any]],
    source: Path,
) -> None:
    for requirement in requirements:
        review_kind = requirement["kind"]
        for pack_id in requirement["rule_packs"]:
            pack_kind = rule_packs[pack_id]["payload"]["review_kind"]
            if pack_kind != review_kind:
                raise ArchitectureError(
                    f"{source} review {requirement['id']} has kind {review_kind}, "
                    f"but Rule Pack {pack_id} has kind {pack_kind}"
                )


def expected_rules(rule_packs: dict[str, dict[str, Any]]) -> dict[str, str]:
    rules: dict[str, str] = {}
    for pack_id, record in rule_packs.items():
        for rule in record["payload"]["rules"]:
            rule_id = rule["id"]
            if rule_id in rules:
                raise ArchitectureError(
                    f"Rule {rule_id} appears in both {rules[rule_id]} and {pack_id}"
                )
            rules[rule_id] = pack_id
    return rules


def load_evidence_provider_catalog() -> tuple[Path, dict[str, dict[str, Any]]]:
    path = EVIDENCE_PROVIDER_ROOT / "catalog.yaml"
    payload = validate_file(path, "evidence-provider.schema.json")
    providers: dict[str, dict[str, Any]] = {}
    for provider in payload["providers"]:
        provider_id = provider["id"]
        if provider_id in providers:
            raise ArchitectureError(f"{path} has duplicate provider ID {provider_id}")
        providers[provider_id] = provider
    return path, providers


def validate_provider_command_safety(command: list[str], source: Path) -> None:
    executable = re.split(r"[\\/]", command[0])[-1].lower()
    normalized_executable = (
        executable[:-4] if executable.endswith(".exe") else executable
    )
    arguments = [item.lower() for item in command[1:]]
    forbidden_wrappers = SHELL_INTERPRETERS | {
        "bunx",
        "corepack",
        "env",
        "npx",
        "uvx",
        "gradlew",
        "mvnw",
        "xargs",
    }
    script_suffix = Path(executable).suffix.lower()
    if (
        executable in forbidden_wrappers
        or normalized_executable in forbidden_wrappers
        or script_suffix.endswith("sh")
        or script_suffix in {".bat", ".cmd", ".nu", ".oil", ".ps1"}
    ):
        raise ArchitectureError(
            f"{source} evidence provider command uses forbidden package or shell "
            f"runner {command[0]}"
        )

    package_actions = {
        "apt": {"install"},
        "apt-get": {"install"},
        "brew": {"install"},
        "bun": {"add", "install", "x"},
        "cargo": {"install"},
        "dnf": {"install"},
        "gem": {"install"},
        "go": {"install"},
        "npm": {
            "add",
            "ci",
            "clean-install",
            "exec",
            "i",
            "ic",
            "install",
            "install-ci-test",
            "install-test",
        },
        "pip": {"install"},
        "pip3": {"install"},
        "pnpm": {"add", "dlx", "exec", "i", "install"},
        "yarn": {"add", "dlx", "install"},
        "yum": {"install"},
    }
    if (
        normalized_executable in package_actions
        and set(arguments) & package_actions[normalized_executable]
    ):
        raise ArchitectureError(
            f"{source} evidence provider command may install or download tools: "
            + " ".join(command)
        )
    offline_tools = {
        "cargo": {"--offline"},
        "gradle": {"--offline"},
        "gradle.exe": {"--offline"},
        "mvn": {"--offline", "-o"},
        "mvn.cmd": {"--offline", "-o"},
    }
    if (
        normalized_executable in offline_tools
        and not set(arguments) & offline_tools[normalized_executable]
    ):
        raise ArchitectureError(
            f"{source} evidence provider command must use offline mode: "
            + " ".join(command)
        )
    if (
        normalized_executable in {"python", "python3", "py"}
        and len(arguments) >= 3
        and arguments[0:2] == ["-m", "pip"]
        and "install" in arguments[2:]
    ):
        raise ArchitectureError(
            f"{source} evidence provider command may install tools: "
            + " ".join(command)
        )


def validate_evidence_provider_config(
    path: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    data = validate_file(path, "evidence-provider-config.schema.json")
    _, bundled = load_evidence_provider_catalog()
    configured: dict[str, dict[str, Any]] = {}
    for provider in data["providers"]:
        provider_id = provider["id"]
        if provider_id in configured:
            raise ArchitectureError(f"{path} has duplicate provider ID {provider_id}")
        validate_provider_command_safety(provider["command"], path)
        configured[provider_id] = provider
    unknown = sorted(set(configured) - set(bundled))
    if unknown:
        raise ArchitectureError(
            f"{path} configures unknown providers: " + ", ".join(unknown)
        )
    for provider_id, config in configured.items():
        if (
            config["enabled"]
            and bundled[provider_id]["trust"] == "deterministic"
            and (
                not config.get("dependency_inputs")
                or config.get("cache_mode") != "isolated"
            )
        ):
            raise ArchitectureError(
                f"{path} enabled deterministic evidence provider {provider_id} "
                "requires a non-empty dependency_inputs closure and cache_mode "
                "isolated"
            )
    return data, configured


def provider_detect_matches(root: Path, provider: dict[str, Any]) -> list[str]:
    matches: set[str] = set()
    for marker in provider["detect"]:
        if any(character in marker for character in "*?["):
            for path in root.glob(marker):
                matches.add(path.relative_to(root).as_posix())
        else:
            path = root / marker
            if path.exists():
                matches.add(path.relative_to(root).as_posix())
    return sorted(matches)


def evidence_provider_status(project_root: Path) -> list[dict[str, Any]]:
    root = project_root.resolve()
    _, providers = load_evidence_provider_catalog()
    _, configured = validate_evidence_provider_config(
        root / ".architecture" / "evidence-providers.yaml"
    )
    result: list[dict[str, Any]] = []
    for provider_id, provider in providers.items():
        config = configured.get(provider_id)
        detected = provider_detect_matches(root, provider)
        executable: str | None = None
        executable_available = False
        if config is not None:
            try:
                executable = str(
                    resolve_provider_executable(root, config["command"][0])
                )
                executable_available = True
            except ArchitectureError:
                pass
        if config is None:
            readiness_reason = "not configured"
        elif not config["enabled"]:
            readiness_reason = "configured but disabled"
        elif not executable_available:
            readiness_reason = "configured and enabled, but executable is unavailable"
        elif not detected and not config["allow_without_detection"]:
            readiness_reason = (
                "configured and enabled, but no project marker was detected"
            )
        else:
            readiness_reason = "ready"
        command = " ".join(provider["command"])
        missing_tool_guidance = (
            provider.get("missing_tool_guidance")
            or (
                f"Catalog command '{command}' is unavailable; install or configure it "
                "outside this runtime, then explicitly configure and enable "
                "the provider."
            )
            if not executable_available
            else "Catalog tool is available; this runtime never installs providers."
        )
        result.append(
            {
                "id": provider_id,
                "configured": config is not None,
                "configuration_status": "configured" if config else "unconfigured",
                "enabled": config["enabled"] if config else False,
                "detected": detected,
                "executable": executable,
                "executable_available": executable_available,
                "ready": readiness_reason == "ready",
                "readiness_reason": readiness_reason,
                "missing_tool_guidance": missing_tool_guidance,
            }
        )
    return result


def output_record(root: Path, path: Path, content: bytes) -> dict[str, Any]:
    path.write_bytes(content)
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_bytes(content),
        "bytes": len(content),
    }


def existing_output_record(root: Path, path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_bytes(content),
        "bytes": len(content),
    }


def validate_provider_content(
    output_format: str,
    content: bytes,
) -> tuple[str, str | None]:
    if output_format in {"exit-code", "text"}:
        return "not-applicable", None
    if not content.strip():
        return "invalid", f"{output_format} output is empty"
    try:
        if output_format in {"json", "sarif"}:
            parsed = json.loads(content)
            if output_format == "sarif" and (
                not isinstance(parsed, dict)
                or parsed.get("version") != "2.1.0"
                or not isinstance(parsed.get("runs"), list)
            ):
                return "invalid", "SARIF requires version 2.1.0 and a runs array"
        elif output_format == "junit":
            upper_content = content.upper()
            if b"<!DOCTYPE" in upper_content or b"<!ENTITY" in upper_content:
                return (
                    "invalid",
                    "JUnit XML must not contain DTD or entity declarations",
                )
            parsed_xml = ElementTree.fromstring(content)
            if parsed_xml.tag.rsplit("}", 1)[-1] not in {
                "testsuite",
                "testsuites",
            }:
                return "invalid", "JUnit XML root must be testsuite or testsuites"
    except (json.JSONDecodeError, ElementTree.ParseError, UnicodeDecodeError) as exc:
        return "invalid", f"{output_format} parsing failed: {exc}"
    return "valid", None


def resolve_provider_executable(root: Path, command: str) -> Path:
    configured = Path(command)
    if configured.is_absolute():
        executable = configured
    elif configured.parent != Path():
        executable = require_within_root(
            root,
            root / configured,
            "evidence provider executable",
        )
    else:
        located = shutil.which(command)
        if located is None:
            raise ArchitectureError(
                f"Evidence provider executable is unavailable: {command}"
            )
        executable = Path(located)
    resolved = executable.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ArchitectureError(
            f"Evidence provider executable is unavailable: {command}"
        )
    try:
        first_line = resolved.read_bytes()[:256].splitlines()[0].lower()
    except (OSError, IndexError):
        first_line = b""
    shebang_programs = {
        Path(token.decode("utf-8", errors="ignore")).name.lower()
        for token in first_line[2:].split()
        if token and not token.startswith(b"-")
    }
    if first_line.startswith(b"#!") and any(
        program.endswith("sh") or program in SHELL_INTERPRETERS
        for program in shebang_programs
    ):
        raise ArchitectureError(
            f"Evidence provider executable is a forbidden shell wrapper: {command}"
        )
    return resolved


def provider_executable_reference(root: Path, executable_path: Path) -> str:
    """Return a portable reference for a project-local provider executable."""
    try:
        return executable_path.relative_to(root).as_posix()
    except ValueError:
        return str(executable_path)


def provider_path_reference(root: Path, path: Path) -> str:
    """Return a portable repository-relative reference for a provider path."""
    return path.relative_to(root).as_posix()


def resolve_recorded_provider_executable(root: Path, recorded: str) -> Path:
    """Resolve a recorded provider executable in the current checkout."""
    path = Path(recorded)
    if not path.is_absolute():
        return (root / path).resolve()
    return path.resolve()


def provider_dependency_inputs(
    root: Path,
    configured_inputs: list[str],
) -> list[dict[str, Any]]:
    """Resolve and hash a declared, project-contained provider dependency closure."""
    resolved: dict[str, Path] = {}
    for configured in configured_inputs:
        pattern = Path(configured)
        if (
            pattern.is_absolute()
            or ".." in pattern.parts
            or "\\" in configured
            or pattern.as_posix() != configured
        ):
            raise ArchitectureError(
                f"Evidence provider dependency input escapes the project: {configured}"
            )
        matches = sorted(root.glob(configured))
        if not matches:
            raise ArchitectureError(
                f"Evidence provider dependency input matches nothing: {configured}"
            )
        for match in matches:
            contained = require_within_root(
                root,
                match,
                "evidence provider dependency input",
            )
            candidates = (
                sorted(path for path in contained.rglob("*") if path.is_file())
                if contained.is_dir()
                else [contained]
            )
            for candidate in candidates:
                if candidate.is_symlink():
                    raise ArchitectureError(
                        "Evidence provider dependency closures cannot contain "
                        f"symbolic links: {candidate}"
                    )
                relative = candidate.relative_to(root).as_posix()
                resolved[relative] = candidate
    return [
        {
            "path": relative,
            "sha256": file_sha256(candidate),
            "bytes": candidate.stat().st_size,
        }
        for relative, candidate in sorted(resolved.items())
    ]


def run_evidence_provider(
    project_root: Path,
    provider_id: str,
    *,
    output_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    root = project_root.resolve()
    profile = validate_file(
        root / ".architecture" / "profile.yaml",
        "project-profile.schema.json",
    )
    catalog_path, providers = load_evidence_provider_catalog()
    if provider_id not in providers:
        raise ArchitectureError(f"Unknown evidence provider {provider_id}")
    _, configured = validate_evidence_provider_config(
        root / ".architecture" / "evidence-providers.yaml"
    )
    config = configured.get(provider_id)
    if config is None:
        raise ArchitectureError(
            f"Evidence provider {provider_id} is not configured; refusing to run "
            "an unconfigured catalog entry"
        )
    if not config["enabled"]:
        raise ArchitectureError(
            f"Evidence provider {provider_id} is disabled in project configuration"
        )
    provider = providers[provider_id]
    configured_inputs = config.get("dependency_inputs", [])
    cache_mode = config.get("cache_mode", "unbound")
    if provider["trust"] == "deterministic" and (
        not configured_inputs or cache_mode != "isolated"
    ):
        raise ArchitectureError(
            f"Deterministic evidence provider {provider_id} requires a non-empty "
            "dependency_inputs closure and cache_mode isolated"
        )
    dependency_inputs_before = provider_dependency_inputs(root, configured_inputs)
    matches = provider_detect_matches(root, provider)
    if not matches and not config["allow_without_detection"]:
        raise ArchitectureError(
            f"Evidence provider {provider_id} has no matching project markers"
        )
    executable_path = resolve_provider_executable(root, config["command"][0])
    commit = current_git_commit(root)
    dirty_tree_at_start = not git_is_clean(root)
    timestamp = datetime.now(UTC)
    run_id = f"{provider_id}-{timestamp.strftime('%Y%m%dt%H%M%S%fz')}-{commit[:12]}"
    if output_path is None:
        evidence_root = root / ".architecture" / "evidence"
        evidence_root.mkdir(parents=True, exist_ok=True)
        artifact_path = evidence_root / f"{run_id}.yaml"
    else:
        candidate = output_path if output_path.is_absolute() else root / output_path
        artifact_path = require_within_root(root, candidate, "evidence output")
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if artifact_path.exists():
        raise ArchitectureError(
            f"Refusing to overwrite evidence artifact: {artifact_path}"
        )
    stdout_path = artifact_path.with_suffix(".stdout")
    stderr_path = artifact_path.with_suffix(".stderr")
    structured_path = artifact_path.with_suffix(".result")
    if stdout_path.exists() or stderr_path.exists() or structured_path.exists():
        raise ArchitectureError(
            f"Refusing to overwrite evidence output beside {artifact_path}"
        )
    actual_command = [
        argument.replace("{evidence_output}", str(structured_path))
        for argument in config["command"]
    ]
    actual_command[0] = str(executable_path)
    if config["output_source"] == "file":
        if not any("{evidence_output}" in argument for argument in config["command"]):
            raise ArchitectureError(
                f"Evidence provider {provider_id} file output command must use "
                "{evidence_output}"
            )
    elif any("{evidence_output}" in argument for argument in config["command"]):
        raise ArchitectureError(
            f"Evidence provider {provider_id} uses {{evidence_output}} without "
            "file output"
        )

    safe_environment_names = {
        "PATH",
        "LANG",
        "LC_ALL",
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        *config["environment_allowlist"],
    }
    if config.get("cache_mode") == "isolated":
        safe_environment_names.update(
            {
                "HOME",
                "XDG_CACHE_HOME",
                "PIP_CACHE_DIR",
                "npm_config_cache",
                "YARN_CACHE_FOLDER",
                "CARGO_HOME",
                "GOMODCACHE",
                "GRADLE_USER_HOME",
            }
        )
    allowed_environment = {
        name: os.environ[name] for name in safe_environment_names if name in os.environ
    }
    started_at = datetime.now(UTC)
    exit_code: int | None
    with tempfile.TemporaryDirectory(prefix="hengmu-provider-cache-") as cache_dir:
        if cache_mode == "isolated":
            cache_root = Path(cache_dir)
            isolated_cache_environment = {
                "HOME": cache_root / "home",
                "XDG_CACHE_HOME": cache_root / "xdg",
                "PIP_CACHE_DIR": cache_root / "pip",
                "npm_config_cache": cache_root / "npm",
                "YARN_CACHE_FOLDER": cache_root / "yarn",
                "CARGO_HOME": cache_root / "cargo",
                "GOMODCACHE": cache_root / "go-mod",
                "GRADLE_USER_HOME": cache_root / "gradle",
            }
            for name, cache_path in isolated_cache_environment.items():
                cache_path.mkdir(parents=True, exist_ok=True)
                allowed_environment[name] = str(cache_path)
        try:
            process = subprocess.run(
                actual_command,
                cwd=root,
                env=allowed_environment,
                capture_output=True,
                check=False,
                timeout=config["timeout_seconds"],
            )
            stdout = process.stdout
            stderr = process.stderr
            exit_code = process.returncode
            status = "passed" if exit_code in config["success_exit_codes"] else "failed"
        except subprocess.TimeoutExpired as exc:
            stdout = (
                exc.stdout
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "").encode("utf-8")
            )
            stderr = (
                exc.stderr
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "").encode("utf-8")
            )
            exit_code = None
            status = "timed-out"
    completed_at = datetime.now(UTC)
    stdout_record = output_record(root, stdout_path, stdout)
    stderr_record = output_record(root, stderr_path, stderr)
    structured_record: dict[str, Any] | None = None
    if config["output_source"] == "stdout":
        structured_content = stdout
        structured_record = stdout_record
    elif config["output_source"] == "stderr":
        structured_content = stderr
        structured_record = stderr_record
    elif structured_path.is_file():
        structured_content = structured_path.read_bytes()
        structured_record = existing_output_record(root, structured_path)
    else:
        structured_content = b""
        status = "failed"
    content_validation, content_error = validate_provider_content(
        provider["output"],
        structured_content,
    )
    if content_validation == "invalid" and status != "timed-out":
        status = "failed"
    generated_paths = {
        path.relative_to(root).as_posix()
        for path in (artifact_path, stdout_path, stderr_path, structured_path)
    }
    completed_commit = current_git_commit(root)
    dirty_tree_after = bool(git_worktree_paths(root) - generated_paths)
    dependency_inputs_after = provider_dependency_inputs(
        root,
        configured_inputs,
    )
    artifact: dict[str, Any] = {
        "schema_version": "1.2",
        "run": {
            "id": run_id,
            "provider_id": provider_id,
            "provider_catalog_sha256": file_sha256(catalog_path),
            "provider_definition_sha256": canonical_sha256(provider),
            "provider_config_sha256": canonical_sha256(config),
            "evidence_type": provider["evidence_type"],
            "trust": provider["trust"],
            "project_id": profile["project"]["id"],
            "repository_identity": profile["project"]["id"],
            "commit": commit,
            "dirty_tree": dirty_tree_at_start,
            "completed_commit": completed_commit,
            "dirty_tree_after": dirty_tree_after,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "command": [
                provider_executable_reference(root, executable_path)
                if index == 0
                else argument.replace(
                    str(structured_path),
                    provider_path_reference(root, structured_path),
                )
                for index, argument in enumerate(actual_command)
            ],
            "executable": provider_executable_reference(root, executable_path),
            "executable_sha256": file_sha256(executable_path),
            "detect_matches": matches,
            "output_source": config["output_source"],
            "timeout_seconds": config["timeout_seconds"],
            "success_exit_codes": config["success_exit_codes"],
            "environment_names": sorted(allowed_environment),
            "dependency_inputs_before": dependency_inputs_before,
            "dependency_inputs_after": dependency_inputs_after,
            "cache_mode": cache_mode,
        },
        "result": {
            "status": status,
            "exit_code": exit_code,
            "output_format": provider["output"],
            "content_validation": content_validation,
            "stdout": stdout_record,
            "stderr": stderr_record,
        },
    }
    if structured_record is not None:
        artifact["result"]["structured_output"] = structured_record
    if content_error is not None:
        artifact["result"]["content_error"] = content_error
    validate_data(artifact, "evidence-run.schema.json", artifact_path)
    write_yaml(artifact_path, artifact)
    return artifact_path, artifact


def validate_evidence_run(
    path: Path,
    project_root: Path,
    *,
    require_passed: bool = False,
) -> dict[str, Any]:
    root = project_root.resolve()
    candidate = path if path.is_absolute() else root / path
    artifact_path = require_within_root(root, candidate, "evidence run")
    data = validate_file(artifact_path, "evidence-run.schema.json")
    profile = validate_file(
        root / ".architecture" / "profile.yaml",
        "project-profile.schema.json",
    )
    if not repository_identities_match(
        data["run"]["project_id"],
        profile["project"]["id"],
    ):
        raise ArchitectureError(
            f"{artifact_path} project_id does not match project profile"
        )
    if not repository_identities_match(
        data["run"]["repository_identity"],
        profile["project"]["id"],
    ):
        raise ArchitectureError(
            f"{artifact_path} repository_identity does not match project profile"
        )
    catalog_path, providers = load_evidence_provider_catalog()
    provider = providers.get(data["run"]["provider_id"])
    if provider is None:
        raise ArchitectureError(
            f"{artifact_path} references an unknown evidence provider"
        )
    if data["run"]["provider_catalog_sha256"] != file_sha256(catalog_path):
        raise ArchitectureError(f"{artifact_path} provider catalog hash is stale")
    if data["run"]["provider_definition_sha256"] != canonical_sha256(provider):
        raise ArchitectureError(f"{artifact_path} provider definition hash is stale")
    _, configured = validate_evidence_provider_config(
        root / ".architecture" / "evidence-providers.yaml"
    )
    config = configured.get(data["run"]["provider_id"])
    if config is None:
        raise ArchitectureError(
            f"{artifact_path} provider is no longer explicitly configured"
        )
    if data["run"]["provider_config_sha256"] != canonical_sha256(config):
        raise ArchitectureError(f"{artifact_path} provider configuration hash is stale")
    if data["run"]["evidence_type"] != provider["evidence_type"]:
        raise ArchitectureError(
            f"{artifact_path} evidence type does not match provider"
        )
    if data["run"]["trust"] != provider["trust"]:
        raise ArchitectureError(f"{artifact_path} trust does not match provider")
    if data["schema_version"] == "1.2":
        expected_dependencies = provider_dependency_inputs(
            root,
            config.get("dependency_inputs", []),
        )
        if data["run"]["dependency_inputs_after"] != expected_dependencies:
            raise ArchitectureError(
                f"{artifact_path} dependency input closure is stale"
            )
        if data["run"]["cache_mode"] != config.get("cache_mode", "unbound"):
            raise ArchitectureError(f"{artifact_path} cache mode does not match config")
    if data["run"]["output_source"] != config["output_source"]:
        raise ArchitectureError(f"{artifact_path} output source does not match config")
    if data["run"]["timeout_seconds"] != config["timeout_seconds"]:
        raise ArchitectureError(f"{artifact_path} timeout does not match config")
    if data["run"]["success_exit_codes"] != config["success_exit_codes"]:
        raise ArchitectureError(
            f"{artifact_path} success exit codes do not match config"
        )
    configured_executable = resolve_provider_executable(root, config["command"][0])
    executable_path = resolve_recorded_provider_executable(
        root,
        data["run"]["executable"],
    )
    if executable_path != configured_executable:
        raise ArchitectureError(
            f"{artifact_path} executable does not match current command resolution"
        )
    if file_sha256(executable_path) != data["run"]["executable_sha256"]:
        raise ArchitectureError(f"{artifact_path} executable hash is stale")
    safe_environment_names = {
        "PATH",
        "LANG",
        "LC_ALL",
        "SYSTEMROOT",
        "WINDIR",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        *config["environment_allowlist"],
    }
    if data["run"].get("cache_mode") == "isolated":
        safe_environment_names.update(
            {
                "HOME",
                "XDG_CACHE_HOME",
                "PIP_CACHE_DIR",
                "npm_config_cache",
                "YARN_CACHE_FOLDER",
                "CARGO_HOME",
                "GOMODCACHE",
                "GRADLE_USER_HOME",
            }
        )
    unexpected_environment = sorted(
        set(data["run"]["environment_names"]) - safe_environment_names
    )
    if unexpected_environment:
        raise ArchitectureError(
            f"{artifact_path} records non-allowlisted environment names: "
            + ", ".join(unexpected_environment)
        )
    expected_command = [
        argument.replace(
            "{evidence_output}",
            str(artifact_path.with_suffix(".result")),
        )
        for argument in config["command"]
    ]
    expected_command[0] = str(executable_path)
    output_path_reference = provider_path_reference(
        root,
        artifact_path.with_suffix(".result"),
    )
    recorded_command = [
        argument.replace(
            output_path_reference,
            str(artifact_path.with_suffix(".result")),
        )
        for argument in data["run"]["command"]
    ]
    recorded_command[0] = str(executable_path)
    if recorded_command != expected_command:
        raise ArchitectureError(f"{artifact_path} command does not match config")
    started_at = datetime.fromisoformat(
        data["run"]["started_at"].replace("Z", "+00:00")
    )
    completed_at = datetime.fromisoformat(
        data["run"]["completed_at"].replace("Z", "+00:00")
    )
    if completed_at < started_at:
        raise ArchitectureError(f"{artifact_path} completes before it starts")
    git_output(root, "cat-file", "-e", f"{data['run']['commit']}^{{commit}}")
    if data["schema_version"] == "1.1":
        git_output(
            root,
            "cat-file",
            "-e",
            f"{data['run']['completed_commit']}^{{commit}}",
        )
    output_contents: dict[str, bytes] = {}
    for stream in ("stdout", "stderr"):
        record = data["result"][stream]
        output_path = require_within_root(
            root,
            root / record["path"],
            f"{artifact_path}:{stream}",
        )
        if not output_path.is_file():
            raise ArchitectureError(
                f"{artifact_path} {stream} output is missing: {output_path}"
            )
        content = output_path.read_bytes()
        output_contents[stream] = content
        if len(content) != record["bytes"]:
            raise ArchitectureError(
                f"{artifact_path} {stream} byte count does not match"
            )
        if sha256_bytes(content) != record["sha256"]:
            raise ArchitectureError(
                f"{artifact_path} {stream} output hash does not match"
            )
    output_source = data["run"]["output_source"]
    if output_source in {"stdout", "stderr"}:
        structured_content = output_contents[output_source]
    else:
        structured_record = data["result"].get("structured_output")
        if structured_record is None:
            raise ArchitectureError(
                f"{artifact_path} file output has no structured_output record"
            )
        structured_path = require_within_root(
            root,
            root / structured_record["path"],
            f"{artifact_path}:structured_output",
        )
        if structured_path != artifact_path.with_suffix(".result"):
            raise ArchitectureError(
                f"{artifact_path} structured output path is not run-scoped"
            )
        if not structured_path.is_file():
            raise ArchitectureError(
                f"{artifact_path} structured output is missing: {structured_path}"
            )
        structured_content = structured_path.read_bytes()
        if len(structured_content) != structured_record["bytes"]:
            raise ArchitectureError(
                f"{artifact_path} structured output byte count does not match"
            )
        if sha256_bytes(structured_content) != structured_record["sha256"]:
            raise ArchitectureError(
                f"{artifact_path} structured output hash does not match"
            )
    content_validation, content_error = validate_provider_content(
        provider["output"],
        structured_content,
    )
    if data["result"]["content_validation"] != content_validation:
        raise ArchitectureError(
            f"{artifact_path} content validation result does not match output"
        )
    if data["result"].get("content_error") != content_error:
        raise ArchitectureError(
            f"{artifact_path} content validation error does not match output"
        )
    if data["result"]["output_format"] != provider["output"]:
        raise ArchitectureError(
            f"{artifact_path} output format does not match provider"
        )
    exit_code = data["result"]["exit_code"]
    expected_status = (
        "timed-out"
        if exit_code is None
        else ("passed" if exit_code in data["run"]["success_exit_codes"] else "failed")
    )
    if content_validation == "invalid" and expected_status != "timed-out":
        expected_status = "failed"
    if data["result"]["status"] != expected_status:
        raise ArchitectureError(
            f"{artifact_path} status does not match exit and content results"
        )
    if data["result"]["status"] == "passed" and content_validation == "invalid":
        raise ArchitectureError(
            f"{artifact_path} passed despite invalid structured output"
        )
    if require_passed and data["run"]["dirty_tree"]:
        raise ArchitectureError(
            f"{artifact_path} was produced from a dirty working tree and cannot "
            "enter trusted Review or Gate evidence"
        )
    if require_passed and data["schema_version"] != "1.2":
        raise ArchitectureError(
            f"{artifact_path} legacy provider run lacks post-run repository state "
            "and dependency closure"
        )
    if require_passed and data["run"]["completed_commit"] != data["run"]["commit"]:
        raise ArchitectureError(
            f"{artifact_path} provider changed HEAD during execution"
        )
    if require_passed and data["run"]["dirty_tree_after"]:
        raise ArchitectureError(
            f"{artifact_path} provider changed the working tree during execution"
        )
    if require_passed and (
        data["run"]["trust"] == "deterministic"
        and (
            not data["run"]["dependency_inputs_before"]
            or data["run"]["cache_mode"] != "isolated"
            or data["run"]["dependency_inputs_before"]
            != data["run"]["dependency_inputs_after"]
        )
    ):
        raise ArchitectureError(
            f"{artifact_path} deterministic provider lacks a stable dependency "
            "closure and isolated cache"
        )
    if require_passed and data["result"]["status"] != "passed":
        raise ArchitectureError(
            f"{artifact_path} provider result is {data['result']['status']}"
        )
    return data


def validate_coverage_evidence_binding(
    item: dict[str, Any],
    root: Path,
    expected_commit: str,
    label: str,
) -> None:
    relative = Path(item["path"])
    if relative.is_absolute() or ".." in relative.parts or "\\" in item["path"]:
        raise ArchitectureError(f"{label} path escapes the repository")
    expected = git_output(root, "rev-parse", f"{expected_commit}^{{commit}}")
    observed = git_output(root, "rev-parse", f"{item['commit']}^{{commit}}")
    if observed != expected:
        raise ArchitectureError(f"{label} is not bound to the reviewed commit")
    object_name = f"{observed}:{relative.as_posix()}"
    git_output(root, "cat-file", "-e", object_name)
    blob_sha = git_output(root, "rev-parse", object_name)
    if blob_sha != item["blob_sha"]:
        raise ArchitectureError(f"{label} blob SHA does not match")
    if "line_start" in item:
        content = git_raw_output(root, "show", object_name)
        lines = content.splitlines()
        line_start = item["line_start"]
        line_end = item.get("line_end", line_start)
        if line_end < line_start or line_end > len(lines):
            raise ArchitectureError(f"{label} line range is invalid")


def validate_review(
    path: Path,
    *,
    rule_pack_ids: list[str] | None = None,
    strict_trust: bool = False,
    repository_root: Path | None = None,
    allow_unverifiable_historical: bool = False,
    require_current_selection: bool = False,
) -> dict[str, Any]:
    data = validate_file(path, "review.schema.json")
    finding_ids: set[str] = set()
    findings = data["findings"]

    for index, finding in enumerate(findings):
        source = Path(f"{path}#findings[{index}]")
        if not isinstance(finding, dict):
            raise ArchitectureError(f"{source} must be a mapping")
        validate_data(finding, "finding.schema.json", source)
        finding_id = finding["id"]
        if finding_id in finding_ids:
            raise ArchitectureError(f"{path} has duplicate finding ID {finding_id}")
        finding_ids.add(finding_id)
        if finding["confidence"] < 0.60:
            raise ArchitectureError(
                f"{path} finding {finding_id} has confidence below 0.60"
            )
        if finding["kind"] == "strength" and finding["severity"] != "info":
            raise ArchitectureError(
                f"{path} strength {finding_id} must use severity 'info'"
            )
        if (
            finding["verification"]["status"] == "rejected"
            and finding["status"] != "rejected"
        ):
            raise ArchitectureError(
                f"{path} rejected finding {finding_id} must have status 'rejected'"
            )
        if finding.get("fingerprint"):
            expected_fingerprint = finding_fingerprint(
                data["review"]["subject"]["id"],
                finding,
            )
            if finding["fingerprint"] != expected_fingerprint:
                raise ArchitectureError(
                    f"{path} finding {finding_id} fingerprint does not match content"
                )
        if data["schema_version"] == "1.2":
            required_finding_fields = (
                "fingerprint",
                "evidence_level",
                "evidence_fingerprint",
                "fact_inference_boundary",
                "applicability",
                "source_candidate_ids",
                "rule_pack_version",
                "staleness_state",
                "quality_attribute_impacts",
                "decision_references",
            )
            missing_finding_fields = [
                field for field in required_finding_fields if field not in finding
            ]
            if missing_finding_fields:
                raise ArchitectureError(
                    f"{path} 1.2 finding {finding_id} is missing: "
                    + ", ".join(missing_finding_fields)
                )
            expected_evidence_fingerprint = canonical_sha256(finding["evidence"])
            if finding["evidence_fingerprint"] != expected_evidence_fingerprint:
                raise ArchitectureError(
                    f"{path} finding {finding_id} evidence_fingerprint "
                    "does not match evidence"
                )

    review_state = data["review"]["verification_state"]
    verification_states = [finding["verification"]["status"] for finding in findings]
    if review_state == "candidates":
        non_candidates = [
            finding["id"]
            for finding in findings
            if finding["verification"]["status"] != "candidate"
        ]
        if non_candidates:
            raise ArchitectureError(
                f"{path} is a candidate review but contains verified IDs: "
                + ", ".join(non_candidates)
            )
    elif "candidate" in verification_states:
        candidate_ids = [
            finding["id"]
            for finding in findings
            if finding["verification"]["status"] == "candidate"
        ]
        raise ArchitectureError(
            f"{path} is verified but still has candidate IDs: "
            + ", ".join(candidate_ids)
        )

    if strict_trust:
        if data["schema_version"] not in {"1.1", "1.2"}:
            raise ArchitectureError(
                f"{path} uses legacy schema {data['schema_version']}; "
                "migrate to 1.1 or 1.2 before deterministic enforcement"
            )
        required_review_fields = (
            "repository_identity",
            "profile_sha256",
            "dirty_tree",
            "rule_packs",
            "scope_manifest",
        )
        missing_review_fields = [
            field for field in required_review_fields if field not in data["review"]
        ]
        if missing_review_fields:
            raise ArchitectureError(
                f"{path} trusted review is missing: " + ", ".join(missing_review_fields)
            )
        if (
            data["schema_version"] == "1.2"
            and data["review"]["kind"] != "portfolio"
            and not allow_unverifiable_historical
            and not data["review"].get("commit")
        ):
            raise ArchitectureError(
                f"{path} current trusted project Review requires review.commit"
            )
        for scoped_path in data["review"]["scope_manifest"]:
            scoped = Path(scoped_path)
            if scoped.is_absolute() or ".." in scoped.parts:
                raise ArchitectureError(
                    f"{path} scope_manifest path escapes repository: {scoped_path}"
                )
        if review_state == "verified":
            if not data.get("coverage_complete"):
                raise ArchitectureError(f"{path} verified coverage is not complete")
            if "verification_run" not in data["review"]:
                raise ArchitectureError(f"{path} has no verification_run")
            if "source_candidate" not in data["review"]:
                raise ArchitectureError(f"{path} has no source_candidate binding")
            verification_run = data["review"]["verification_run"]
            run_started = datetime.fromisoformat(
                verification_run["started_at"].replace("Z", "+00:00")
            )
            run_completed = datetime.fromisoformat(
                verification_run["completed_at"].replace("Z", "+00:00")
            )
            if run_completed < run_started:
                raise ArchitectureError(
                    f"{path} verification run completes before it starts"
                )
            for finding in findings:
                verification = finding["verification"]
                if verification["status"] == "candidate":
                    continue
                required_verification = (
                    "level",
                    "verifier",
                    "verified_at",
                    "source_candidate",
                )
                missing = [
                    field
                    for field in required_verification
                    if field not in verification
                ]
                if missing:
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} verification is missing: "
                        + ", ".join(missing)
                    )
                if not finding.get("fingerprint"):
                    raise ArchitectureError(
                        f"{path} verified finding {finding['id']} has no fingerprint"
                    )
                if verification["verifier"]["run_id"] != verification_run["id"]:
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} verifier run does not "
                        "match review verification_run"
                    )
                verifier_identity = verification["verifier"]["identity"]
                if verifier_identity not in data["review"]["reviewers"]:
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} verifier is absent "
                        "from review.reviewers"
                    )
                if (
                    verification.get("verified_by", verifier_identity)
                    != verifier_identity
                ):
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} verified_by conflicts "
                        "with verifier identity"
                    )
                level = verification["level"]
                if (
                    level in {"V3", "V4", "V5"}
                    and verification["verifier"]["type"] != "human"
                ):
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} {level} verification "
                        "requires a human verifier"
                    )
                if level == "V5" and "signature" not in data["review"]:
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} V5 verification "
                        "requires a signed review"
                    )
                verified_at = datetime.fromisoformat(
                    verification["verified_at"].replace("Z", "+00:00")
                )
                if not run_started <= verified_at <= run_completed:
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} verified_at is outside "
                        "the verification run"
                    )

    expected_counts = {
        "raw_findings": len(findings),
        "confirmed": verification_states.count("confirmed"),
        "rejected": verification_states.count("rejected"),
        "needs_evidence": verification_states.count("needs-evidence"),
    }
    for key, expected in expected_counts.items():
        actual = data["summary"][key]
        if actual != expected:
            raise ArchitectureError(
                f"{path} summary.{key} is {actual}, expected {expected}"
            )

    coverage_ids: set[str] = set()
    finding_coverage: dict[str, list[str]] = {}
    for coverage in data["coverage"]:
        if coverage["rule_id"] in coverage_ids:
            raise ArchitectureError(
                f"{path} has duplicate coverage rule {coverage['rule_id']}"
            )
        coverage_ids.add(coverage["rule_id"])
        missing = sorted(set(coverage["finding_ids"]) - finding_ids)
        if missing:
            raise ArchitectureError(
                f"{path} coverage {coverage['rule_id']} references unknown IDs: "
                + ", ".join(missing)
            )
        if coverage["status"] != "assessed" and not coverage.get("reason"):
            raise ArchitectureError(
                f"{path} coverage {coverage['rule_id']} requires a reason for "
                f"{coverage['status']}"
            )
        if (
            strict_trust
            and data["schema_version"] == "1.2"
            and not allow_unverifiable_historical
            and coverage["status"] == "assessed"
            and not coverage.get("evidence")
        ):
            raise ArchitectureError(
                f"{path} assessed coverage {coverage['rule_id']} has no evidence"
            )
        for finding_id in coverage["finding_ids"]:
            finding_coverage.setdefault(finding_id, []).append(coverage["rule_id"])

    by_id = {finding["id"]: finding for finding in findings}
    for finding_id, coverage_rules in finding_coverage.items():
        expected_rule = by_id[finding_id]["rule_id"]
        if coverage_rules != [expected_rule]:
            raise ArchitectureError(
                f"{path} finding {finding_id} must appear only under "
                f"coverage rule {expected_rule}"
            )
    missing_coverage_findings = sorted(finding_ids - set(finding_coverage))
    if missing_coverage_findings:
        raise ArchitectureError(
            f"{path} findings missing from coverage: "
            + ", ".join(missing_coverage_findings)
        )

    if rule_pack_ids is not None:
        allowed_pack_ids = set(rule_pack_ids)
        declared_pack_items = data["review"].get("rule_packs", [])
        declared_pack_ids = [item["id"] for item in declared_pack_items]
        if len(declared_pack_ids) != len(set(declared_pack_ids)):
            raise ArchitectureError(f"{path} declares a rule pack more than once")
        unknown_declared = sorted(set(declared_pack_ids) - allowed_pack_ids)
        if unknown_declared:
            raise ArchitectureError(
                f"{path} declares rule packs absent from project profile: "
                + ", ".join(unknown_declared)
            )
        effective_pack_ids = declared_pack_ids or list(rule_pack_ids)
        if strict_trust and not declared_pack_ids:
            raise ArchitectureError(f"{path} trusted review declares no rule packs")
        required_core = REVIEW_KIND_CORE_PACK[data["review"]["kind"]]
        if strict_trust and required_core not in effective_pack_ids:
            raise ArchitectureError(
                f"{path} {data['review']['kind']} review must load {required_core}"
            )
        packs = load_rule_packs(
            effective_pack_ids,
            (
                local_rule_pack_roots(repository_root)
                if repository_root is not None
                else None
            ),
        )
        wrong_kind = sorted(
            pack_id
            for pack_id, record in packs.items()
            if record["payload"]["review_kind"] != data["review"]["kind"]
        )
        if wrong_kind:
            raise ArchitectureError(
                f"{path} loads Rule Packs for a different review kind: "
                + ", ".join(wrong_kind)
            )
        rules = expected_rules(packs)
        unknown_coverage = sorted(coverage_ids - set(rules))
        missing_rules = sorted(set(rules) - coverage_ids)
        if unknown_coverage:
            raise ArchitectureError(
                f"{path} coverage references unloaded rules: "
                + ", ".join(unknown_coverage)
            )
        if strict_trust and missing_rules:
            raise ArchitectureError(
                f"{path} coverage is missing loaded rules: " + ", ".join(missing_rules)
            )
        declared_packs = {item["id"]: item for item in declared_pack_items}
        if strict_trust and set(declared_packs) != set(packs):
            raise ArchitectureError(
                f"{path} declared rule packs do not match effective review packs"
            )
        for pack_id, record in packs.items():
            declaration = declared_packs.get(pack_id)
            if declaration is None:
                continue
            if declaration["version"] != record["payload"]["version"]:
                raise ArchitectureError(
                    f"{path} rule pack {pack_id} version does not match bundle"
                )
            if declaration["sha256"] != file_sha256(record["path"]):
                raise ArchitectureError(
                    f"{path} rule pack {pack_id} hash does not match bundle"
                )

    if strict_trust and repository_root is not None:
        root = repository_root.resolve()
        profile_path = require_within_root(
            root,
            root / data["review"].get("profile", ".architecture/profile.yaml"),
            "review.profile",
        )
        if data["review"]["profile_sha256"] != file_sha256(profile_path):
            raise ArchitectureError(
                f"{path} profile hash does not match {profile_path}"
            )
        if data["schema_version"] == "1.2":
            profile = validate_file(profile_path, "project-profile.schema.json")
            facts_binding = data["repository_facts"]
            facts_path = require_within_root(
                root,
                root / facts_binding["path"],
                "review.repository_facts.path",
            )
            facts = validate_file(facts_path, "repository-facts.schema.json")
            if facts_binding["sha256"] != file_sha256(facts_path):
                raise ArchitectureError(
                    f"{path} repository facts hash does not match {facts_path}"
                )
            declared_facts_root = Path(facts["repository"]["root"])
            facts_root = (
                root
                if declared_facts_root == Path()
                else declared_facts_root.expanduser().resolve()
            )
            if facts_root != root:
                raise ArchitectureError(
                    f"{path} repository facts describe a different root"
                )
            facts_commit = facts["repository"]["commit"]
            review_commit = data["review"].get("commit", "unknown")
            if (
                facts_commit != "unknown"
                and review_commit != "unknown"
                and facts_commit != review_commit
            ):
                raise ArchitectureError(
                    f"{path} repository facts commit does not match the Review"
                )
            profile_facts = profile["project"].get("repository_facts")
            if profile_facts is None:
                raise ArchitectureError(
                    f"{path} 1.2 Profile has no repository_facts binding"
                )
            profile_facts_path = require_within_root(
                root,
                root / profile_facts["path"],
                "profile.project.repository_facts.path",
            )
            if (
                profile_facts_path != facts_path
                or profile_facts["sha256"] != facts_binding["sha256"]
            ):
                raise ArchitectureError(
                    f"{path} Profile and Review bind different repository facts"
                )
            selection_binding = data["knowledge_selection"]
            selection_path = require_within_root(
                root,
                root / selection_binding["path"],
                "review.knowledge_selection.path",
            )
            selection = validate_knowledge_selection_artifact(
                selection_path,
                facts_path=facts_path,
                profile_path=profile_path,
                require_trusted_runtime=not allow_unverifiable_historical,
                require_current_runtime=require_current_selection,
            )
            if selection_binding["sha256"] != file_sha256(selection_path):
                raise ArchitectureError(
                    f"{path} knowledge selection hash does not match {selection_path}"
                )
            selected = {
                item["id"]: {
                    "version": item["version"],
                    "sha256": item["sha256"],
                }
                for item in data["selected_knowledge"]
            }
            if len(selected) != len(data["selected_knowledge"]):
                raise ArchitectureError(f"{path} repeats a selected knowledge ID")
            expected_selected = {
                item["id"]: {
                    "version": item["version"],
                    "sha256": item["sha256"],
                }
                for item in selection["selection"]
            }
            if selected != expected_selected:
                raise ArchitectureError(
                    f"{path} selected_knowledge does not match its selection artifact"
                )
            try:
                _, bundled_knowledge = validate_knowledge_tree(
                    KNOWLEDGE_ROOT,
                    schema_root=SCHEMA_ROOT,
                )
            except KnowledgeError as exc:
                raise ArchitectureError(str(exc)) from exc
            for entry_id, snapshot in selected.items():
                entry = bundled_knowledge.get(entry_id)
                if entry is None:
                    raise ArchitectureError(
                        f"{path} selects unknown knowledge {entry_id}"
                    )
                if (
                    not allow_unverifiable_historical
                    and snapshot["version"] != entry.metadata["version"]
                ):
                    raise ArchitectureError(
                        f"{path} knowledge {entry_id} version is stale"
                    )
                if (
                    not allow_unverifiable_historical
                    and snapshot["sha256"] != entry.sha256
                ):
                    raise ArchitectureError(
                        f"{path} knowledge {entry_id} hash is stale"
                    )
            selected_paths = {
                item["id"]: item["path"] for item in selection["selection"]
            }
            for entry_id, entry in bundled_knowledge.items():
                if entry_id not in selected_paths:
                    continue
                expected_path = entry.path.relative_to(KNOWLEDGE_ROOT).as_posix()
                if selected_paths[entry_id] != expected_path:
                    raise ArchitectureError(
                        f"{path} knowledge {entry_id} selection path is stale"
                    )
            critical_flows_path = require_within_root(
                root,
                root / profile["project"]["critical_flows_file"],
                "profile.project.critical_flows_file",
            )
            headings = re.findall(
                r"^## (?!Flow template\s*$)(.+?)\s*$",
                critical_flows_path.read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )
            expected_flow_ids = {slugify(heading) for heading in headings}
            flow_coverage = {
                item["id"]: item for item in data["critical_flow_coverage"]
            }
            if len(flow_coverage) != len(data["critical_flow_coverage"]):
                raise ArchitectureError(f"{path} repeats a critical-flow coverage ID")
            if set(flow_coverage) != expected_flow_ids:
                missing = sorted(expected_flow_ids - set(flow_coverage))
                unknown = sorted(set(flow_coverage) - expected_flow_ids)
                details = []
                if missing:
                    details.append("missing " + ", ".join(missing))
                if unknown:
                    details.append("unknown " + ", ".join(unknown))
                raise ArchitectureError(
                    f"{path} critical-flow coverage mismatch: " + "; ".join(details)
                )
            for flow_id, item in flow_coverage.items():
                if item["status"] != "assessed" and not item.get("reason"):
                    raise ArchitectureError(
                        f"{path} critical flow {flow_id} requires a reason"
                    )
                if review_state == "verified" and item["status"] == "not_assessed":
                    raise ArchitectureError(
                        f"{path} verified review leaves critical flow "
                        f"{flow_id} not assessed"
                    )
                if (
                    not allow_unverifiable_historical
                    and item["status"] == "assessed"
                    and not item.get("evidence")
                ):
                    raise ArchitectureError(
                        f"{path} assessed critical flow {flow_id} has no evidence"
                    )
            if not allow_unverifiable_historical:
                review_commit = data["review"].get("commit")
                if review_commit is None:
                    raise ArchitectureError(
                        f"{path} current coverage evidence requires review.commit"
                    )
                for coverage in data["coverage"]:
                    for index, item in enumerate(coverage.get("evidence", [])):
                        validate_coverage_evidence_binding(
                            item,
                            root,
                            review_commit,
                            f"{path} coverage {coverage['rule_id']} evidence {index}",
                        )
                for flow_id, coverage in flow_coverage.items():
                    for index, item in enumerate(coverage.get("evidence", [])):
                        validate_coverage_evidence_binding(
                            item,
                            root,
                            review_commit,
                            f"{path} critical flow {flow_id} evidence {index}",
                        )
        reviewed_commit = data["review"].get("commit")
        historical_evidence = bool(
            allow_unverifiable_historical
            and reviewed_commit
            and reviewed_commit != current_git_commit(root)
        )
        provider_runs: dict[Path, dict[str, Any]] = {}
        for reference in data.get("tool_evidence", []):
            try:
                run_path = require_within_root(
                    root,
                    root / reference["run_path"],
                    "review.tool_evidence.run_path",
                )
                if reference["run_sha256"] != file_sha256(run_path):
                    raise ArchitectureError(
                        f"{path} tool evidence hash does not match {run_path}"
                    )
                evidence_run = validate_evidence_run(
                    run_path,
                    root,
                    require_passed=True,
                )
            except ArchitectureError:
                if not historical_evidence:
                    raise
                continue
            if evidence_run["run"]["provider_id"] != reference["provider_id"]:
                raise ArchitectureError(
                    f"{path} tool evidence provider does not match {run_path}"
                )
            provider_runs[run_path] = evidence_run
            if (
                reviewed_commit is not None
                and evidence_run["run"]["commit"] != reviewed_commit
            ):
                raise ArchitectureError(
                    f"{path} tool evidence commit does not match review commit"
                )
        for finding in findings:
            deterministic_finding_evidence = False
            for item in finding["evidence"]:
                if item["type"] != "tool":
                    continue
                run_path = require_within_root(
                    root,
                    root / item["provider_run"],
                    f"{path} finding {finding['id']} tool evidence",
                )
                referenced_run = provider_runs.get(run_path)
                if referenced_run is None:
                    if historical_evidence:
                        continue
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} tool evidence is not "
                        "declared in review.tool_evidence"
                    )
                if item["provider_run_sha256"] != file_sha256(run_path):
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} tool evidence hash "
                        "does not match its run"
                    )
                if referenced_run["run"]["provider_id"] != item["provider_id"]:
                    raise ArchitectureError(
                        f"{path} finding {finding['id']} tool evidence provider "
                        "does not match its run"
                    )
                if referenced_run["run"]["trust"] == "deterministic":
                    deterministic_finding_evidence = True
            if (
                finding["verification"].get("level") in {"V4", "V5"}
                and not deterministic_finding_evidence
                and not historical_evidence
            ):
                raise ArchitectureError(
                    f"{path} finding {finding['id']} "
                    f"{finding['verification']['level']} verification requires "
                    "a passed deterministic tool evidence run cited by the Finding"
                )
        if review_state == "verified":
            source = data["review"]["source_candidate"]
            candidate_path = require_within_root(
                root,
                root / source["path"],
                "review.source_candidate.path",
            )
            candidate = (
                validate_review(
                    candidate_path,
                    rule_pack_ids=rule_pack_ids,
                    strict_trust=True,
                    repository_root=root,
                    allow_unverifiable_historical=allow_unverifiable_historical,
                    require_current_selection=require_current_selection,
                )
                if data["schema_version"] == "1.2"
                else validate_review(candidate_path)
            )
            if candidate["review"]["verification_state"] != "candidates":
                raise ArchitectureError(
                    f"{path} source candidate is not a candidate review"
                )
            verified_snapshot = _declared_review_snapshot(data["review"])
            candidate_snapshot = _declared_review_snapshot(candidate["review"])
            source_snapshot = _declared_source_snapshot(source)
            if (
                data["schema_version"] == "1.2"
                and not allow_unverifiable_historical
                and not source_snapshot
            ):
                raise ArchitectureError(
                    f"{path} source candidate has no explicit repository snapshot"
                )
            if source_snapshot and source_snapshot != verified_snapshot:
                raise ArchitectureError(
                    f"{path} source candidate repository snapshot does not match "
                    "the verified Review"
                )
            if candidate_snapshot != verified_snapshot:
                raise ArchitectureError(
                    f"{path} source candidate Review commits do not match the "
                    "verified Review"
                )
            if candidate["review"]["id"] != source["review_id"]:
                raise ArchitectureError(
                    f"{path} source candidate review ID does not match"
                )
            if file_sha256(candidate_path) != source["sha256"]:
                raise ArchitectureError(f"{path} source candidate hash does not match")
            candidate_findings = {
                finding["id"]: finding for finding in candidate["findings"]
            }
            verified_findings = {finding["id"]: finding for finding in data["findings"]}
            if set(candidate_findings) != set(verified_findings):
                raise ArchitectureError(
                    f"{path} verified Finding IDs do not match source candidate"
                )
            for finding_id, finding in verified_findings.items():
                candidate_fingerprint = finding_fingerprint(
                    data["review"]["subject"]["id"],
                    candidate_findings[finding_id],
                )
                if finding["fingerprint"] != candidate_fingerprint:
                    raise ArchitectureError(
                        f"{path} finding {finding_id} semantics differ from "
                        "source candidate"
                    )
                finding_source = finding["verification"]["source_candidate"]
                if (
                    finding_source["review_id"] != source["review_id"]
                    or finding_source["sha256"] != source["sha256"]
                ):
                    raise ArchitectureError(
                        f"{path} finding {finding_id} candidate binding does "
                        "not match review binding"
                    )

    return data


def decision_knowledge_snapshot() -> list[dict[str, str]]:
    required = {
        "architecture-style",
        "pattern",
        "technology-profile",
    }
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for catalog_path in sorted(KNOWLEDGE_ROOT.rglob("*.yaml")):
        if catalog_path.name == "manifest.yaml":
            continue
        catalog = validate_file(
            catalog_path,
            "knowledge-catalog.schema.json",
        )
        kind = catalog["kind"]
        if kind not in required:
            continue
        if kind in seen:
            raise ArchitectureError(
                f"Bundled knowledge has multiple catalogs for {kind}"
            )
        seen.add(kind)
        result.append(
            {
                "kind": kind,
                "catalog_version": catalog["catalog_version"],
                "sha256": file_sha256(catalog_path),
            }
        )
    if seen != required:
        raise ArchitectureError(
            "Bundled decision knowledge is missing: "
            + ", ".join(sorted(required - seen))
        )
    return sorted(result, key=lambda item: item["kind"])


def _declared_review_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """Return only the reviewed repository identity declared by an artifact."""
    snapshot: dict[str, Any] = {}
    if "commits" in value:
        commits = value["commits"]
        if isinstance(commits, dict):
            snapshot["commits"] = tuple(sorted(commits.items()))
        elif isinstance(commits, list):
            snapshot["commits"] = tuple(sorted(commits))
        else:
            snapshot["commits"] = commits
    elif "commit" in value:
        snapshot["commits"] = (value["commit"],)
    for key in ("repository_snapshot", "snapshot"):
        if key in value:
            snapshot["repository_snapshot"] = value[key]
    return snapshot


def _declared_source_snapshot(source: dict[str, Any]) -> dict[str, Any]:
    return _declared_review_snapshot(source)


def _brief_constraint_entries(data: dict[str, Any]) -> list[Any]:
    if isinstance(data.get("architecture_constraints"), list):
        return list(data["architecture_constraints"])
    context = data.get("context", {})
    if isinstance(context, dict) and isinstance(context.get("constraints"), list):
        return list(context["constraints"])
    constraints = data.get("constraints", [])
    return constraints if isinstance(constraints, list) else []


def _constraint_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    for key in ("id", "constraint_id"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate
    return None


def _constraint_semantics(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    disposition = value.get("disposition")
    if isinstance(disposition, str):
        if disposition in {"required", "preferred", "prohibited"}:
            return disposition
        return "unknown"
    for key in (
        "semantics",
        "semantic",
        "kind",
        "type",
        "requirement",
        "polarity",
    ):
        raw = value.get(key)
        if not isinstance(raw, str):
            continue
        normalized = raw.lower().replace("_", "-").replace(" ", "-")
        if any(token in normalized for token in ("prohibit", "forbid", "must-not")):
            return "prohibited"
        if any(token in normalized for token in ("require", "must", "mandatory")):
            return "required"
        if normalized in {"optional", "allowed", "preferred"}:
            return "optional"
        if normalized in {"unknown", "unresolved", "undetermined"}:
            return "unknown"
    return "unknown"


def _constraint_outcome(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    for key in (
        "status",
        "result",
        "disposition",
        "effect",
        "satisfaction",
        "compliance",
    ):
        raw = value.get(key)
        if not isinstance(raw, str):
            continue
        normalized = raw.lower().replace("_", "-").replace(" ", "-")
        if any(token in normalized for token in ("violat", "forbid", "fail", "reject")):
            return "violated"
        if any(
            token in normalized
            for token in ("satisf", "comply", "compliant", "met", "allowed", "pass")
        ):
            return "satisfied"
        if normalized in {"unknown", "unresolved", "undetermined"}:
            return "unknown"
    return "unknown"


def _option_constraint_entries(option: dict[str, Any]) -> list[Any] | None:
    for key in (
        "constraint_coverage",
        "constraint_evaluations",
        "constraint_assessments",
        "constraint_bindings",
        "constraints",
    ):
        value = option.get(key)
        if isinstance(value, list):
            return value
    return None


def _target_list(target: dict[str, Any], *keys: str) -> list[Any]:
    values: list[Any] = []
    for key in keys:
        value = target.get(key)
        if isinstance(value, list):
            values.extend(value)
    return values


def _structured_ids(
    path: Path,
    entries: list[Any],
    label: str,
    *,
    fields: tuple[str, ...] = ("id",),
    allow_strings: bool = False,
) -> set[str]:
    """Collect IDs from one schema-defined collection and reject duplicates."""
    result: set[str] = set()
    for index, entry in enumerate(entries):
        item_id: str | None = (
            entry if allow_strings and isinstance(entry, str) else None
        )
        if isinstance(entry, dict):
            for field in fields:
                candidate = entry.get(field)
                if isinstance(candidate, str):
                    item_id = candidate
                    break
        if item_id is None:
            continue
        if item_id in result:
            raise ArchitectureError(
                f"{path} {label} repeats ID {item_id} at index {index}"
            )
        result.add(item_id)
    return result


def _register_target_ids(
    path: Path,
    registry: dict[str, str],
    entries: list[Any],
    label: str,
    *,
    fields: tuple[str, ...] = ("id",),
    allow_strings: bool = False,
) -> set[str]:
    ids = _structured_ids(
        path,
        entries,
        label,
        fields=fields,
        allow_strings=allow_strings,
    )
    for item_id in ids:
        previous = registry.get(item_id)
        if previous is not None:
            raise ArchitectureError(
                f"{path} target ID {item_id} is used by both {previous} and {label}"
            )
        registry[item_id] = label
    return ids


def _reference_values(entries: list[Any], fields: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for field in fields:
            value = entry.get(field)
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(item for item in value if isinstance(item, str))
    return values


def _validate_greenfield_target_architecture(
    path: Path,
    data: dict[str, Any],
    brief: dict[str, Any],
) -> None:
    constraints = _brief_constraint_entries(brief)
    constraint_by_id = {
        item_id: item
        for item in constraints
        if (item_id := _constraint_id(item)) is not None
    }
    options = data["options"]
    option_outcomes: dict[str, dict[str, str]] = {}
    option_assessments: dict[str, dict[str, dict[str, Any]]] = {}
    for option in options:
        entries = _option_constraint_entries(option)
        if entries is None:
            if constraint_by_id:
                raise ArchitectureError(
                    f"{path} option {option['id']} has no exact constraint coverage"
                )
            continue
        ids = [_constraint_id(entry) for entry in entries]
        if any(item_id is None for item_id in ids):
            raise ArchitectureError(
                f"{path} option {option['id']} has a constraint coverage entry "
                "without an ID"
            )
        valid_ids = [item_id for item_id in ids if item_id is not None]
        if len(valid_ids) != len(set(valid_ids)) or set(valid_ids) != set(
            constraint_by_id
        ):
            missing = sorted(set(constraint_by_id) - set(valid_ids))
            unknown = sorted(set(valid_ids) - set(constraint_by_id))
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unknown:
                details.append("unknown " + ", ".join(unknown))
            if len(valid_ids) != len(set(valid_ids)):
                details.append("duplicate IDs")
            raise ArchitectureError(
                f"{path} option {option['id']} constraint coverage is not exact: "
                + "; ".join(details)
            )
        option_outcomes[option["id"]] = {
            item_id: _constraint_outcome(entry)
            for item_id, entry in zip(ids, entries, strict=True)
            if item_id is not None
        }
        option_assessments[option["id"]] = {
            item_id: entry
            for item_id, entry in zip(ids, entries, strict=True)
            if item_id is not None and isinstance(entry, dict)
        }
        for item_id, entry in option_assessments[option["id"]].items():
            expected_knowledge = constraint_by_id[item_id].get("knowledge_id")
            if entry.get("knowledge_id") != expected_knowledge:
                raise ArchitectureError(
                    f"{path} option {option['id']} constraint {item_id} knowledge "
                    "binding does not match the Design Brief"
                )

    selected_option = data["selected_option"]
    selected_outcomes = option_outcomes.get(selected_option, {})
    hard_eliminations = {
        item["option_id"] for item in data.get("hard_eliminations", [])
    }
    for constraint_id, constraint in constraint_by_id.items():
        outcome = selected_outcomes.get(constraint_id, "unknown")
        semantics = _constraint_semantics(constraint)
        if semantics == "required" and outcome in {"violated", "not-applicable"}:
            raise ArchitectureError(
                f"{path} selected option {selected_option} violates required "
                f"constraint {constraint_id}"
            )
        if semantics == "prohibited" and outcome != "satisfied":
            raise ArchitectureError(
                f"{path} selected option {selected_option} adopts prohibited target "
                f"for constraint {constraint_id} (assessment is {outcome})"
            )
        if (
            data["decision"]["status"] == "accepted"
            and semantics in {"required", "preferred"}
            and outcome == "unknown"
        ):
            raise ArchitectureError(
                f"{path} accepted decision leaves {semantics} constraint "
                f"{constraint_id} unknown"
            )
    for option_id, outcomes in option_outcomes.items():
        if option_id == selected_option:
            continue
        for constraint_id, constraint in constraint_by_id.items():
            if (
                _constraint_semantics(constraint) == "prohibited"
                and outcomes.get(constraint_id, "unknown") != "satisfied"
                and option_id not in hard_eliminations
            ):
                raise ArchitectureError(
                    f"{path} option {option_id} does not satisfy prohibited "
                    f"constraint {constraint_id} and must be hard-eliminated"
                )

    target = data.get("target_architecture")
    if not isinstance(target, dict):
        raise ArchitectureError(f"{path} schema 1.4 requires target_architecture")
    if target.get("option_id") != selected_option:
        raise ArchitectureError(
            f"{path} target_architecture.option_id does not match selected_option"
        )

    id_registry: dict[str, str] = {}
    runtime_entries = _target_list(target, "runtime_units", "runtime_components")
    runtime = target.get("runtime")
    if isinstance(runtime, dict):
        runtime_entries.extend(
            _target_list(runtime, "runtime_units", "units", "components")
        )
    runtime_ids = _register_target_ids(
        path, id_registry, runtime_entries, "runtime_units"
    )
    runtime_id_set = set(runtime_ids)

    deployment_entries = _target_list(target, "deployment_units")
    deployment_ids = _register_target_ids(
        path, id_registry, deployment_entries, "deployment_units"
    )
    external_entries = _target_list(target, "external_systems")
    external_ids = _register_target_ids(
        path, id_registry, external_entries, "external_systems"
    )
    trust_entries = _target_list(target, "trust_boundaries")
    trust_ids = _register_target_ids(
        path,
        id_registry,
        trust_entries,
        "trust_boundaries",
    )
    data_entries = _target_list(target, "data_ownership")
    _register_target_ids(
        path,
        id_registry,
        data_entries,
        "data_ownership",
        fields=("data_id", "id"),
    )
    interface_entries = _target_list(target, "interfaces", "interface_bindings")
    _register_target_ids(path, id_registry, interface_entries, "interfaces")
    flow_entries = _target_list(
        target,
        "critical_flow_bindings",
        "flows",
        "critical_flows",
        "flow_bindings",
    )
    _register_target_ids(
        path,
        id_registry,
        flow_entries,
        "critical_flow_bindings",
        fields=("flow_id", "id"),
    )
    deployment_refs = _reference_values(
        runtime_entries, ("deployment_unit", "deployment_unit_id")
    )
    unknown_deployments = sorted(set(deployment_refs) - deployment_ids)
    if unknown_deployments:
        raise ArchitectureError(
            f"{path} runtime units reference unknown deployment units: "
            + ", ".join(unknown_deployments)
        )
    owner_refs = _reference_values(data_entries, ("owner", "owner_id"))
    unknown_owners = sorted(set(owner_refs) - runtime_id_set)
    if unknown_owners:
        raise ArchitectureError(
            f"{path} target_architecture data owners do not reference runtime units: "
            + ", ".join(unknown_owners)
        )

    endpoint_refs = _reference_values(
        interface_entries, ("from", "to", "from_id", "to_id")
    )
    endpoint_ids = runtime_id_set | external_ids
    unknown_endpoints = sorted(set(endpoint_refs) - endpoint_ids)
    if unknown_endpoints:
        raise ArchitectureError(
            f"{path} target_architecture interface endpoints reference unknown "
            "runtime IDs or external IDs: " + ", ".join(unknown_endpoints)
        )
    trust_refs = _reference_values(
        external_entries, ("trust_boundary", "trust_boundary_id")
    )
    unknown_trust = sorted(set(trust_refs) - trust_ids)
    if unknown_trust:
        raise ArchitectureError(
            f"{path} external systems reference unknown trust boundaries: "
            + ", ".join(unknown_trust)
        )
    boundary_runtime_refs = _reference_values(trust_entries, ("runtime_units",))
    unknown_boundary_units = sorted(set(boundary_runtime_refs) - runtime_id_set)
    if unknown_boundary_units:
        raise ArchitectureError(
            f"{path} trust boundaries reference unknown runtime units: "
            + ", ".join(unknown_boundary_units)
        )
    flow_runtime_refs = _reference_values(flow_entries, ("runtime_units",))
    unknown_flow_units = sorted(set(flow_runtime_refs) - runtime_id_set)
    if unknown_flow_units:
        raise ArchitectureError(
            f"{path} critical-flow bindings reference unknown runtime units: "
            + ", ".join(unknown_flow_units)
        )

    brief_flow_ids = {
        item["id"] for item in brief.get("critical_flows", []) if isinstance(item, dict)
    }
    bound_flow_ids: list[str] = []
    for entry in flow_entries:
        if not isinstance(entry, dict):
            continue
        for key in ("brief_flow_id", "design_brief_flow_id", "flow_id", "id"):
            if isinstance(entry.get(key), str):
                bound_flow_ids.append(entry[key])
                break
    if set(bound_flow_ids) != brief_flow_ids or len(bound_flow_ids) != len(
        set(bound_flow_ids)
    ):
        missing = sorted(brief_flow_ids - set(bound_flow_ids))
        duplicates = sorted(
            {item_id for item_id in bound_flow_ids if bound_flow_ids.count(item_id) > 1}
        )
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if duplicates:
            details.append("duplicate " + ", ".join(duplicates))
        raise ArchitectureError(
            f"{path} target_architecture critical-flow bindings are not exact: "
            + "; ".join(details)
        )

    selected = next(option for option in options if option["id"] == selected_option)
    selected_technology_ids = {
        technology
        for technology in selected.get("technologies", [])
        if isinstance(technology, str)
        and re.fullmatch(r"technology\.[a-z0-9][a-z0-9._-]*", technology)
    }
    target_technologies = {
        technology
        for runtime_unit in runtime_entries
        if isinstance(runtime_unit, dict)
        for technology in runtime_unit.get("technologies", [])
        if isinstance(technology, str)
        and re.fullmatch(r"technology\.[a-z0-9][a-z0-9._-]*", technology)
    }
    outside = sorted(target_technologies - selected_technology_ids)
    if outside:
        raise ArchitectureError(
            f"{path} target_architecture uses technologies absent from "
            "selected option: " + ", ".join(outside)
        )

    selected_assessments = option_assessments.get(selected_option, {})
    target_ids = set(id_registry)
    selected_reference_fields = {
        "architecture-style": set(selected.get("architecture_styles", [])),
        "pattern": set(selected.get("patterns", [])),
        "technology": selected_technology_ids,
    }
    selected_knowledge_ids = set().union(*selected_reference_fields.values())
    runtime_technologies = {
        unit["id"]: set(unit.get("technologies", []))
        for unit in runtime_entries
        if isinstance(unit, dict) and isinstance(unit.get("id"), str)
    }
    for constraint_id, constraint in constraint_by_id.items():
        assessment = selected_assessments.get(constraint_id, {})
        references = set(assessment.get("target_refs", []))
        unknown_references = sorted(references - target_ids)
        if unknown_references:
            raise ArchitectureError(
                f"{path} selected constraint {constraint_id} references unknown "
                "target IDs: " + ", ".join(unknown_references)
            )
        if assessment.get("status") != "satisfied":
            continue
        disposition = _constraint_semantics(constraint)
        kind = constraint.get("kind")
        knowledge_id = constraint.get("knowledge_id")
        if disposition == "prohibited":
            if knowledge_id and (
                knowledge_id in selected_knowledge_ids
                or knowledge_id in target_technologies
            ):
                raise ArchitectureError(
                    f"{path} selected option contains prohibited knowledge "
                    f"{knowledge_id} for constraint {constraint_id}"
                )
            continue
        if knowledge_id and knowledge_id not in selected_knowledge_ids:
            raise ArchitectureError(
                f"{path} selected option marks constraint {constraint_id} "
                f"satisfied but omits {knowledge_id}"
            )
        if kind == "technology" and knowledge_id:
            implementing_units = {
                unit_id
                for unit_id, technologies in runtime_technologies.items()
                if knowledge_id in technologies
            }
            if not implementing_units:
                raise ArchitectureError(
                    f"{path} target architecture marks constraint {constraint_id} "
                    f"satisfied but no runtime unit uses {knowledge_id}"
                )
            if not references or not references.issubset(implementing_units):
                raise ArchitectureError(
                    f"{path} constraint {constraint_id} target_refs must identify "
                    f"runtime units that use {knowledge_id}"
                )
        elif kind not in {"architecture-style", "pattern"} and not references:
            raise ArchitectureError(
                f"{path} satisfied constraint {constraint_id} has no target_refs"
            )


def validate_design_brief(
    path: Path,
    *,
    repository_root: Path | None = None,
    allow_unverifiable_historical: bool = False,
) -> dict[str, Any]:
    data = validate_file(path, "architecture-design-brief.schema.json")
    scenario_ids = [item["id"] for item in data["quality_scenarios"]]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ArchitectureError(f"{path} repeats a quality scenario ID")
    flow_ids = [item["id"] for item in data["critical_flows"]]
    if len(flow_ids) != len(set(flow_ids)):
        raise ArchitectureError(f"{path} repeats a critical flow ID")
    question_ids = [item["id"] for item in data["decision_questions"]]
    if len(question_ids) != len(set(question_ids)):
        raise ArchitectureError(f"{path} repeats a decision question ID")
    if data["schema_version"] == "1.1":
        constraints = _brief_constraint_entries(data)
        constraint_ids = [_constraint_id(item) for item in constraints]
        if any(item_id is None for item_id in constraint_ids):
            raise ArchitectureError(f"{path} 1.1 constraint is missing an ID")
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ArchitectureError(f"{path} repeats a constraint ID")
        design_mode = data.get("design_mode")
        if design_mode == "constrained" and not constraints:
            raise ArchitectureError(
                f"{path} constrained design briefs require at least one constraint"
            )
        if design_mode == "open" and constraints:
            raise ArchitectureError(
                f"{path} open design briefs cannot declare architecture constraints"
            )
        if data["brief"]["status"] == "approved" and not allow_unverifiable_historical:
            approval = data["brief"].get("approval")
            if approval is None:
                raise ArchitectureError(f"{path} approved Design Brief has no approval")
            if repository_root is None:
                raise ArchitectureError(
                    f"{path} approved Design Brief requires a repository root to "
                    "verify approval authority and evidence"
                )
            root = repository_root.resolve()
            policy = validate_file(
                root / ".architecture" / "gate-policy.yaml",
                "gate-policy.schema.json",
            )
            unauthorized = sorted(
                set(approval["approved_by"]) - set(policy["roles"]["decision_makers"])
            )
            if unauthorized:
                raise ArchitectureError(
                    f"{path} Design Brief approval includes unauthorized identities: "
                    + ", ".join(unauthorized)
                )
            signature_identities = [
                signature["identity"] for signature in approval["signatures"]
            ]
            if set(signature_identities) != set(approval["approved_by"]) or len(
                signature_identities
            ) != len(set(signature_identities)):
                raise ArchitectureError(
                    f"{path} Design Brief signatures must exactly match approved_by"
                )
            signature_policy = policy.get("artifact_signatures")
            if signature_policy is None:
                raise ArchitectureError(
                    f"{path} approved Design Brief requires artifact_signatures policy"
                )
            for evidence in approval["evidence"]:
                evidence_path = require_within_root(
                    root,
                    root / evidence["path"],
                    "Design Brief approval evidence",
                )
                if not evidence_path.is_file():
                    raise ArchitectureError(
                        f"{path} Design Brief approval evidence is missing: "
                        f"{evidence_path}"
                    )
                if file_sha256(evidence_path) != evidence["sha256"]:
                    raise ArchitectureError(
                        f"{path} Design Brief approval evidence hash does not match "
                        f"{evidence_path}"
                    )
            for signature in approval["signatures"]:
                verify_ssh_artifact_signature(
                    path,
                    signature,
                    root,
                    signature_policy,
                    "Design Brief",
                )
    return data


def validate_project_file_binding(
    decision_path: Path,
    binding: dict[str, Any],
    repository_root: Path,
    field: str,
) -> Path:
    root = repository_root.resolve()
    binding_path = binding["path"]
    relative_path = Path(binding_path)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or "\\" in binding_path
        or relative_path.as_posix() != binding_path
    ):
        raise ArchitectureError(
            f"{decision_path} {field} path must be a normalized project-relative path"
        )
    resolved = require_within_root(
        root,
        root / relative_path,
        field,
    )
    if binding["sha256"] != file_sha256(resolved):
        raise ArchitectureError(
            f"{decision_path} {field} hash does not match {resolved}"
        )
    return resolved


def validate_evolution_assessment_binding(
    decision_path: Path,
    data: dict[str, Any],
    repository_root: Path | None = None,
) -> None:
    assessment_kind = data["decision"].get("assessment_kind", "standard")
    binding = data.get("evolution_assessment")
    if assessment_kind != "technology-evolution":
        if binding is not None:
            raise ArchitectureError(
                f"{decision_path} evolution_assessment requires "
                "decision.assessment_kind technology-evolution"
            )
        return
    if binding is None:
        raise ArchitectureError(
            f"{decision_path} technology-evolution decision requires an "
            "evolution_assessment binding"
        )

    selected_option = data["selected_option"]
    disposition = binding["disposition"]
    if selected_option == "keep-current" and disposition == "adopt":
        raise ArchitectureError(
            f"{decision_path} keep-current cannot use an adopt disposition"
        )
    if selected_option != "keep-current" and disposition != "adopt":
        raise ArchitectureError(
            f"{decision_path} selected upgrade or replacement requires an "
            "adopt disposition"
        )
    if disposition == "adopt":
        stale_claims = [
            item["claim"]
            for item in binding["volatile_claims"]
            if item["freshness"] != "current"
        ]
        if stale_claims:
            raise ArchitectureError(
                f"{decision_path} adoption requires current official evidence for: "
                + ", ".join(stale_claims)
            )
        pilot = binding["pilot"]
        if pilot["status"] != "completed":
            raise ArchitectureError(
                f"{decision_path} adoption requires a completed pilot"
            )
        if not pilot["observed_measures"]:
            raise ArchitectureError(
                f"{decision_path} adoption requires pilot observed measures"
            )

    if repository_root is None:
        return
    root = repository_root.resolve()
    assessment_path = validate_project_file_binding(
        decision_path,
        binding,
        root,
        "decision.evolution_assessment",
    )
    if assessment_path.suffix.lower() != ".md":
        raise ArchitectureError(
            f"{decision_path} evolution assessment must be Markdown"
        )
    try:
        body = assessment_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ArchitectureError(
            f"{assessment_path} evolution assessment must be UTF-8"
        ) from exc
    headings = set(re.findall(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
    missing = sorted(set(EVOLUTION_ASSESSMENT_HEADINGS) - headings)
    if missing:
        raise ArchitectureError(
            f"{assessment_path} evolution assessment is missing sections: "
            + ", ".join(missing)
        )

    evidence_bindings: list[tuple[str, dict[str, Any]]] = []
    evidence_bindings.extend(
        (
            f"evolution_assessment.baseline.measures[{index}].evidence",
            measure["evidence"],
        )
        for index, measure in enumerate(binding["baseline"]["measures"])
    )
    evidence_bindings.extend(
        (f"evolution_assessment.gap.evidence[{index}]", evidence)
        for index, evidence in enumerate(binding["gap"]["evidence"])
    )
    evidence_bindings.extend(
        (f"evolution_assessment.volatile_claims[{index}].capture", claim["capture"])
        for index, claim in enumerate(binding["volatile_claims"])
    )
    evidence_bindings.extend(
        (
            f"evolution_assessment.pilot.observed_measures[{index}].evidence",
            measure["evidence"],
        )
        for index, measure in enumerate(binding["pilot"]["observed_measures"])
    )
    for field, evidence in evidence_bindings:
        validate_project_file_binding(
            decision_path,
            evidence,
            root,
            field,
        )


def validate_decision(
    path: Path,
    *,
    review_path: Path | None = None,
    design_brief_path: Path | None = None,
    require_accepted: bool = False,
    repository_root: Path | None = None,
    allow_unverifiable_historical: bool = False,
    require_current_selection: bool = False,
) -> dict[str, Any]:
    data = validate_file(path, "architecture-decision.schema.json")
    decision_kind = data["decision"].get("decision_kind", "remediation")
    if data["schema_version"] == "1.4":
        if decision_kind != "greenfield":
            raise ArchitectureError(
                f"{path} Decision 1.4 must declare decision_kind greenfield"
            )
        if data["decision"].get("architecture_intent") != "target-architecture":
            raise ArchitectureError(
                f"{path} Decision 1.4 must declare target-architecture intent"
            )
        if not data["decision"].get("source_context") or not data["decision"].get(
            "source_context_sha256"
        ):
            raise ArchitectureError(
                f"{path} Decision 1.4 requires a Design Brief source_context and hash"
            )
        if data["problem"].get("finding_ids"):
            raise ArchitectureError(
                f"{path} Greenfield Decision 1.4 cannot contain Finding IDs"
            )
    if decision_kind == "greenfield" and review_path is not None:
        raise ArchitectureError(
            f"{path} greenfield decision cannot bind a source review"
        )
    if decision_kind == "remediation" and design_brief_path is not None:
        raise ArchitectureError(
            f"{path} remediation decision cannot bind a design brief"
        )
    option_ids = [option["id"] for option in data["options"]]
    if len(option_ids) != len(set(option_ids)):
        raise ArchitectureError(f"{path} has duplicate decision option IDs")
    if data["selected_option"] not in option_ids:
        raise ArchitectureError(
            f"{path} selected_option {data['selected_option']} does not exist"
        )
    if "keep-current" not in option_ids:
        raise ArchitectureError(f"{path} must include a keep-current option")
    quality_attributes = set(data["problem"]["quality_attributes"])
    knowledge_ids: dict[str, set[str]] = {}
    snapshots_by_id: dict[str, dict[str, Any]] = {}
    if data["schema_version"] == "1.1":
        knowledge_records: dict[str, tuple[Path, dict[str, Any]]] = {}
        for catalog_path in sorted(KNOWLEDGE_ROOT.rglob("*.yaml")):
            if catalog_path.name == "manifest.yaml":
                continue
            catalog = validate_file(
                catalog_path,
                "knowledge-catalog.schema.json",
            )
            if catalog["kind"] in knowledge_records:
                raise ArchitectureError(
                    f"Bundled knowledge has multiple catalogs for {catalog['kind']}"
                )
            knowledge_records[catalog["kind"]] = (catalog_path, catalog)
            knowledge_ids.setdefault(catalog["kind"], set()).update(
                entry["id"] for entry in catalog["entries"]
            )
        snapshots = {item["kind"]: item for item in data["knowledge_snapshot"]}
        if len(snapshots) != len(data["knowledge_snapshot"]):
            raise ArchitectureError(f"{path} repeats a knowledge snapshot kind")
        required_snapshot_kinds = {
            "architecture-style",
            "pattern",
            "technology-profile",
        }
        if set(snapshots) != required_snapshot_kinds:
            raise ArchitectureError(
                f"{path} knowledge snapshot must contain exactly: "
                + ", ".join(sorted(required_snapshot_kinds))
            )
        for kind, snapshot in snapshots.items():
            catalog_path, catalog = knowledge_records[kind]
            if snapshot["catalog_version"] != catalog["catalog_version"]:
                raise ArchitectureError(
                    f"{path} {kind} catalog version does not match bundled knowledge"
                )
            if snapshot["sha256"] != file_sha256(catalog_path):
                raise ArchitectureError(
                    f"{path} {kind} knowledge snapshot hash is stale"
                )
    else:
        try:
            _, markdown_knowledge = validate_knowledge_tree(
                KNOWLEDGE_ROOT,
                schema_root=SCHEMA_ROOT,
            )
        except KnowledgeError as exc:
            raise ArchitectureError(str(exc)) from exc
        snapshots_by_id = {item["id"]: item for item in data["knowledge_snapshot"]}
        if len(snapshots_by_id) != len(data["knowledge_snapshot"]):
            raise ArchitectureError(f"{path} repeats a knowledge snapshot ID")
        kind_map = {
            "architecture-style": "architecture-style",
            "pattern": "pattern",
            "technology-profile": "technology-profile",
        }
        for entry_id, snapshot in snapshots_by_id.items():
            entry = markdown_knowledge.get(entry_id)
            if entry is None:
                raise ArchitectureError(
                    f"{path} snapshots unknown knowledge {entry_id}"
                )
            if (
                not allow_unverifiable_historical
                and snapshot["version"] != entry.metadata["version"]
            ):
                raise ArchitectureError(f"{path} knowledge {entry_id} version is stale")
            if not allow_unverifiable_historical and snapshot["sha256"] != entry.sha256:
                raise ArchitectureError(f"{path} knowledge {entry_id} hash is stale")
            entry_kind = entry.metadata["kind"]
            if entry_kind in kind_map:
                knowledge_ids.setdefault(kind_map[entry_kind], set()).add(entry_id)
    reference_fields = {
        "architecture_styles": "architecture-style",
        "patterns": "pattern",
        "technologies": "technology-profile",
    }
    for option in data["options"]:
        if (
            option["id"] == "keep-current"
            and option["complexity_tier"] != "keep-current"
        ):
            raise ArchitectureError(
                f"{path} keep-current option must use keep-current complexity tier"
            )
        if (
            option["id"] != "keep-current"
            and option["complexity_tier"] == "keep-current"
        ):
            raise ArchitectureError(
                f"{path} only keep-current may use keep-current complexity tier"
            )
        effects = [
            effect["attribute"] for effect in option["quality_attribute_effects"]
        ]
        if len(effects) != len(set(effects)):
            raise ArchitectureError(
                f"{path} option {option['id']} repeats a quality attribute effect"
            )
        if set(effects) != quality_attributes:
            raise ArchitectureError(
                f"{path} option {option['id']} must evaluate every problem "
                "quality attribute exactly once"
            )
        for field, kind in reference_fields.items():
            unknown = sorted(set(option[field]) - knowledge_ids.get(kind, set()))
            if unknown:
                raise ArchitectureError(
                    f"{path} option {option['id']} references unknown {field}: "
                    + ", ".join(unknown)
                )
        if option["id"] == data["selected_option"]:
            if option["rejected_reasons"]:
                raise ArchitectureError(
                    f"{path} selected option cannot have rejected_reasons"
                )
        elif not option["rejected_reasons"]:
            raise ArchitectureError(
                f"{path} non-selected option {option['id']} requires rejected_reasons"
            )
    for elimination in data.get("hard_eliminations", []):
        if elimination["option_id"] not in option_ids:
            raise ArchitectureError(
                f"{path} hard elimination references unknown option "
                f"{elimination['option_id']}"
            )
        if elimination["option_id"] == data["selected_option"]:
            raise ArchitectureError(
                f"{path} selected option cannot also be hard-eliminated"
            )
    if require_accepted and data["decision"]["status"] != "accepted":
        raise ArchitectureError(f"{path} must be accepted before remediation planning")
    if data["decision"]["status"] == "accepted" and not data.get("acceptance_evidence"):
        raise ArchitectureError(f"{path} accepted decision has no acceptance evidence")
    validate_evolution_assessment_binding(path, data, repository_root)
    profile: dict[str, Any] | None = None
    root: Path | None = None
    if repository_root is not None:
        root = repository_root.resolve()
        profile_path = root / ".architecture" / "profile.yaml"
        if profile_path.is_file():
            profile = validate_file(
                profile_path,
                "project-profile.schema.json",
            )
            declared_quality = {
                item["id"] for item in profile["project"].get("quality_attributes", [])
            } | set(profile["project"]["critical_qualities"])
            unknown_quality = sorted(quality_attributes - declared_quality)
            if unknown_quality:
                raise ArchitectureError(
                    f"{path} decision uses quality attributes absent from the "
                    "project Profile: " + ", ".join(unknown_quality)
                )
        if data["schema_version"] in {"1.2", "1.3", "1.4"}:
            if profile is None:
                raise ArchitectureError(
                    f"{path} requires a project Profile for current bindings"
                )
            selection_path = require_within_root(
                root,
                root / data["decision"]["knowledge_selection_path"],
                "decision.knowledge_selection_path",
            )
            facts_binding = profile["project"].get("repository_facts")
            selection_facts_path = (
                require_within_root(
                    root,
                    root / facts_binding["path"],
                    "profile.project.repository_facts.path",
                )
                if facts_binding is not None
                else None
            )
            selection = validate_knowledge_selection_artifact(
                selection_path,
                facts_path=(
                    None if allow_unverifiable_historical else selection_facts_path
                ),
                profile_path=(None if allow_unverifiable_historical else profile_path),
                require_trusted_runtime=not allow_unverifiable_historical,
                require_current_runtime=require_current_selection,
            )
            if data["decision"]["knowledge_selection_sha256"] != file_sha256(
                selection_path
            ):
                raise ArchitectureError(
                    f"{path} knowledge selection hash does not match {selection_path}"
                )
            selection_snapshot = {
                item["id"]: {
                    "version": item["version"],
                    "sha256": item["sha256"],
                }
                for item in selection["selection"]
            }
            decision_snapshot = {
                entry_id: {
                    "version": snapshot["version"],
                    "sha256": snapshot["sha256"],
                }
                for entry_id, snapshot in snapshots_by_id.items()
            }
            if decision_snapshot != selection_snapshot:
                raise ArchitectureError(
                    f"{path} knowledge snapshot does not match selection artifact"
                )
            if (
                not allow_unverifiable_historical
                and profile_path.is_file()
                and selection["inputs"].get("profile_sha256")
                != file_sha256(profile_path)
            ):
                raise ArchitectureError(
                    f"{path} knowledge selection is bound to another profile"
                )
    if decision_kind == "greenfield":
        if design_brief_path is None:
            if root is None:
                raise ArchitectureError(
                    f"{path} greenfield decision requires --design-brief or --project"
                )
            design_brief_path = root / data["decision"]["source_context"]
        design_brief_path = design_brief_path.resolve()
        if root is not None:
            design_brief_path = require_within_root(
                root,
                design_brief_path,
                "decision source design brief",
            )
            expected_context = design_brief_path.relative_to(root).as_posix()
            if data["decision"]["source_context"] != expected_context:
                raise ArchitectureError(
                    f"{path} source_context does not match {design_brief_path}"
                )
        brief = validate_design_brief(
            design_brief_path,
            repository_root=root,
            allow_unverifiable_historical=allow_unverifiable_historical,
        )
        if data["schema_version"] == "1.4" and brief["schema_version"] != "1.1":
            raise ArchitectureError(
                f"{path} Decision 1.4 requires a schema 1.1 Design Brief"
            )
        if brief["schema_version"] == "1.1" and data["schema_version"] != "1.4":
            raise ArchitectureError(
                f"{path} schema 1.1 Design Brief requires a Decision 1.4 target"
            )
        if brief["brief"]["status"] != "approved":
            raise ArchitectureError(
                f"{path} Greenfield decisions require an approved Design Brief"
            )
        if data["decision"]["source_context_sha256"] != file_sha256(design_brief_path):
            raise ArchitectureError(
                f"{path} source_context_sha256 does not match {design_brief_path}"
            )
        brief_attributes = {item["attribute"] for item in brief["quality_scenarios"]}
        missing_scenarios = sorted(quality_attributes - brief_attributes)
        if missing_scenarios:
            raise ArchitectureError(
                f"{path} quality attributes are absent from the design brief: "
                + ", ".join(missing_scenarios)
            )
        if (
            data["schema_version"] == "1.4"
            and data["decision"].get("architecture_intent") == "target-architecture"
        ) or (
            data["schema_version"] == "1.3"
            and isinstance(data.get("target_architecture"), dict)
        ):
            _validate_greenfield_target_architecture(path, data, brief)
    if review_path is not None:
        review_path = review_path.resolve()
        review = (
            validate_review(
                review_path,
                rule_pack_ids=profile["project"]["rule_packs"],
                strict_trust=True,
                repository_root=root,
                allow_unverifiable_historical=allow_unverifiable_historical,
            )
            if profile is not None and root is not None
            else validate_review(review_path)
        )
        if (
            review["schema_version"] not in {"1.1", "1.2"}
            or review["review"]["verification_state"] != "verified"
        ):
            raise ArchitectureError(
                f"{path} requires a trusted 1.1 or 1.2 source review"
            )
        if (
            data["schema_version"] in {"1.2", "1.3", "1.4"}
            and review["schema_version"] != "1.2"
        ):
            raise ArchitectureError(
                f"{path} schema {data['schema_version']} requires a trusted "
                "1.2 source review"
            )
        if data["decision"]["source_review"] != review["review"]["id"]:
            raise ArchitectureError(
                f"{path} source_review does not match {review_path}"
            )
        if data["decision"]["source_review_sha256"] != file_sha256(review_path):
            raise ArchitectureError(
                f"{path} source_review_sha256 does not match {review_path}"
            )
        confirmed = {
            finding["id"]
            for finding in review["findings"]
            if finding["verification"]["status"] == "confirmed"
            and finding["status"] != "resolved"
        }
        unknown = sorted(set(data["problem"]["finding_ids"]) - confirmed)
        if unknown:
            raise ArchitectureError(
                f"{path} decision references non-confirmed findings: "
                + ", ".join(unknown)
            )
    return data


def _validate_greenfield_plan_bindings(
    path: Path,
    data: dict[str, Any],
    decision: dict[str, Any],
    brief: dict[str, Any],
) -> None:
    target = decision.get("target_architecture")
    if not isinstance(target, dict):
        raise ArchitectureError(f"{path} Greenfield plan has no target architecture")
    runtime_entries = _target_list(target, "runtime_units", "runtime_components")
    runtime = target.get("runtime")
    if isinstance(runtime, dict):
        runtime_entries.extend(
            _target_list(
                runtime,
                "runtime_units",
                "units",
                "components",
            )
        )
    runtime_ids = _structured_ids(path, runtime_entries, "runtime_units")
    deployment_ids = _structured_ids(
        path, _target_list(target, "deployment_units"), "deployment_units"
    )
    data_ids = _structured_ids(
        path,
        _target_list(target, "data_ownership"),
        "data_ownership",
    )
    interface_ids = _structured_ids(
        path,
        _target_list(target, "interfaces", "interface_bindings"),
        "interfaces",
    )
    trust_ids = _structured_ids(
        path,
        _target_list(target, "trust_boundaries"),
        "trust_boundaries",
    )
    flow_entries = _target_list(
        target,
        "critical_flow_bindings",
        "flows",
        "critical_flows",
        "flow_bindings",
    )
    flow_ids = _structured_ids(
        path,
        flow_entries,
        "critical_flow_bindings",
        fields=("flow_id", "id"),
    )
    constraint_ids = {
        item_id
        for item in _brief_constraint_entries(brief)
        if (item_id := _constraint_id(item)) is not None
    }
    target_technology_ids = {
        technology
        for runtime_unit in runtime_entries
        if isinstance(runtime_unit, dict)
        for technology in runtime_unit.get("technologies", [])
        if isinstance(technology, str)
        and re.fullmatch(r"technology\.[a-z0-9][a-z0-9._-]*", technology)
    }
    binding_fields: dict[str, set[str]] = {
        "runtime_units": runtime_ids,
        "critical_flows": flow_ids,
        "constraints": constraint_ids,
        "deployment_units": deployment_ids,
        "data_owners": data_ids,
        "interfaces": interface_ids,
        "trust_boundaries": trust_ids,
        "technologies": target_technology_ids,
    }

    observed_bindings: dict[str, set[str]] = {field: set() for field in binding_fields}

    def check_bindings(bindings: Any, field: str) -> None:
        if not isinstance(bindings, dict):
            return
        for binding_field, allowed in binding_fields.items():
            value = bindings.get(binding_field)
            if value is None:
                continue
            values = [value] if isinstance(value, str) else value
            if not isinstance(values, list):
                continue
            observed = {item for item in values if isinstance(item, str)}
            observed_bindings[binding_field].update(observed)
            unknown = sorted(observed - allowed)
            if unknown:
                raise ArchitectureError(
                    f"{path} {field}.{binding_field} references unknown target IDs: "
                    + ", ".join(unknown)
                )

    bindings_found = False
    for index, item in enumerate(data["items"]):
        bindings = item.get("target_bindings")
        if bindings is None:
            continue
        bindings_found = True
        check_bindings(bindings, f"items[{index}].target_bindings")
    if not bindings_found:
        raise ArchitectureError(f"{path} Greenfield plan has no target_bindings")
    for binding_field, expected in binding_fields.items():
        missing = sorted(expected - observed_bindings[binding_field])
        if missing:
            raise ArchitectureError(
                f"{path} Greenfield plan does not cover target {binding_field}: "
                + ", ".join(missing)
            )


def validate_plan(
    path: Path,
    *,
    review_path: Path | None = None,
    decision_path: Path | None = None,
    design_brief_path: Path | None = None,
    repository_root: Path | None = None,
    allow_unverifiable_historical: bool = False,
    require_current_selection: bool = False,
) -> dict[str, Any]:
    data = validate_file(path, "remediation-plan.schema.json")
    plan_kind = data["plan"].get("plan_kind", "remediation")
    greenfield_plan = (
        data["schema_version"] == "1.3" and plan_kind == "greenfield-implementation"
    )
    legacy_plan = not greenfield_plan and data["schema_version"] in {
        "1.1",
        "1.2",
        "1.3",
    }
    item_ids: set[str] = set()
    planned_findings: set[str] = set()
    for item in data["items"]:
        item_id = item["id"]
        if item_id in item_ids:
            raise ArchitectureError(f"{path} has duplicate plan item ID {item_id}")
        item_ids.add(item_id)
        finding_ids = item.get("finding_ids", [])
        duplicates = sorted(set(finding_ids) & planned_findings)
        if duplicates:
            raise ArchitectureError(
                f"{path} plans finding IDs more than once: " + ", ".join(duplicates)
            )
        planned_findings.update(finding_ids)
        if legacy_plan:
            required_item_fields = (
                "migration_type",
                "data_compatibility",
                "deployment_strategy",
                "observability_changes",
                "stop_conditions",
                "acceptance_evidence_types",
                "completion_evidence",
            )
            missing = [field for field in required_item_fields if field not in item]
            if missing:
                raise ArchitectureError(
                    f"{path} item {item_id} is missing: " + ", ".join(missing)
                )
        if data["plan"].get("status") == "complete" and data["schema_version"] != "1.0":
            completion_evidence = item.get("completion_evidence", [])
            if not completion_evidence:
                raise ArchitectureError(
                    f"{path} complete item {item_id} has no completion evidence"
                )
            observed_types = {entry["type"] for entry in completion_evidence}
            missing_types = sorted(
                set(item.get("acceptance_evidence_types", [])) - observed_types
            )
            if missing_types:
                raise ArchitectureError(
                    f"{path} complete item {item_id} is missing acceptance "
                    "evidence types: " + ", ".join(missing_types)
                )
            if repository_root is None:
                raise ArchitectureError(
                    f"{path} complete plan requires a repository root to "
                    "resolve completion evidence"
                )
            root = repository_root.resolve()
            for entry in completion_evidence:
                completion_path = require_within_root(
                    root,
                    root / entry["location"],
                    f"{path}:{item_id}.completion_evidence",
                )
                if not completion_path.is_file():
                    raise ArchitectureError(
                        f"{path} completion evidence is missing: {completion_path}"
                    )
                if file_sha256(completion_path) != entry["sha256"]:
                    raise ArchitectureError(
                        f"{path} completion evidence hash does not match "
                        f"{completion_path}"
                    )
                provider_fields = {
                    field for field in ("provider_id", "run_id") if entry.get(field)
                }
                if provider_fields and provider_fields != {"provider_id", "run_id"}:
                    raise ArchitectureError(
                        f"{path} completion evidence must bind provider_id and "
                        "run_id together"
                    )
                if provider_fields:
                    run_path = (
                        root / ".architecture" / "evidence" / f"{entry['run_id']}.yaml"
                    )
                    evidence_run = validate_evidence_run(
                        run_path,
                        root,
                        require_passed=True,
                    )
                    if evidence_run["run"]["provider_id"] != entry["provider_id"]:
                        raise ArchitectureError(
                            f"{path} completion evidence provider does not match "
                            f"run {entry['run_id']}"
                        )
                    bound_outputs = {
                        (record["path"], record["sha256"])
                        for field in ("stdout", "stderr", "structured_output")
                        if (record := evidence_run["result"].get(field)) is not None
                    }
                    if (entry["location"], entry["sha256"]) not in bound_outputs:
                        raise ArchitectureError(
                            f"{path} completion evidence does not bind an output "
                            f"of provider run {entry['run_id']}"
                        )
        if data["schema_version"] in {"1.2", "1.3"} and not greenfield_plan:
            bindings = {binding["id"]: binding for binding in item["finding_bindings"]}
            if len(bindings) != len(item["finding_bindings"]):
                raise ArchitectureError(
                    f"{path} item {item_id} repeats a finding binding"
                )
            if set(bindings) != set(item["finding_ids"]):
                raise ArchitectureError(
                    f"{path} item {item_id} finding_bindings must exactly "
                    "match finding_ids"
                )

    review: dict[str, Any] | None = None
    if review_path is not None:
        review_path = review_path.resolve()
        review = validate_review(review_path)
        if review["review"]["verification_state"] != "verified":
            raise ArchitectureError(f"{path} requires a verified source review")
        if data["schema_version"] in {
            "1.1",
            "1.2",
        } and review["schema_version"] not in {"1.1", "1.2"}:
            raise ArchitectureError(
                f"{path} requires a trusted 1.1 or 1.2 source review"
            )
        if data["plan"].get("source_review") != review["review"]["id"]:
            raise ArchitectureError(
                f"{path} source_review does not match {review_path}"
            )
        if data["schema_version"] in {"1.1", "1.2"} and data["plan"].get(
            "source_review_sha256"
        ) != file_sha256(review_path):
            raise ArchitectureError(
                f"{path} source_review_sha256 does not match {review_path}"
            )
        confirmed_open = {
            finding["id"]
            for finding in review["findings"]
            if finding["verification"]["status"] == "confirmed"
            and finding["status"] not in {"resolved", "rejected"}
        }
        unknown = sorted(planned_findings - confirmed_open)
        if unknown:
            raise ArchitectureError(
                f"{path} plans non-confirmed or resolved findings: "
                + ", ".join(unknown)
            )
        if data["schema_version"] in {"1.2", "1.3"}:
            if review["schema_version"] != "1.2":
                raise ArchitectureError(
                    f"{path} schema 1.2 requires a trusted 1.2 source review"
                )
            review_findings = {finding["id"]: finding for finding in review["findings"]}
            for item in data["items"]:
                for binding in item["finding_bindings"]:
                    finding = review_findings[binding["id"]]
                    if binding["fingerprint"] != finding["fingerprint"]:
                        raise ArchitectureError(
                            f"{path} item {item['id']} has a stale fingerprint "
                            f"for {binding['id']}"
                        )

    if legacy_plan:
        if review_path is None:
            raise ArchitectureError(
                f"{path} schema {data['schema_version']} requires --review "
                "for source validation"
            )
        if decision_path is None:
            raise ArchitectureError(
                f"{path} schema {data['schema_version']} requires --decision "
                "for source validation"
            )
        decision_path = decision_path.resolve()
        decision = validate_decision(
            decision_path,
            review_path=review_path,
            require_accepted=True,
            repository_root=repository_root,
            allow_unverifiable_historical=allow_unverifiable_historical,
            require_current_selection=require_current_selection,
        )
        if data["plan"].get("source_decision") != decision["decision"]["id"]:
            raise ArchitectureError(
                f"{path} source_decision does not match {decision_path}"
            )
        if data["plan"].get("source_decision_sha256") != file_sha256(decision_path):
            raise ArchitectureError(
                f"{path} source_decision_sha256 does not match {decision_path}"
            )
        decision_findings = set(decision["problem"]["finding_ids"])
        if not planned_findings.issubset(decision_findings):
            raise ArchitectureError(
                f"{path} plans findings absent from the accepted decision"
            )
        if data["schema_version"] in {"1.2", "1.3"}:
            if decision["schema_version"] != "1.2":
                raise ArchitectureError(
                    f"{path} schema 1.2 requires an accepted 1.2 decision"
                )
            decision_knowledge = {item["id"] for item in decision["knowledge_snapshot"]}
            for item in data["items"]:
                unknown_knowledge = sorted(
                    set(item["knowledge_ids"]) - decision_knowledge
                )
                if unknown_knowledge:
                    raise ArchitectureError(
                        f"{path} item {item['id']} references knowledge absent "
                        "from the accepted decision: " + ", ".join(unknown_knowledge)
                    )
    if greenfield_plan:
        if review_path is not None:
            raise ArchitectureError(
                f"{path} Greenfield implementation plans cannot bind a source Review"
            )
        if design_brief_path is None or decision_path is None:
            raise ArchitectureError(
                f"{path} schema 1.3 requires --design-brief and --decision"
            )
        design_brief_path = design_brief_path.resolve()
        brief = validate_design_brief(
            design_brief_path,
            repository_root=repository_root,
            allow_unverifiable_historical=allow_unverifiable_historical,
        )
        if brief["brief"]["status"] != "approved":
            raise ArchitectureError(
                f"{path} Greenfield implementation requires an approved Design Brief"
            )
        decision_path = decision_path.resolve()
        decision = validate_decision(
            decision_path,
            design_brief_path=design_brief_path,
            require_accepted=True,
            repository_root=repository_root,
            allow_unverifiable_historical=allow_unverifiable_historical,
            require_current_selection=require_current_selection,
        )
        if decision["decision"].get("decision_kind", "remediation") != "greenfield":
            raise ArchitectureError(
                f"{path} schema 1.3 requires an accepted Greenfield Decision"
            )
        if not isinstance(decision.get("target_architecture"), dict):
            raise ArchitectureError(
                f"{path} schema 1.3 Greenfield plans require a Decision "
                "target_architecture"
            )
        if data["plan"].get("source_decision") != decision["decision"]["id"]:
            raise ArchitectureError(
                f"{path} source_decision does not match {decision_path}"
            )
        if data["plan"].get("source_decision_sha256") != file_sha256(decision_path):
            raise ArchitectureError(
                f"{path} source_decision_sha256 does not match {decision_path}"
            )
        brief_path_value = next(
            (
                data["plan"].get(field)
                for field in ("source_design_brief", "source_context", "design_brief")
                if data["plan"].get(field) is not None
            ),
        )
        brief_hash_value = next(
            (
                data["plan"].get(field)
                for field in (
                    "source_design_brief_sha256",
                    "source_context_sha256",
                    "design_brief_sha256",
                )
                if data["plan"].get(field) is not None
            ),
        )
        if repository_root is not None:
            root = repository_root.resolve()
            expected_brief_path = require_within_root(
                root,
                design_brief_path,
                "plan source design brief",
            )
            expected_brief_value = expected_brief_path.relative_to(root).as_posix()
            if brief_path_value != expected_brief_value:
                raise ArchitectureError(
                    f"{path} source Design Brief path does not match "
                    f"{design_brief_path}"
                )
        elif brief_path_value not in {None, str(design_brief_path)}:
            raise ArchitectureError(
                f"{path} source Design Brief path does not match {design_brief_path}"
            )
        if brief_hash_value != file_sha256(design_brief_path):
            raise ArchitectureError(
                f"{path} source Design Brief hash does not match {design_brief_path}"
            )
        if any(item.get("finding_ids") for item in data["items"]):
            raise ArchitectureError(
                f"{path} Greenfield implementation plans cannot contain Finding IDs"
            )
        if any(item.get("finding_bindings") for item in data["items"]):
            raise ArchitectureError(
                f"{path} Greenfield implementation plans cannot contain "
                "Finding bindings"
            )
        _validate_greenfield_plan_bindings(path, data, decision, brief)
    return data


def validate_risk_acceptances(path: Path) -> dict[str, Any]:
    data = validate_file(path, "risk-acceptance.schema.json")
    ensure_unique_entries(data["acceptances"], "finding_id", path)
    for entry in data["acceptances"]:
        if entry["accepted_by"] == entry["approved_by"]:
            raise ArchitectureError(
                f"{path} risk acceptance {entry['finding_id']} requires "
                "separate accepter and approver identities"
            )
        accepted_at = datetime.fromisoformat(
            entry["accepted_at"].replace("Z", "+00:00")
        ).date()
        expires_on = parse_date(
            entry["expires_on"],
            f"{path}:{entry['finding_id']}.expires_on",
        )
        if expires_on < accepted_at:
            raise ArchitectureError(
                f"{path} risk acceptance {entry['finding_id']} expires "
                "before it is accepted"
            )
    return data


def validate_baseline(path: Path) -> dict[str, Any]:
    data = validate_file(path, "baseline.schema.json")
    ensure_unique_entries(data["findings"], "id", path)
    for entry in data["findings"]:
        recorded_on = parse_date(
            entry["recorded_on"],
            f"{path}:{entry['id']}.recorded_on",
        )
        if entry.get("expires_on") is None:
            continue
        expires_on = parse_date(
            entry["expires_on"],
            f"{path}:{entry['id']}.expires_on",
        )
        if expires_on < recorded_on:
            raise ArchitectureError(
                f"{path} baseline {entry['id']} expires before it is recorded"
            )
    return data


def validate_knowledge(today: date | None = None) -> dict[str, Any]:
    evaluation_date = today or datetime.now(UTC).date()
    try:
        manifest, markdown_entries = validate_knowledge_tree(
            KNOWLEDGE_ROOT,
            schema_root=SCHEMA_ROOT,
            today=evaluation_date,
        )
    except KnowledgeError as exc:
        raise ArchitectureError(str(exc)) from exc
    catalogs: list[str] = []
    entries: dict[str, Path] = {}
    stale: list[str] = []
    for path in sorted(KNOWLEDGE_ROOT.rglob("*.yaml")):
        if path.name == "manifest.yaml":
            continue
        payload = validate_file(path, "knowledge-catalog.schema.json")
        catalogs.append(str(path))
        for entry in payload["entries"]:
            entry_id = f"{payload['kind']}:{entry['id']}"
            if entry_id in entries:
                raise ArchitectureError(
                    f"Knowledge entry {entry_id} appears in {entries[entry_id]} "
                    f"and {path}"
                )
            entries[entry_id] = path
            reviewed_on = parse_date(
                entry["freshness"]["reviewed_on"],
                f"{path}:{entry_id}.freshness.reviewed_on",
            )
            if reviewed_on > evaluation_date:
                raise ArchitectureError(
                    f"Knowledge entry {entry_id} has future review date "
                    f"{reviewed_on.isoformat()}"
                )
            age = (evaluation_date - reviewed_on).days
            if age > entry["freshness"]["review_after_days"]:
                stale.append(entry_id)
            source_urls: set[str] = set()
            for source in entry["sources"]:
                url = source["url"]
                if not url.startswith("https://"):
                    raise ArchitectureError(
                        f"Knowledge entry {entry_id} must use HTTPS sources: {url}"
                    )
                if url in source_urls:
                    raise ArchitectureError(
                        f"Knowledge entry {entry_id} repeats source {url}"
                    )
                source_urls.add(url)
    rule_pack_ids = [
        validate_file(path, "rule-pack.schema.json")["id"]
        for path in sorted(RULE_ROOT.glob("*.yaml"))
    ]
    rule_packs = load_rule_packs(rule_pack_ids)
    expected_rules(rule_packs)
    provider_path = EVIDENCE_PROVIDER_ROOT / "catalog.yaml"
    providers = validate_file(provider_path, "evidence-provider.schema.json")
    provider_ids = [provider["id"] for provider in providers["providers"]]
    if len(provider_ids) != len(set(provider_ids)):
        raise ArchitectureError(f"{provider_path} has duplicate provider IDs")
    if stale:
        raise ArchitectureError(
            "Stale architecture knowledge entries: " + ", ".join(sorted(stale))
        )
    return {
        "catalogs": len(catalogs),
        "entries": len(entries),
        "knowledge_packs": len(manifest["packs"]),
        "markdown_entries": len(markdown_entries),
        "rule_packs": len(rule_packs),
        "providers": len(provider_ids),
        "stale": stale,
    }


def resolve_from_root(
    root: Path,
    configured_path: str,
    *,
    allow_outside: bool = False,
) -> Path:
    path = Path(configured_path)
    candidate = path if path.is_absolute() else root / path
    if allow_outside:
        return candidate.resolve()
    return require_within_root(root, candidate, "configured path")


def validate_profile_review_requirements(
    profile: dict[str, Any],
    source: Path,
) -> list[dict[str, Any]]:
    project = profile["project"]
    requirements = project.get("review_requirements", [])
    ids = [requirement["id"] for requirement in requirements]
    if len(ids) != len(set(ids)):
        raise ArchitectureError(f"{source} has duplicate review requirement IDs")
    if set(ids) != set(project["required_reviews"]):
        raise ArchitectureError(
            f"{source} review_requirements must exactly map required_reviews"
        )
    allowed_packs = set(project["rule_packs"])
    selected_packs: set[str] = set()
    for requirement in requirements:
        selected_packs.update(requirement["rule_packs"])
        unknown = sorted(set(requirement["rule_packs"]) - allowed_packs)
        if unknown:
            raise ArchitectureError(
                f"{source} review {requirement['id']} uses unloaded packs: "
                + ", ".join(unknown)
            )
        core_pack = REVIEW_KIND_CORE_PACK[requirement["kind"]]
        if core_pack not in requirement["rule_packs"]:
            raise ArchitectureError(
                f"{source} review {requirement['id']} requires {core_pack}"
            )
        known_kind = REVIEW_WORKFLOW_KIND.get(requirement["id"])
        if known_kind is not None and known_kind != requirement["kind"]:
            raise ArchitectureError(
                f"{source} review {requirement['id']} must use kind {known_kind}"
            )
    if selected_packs != allowed_packs:
        unused = sorted(allowed_packs - selected_packs)
        raise ArchitectureError(
            f"{source} rule_packs must exactly equal the review requirement "
            "union; unassigned packs: " + ", ".join(unused)
        )
    return cast(list[dict[str, Any]], requirements)


def validate_project(root: Path) -> list[Path]:
    root = root.resolve()
    config_root = root / ".architecture"
    profile_path = config_root / "profile.yaml"
    policy_path = config_root / "gate-policy.yaml"
    baseline_path = config_root / "baseline.yaml"
    risk_acceptance_path = config_root / "risk-acceptances.yaml"
    provider_config_path = config_root / "evidence-providers.yaml"

    profile = validate_file(profile_path, "project-profile.schema.json")
    repository_facts_path: Path | None = None
    if "repository_facts" in profile["project"]:
        binding = profile["project"]["repository_facts"]
        repository_facts_path = resolve_from_root(root, binding["path"])
        validate_file(
            repository_facts_path,
            "repository-facts.schema.json",
        )
        if binding["sha256"] != file_sha256(repository_facts_path):
            raise ArchitectureError(
                f"{profile_path} repository facts hash does not match "
                f"{repository_facts_path}"
            )
    review_requirements = validate_profile_review_requirements(profile, profile_path)
    policy = validate_file(policy_path, "gate-policy.schema.json")
    validate_baseline(baseline_path)
    if risk_acceptance_path.is_file():
        validate_risk_acceptances(risk_acceptance_path)
    elif policy["schema_version"] in TRUSTED_POLICY_VERSIONS:
        raise ArchitectureError(f"Missing file: {risk_acceptance_path}")
    validate_evidence_provider_config(provider_config_path)
    rule_packs = load_rule_packs(
        profile["project"]["rule_packs"],
        local_rule_pack_roots(root),
    )
    validate_review_rule_pack_kinds(
        review_requirements,
        rule_packs,
        profile_path,
    )

    if policy["schema_version"] in TRUSTED_POLICY_VERSIONS:
        configured_acceptance_path = resolve_from_root(
            root,
            policy["risk_acceptances_file"],
        )
        if configured_acceptance_path != risk_acceptance_path.resolve():
            raise ArchitectureError(
                "Project risk_acceptances_file must resolve to "
                f"{risk_acceptance_path.resolve()}"
            )

    validated = [
        profile_path,
        policy_path,
        baseline_path,
    ]
    if repository_facts_path is not None:
        validated.append(repository_facts_path)
    if risk_acceptance_path.is_file():
        validated.append(risk_acceptance_path)
    validated.append(provider_config_path)
    for field in ("constraints_file", "critical_flows_file"):
        expected = resolve_from_root(root, profile["project"][field])
        if not expected.is_file():
            raise ArchitectureError(
                f"Profile field project.{field} points to missing file: {expected}"
            )
        validated.append(expected)

    reviews_root = resolve_from_root(root, profile["project"]["review_output"])
    if not reviews_root.is_dir():
        raise ArchitectureError(f"Missing review output directory: {reviews_root}")

    review_records: dict[str, Path] = {}
    decision_records: dict[str, tuple[Path, dict[str, Any]]] = {}
    plan_artifacts: list[tuple[Path, dict[str, Any]]] = []
    for artifact in sorted(reviews_root.glob("*.yaml")):
        payload = load_yaml(artifact)
        if "review" in payload:
            review = validate_review(
                artifact,
                rule_pack_ids=profile["project"]["rule_packs"],
                strict_trust=payload.get("schema_version") in {"1.1", "1.2"}
                and payload["review"].get("verification_state") == "verified",
                repository_root=root,
                allow_unverifiable_historical=True,
            )
            repository_identity = review["review"].get("repository_identity")
            if repository_identity is not None and not repository_identities_match(
                repository_identity,
                profile["project"]["id"],
            ):
                raise ArchitectureError(
                    f"{artifact} repository_identity does not match project profile"
                )
            if review["review"]["id"] in review_records:
                raise ArchitectureError(
                    f"Duplicate review ID {review['review']['id']} in {reviews_root}"
                )
            review_records[review["review"]["id"]] = artifact
        elif "decision" in payload:
            decision = validate_decision(
                artifact,
                repository_root=root,
                allow_unverifiable_historical=True,
            )
            decision_id = decision["decision"]["id"]
            if decision_id in decision_records:
                raise ArchitectureError(
                    f"Duplicate decision ID {decision_id} in {reviews_root}"
                )
            decision_records[decision_id] = (artifact, decision)
        elif "plan" in payload:
            plan_artifacts.append((artifact, payload))
        else:
            raise ArchitectureError(
                f"Unknown YAML artifact in reviews directory: {artifact}"
            )
        validated.append(artifact)
    for artifact, decision in decision_records.values():
        if decision["decision"].get("decision_kind", "remediation") == "greenfield":
            brief_path = require_within_root(
                root,
                root / decision["decision"]["source_context"],
                "decision source design brief",
            )
            validate_decision(
                artifact,
                design_brief_path=brief_path,
                repository_root=root,
                allow_unverifiable_historical=True,
            )
        else:
            source_review = review_records.get(decision["decision"]["source_review"])
            if source_review is None:
                raise ArchitectureError(
                    f"{artifact} references a missing source review "
                    f"{decision['decision']['source_review']}"
                )
            validate_decision(
                artifact,
                review_path=source_review,
                repository_root=root,
                allow_unverifiable_historical=True,
            )
    for artifact, payload in plan_artifacts:
        source_decision_record = decision_records.get(
            payload["plan"].get("source_decision", "")
        )
        source_decision = (
            source_decision_record[0] if source_decision_record is not None else None
        )
        if (
            payload.get("schema_version") == "1.3"
            and payload["plan"].get("plan_kind") == "greenfield-implementation"
        ):
            if (
                source_decision_record is None
                or source_decision_record[1]["decision"].get(
                    "decision_kind", "remediation"
                )
                != "greenfield"
            ):
                raise ArchitectureError(
                    f"{artifact} references a missing Greenfield source decision"
                )
            brief_path = require_within_root(
                root,
                root / source_decision_record[1]["decision"]["source_context"],
                "plan source design brief",
            )
            validate_plan(
                artifact,
                decision_path=source_decision,
                design_brief_path=brief_path,
                repository_root=root,
                allow_unverifiable_historical=True,
            )
        else:
            source_review = review_records.get(payload["plan"]["source_review"])
            if source_review is None:
                raise ArchitectureError(
                    f"{artifact} references a missing source review "
                    f"{payload['plan']['source_review']}"
                )
            validate_plan(
                artifact,
                review_path=source_review,
                decision_path=source_decision,
                repository_root=root,
                allow_unverifiable_historical=True,
            )
    return validated


def validate_portfolio(root: Path) -> list[Path]:
    root = root.resolve()
    config_root = root / ".architecture-portfolio"
    registry_path = config_root / "portfolio.yaml"
    registry = validate_file(registry_path, "portfolio.schema.json")
    policy_path = config_root / "gate-policy.yaml"
    baseline_path = config_root / "baseline.yaml"
    risk_acceptance_path = config_root / "risk-acceptances.yaml"
    policy = validate_file(policy_path, "gate-policy.schema.json")
    validate_baseline(baseline_path)
    if risk_acceptance_path.is_file():
        validate_risk_acceptances(risk_acceptance_path)
    elif policy["schema_version"] in TRUSTED_POLICY_VERSIONS:
        raise ArchitectureError(f"Missing file: {risk_acceptance_path}")
    catalog_schemas = {
        "shared_capabilities": "shared-capabilities.schema.json",
        "technologies": "technology-catalog.schema.json",
        "dependencies": "dependency-map.schema.json",
    }
    if policy["schema_version"] in TRUSTED_POLICY_VERSIONS:
        configured_acceptance_path = resolve_from_root(
            root,
            policy["risk_acceptances_file"],
        )
        if configured_acceptance_path != risk_acceptance_path.resolve():
            raise ArchitectureError(
                "Portfolio risk_acceptances_file must resolve to "
                f"{risk_acceptance_path.resolve()}"
            )
    validated = [
        registry_path,
        policy_path,
        baseline_path,
    ]
    if risk_acceptance_path.is_file():
        validated.append(risk_acceptance_path)
    for key, schema_name in catalog_schemas.items():
        catalog_path = resolve_from_root(root, registry["catalogs"][key])
        validate_file(catalog_path, schema_name)
        validated.append(catalog_path)

    project_ids = [project["id"] for project in registry["projects"]]
    duplicate_ids = sorted(
        {project_id for project_id in project_ids if project_ids.count(project_id) > 1}
    )
    if duplicate_ids:
        raise ArchitectureError(
            f"{registry_path} has duplicate project IDs: " + ", ".join(duplicate_ids)
        )
    known_ids = set(project_ids)
    for project in registry["projects"]:
        unknown = sorted(set(project["depends_on"]) - known_ids)
        if unknown:
            raise ArchitectureError(
                f"{registry_path} project {project['id']} depends on unknown IDs: "
                + ", ".join(unknown)
            )

    reviews_root = resolve_from_root(root, registry["portfolio"]["review_output"])
    if not reviews_root.is_dir():
        raise ArchitectureError(f"Missing portfolio review directory: {reviews_root}")
    review_records: dict[str, Path] = {}
    decision_records: dict[str, tuple[Path, dict[str, Any]]] = {}
    plan_artifacts: list[tuple[Path, dict[str, Any]]] = []
    for artifact in sorted(reviews_root.glob("*.yaml")):
        payload = load_yaml(artifact)
        if "review" in payload:
            review = validate_review(
                artifact,
                rule_pack_ids=["portfolio-core"],
                strict_trust=payload.get("schema_version") in {"1.1", "1.2"}
                and payload["review"].get("verification_state") == "verified",
                repository_root=root,
            )
            if review["review"].get("repository_identity") not in {
                None,
                registry["portfolio"]["id"],
            }:
                raise ArchitectureError(
                    f"{artifact} repository_identity does not match portfolio registry"
                )
            if review["review"]["id"] in review_records:
                raise ArchitectureError(
                    f"Duplicate review ID {review['review']['id']} in {reviews_root}"
                )
            review_records[review["review"]["id"]] = artifact
        elif "decision" in payload:
            decision = validate_decision(artifact)
            decision_id = decision["decision"]["id"]
            if decision_id in decision_records:
                raise ArchitectureError(
                    f"Duplicate decision ID {decision_id} in {reviews_root}"
                )
            decision_records[decision_id] = (artifact, decision)
        elif "plan" in payload:
            plan_artifacts.append((artifact, payload))
        else:
            raise ArchitectureError(
                f"Unknown YAML artifact in portfolio reviews: {artifact}"
            )
        validated.append(artifact)
    for artifact, decision in decision_records.values():
        if decision["decision"].get("decision_kind", "remediation") == "greenfield":
            brief_path = require_within_root(
                root,
                root / decision["decision"]["source_context"],
                "decision source design brief",
            )
            validate_decision(artifact, design_brief_path=brief_path)
        else:
            source_review = review_records.get(decision["decision"]["source_review"])
            if source_review is None:
                raise ArchitectureError(
                    f"{artifact} references a missing source review "
                    f"{decision['decision']['source_review']}"
                )
            validate_decision(artifact, review_path=source_review)
    for artifact, payload in plan_artifacts:
        source_decision_record = decision_records.get(
            payload["plan"].get("source_decision", "")
        )
        if (
            payload.get("schema_version") == "1.3"
            and payload["plan"].get("plan_kind") == "greenfield-implementation"
        ):
            if (
                source_decision_record is None
                or source_decision_record[1]["decision"].get(
                    "decision_kind", "remediation"
                )
                != "greenfield"
            ):
                raise ArchitectureError(
                    f"{artifact} references a missing Greenfield source decision"
                )
            brief_path = require_within_root(
                root,
                root / source_decision_record[1]["decision"]["source_context"],
                "plan source design brief",
            )
            validate_plan(
                artifact,
                decision_path=source_decision_record[0],
                design_brief_path=brief_path,
                repository_root=root,
            )
        else:
            source_review = review_records.get(payload["plan"]["source_review"])
            if source_review is None:
                raise ArchitectureError(
                    f"{artifact} references a missing source review "
                    f"{payload['plan']['source_review']}"
                )
            validate_plan(
                artifact,
                review_path=source_review,
                decision_path=(
                    source_decision_record[0]
                    if source_decision_record is not None
                    else None
                ),
                repository_root=root,
            )
    return validated


def copy_template(name: str, destination: Path) -> None:
    source = TEMPLATE_ROOT / name
    if not source.is_file():
        raise ArchitectureError(f"Missing bundled template: {source}")
    shutil.copyfile(source, destination)


def init_project(args: argparse.Namespace) -> Path:
    root = Path(args.repo).resolve()
    if not root.is_dir():
        raise ArchitectureError(f"Repository directory does not exist: {root}")
    target = root / ".architecture"
    if target.exists():
        raise ArchitectureError(f"Refusing to overwrite existing directory: {target}")

    project_name = args.name or root.name
    project_id = args.project_id or slugify(project_name)
    selected_reviews = args.reviews or ["project-architecture"]
    selected_packs = args.rule_packs or ["project-core"]
    bundled_packs = load_rule_packs(selected_packs)
    review_requirements: list[dict[str, Any]] = []
    for workflow in selected_reviews:
        review_kind = REVIEW_WORKFLOW_KIND.get(workflow, "project")
        core_pack = REVIEW_KIND_CORE_PACK[review_kind]
        if core_pack not in selected_packs:
            raise ArchitectureError(f"Review {workflow} requires rule pack {core_pack}")
        workflow_packs = [
            pack_id
            for pack_id in selected_packs
            if bundled_packs[pack_id]["payload"]["review_kind"] == review_kind
        ]
        review_requirements.append(
            {
                "id": workflow,
                "kind": review_kind,
                "rule_packs": workflow_packs,
            }
        )
    used_packs = {
        pack_id
        for requirement in review_requirements
        for pack_id in requirement["rule_packs"]
    }
    unused_packs = sorted(set(selected_packs) - used_packs)
    if unused_packs:
        raise ArchitectureError(
            "Rule Packs have no selected review of their kind: "
            + ", ".join(unused_packs)
        )
    with tempfile.TemporaryDirectory(prefix=".architecture-init-", dir=root) as temp:
        staged = Path(temp) / ".architecture"
        staged.mkdir()
        (staged / "reviews").mkdir()
        (staged / "reviews" / ".gitkeep").touch()
        # Run records are optional, informational trajectory metadata for
        # high-risk work. They are deliberately separate from trusted reviews.
        (staged / "runs").mkdir()
        (staged / "runs" / ".gitkeep").touch()
        (staged / "evidence").mkdir()
        (staged / "rules").mkdir()
        (staged / "rules" / ".gitkeep").touch()
        repository_facts = inspect_repository(root)
        facts_path = staged / "repository-facts.yaml"
        write_yaml(facts_path, repository_facts)

        if getattr(args, "infer_profile", False):
            profile = build_profile(facts_path)
            profile["project"]["id"] = project_id
            profile["project"]["name"] = project_name
            profile["project"]["repository_facts"] = {
                "path": ".architecture/repository-facts.yaml",
                "sha256": file_sha256(facts_path),
            }
            profile["project"]["profile_sources"]["detected"] = [
                ".architecture/repository-facts.yaml"
            ]
        else:
            profile = load_yaml(TEMPLATE_ROOT / "profile.yaml")
            profile["project"].update(
                {
                    "id": project_id,
                    "name": project_name,
                    "type": args.types or ["service"],
                    "lifecycle": args.lifecycle,
                    "criticality": args.criticality,
                    "owners": args.owners or ["unassigned"],
                    "critical_qualities": args.qualities
                    or ["maintainability", "recoverability"],
                    "required_reviews": selected_reviews,
                    "review_requirements": review_requirements,
                    "rule_packs": selected_packs,
                    "data_classification": args.data_classification,
                    "repository_facts": {
                        "path": ".architecture/repository-facts.yaml",
                        "sha256": file_sha256(facts_path),
                    },
                    "required_knowledge_domains": derive_domains(repository_facts),
                    "profile_sources": {
                        "detected": [".architecture/repository-facts.yaml"],
                        "declared": [],
                        "inferred": [
                            {
                                "inference": (
                                    "Knowledge domains inferred from deterministic "
                                    "repository facts."
                                ),
                                "confidence": 0.8,
                                "basis": [".architecture/repository-facts.yaml"],
                            }
                        ],
                    },
                }
            )
        validate_data(
            profile,
            "project-profile.schema.json",
            staged / "profile.yaml",
        )
        write_yaml(staged / "profile.yaml", profile)
        copy_template("constraints.md", staged / "constraints.md")
        copy_template("critical-flows.md", staged / "critical-flows.md")
        copy_template("gate-policy.yaml", staged / "gate-policy.yaml")
        copy_template("baseline.yaml", staged / "baseline.yaml")
        copy_template("risk-acceptances.yaml", staged / "risk-acceptances.yaml")
        copy_template(
            "evidence-providers.yaml",
            staged / "evidence-providers.yaml",
        )
        staged.rename(target)
    return target


def prepare_project_audit(args: argparse.Namespace) -> tuple[Path, bool]:
    """Create a facts-derived project control plane or validate the existing one."""
    root = Path(args.repo).resolve()
    if not root.is_dir():
        raise ArchitectureError(f"Repository directory does not exist: {root}")
    target = root / ".architecture"
    if target.exists():
        validate_project(root)
        return target, False

    target = init_project(
        argparse.Namespace(
            repo=str(root),
            name=args.name,
            project_id=args.project_id,
            types=[],
            lifecycle="active",
            criticality="medium",
            owners=[],
            qualities=[],
            reviews=[],
            rule_packs=[],
            data_classification="internal",
            infer_profile=True,
        )
    )
    validate_project(root)
    return target, True


def init_portfolio(args: argparse.Namespace) -> Path:
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise ArchitectureError(f"Portfolio root does not exist: {root}")
    target = root / ".architecture-portfolio"
    if target.exists():
        raise ArchitectureError(f"Refusing to overwrite existing directory: {target}")

    portfolio_name = args.name or f"{root.name} Portfolio"
    portfolio_id = args.portfolio_id or slugify(portfolio_name)
    with tempfile.TemporaryDirectory(
        prefix=".architecture-portfolio-init-",
        dir=root,
    ) as temp:
        staged = Path(temp) / ".architecture-portfolio"
        staged.mkdir()
        (staged / "reviews").mkdir()
        (staged / "reviews" / ".gitkeep").touch()
        (staged / "runs").mkdir()
        (staged / "runs" / ".gitkeep").touch()

        portfolio = load_yaml(TEMPLATE_ROOT / "portfolio.yaml")
        portfolio["portfolio"].update(
            {
                "id": portfolio_id,
                "name": portfolio_name,
                "owners": args.owners or ["unassigned"],
                "review_horizon_months": args.review_horizon_months,
            }
        )
        validate_data(
            portfolio,
            "portfolio.schema.json",
            staged / "portfolio.yaml",
        )
        write_yaml(staged / "portfolio.yaml", portfolio)
        copy_template(
            "shared-capabilities.yaml",
            staged / "shared-capabilities.yaml",
        )
        copy_template(
            "technology-catalog.yaml",
            staged / "technology-catalog.yaml",
        )
        copy_template("dependency-map.yaml", staged / "dependency-map.yaml")
        copy_template(
            "portfolio-gate-policy.yaml",
            staged / "gate-policy.yaml",
        )
        copy_template("baseline.yaml", staged / "baseline.yaml")
        copy_template("risk-acceptances.yaml", staged / "risk-acceptances.yaml")
        staged.rename(target)
    return target


def parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ArchitectureError(f"Invalid date for {field}: {value}") from exc


def active_until(expires_on: str | None, today: date) -> bool:
    return expires_on is None or parse_date(expires_on, "expires_on") >= today


def current_git_commit(root: Path) -> str:
    return git_output(root, "rev-parse", "HEAD")


def git_is_clean(root: Path) -> bool:
    return not git_output(root, "status", "--porcelain")


def git_worktree_paths(root: Path) -> set[str]:
    paths: set[str] = set()
    for arguments in (
        ("diff", "--name-only"),
        ("diff", "--cached", "--name-only"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        output = git_output(root, *arguments)
        paths.update(line for line in output.splitlines() if line)
    return paths


def git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    process = git_process(root, "merge-base", "--is-ancestor", ancestor, descendant)
    if process.returncode not in {0, 1}:
        raise ArchitectureError(
            f"Cannot compare commits {ancestor} and {descendant}: "
            f"{process.stderr.strip()}"
        )
    return process.returncode == 0


def git_changed_paths(root: Path, old_commit: str, new_commit: str) -> set[str]:
    output = git_output(
        root,
        "diff",
        "--name-only",
        "--diff-filter=ACDMRTUXB",
        f"{old_commit}..{new_commit}",
    )
    return {line for line in output.splitlines() if line}


def evidence_path(item: dict[str, Any]) -> str | None:
    path_value = item.get("path")
    if isinstance(path_value, str) and path_value:
        return path_value
    location = item.get("location", "")
    if "://" in location or not location:
        return None
    candidate = location.rsplit(":", 1)[0]
    return candidate if candidate else None


def verify_review_evidence(
    review: dict[str, Any],
    repository_root: Path,
) -> list[dict[str, str]]:
    root = repository_root.resolve()
    results: list[dict[str, str]] = []
    repository_identity = review["review"].get(
        "repository_identity",
        review["review"]["subject"]["id"],
    )
    reviewed_commit = (
        review["review"].get("commit")
        if review.get("schema_version") == "1.2"
        else None
    )
    resolved_reviewed_commit = (
        git_output(root, "rev-parse", f"{reviewed_commit}^{{commit}}")
        if reviewed_commit
        else None
    )
    for finding in review["findings"]:
        if finding["verification"]["status"] != "confirmed":
            continue
        current_evidence = False
        for index, item in enumerate(finding["evidence"]):
            if item["type"] == "tool":
                run_path = require_within_root(
                    root,
                    root / item["provider_run"],
                    f"Finding {finding['id']} tool evidence",
                )
                if item["provider_run_sha256"] != file_sha256(run_path):
                    raise ArchitectureError(
                        f"Finding {finding['id']} evidence {index} provider "
                        "run hash does not match"
                    )
                evidence_run = validate_evidence_run(
                    run_path,
                    root,
                    require_passed=True,
                )
                if evidence_run["run"]["provider_id"] != item["provider_id"]:
                    raise ArchitectureError(
                        f"Finding {finding['id']} evidence {index} provider "
                        "does not match run"
                    )
                current_evidence = True
                results.append(
                    {
                        "finding_id": finding["id"],
                        "evidence": str(index),
                        "status": "resolved",
                        "provider_id": item["provider_id"],
                    }
                )
                continue
            if item["type"] in {"runtime", "history", "document"}:
                results.append(
                    {
                        "finding_id": finding["id"],
                        "evidence": str(index),
                        "status": "not-resolved",
                        "reason": (
                            f"{item['type']} evidence requires its owning provider"
                        ),
                    }
                )
                continue
            path_text = evidence_path(item)
            commit = item.get("commit", item.get("source_commit"))
            if (
                not path_text
                or not commit
                or not re.fullmatch(
                    r"[a-fA-F0-9]{7,64}",
                    commit,
                )
            ):
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} requires a "
                    "repository path and Git commit"
                )
            if item.get("repository", repository_identity) != repository_identity:
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} repository "
                    "does not match review subject"
                )
            relative = Path(path_text)
            if relative.is_absolute() or ".." in relative.parts:
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} path escapes repository"
                )
            object_name = f"{commit}:{relative.as_posix()}"
            resolved_evidence_commit = git_output(
                root,
                "rev-parse",
                f"{commit}^{{commit}}",
            )
            if (
                resolved_reviewed_commit is not None
                and resolved_evidence_commit != resolved_reviewed_commit
                and not item.get("historical", False)
            ):
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} is from another "
                    "snapshot and is not classified historical"
                )
            if (
                resolved_reviewed_commit is not None
                and resolved_evidence_commit == resolved_reviewed_commit
                and not item.get("historical", False)
            ):
                current_evidence = True
            process = git_process(root, "cat-file", "-e", object_name)
            if process.returncode != 0:
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} cannot resolve "
                    f"{object_name}"
                )
            blob_sha = git_output(root, "rev-parse", object_name)
            if item.get("blob_sha") and item["blob_sha"] != blob_sha:
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} blob SHA changed"
                )
            content = git_raw_output(root, "show", object_name)
            lines = content.splitlines()
            line_start = item.get("line_start")
            line_end = item.get("line_end", line_start)
            if line_start is not None and (
                line_end is None or line_end < line_start or line_end > len(lines)
            ):
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} line range is invalid"
                )
            symbol = item.get("symbol")
            if symbol and symbol not in content:
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} symbol "
                    f"{symbol!r} is absent at {object_name}"
                )
            excerpt = item.get("excerpt")
            selected_content = content
            if line_start is not None and line_end is not None:
                selected_content = "\n".join(lines[line_start - 1 : line_end])
            if excerpt and excerpt not in selected_content:
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} excerpt is "
                    "absent from the bound source range"
                )
            if (
                excerpt
                and item.get("excerpt_sha256")
                and sha256_bytes(excerpt.encode("utf-8")) != item["excerpt_sha256"]
            ):
                raise ArchitectureError(
                    f"Finding {finding['id']} evidence {index} excerpt hash "
                    "does not match"
                )
            results.append(
                {
                    "finding_id": finding["id"],
                    "evidence": str(index),
                    "status": "resolved",
                    "blob_sha": blob_sha,
                }
            )
        if review.get("schema_version") == "1.2" and not current_evidence:
            raise ArchitectureError(
                f"Finding {finding['id']} has no evidence bound to the reviewed "
                "repository snapshot"
            )
    return results


def find_latest_review(reviews_root: Path) -> Path:
    candidates: list[tuple[datetime, str, Path]] = []
    for path in reviews_root.glob("*-verified.yaml"):
        payload = validate_review(path)
        if payload["review"]["verification_state"] != "verified":
            continue
        performed_at = datetime.fromisoformat(
            payload["review"]["performed_at"].replace("Z", "+00:00")
        )
        candidates.append((performed_at, payload["review"]["id"], path))
    if not candidates:
        raise ArchitectureError(f"No verified review found in {reviews_root}")
    candidates.sort(key=lambda item: (item[0], item[1], item[2].name))
    return candidates[-1][2]


def validate_history_anchors(
    repository_root: Path,
    review_path: Path | None = None,
) -> dict[str, Any]:
    """Require selector and reviewed implementation commits in HEAD history."""
    root = repository_root.resolve()
    selector_source_path = root / "resources" / "selector-source.json"
    selector_source = validate_file(
        selector_source_path,
        "selector-source.schema.json",
    )
    if review_path is None:
        review_path = find_latest_review(root / ".architecture" / "reviews")
    elif not review_path.is_absolute():
        review_path = root / review_path
    review_path = require_within_root(root, review_path, "history anchor review")
    review = validate_review(review_path)
    reviewed_commit = review["review"].get("commit")
    if not reviewed_commit or reviewed_commit == "unknown":
        raise ArchitectureError(
            f"{review_path} does not identify a reviewed implementation commit"
        )
    head = current_git_commit(root)
    anchors = {
        "selector_source": selector_source["commit"],
        "reviewed_implementation": reviewed_commit,
    }
    for name, commit in anchors.items():
        git_output(root, "cat-file", "-e", f"{commit}^{{commit}}")
        if not git_is_ancestor(root, commit, head):
            raise ArchitectureError(
                f"{name} anchor {commit} is not an ancestor of HEAD {head}; "
                "preserve history and merge with a merge commit"
            )
    try:
        anchored_manifest = json.loads(
            git_raw_output(
                root,
                "show",
                f"{selector_source['commit']}:.codex-plugin/plugin.json",
            )
        )
    except json.JSONDecodeError as exc:
        raise ArchitectureError(
            "Selector source anchor contains a malformed plugin manifest"
        ) from exc
    if anchored_manifest.get("version") != selector_source["plugin_version"]:
        raise ArchitectureError(
            "Selector source anchor plugin version does not match "
            "resources/selector-source.json"
        )
    return {
        "head": head,
        "selector_source": {
            "path": selector_source_path.relative_to(root).as_posix(),
            "commit": anchors["selector_source"],
        },
        "reviewed_implementation": {
            "review": review_path.relative_to(root).as_posix(),
            "commit": anchors["reviewed_implementation"],
        },
    }


def verify_ssh_artifact_signature(
    artifact_path: Path,
    signature: dict[str, Any],
    root: Path,
    signature_policy: dict[str, Any],
    artifact_label: str,
) -> None:
    if signature["namespace"] != signature_policy["namespace"]:
        raise ArchitectureError(
            f"{artifact_path} signature namespace does not match policy"
        )
    signature_path = require_within_root(
        root,
        root / signature["path"],
        f"{artifact_label}.signature.path",
    )
    allowed_signers = require_within_root(
        root,
        root / signature_policy["allowed_signers_file"],
        "artifact_signatures.allowed_signers_file",
    )
    if not signature_path.is_file():
        raise ArchitectureError(f"Missing {artifact_label} signature: {signature_path}")
    if not allowed_signers.is_file():
        raise ArchitectureError(f"Missing allowed signers file: {allowed_signers}")
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        raise ArchitectureError(
            f"ssh-keygen is required for {artifact_label} signature verification"
        )
    process = subprocess.run(
        [
            ssh_keygen,
            "-Y",
            "verify",
            "-f",
            str(allowed_signers),
            "-I",
            signature["identity"],
            "-n",
            signature["namespace"],
            "-s",
            str(signature_path),
        ],
        input=artifact_path.read_bytes(),
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        detail = (
            process.stderr.decode("utf-8", errors="replace").strip()
            or process.stdout.decode("utf-8", errors="replace").strip()
            or "signature verification failed"
        )
        raise ArchitectureError(f"{artifact_path} SSH signature is invalid: {detail}")


def verify_review_signature(
    review_path: Path,
    review: dict[str, Any],
    root: Path,
    signature_policy: dict[str, Any],
) -> None:
    signature = review["review"].get("signature")
    if signature is None:
        raise ArchitectureError(f"{review_path} has no detached review signature")
    verify_ssh_artifact_signature(
        review_path,
        signature,
        root,
        signature_policy,
        "review",
    )


def ensure_unique_entries(
    entries: list[dict[str, Any]],
    key: str,
    source: Path,
) -> None:
    values = [entry[key] for entry in entries]
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ArchitectureError(
            f"{source} has duplicate {key} values: " + ", ".join(duplicates)
        )


def matching_paths(paths: set[str], patterns: list[str]) -> list[str]:
    return sorted(
        path
        for path in paths
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
    )


def related_governance_artifacts(
    config_root: Path,
    review_path: Path,
    review: dict[str, Any],
) -> tuple[
    list[tuple[Path, dict[str, Any]]],
    list[tuple[Path, dict[str, Any]]],
]:
    decisions: list[tuple[Path, dict[str, Any]]] = []
    decision_paths: dict[str, Path] = {}
    plan_candidates: list[Path] = []
    for artifact in sorted((config_root / "reviews").glob("*.yaml")):
        payload = load_yaml(artifact)
        if "decision" in payload:
            validate_data(payload, "architecture-decision.schema.json", artifact)
            decision_id = payload["decision"]["id"]
            if decision_id in decision_paths:
                raise ArchitectureError(
                    f"Duplicate architecture decision ID {decision_id} in "
                    f"{decision_paths[decision_id]} and {artifact}"
                )
            decision_paths[decision_id] = artifact
            decision_kind = payload["decision"].get("decision_kind", "remediation")
            if (
                decision_kind != "greenfield"
                and payload["decision"]["source_review"] == review["review"]["id"]
            ):
                decisions.append(
                    (
                        artifact,
                        validate_decision(
                            artifact,
                            review_path=review_path,
                            repository_root=(
                                config_root.parent
                                if config_root.name == ".architecture"
                                else None
                            ),
                        ),
                    )
                )
        elif (
            "plan" in payload
            and payload["plan"].get("plan_kind") != "greenfield-implementation"
            and payload["plan"].get("source_review") == review["review"]["id"]
        ):
            plan_candidates.append(artifact)
    plans: list[tuple[Path, dict[str, Any]]] = []
    for artifact in plan_candidates:
        payload = load_yaml(artifact)
        decision_path = decision_paths.get(payload["plan"].get("source_decision", ""))
        if (
            payload.get("schema_version") == "1.3"
            and payload["plan"].get("plan_kind") == "greenfield-implementation"
        ):
            if decision_path is None:
                raise ArchitectureError(
                    f"{artifact} references a missing Greenfield source decision"
                )
            decision_payload = load_yaml(decision_path)
            if decision_payload["decision"].get("decision_kind") != "greenfield":
                raise ArchitectureError(
                    f"{artifact} schema 1.3 requires a Greenfield source decision"
                )
            brief_path = require_within_root(
                config_root.parent,
                config_root.parent / decision_payload["decision"]["source_context"],
                "plan source design brief",
            )
            plans.append(
                (
                    artifact,
                    validate_plan(
                        artifact,
                        decision_path=decision_path,
                        design_brief_path=brief_path,
                        repository_root=config_root.parent,
                    ),
                )
            )
        else:
            plans.append(
                (
                    artifact,
                    validate_plan(
                        artifact,
                        review_path=review_path,
                        decision_path=decision_path,
                        repository_root=config_root.parent,
                    ),
                )
            )
    return decisions, plans


def completed_required_reviews(
    root: Path,
    config_root: Path,
    profile: dict[str, Any],
    *,
    head: str,
    freshness_strategy: str,
    evaluation_date: date,
    max_review_age_days: int,
) -> dict[str, str]:
    requirements = validate_profile_review_requirements(
        profile,
        root / ".architecture" / "profile.yaml",
    )
    completed: dict[str, str] = {}
    for requirement in requirements:
        candidates: list[tuple[datetime, str, Path]] = []
        for path in sorted((config_root / "reviews").glob("*.yaml")):
            payload = load_yaml(path)
            if "review" not in payload:
                continue
            if payload["review"].get("verification_state") != "verified":
                continue
            if payload["review"].get("workflow") != requirement["id"]:
                continue
            try:
                review = validate_review(
                    path,
                    rule_pack_ids=profile["project"]["rule_packs"],
                    strict_trust=True,
                    repository_root=root,
                    require_current_selection=True,
                )
            except ArchitectureError:
                # Historical artifacts remain inspectable project records, but an
                # invalid or unverifiable record cannot satisfy the current Gate.
                # Continue searching so one stale record cannot poison a newer,
                # independently trusted Review for the same workflow.
                continue
            if review["review"]["kind"] != requirement["kind"]:
                continue
            declared_packs = {item["id"] for item in review["review"]["rule_packs"]}
            if declared_packs != set(requirement["rule_packs"]):
                continue
            commit = review["review"].get("commit")
            if freshness_strategy == "exact-commit" and commit != head:
                continue
            if freshness_strategy in {"ancestor", "diff-aware"} and (
                not commit or not git_is_ancestor(root, commit, head)
            ):
                continue
            performed_at = datetime.fromisoformat(
                review["review"]["performed_at"].replace("Z", "+00:00")
            )
            age = (evaluation_date - performed_at.date()).days
            if age < 0 or age > max_review_age_days:
                continue
            if freshness_strategy == "diff-aware" and commit and commit != head:
                changed_paths = git_changed_paths(root, commit, head)
                if any(
                    not _path_in_scope(path, review["review"]["scope_manifest"])
                    for path in changed_paths
                ):
                    continue
                evidence_paths = {
                    evidence_path(item)
                    for finding in review["findings"]
                    for item in finding["evidence"]
                    if evidence_path(item) is not None
                }
                if changed_paths & evidence_paths:
                    continue
            candidates.append((performed_at, review["review"]["id"], path))
        if not candidates:
            raise ArchitectureError(
                f"Required review {requirement['id']} has no trusted "
                "artifact satisfying its kind, rule packs, and freshness"
            )
        candidates.sort(key=lambda item: (item[0], item[1], item[2].name))
        completed[requirement["id"]] = str(candidates[-1][2])
    return completed


def gate_from_config(
    root: Path,
    config_root: Path,
    review_path: Path | None,
    today: date | None = None,
    commit_root: Path | None = None,
    mode: str = "all",
    base_commit: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    policy_path = config_root / "gate-policy.yaml"
    baseline_path = config_root / "baseline.yaml"
    policy = validate_file(policy_path, "gate-policy.schema.json")
    baseline = validate_baseline(baseline_path)
    if policy["schema_version"] not in TRUSTED_POLICY_VERSIONS:
        raise ArchitectureError(
            f"{policy_path} uses legacy schema; migrate policy to 1.1 or 1.2 "
            "before gating"
        )
    required_policy_fields = (
        "risk_acceptances_file",
        "roles",
        "role_separation",
        "stages",
        "release_requirements",
        "change_requirements",
    )
    missing_policy = [field for field in required_policy_fields if field not in policy]
    if missing_policy:
        raise ArchitectureError(
            f"{policy_path} trusted policy is missing: " + ", ".join(missing_policy)
        )
    if "accepted-risk" not in policy["block"]["statuses"]:
        raise ArchitectureError(
            f"{policy_path} must include accepted-risk in block.statuses"
        )
    for separation in policy["role_separation"]["separate"]:
        left = separation["left"]
        right = separation["right"]
        if left == right:
            raise ArchitectureError(
                f"{policy_path} role separation cannot compare {left} with itself"
            )
        overlap = sorted(set(policy["roles"][left]) & set(policy["roles"][right]))
        if overlap:
            raise ArchitectureError(
                f"{policy_path} requires separate {left} and {right} identities, "
                "but overlaps: " + ", ".join(overlap)
            )
    risk_acceptance_path = resolve_from_root(
        root,
        policy["risk_acceptances_file"],
    )
    risk_registry = validate_risk_acceptances(risk_acceptance_path)
    ensure_unique_entries(
        policy["waivers"],
        "finding_id",
        policy_path,
    )
    if review_path is None:
        review_path = find_latest_review(config_root / "reviews")
    elif not review_path.is_absolute():
        review_path = root / review_path
    review_path = require_within_root(root, review_path, "gate review")
    rule_pack_ids: list[str] | None = None
    profile: dict[str, Any] | None = None
    if commit_root is not None:
        profile = validate_file(
            root / ".architecture" / "profile.yaml",
            "project-profile.schema.json",
        )
        rule_pack_ids = profile["project"]["rule_packs"]
        validate_profile_review_requirements(
            profile,
            root / ".architecture" / "profile.yaml",
        )
        expected_identity = profile["project"]["id"]
    else:
        rule_pack_ids = ["portfolio-core"]
        registry = validate_file(
            root / ".architecture-portfolio" / "portfolio.yaml",
            "portfolio.schema.json",
        )
        expected_identity = registry["portfolio"]["id"]
    review = validate_review(
        review_path,
        rule_pack_ids=rule_pack_ids,
        strict_trust=True,
        repository_root=root,
        require_current_selection=commit_root is not None,
    )
    if not repository_identities_match(
        review["review"]["repository_identity"],
        expected_identity,
    ):
        raise ArchitectureError(
            f"{review_path} repository_identity does not match configured subject"
        )
    if profile is not None:
        requirement_by_id = {
            requirement["id"]: requirement
            for requirement in profile["project"]["review_requirements"]
        }
        workflow = review["review"]["workflow"]
        requirement = requirement_by_id.get(workflow)
        if requirement is None:
            raise ArchitectureError(
                f"{review_path} workflow {workflow} is not required by the project"
            )
        declared_pack_ids = {item["id"] for item in review["review"]["rule_packs"]}
        if review["review"]["kind"] != requirement["kind"] or declared_pack_ids != set(
            requirement["rule_packs"]
        ):
            raise ArchitectureError(
                f"{review_path} kind and Rule Packs do not match workflow {workflow}"
            )

    if review["review"]["verification_state"] != "verified":
        raise ArchitectureError(f"Gate requires a verified review: {review_path}")

    evaluation_date = today or datetime.now(UTC).date()
    block = policy["block"]
    policy_failures: list[str] = []
    warnings: list[str] = []
    completed_reviews: dict[str, str] = {}
    stages = policy["stages"]
    stage_order = ("contract", "finding", "change", "release")
    if mode == "all":
        selected_stages = {stage for stage in stage_order if stages.get(stage, False)}
    else:
        if not stages.get(mode, False):
            raise ArchitectureError(
                f"{policy_path} has disabled requested gate stage {mode}"
            )
        selected_stages = set(stage_order[: stage_order.index(mode) + 1])
        disabled_dependencies = sorted(
            stage for stage in selected_stages if not stages.get(stage, False)
        )
        if disabled_dependencies:
            raise ArchitectureError(
                f"{policy_path} disables prerequisite stages: "
                + ", ".join(disabled_dependencies)
            )

    if review["review"]["kind"] not in policy["review_kinds"]:
        policy_failures.append(
            f"Review kind {review['review']['kind']} is not allowed by policy"
        )
    unauthorized_auditors = sorted(
        {identity for finding in review["findings"] for identity in finding["found_by"]}
        - set(policy["roles"]["auditors"])
    )
    if unauthorized_auditors:
        policy_failures.append(
            "Review findings include unauthorized auditors: "
            + ", ".join(unauthorized_auditors)
        )

    if commit_root is not None and profile is not None:
        try:
            completed_reviews = completed_required_reviews(
                root,
                config_root,
                profile,
                head=current_git_commit(commit_root),
                freshness_strategy=block.get(
                    "freshness_strategy",
                    "exact-commit",
                ),
                evaluation_date=evaluation_date,
                max_review_age_days=block["max_review_age_days"],
            )
        except ArchitectureError as exc:
            policy_failures.append(str(exc))

    tiered_levels = block.get("verification_levels", {})
    configured_levels = [
        block.get("minimum_verification_level", "V0"),
        tiered_levels.get("accepted_risk", "V0"),
        tiered_levels.get("release", "V0"),
        *tiered_levels.get("by_severity", {}).values(),
    ]
    signature_required = "V5" in configured_levels or any(
        finding["verification"].get("level") == "V5"
        for finding in review["findings"]
        if finding["verification"]["status"] == "confirmed"
    )
    signature_declared = "signature" in review["review"]
    if signature_required or signature_declared:
        signature_policy = policy.get("artifact_signatures")
        if signature_policy is None:
            policy_failures.append(
                "Review signature is required or declared but policy has no "
                "artifact_signatures configuration"
            )
        else:
            signature_identity = review["review"].get("signature", {}).get("identity")
            if signature_identity not in policy["roles"]["verifiers"]:
                policy_failures.append(
                    f"Review signature identity {signature_identity!r} is not "
                    "an authorized verifier"
                )
            else:
                try:
                    verify_review_signature(
                        review_path,
                        review,
                        root,
                        signature_policy,
                    )
                except ArchitectureError as exc:
                    policy_failures.append(str(exc))

    evidence_resolution: list[dict[str, str]] = []
    changed_paths: set[str] = set()
    base_changed_paths: set[str] = set()
    change_impacts: dict[str, list[str]] = {
        "critical": [],
        "public_contract": [],
        "migration": [],
        "security": [],
    }
    reviewed_commit = review["review"].get("commit")
    strategy = block.get(
        "freshness_strategy",
        "exact-commit" if block["require_current_commit"] else "time-window",
    )
    if "change" in selected_stages:
        performed_at = datetime.fromisoformat(
            review["review"]["performed_at"].replace("Z", "+00:00")
        )
        review_age = (evaluation_date - performed_at.date()).days
        if review_age < 0:
            policy_failures.append(
                f"Review date {performed_at.date().isoformat()} is in the future"
            )
        elif review_age > block["max_review_age_days"]:
            policy_failures.append(
                f"Review is {review_age} days old; maximum is "
                f"{block['max_review_age_days']}"
            )

        head: str | None = None
        if commit_root is None:
            if strategy != "time-window":
                policy_failures.append(
                    f"Freshness strategy {strategy} requires a single "
                    "project repository"
                )
        elif strategy == "time-window":
            head = current_git_commit(commit_root)
        elif reviewed_commit:
            head = current_git_commit(commit_root)
            if strategy == "exact-commit" and head != reviewed_commit:
                policy_failures.append(
                    f"Review commit {reviewed_commit} does not match HEAD {head}"
                )
            elif strategy in {"ancestor", "diff-aware"}:
                if not git_is_ancestor(commit_root, reviewed_commit, head):
                    policy_failures.append(
                        f"Review commit {reviewed_commit} is not an ancestor "
                        f"of HEAD {head}"
                    )
                elif strategy == "diff-aware" and head != reviewed_commit:
                    changed_paths = git_changed_paths(
                        commit_root,
                        reviewed_commit,
                        head,
                    )
                    unscoped_changed_paths = sorted(
                        path
                        for path in changed_paths
                        if not _path_in_scope(
                            path,
                            review["review"]["scope_manifest"],
                        )
                    )
                    if unscoped_changed_paths:
                        policy_failures.append(
                            "Paths changed outside the selected review scope: "
                            + ", ".join(unscoped_changed_paths)
                        )
                    bound_paths = {
                        path
                        for finding in review["findings"]
                        for item in finding["evidence"]
                        if (path := evidence_path(item))
                    }
                    stale_paths = sorted(changed_paths & bound_paths)
                    if stale_paths:
                        policy_failures.append(
                            "Evidence changed since review: " + ", ".join(stale_paths)
                        )
                    elif changed_paths and not unscoped_changed_paths:
                        warnings.append(
                            f"{len(changed_paths)} non-evidence path(s) changed "
                            "since review"
                        )
        elif strategy != "time-window":
            policy_failures.append(
                f"Freshness strategy {strategy} requires review.commit"
            )
        if commit_root is not None:
            if block.get("require_clean_tree") and not git_is_clean(commit_root):
                policy_failures.append("Current repository working tree is not clean")
            if review["review"].get("dirty_tree"):
                policy_failures.append("Review was produced from a dirty working tree")
            if block.get("require_evidence_resolution"):
                try:
                    evidence_resolution = verify_review_evidence(review, commit_root)
                except ArchitectureError as exc:
                    policy_failures.append(str(exc))

        if base_commit is not None:
            if commit_root is None:
                policy_failures.append(
                    "--base-commit requires a single project repository"
                )
            else:
                head = head or current_git_commit(commit_root)
                try:
                    git_output(
                        commit_root,
                        "cat-file",
                        "-e",
                        f"{base_commit}^{{commit}}",
                    )
                    if not git_is_ancestor(commit_root, base_commit, head):
                        policy_failures.append(
                            f"Base commit {base_commit} is not an ancestor of "
                            f"HEAD {head}"
                        )
                    else:
                        base_changed_paths = git_changed_paths(
                            commit_root,
                            base_commit,
                            head,
                        )
                except ArchitectureError as exc:
                    policy_failures.append(str(exc))

        change_policy = policy["change_requirements"]
        classification_source = (
            base_changed_paths if base_commit is not None else changed_paths
        )
        change_impacts = {
            "critical": matching_paths(
                classification_source,
                change_policy["critical_paths"],
            ),
            "public_contract": matching_paths(
                classification_source,
                change_policy["public_contract_paths"],
            ),
            "migration": matching_paths(
                classification_source,
                change_policy["migration_paths"],
            ),
            "security": matching_paths(
                classification_source,
                change_policy["security_paths"],
            ),
        }
        freshness_sensitive = sorted(
            set(change_impacts["critical"]) | set(change_impacts["security"])
        )
        if (
            freshness_sensitive
            and change_policy["require_fresh_review_on_critical_change"]
            and commit_root is not None
        ):
            if not reviewed_commit:
                policy_failures.append(
                    "Critical or security-sensitive changes require review.commit"
                )
            elif not git_is_ancestor(
                commit_root,
                reviewed_commit,
                head or current_git_commit(commit_root),
            ):
                policy_failures.append(
                    f"Review commit {reviewed_commit} is not an ancestor of HEAD"
                )
            else:
                sensitive_review_delta = git_changed_paths(
                    commit_root,
                    reviewed_commit,
                    head or current_git_commit(commit_root),
                )
                stale_freshness_sensitive = sorted(
                    path
                    for path in freshness_sensitive
                    if path in sensitive_review_delta
                )
                if stale_freshness_sensitive:
                    policy_failures.append(
                        "Critical or security-sensitive paths changed after the "
                        "selected review: " + ", ".join(stale_freshness_sensitive)
                    )

    decisions: list[tuple[Path, dict[str, Any]]] = []
    plans: list[tuple[Path, dict[str, Any]]] = []
    if "change" in selected_stages or "release" in selected_stages:
        try:
            decisions, plans = related_governance_artifacts(
                config_root,
                review_path,
                review,
            )
        except ArchitectureError as exc:
            policy_failures.append(str(exc))
    accepted_decisions = [
        (path, decision)
        for path, decision in decisions
        if decision["decision"]["status"] == "accepted"
        and set(decision["decision"]["decision_makers"]).issubset(
            set(policy["roles"]["decision_makers"])
        )
    ]
    active_plans = [
        (path, plan)
        for path, plan in plans
        if plan["plan"]["status"] in {"accepted", "in-progress", "complete"}
    ]
    compatible_migration_decisions = [
        (path, decision)
        for path, decision in accepted_decisions
        if decision["selected_option"] == "keep-current"
        and decision.get("migration")
        and set(change_impacts["migration"]).issubset(
            set(decision["migration"].get("affected_paths", []))
        )
        and decision["migration"].get("slices")
        and decision["migration"].get("validation")
        and decision["migration"].get("rollback")
    ]
    if "change" in selected_stages:
        change_policy = policy["change_requirements"]
        if (
            change_impacts["public_contract"]
            and change_policy["require_decision_on_public_contract_change"]
            and not accepted_decisions
        ):
            policy_failures.append(
                "Public contract changes require an accepted, authorized "
                "decision for the selected review: "
                + ", ".join(change_impacts["public_contract"])
            )
        if (
            change_impacts["migration"]
            and change_policy["require_plan_on_migration_change"]
            and not active_plans
            and not compatible_migration_decisions
        ):
            policy_failures.append(
                "Migration changes require an accepted compatible-migration "
                "decision or an active remediation plan for the selected review: "
                + ", ".join(change_impacts["migration"])
            )

    active_baseline: dict[str, dict[str, Any]] = {}
    expired_baseline: list[str] = []
    pending_baseline: list[str] = []
    for entry in baseline["findings"]:
        recorded_on = parse_date(
            entry["recorded_on"],
            f"{baseline_path}:{entry['id']}.recorded_on",
        )
        if recorded_on > evaluation_date:
            pending_baseline.append(entry["id"])
        elif active_until(
            entry.get("expires_on"),
            evaluation_date,
        ):
            active_baseline[entry["id"]] = entry
        else:
            expired_baseline.append(entry["id"])

    active_waivers: dict[str, dict[str, Any]] = {}
    expired_waivers: list[str] = []
    for entry in policy["waivers"]:
        if active_until(entry["expires_on"], evaluation_date):
            active_waivers[entry["finding_id"]] = entry
        else:
            expired_waivers.append(entry["finding_id"])

    active_acceptances: dict[str, dict[str, Any]] = {}
    expired_acceptances: list[str] = []
    pending_acceptances: list[str] = []
    for entry in risk_registry["acceptances"]:
        accepted_on = datetime.fromisoformat(
            entry["accepted_at"].replace("Z", "+00:00")
        ).date()
        if accepted_on > evaluation_date:
            pending_acceptances.append(entry["finding_id"])
        elif active_until(entry["expires_on"], evaluation_date):
            active_acceptances[entry["finding_id"]] = entry
        else:
            expired_acceptances.append(entry["finding_id"])

    blocking: list[dict[str, Any]] = []
    baselined: list[str] = []
    waived: list[str] = []
    unverified: list[str] = []
    accepted_risks: list[str] = []

    for finding in review["findings"] if "finding" in selected_stages else []:
        if finding["kind"] != "risk":
            continue
        if finding["severity"] not in block["severities"]:
            continue
        if finding["status"] not in block["statuses"]:
            continue
        if finding["confidence"] < block["minimum_confidence"]:
            continue

        finding_id = finding["id"]
        if finding["verification"]["status"] != "confirmed":
            unverified.append(finding_id)
            if block["unverified_behavior"] == "fail":
                blocking.append(
                    {
                        "id": finding_id,
                        "severity": finding["severity"],
                        "title": finding["title"],
                        "reason": "unverified finding matches blocking thresholds",
                    }
                )
            elif block["unverified_behavior"] == "warn":
                warnings.append(
                    f"Unverified finding {finding_id} matches blocking thresholds"
                )
            continue

        verification_level = finding["verification"].get("level", "V0")
        required_levels = [
            block.get("minimum_verification_level", "V0"),
            block.get("verification_levels", {})
            .get("by_severity", {})
            .get(finding["severity"], "V0"),
        ]
        if finding["status"] == "accepted-risk":
            required_levels.append(
                block.get("verification_levels", {}).get(
                    "accepted_risk",
                    "V0",
                )
            )
        if "release" in selected_stages:
            required_levels.append(
                block.get("verification_levels", {}).get("release", "V0")
            )
        required_level = highest_verification_level(*required_levels)
        if (
            VERIFICATION_LEVEL_ORDER[verification_level]
            < VERIFICATION_LEVEL_ORDER[required_level]
        ):
            blocking.append(
                {
                    "id": finding_id,
                    "severity": finding["severity"],
                    "title": finding["title"],
                    "reason": (
                        f"verification level {verification_level} is below "
                        f"required {required_level}"
                    ),
                }
            )
            continue
        verifier_identity = finding["verification"]["verifier"]["identity"]
        if verifier_identity not in policy["roles"]["verifiers"]:
            blocking.append(
                {
                    "id": finding_id,
                    "severity": finding["severity"],
                    "title": finding["title"],
                    "reason": f"verifier {verifier_identity!r} is not authorized",
                }
            )
            continue

        fingerprint = finding["fingerprint"]
        if finding["status"] == "accepted-risk":
            acceptance = active_acceptances.get(finding_id)
            if (
                acceptance
                and acceptance["finding_fingerprint"] == fingerprint
                and acceptance["accepted_by"] in policy["roles"]["risk_acceptors"]
                and acceptance["approved_by"] in policy["roles"]["policy_owners"]
            ):
                accepted_risks.append(finding_id)
                continue
            blocking.append(
                {
                    "id": finding_id,
                    "severity": finding["severity"],
                    "title": finding["title"],
                    "reason": "accepted-risk has no matching authorized acceptance",
                }
            )
            continue

        if (
            finding_id in active_baseline
            and active_baseline[finding_id].get("finding_fingerprint") == fingerprint
        ):
            baselined.append(finding_id)
        elif (
            finding_id in active_waivers
            and active_waivers[finding_id].get("finding_fingerprint") == fingerprint
            and active_waivers[finding_id].get("approved_by")
            in policy["roles"]["policy_owners"]
        ):
            waived.append(finding_id)
        else:
            blocking.append(
                {
                    "id": finding_id,
                    "severity": finding["severity"],
                    "title": finding["title"],
                    "reason": "confirmed finding matches blocking thresholds",
                }
            )

    if "release" in selected_stages:
        requirements = policy["release_requirements"]
        required_kinds = set(requirements.get("required_review_kinds", []))
        if profile is not None:
            completed_kinds = {
                requirement["kind"]
                for requirement in profile["project"]["review_requirements"]
                if requirement["id"] in completed_reviews
            }
        else:
            completed_kinds = {review["review"]["kind"]}
        missing_kinds = sorted(required_kinds - completed_kinds)
        if missing_kinds:
            policy_failures.append(
                "Release gate is missing trusted review kinds: "
                + ", ".join(missing_kinds)
            )
        if (
            requirements.get("require_no_dirty_tree")
            and commit_root is not None
            and not git_is_clean(commit_root)
        ):
            policy_failures.append("Release gate requires a clean working tree")
        required_types = set(requirements.get("required_evidence_types", []))
        observed_types = {
            item["type"]
            for finding in review["findings"]
            if finding["verification"]["status"] == "confirmed"
            for item in finding["evidence"]
        }
        if review["evidence_sources"]:
            observed_types.add("source")
        if review.get("tool_evidence"):
            observed_types.add("tool")
            for reference in review["tool_evidence"]:
                run_path = require_within_root(
                    root,
                    root / reference["run_path"],
                    "release tool evidence",
                )
                evidence_run = validate_evidence_run(
                    run_path,
                    root,
                    require_passed=True,
                )
                observed_types.add(evidence_run["run"]["evidence_type"])
        missing_types = sorted(required_types - observed_types)
        if missing_types:
            policy_failures.append(
                "Release gate is missing evidence types: " + ", ".join(missing_types)
            )
        observed_providers = {
            item["provider_id"] for item in review.get("tool_evidence", [])
        }
        missing_providers = sorted(
            set(requirements.get("required_provider_ids", [])) - observed_providers
        )
        if missing_providers:
            policy_failures.append(
                "Release gate is missing passed provider runs: "
                + ", ".join(missing_providers)
            )
        if requirements.get("require_accepted_decisions"):
            if not accepted_decisions:
                policy_failures.append(
                    "Release gate requires an accepted decision for this review"
                )
            if any(
                not set(decision["decision"]["decision_makers"]).issubset(
                    set(policy["roles"]["decision_makers"])
                )
                for _, decision in decisions
                if decision["decision"]["status"] == "accepted"
            ):
                policy_failures.append(
                    "Accepted decision includes an unauthorized decision maker"
                )
        if requirements.get("require_completed_plans"):
            for _, decision in accepted_decisions:
                if (
                    decision["selected_option"] == "keep-current"
                    and decision["decision"].get("decision_kind", "remediation")
                    != "greenfield"
                ):
                    continue
                matching = [
                    plan
                    for _, plan in plans
                    if plan["plan"].get("source_decision") == decision["decision"]["id"]
                    and plan["plan"]["status"] == "complete"
                ]
                if (
                    decision["decision"].get("decision_kind", "remediation")
                    == "greenfield"
                ):
                    matching = [
                        plan for plan in matching if plan.get("schema_version") == "1.3"
                    ]
                    if not matching:
                        policy_failures.append(
                            "Accepted Greenfield decision "
                            f"{decision['decision']['id']} "
                            "requires a complete greenfield implementation plan"
                        )
                    continue
                covered = {
                    finding_id
                    for plan in matching
                    for item in plan["items"]
                    for finding_id in item["finding_ids"]
                }
                missing_findings = sorted(
                    set(decision["problem"]["finding_ids"]) - covered
                )
                if not matching or missing_findings:
                    detail = (
                        " (missing findings: " + ", ".join(missing_findings) + ")"
                        if missing_findings
                        else ""
                    )
                    policy_failures.append(
                        f"Accepted decision {decision['decision']['id']} "
                        f"requires a complete remediation plan{detail}"
                    )

    blocking.sort(key=lambda item: (-SEVERITY_ORDER[item["severity"]], item["id"]))
    passed = not blocking and not policy_failures
    return {
        "status": "pass" if passed else "fail",
        "review": str(review_path),
        "review_id": review["review"]["id"],
        "evaluated_on": evaluation_date.isoformat(),
        "blocking": blocking,
        "policy_failures": policy_failures,
        "baselined": sorted(baselined),
        "waived": sorted(waived),
        "accepted_risks": sorted(accepted_risks),
        "unverified": sorted(unverified),
        "expired_baseline": sorted(expired_baseline),
        "pending_baseline": sorted(pending_baseline),
        "expired_waivers": sorted(expired_waivers),
        "expired_acceptances": sorted(expired_acceptances),
        "pending_acceptances": sorted(pending_acceptances),
        "evidence_resolution": evidence_resolution,
        "changed_paths": sorted(changed_paths),
        "base_changed_paths": sorted(base_changed_paths),
        "change_impacts": change_impacts,
        "required_reviews": completed_reviews,
        "stages": sorted(selected_stages),
        "warnings": warnings,
    }


def gate_project(
    project_root: Path,
    review_path: Path | None,
    today: date | None = None,
    mode: str = "all",
    base_commit: str | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    return gate_from_config(
        project_root,
        project_root / ".architecture",
        review_path,
        today,
        commit_root=project_root,
        mode=mode,
        base_commit=base_commit,
    )


def gate_greenfield(
    project_root: Path,
    decision_path: Path,
    *,
    today: date | None = None,
    mode: str = "all",
    base_commit: str | None = None,
) -> dict[str, Any]:
    """Gate one Brief -> Greenfield Decision -> implementation Plan chain."""
    root = project_root.resolve()
    config_root = root / ".architecture"
    policy_path = config_root / "gate-policy.yaml"
    policy = validate_file(policy_path, "gate-policy.schema.json")
    validate_baseline(config_root / "baseline.yaml")
    if policy["schema_version"] not in TRUSTED_POLICY_VERSIONS:
        raise ArchitectureError(f"{policy_path} uses a legacy Gate policy")
    required_policy_fields = (
        "risk_acceptances_file",
        "roles",
        "role_separation",
        "stages",
        "release_requirements",
        "change_requirements",
    )
    missing_policy = [field for field in required_policy_fields if field not in policy]
    if missing_policy:
        raise ArchitectureError(
            f"{policy_path} trusted policy is missing: " + ", ".join(missing_policy)
        )
    if "accepted-risk" not in policy["block"]["statuses"]:
        raise ArchitectureError(
            f"{policy_path} must include accepted-risk in block.statuses"
        )
    validate_risk_acceptances(resolve_from_root(root, policy["risk_acceptances_file"]))
    ensure_unique_entries(policy["waivers"], "finding_id", policy_path)
    profile_path = config_root / "profile.yaml"
    profile = validate_file(profile_path, "project-profile.schema.json")
    validate_profile_review_requirements(profile, profile_path)
    for separation in policy["role_separation"]["separate"]:
        left = separation["left"]
        right = separation["right"]
        if left == right:
            raise ArchitectureError(
                f"{policy_path} role separation cannot compare {left} with itself"
            )
        overlap = sorted(set(policy["roles"][left]) & set(policy["roles"][right]))
        if overlap:
            raise ArchitectureError(
                f"{policy_path} requires separate {left} and {right} identities, "
                "but overlaps: " + ", ".join(overlap)
            )
    stage_order = ("contract", "finding", "change", "release")
    stages = policy["stages"]
    if mode == "all":
        selected_stages = {stage for stage in stage_order if stages.get(stage, False)}
    else:
        if not stages.get(mode, False):
            raise ArchitectureError(
                f"{policy_path} has disabled requested gate stage {mode}"
            )
        selected_stages = set(stage_order[: stage_order.index(mode) + 1])
        disabled = sorted(
            stage for stage in selected_stages if not stages.get(stage, False)
        )
        if disabled:
            raise ArchitectureError(
                f"{policy_path} disables prerequisite stages: " + ", ".join(disabled)
            )

    candidate = decision_path if decision_path.is_absolute() else root / decision_path
    selected_decision_path = require_within_root(root, candidate, "gate decision")
    payload = validate_file(
        selected_decision_path,
        "architecture-decision.schema.json",
    )
    if payload["decision"].get("decision_kind") != "greenfield":
        raise ArchitectureError("--decision Gate requires a Greenfield Decision")
    brief_path = require_within_root(
        root,
        root / payload["decision"]["source_context"],
        "gate Design Brief",
    )
    require_accepted = bool({"change", "release"} & selected_stages)
    decision = validate_decision(
        selected_decision_path,
        design_brief_path=brief_path,
        require_accepted=require_accepted,
        repository_root=root,
        require_current_selection=True,
    )
    validate_design_brief(brief_path, repository_root=root)

    policy_failures: list[str] = []
    warnings: list[str] = []
    if decision["decision"]["status"] == "accepted":
        unauthorized = sorted(
            set(decision["decision"]["decision_makers"])
            - set(policy["roles"]["decision_makers"])
        )
        if unauthorized:
            policy_failures.append(
                "Accepted Greenfield decision includes unauthorized decision "
                "makers: " + ", ".join(unauthorized)
            )

    plans: list[tuple[Path, dict[str, Any]]] = []
    for artifact in sorted((config_root / "reviews").glob("*.yaml")):
        plan_payload = load_yaml(artifact)
        if "plan" not in plan_payload:
            continue
        if (
            plan_payload["plan"].get("plan_kind") != "greenfield-implementation"
            or plan_payload["plan"].get("source_decision") != decision["decision"]["id"]
        ):
            continue
        plans.append(
            (
                artifact,
                validate_plan(
                    artifact,
                    decision_path=selected_decision_path,
                    design_brief_path=brief_path,
                    repository_root=root,
                    require_current_selection=True,
                ),
            )
        )
    active_plans = [
        plan
        for _, plan in plans
        if plan["plan"]["status"] in {"accepted", "in-progress", "complete"}
    ]
    if "change" in selected_stages and not active_plans:
        policy_failures.append(
            "Accepted Greenfield decision requires an accepted, in-progress, or "
            "complete implementation plan"
        )
    evaluation_date = today or datetime.now(UTC).date()
    changed_paths: set[str] = set()
    base_changed_paths: set[str] = set()
    change_impacts: dict[str, list[str]] = {
        "critical": [],
        "public_contract": [],
        "migration": [],
        "security": [],
    }
    completed_reviews: dict[str, str] = {}

    if "change" in selected_stages:
        block = policy["block"]
        if block.get("require_clean_tree"):
            try:
                if not git_is_clean(root):
                    policy_failures.append(
                        "Current repository working tree is not clean"
                    )
            except ArchitectureError as exc:
                policy_failures.append(str(exc))
        selection_path = require_within_root(
            root,
            root / decision["decision"]["knowledge_selection_path"],
            "Greenfield Gate knowledge selection",
        )
        selection = validate_file(selection_path, "knowledge-selection.schema.json")
        source_commit = base_commit or selection["inputs"].get(
            "project_commit",
            selection["inputs"].get("source_commit"),
        )
        if source_commit and source_commit != "unknown":
            try:
                head = current_git_commit(root)
                git_output(root, "cat-file", "-e", f"{source_commit}^{{commit}}")
                if not git_is_ancestor(root, source_commit, head):
                    policy_failures.append(
                        f"Greenfield source commit {source_commit} is not an "
                        f"ancestor of HEAD {head}"
                    )
                else:
                    changed_paths = git_changed_paths(root, source_commit, head)
                    if base_commit is not None:
                        base_changed_paths = set(changed_paths)
            except ArchitectureError as exc:
                policy_failures.append(str(exc))
        elif base_commit is not None:
            policy_failures.append("--base-commit does not identify a Git commit")
        else:
            warnings.append(
                "Greenfield change classification has no Git source commit; "
                "pass --base-commit for path-sensitive policy"
            )

        change_policy = policy["change_requirements"]
        change_impacts = {
            "critical": matching_paths(
                changed_paths,
                change_policy["critical_paths"],
            ),
            "public_contract": matching_paths(
                changed_paths,
                change_policy["public_contract_paths"],
            ),
            "migration": matching_paths(
                changed_paths,
                change_policy["migration_paths"],
            ),
            "security": matching_paths(
                changed_paths,
                change_policy["security_paths"],
            ),
        }
        freshness_sensitive = sorted(
            set(change_impacts["critical"]) | set(change_impacts["security"])
        )
        if (
            freshness_sensitive
            and change_policy["require_fresh_review_on_critical_change"]
        ):
            try:
                head = current_git_commit(root)
                completed_reviews = completed_required_reviews(
                    root,
                    config_root,
                    profile,
                    head=head,
                    freshness_strategy=block.get(
                        "freshness_strategy",
                        "exact-commit",
                    ),
                    evaluation_date=evaluation_date,
                    max_review_age_days=block["max_review_age_days"],
                )
                stale_sensitive: set[str] = set()
                for review_artifact in completed_reviews.values():
                    current_review = validate_review(Path(review_artifact))
                    review_commit = current_review["review"].get("commit")
                    if review_commit:
                        stale_sensitive.update(
                            set(freshness_sensitive)
                            & git_changed_paths(root, review_commit, head)
                        )
                if stale_sensitive:
                    policy_failures.append(
                        "Critical or security-sensitive paths changed after "
                        "required reviews: " + ", ".join(sorted(stale_sensitive))
                    )
            except ArchitectureError as exc:
                policy_failures.append(str(exc))
        if (
            change_impacts["public_contract"]
            and change_policy["require_decision_on_public_contract_change"]
            and decision["decision"]["status"] != "accepted"
        ):
            policy_failures.append(
                "Public contract changes require this Greenfield Decision to be "
                "accepted"
            )
        if (
            change_impacts["migration"]
            and change_policy["require_plan_on_migration_change"]
            and not active_plans
        ):
            policy_failures.append(
                "Migration changes require an active Greenfield implementation plan"
            )

    complete_plans = [plan for _, plan in plans if plan["plan"]["status"] == "complete"]
    if "release" in selected_stages:
        requirements = policy["release_requirements"]
        if requirements.get("require_completed_plans") and not complete_plans:
            policy_failures.append(
                "Accepted Greenfield decision requires a complete implementation "
                "plan with hashed completion evidence"
            )
        if requirements.get("require_no_dirty_tree"):
            try:
                if not git_is_clean(root):
                    policy_failures.append("Release gate requires a clean working tree")
            except ArchitectureError as exc:
                policy_failures.append(str(exc))

        required_kinds = set(requirements.get("required_review_kinds", []))
        if required_kinds and not completed_reviews:
            try:
                completed_reviews = completed_required_reviews(
                    root,
                    config_root,
                    profile,
                    head=current_git_commit(root),
                    freshness_strategy=policy["block"].get(
                        "freshness_strategy",
                        "exact-commit",
                    ),
                    evaluation_date=evaluation_date,
                    max_review_age_days=policy["block"]["max_review_age_days"],
                )
            except ArchitectureError as exc:
                policy_failures.append(str(exc))
        completed_kinds = {
            requirement["kind"]
            for requirement in profile["project"]["review_requirements"]
            if requirement["id"] in completed_reviews
        }
        missing_kinds = sorted(required_kinds - completed_kinds)
        if missing_kinds:
            policy_failures.append(
                "Release gate is missing trusted review kinds: "
                + ", ".join(missing_kinds)
            )

        completion_evidence = [
            evidence
            for plan in complete_plans
            for item in plan["items"]
            for evidence in item.get("completion_evidence", [])
        ]
        observed_types = {"source"} | {
            evidence["type"] for evidence in completion_evidence
        }
        missing_types = sorted(
            set(requirements.get("required_evidence_types", [])) - observed_types
        )
        if missing_types:
            policy_failures.append(
                "Release gate is missing evidence types: " + ", ".join(missing_types)
            )
        observed_providers = {
            evidence["provider_id"]
            for evidence in completion_evidence
            if evidence.get("provider_id")
        }
        missing_providers = sorted(
            set(requirements.get("required_provider_ids", [])) - observed_providers
        )
        if missing_providers:
            policy_failures.append(
                "Release gate is missing passed provider runs: "
                + ", ".join(missing_providers)
            )
        if (
            requirements.get("require_accepted_decisions")
            and decision["decision"]["status"] != "accepted"
        ):
            policy_failures.append("Release gate requires an accepted decision")

    passed = not policy_failures
    return {
        "status": "pass" if passed else "fail",
        "review": str(selected_decision_path),
        "review_id": decision["decision"]["id"],
        "subject_kind": "greenfield-decision",
        "decision": str(selected_decision_path),
        "design_brief": str(brief_path),
        "plans": [str(path) for path, _ in plans],
        "evaluated_on": evaluation_date.isoformat(),
        "blocking": [],
        "policy_failures": policy_failures,
        "baselined": [],
        "waived": [],
        "accepted_risks": [],
        "unverified": [],
        "expired_baseline": [],
        "pending_baseline": [],
        "expired_waivers": [],
        "expired_acceptances": [],
        "pending_acceptances": [],
        "evidence_resolution": [],
        "changed_paths": sorted(changed_paths),
        "base_changed_paths": sorted(base_changed_paths),
        "change_impacts": change_impacts,
        "required_reviews": completed_reviews,
        "stages": sorted(selected_stages),
        "warnings": warnings,
    }


def gate_portfolio(
    portfolio_root: Path,
    review_path: Path | None,
    today: date | None = None,
    mode: str = "all",
    base_commit: str | None = None,
) -> dict[str, Any]:
    portfolio_root = portfolio_root.resolve()
    return gate_from_config(
        portfolio_root,
        portfolio_root / ".architecture-portfolio",
        review_path,
        today,
        mode=mode,
        base_commit=base_commit,
    )


def print_gate_result(result: dict[str, Any]) -> None:
    print(f"Architecture gate: {result['status'].upper()}")
    label = (
        "Greenfield decision"
        if result.get("subject_kind") == "greenfield-decision"
        else "Review"
    )
    print(f"{label}: {result['review_id']} ({result['review']})")
    for failure in result["policy_failures"]:
        print(f"POLICY: {failure}")
    for finding in result["blocking"]:
        print(
            f"BLOCK: {finding['id']} [{finding['severity']}] "
            f"{finding['title']} — {finding['reason']}"
        )
    if result["baselined"]:
        print("BASELINED: " + ", ".join(result["baselined"]))
    if result["waived"]:
        print("WAIVED: " + ", ".join(result["waived"]))
    if result["accepted_risks"]:
        print("ACCEPTED RISK: " + ", ".join(result["accepted_risks"]))
    if result["expired_baseline"]:
        print("EXPIRED BASELINE: " + ", ".join(result["expired_baseline"]))
    if result["pending_baseline"]:
        print("PENDING BASELINE: " + ", ".join(result["pending_baseline"]))
    if result["expired_waivers"]:
        print("EXPIRED WAIVER: " + ", ".join(result["expired_waivers"]))
    if result["expired_acceptances"]:
        print("EXPIRED ACCEPTANCE: " + ", ".join(result["expired_acceptances"]))
    if result["pending_acceptances"]:
        print("PENDING ACCEPTANCE: " + ", ".join(result["pending_acceptances"]))
    for warning in result["warnings"]:
        print(f"WARN: {warning}")


def review_diff(
    before_path: Path,
    after_path: Path,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    validation: dict[str, Any] = {}
    if project_root is not None:
        root = project_root.resolve()
        if not before_path.is_absolute():
            before_path = root / before_path
        if not after_path.is_absolute():
            after_path = root / after_path
        before_path = require_within_root(root, before_path, "review diff before")
        after_path = require_within_root(root, after_path, "review diff after")
        profile = validate_file(
            root / ".architecture" / "profile.yaml",
            "project-profile.schema.json",
        )
        validation = {
            "rule_pack_ids": profile["project"]["rule_packs"],
            "strict_trust": True,
            "repository_root": root,
        }
    before = validate_review(before_path.resolve(), **validation)
    after = validate_review(after_path.resolve(), **validation)
    before_subject = before["review"]["subject"]["id"]
    after_subject = after["review"]["subject"]["id"]
    if before_subject != after_subject:
        raise ArchitectureError(
            "Review diff requires the same subject, got "
            f"{before_subject} and {after_subject}"
        )
    if before["review"]["kind"] != after["review"]["kind"]:
        raise ArchitectureError("Review diff requires the same review kind")

    before_findings = {item["id"]: item for item in before["findings"]}
    after_findings = {item["id"]: item for item in after["findings"]}
    before_ids = set(before_findings)
    after_ids = set(after_findings)
    comparable_fields = (
        "kind",
        "rule_id",
        "title",
        "invariant",
        "severity",
        "confidence",
        "verification",
        "status",
        "evidence",
        "impact",
        "counter_evidence",
        "last_seen",
        "tags",
    )
    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for finding_id in sorted(before_ids & after_ids):
        previous = before_findings[finding_id]
        current = after_findings[finding_id]
        fields = [
            field
            for field in comparable_fields
            if previous.get(field) != current.get(field)
        ]
        if fields:
            changed.append(
                {
                    "id": finding_id,
                    "changed_fields": fields,
                    "before": {
                        "severity": previous["severity"],
                        "status": previous["status"],
                        "verification": previous["verification"]["status"],
                        "fingerprint": previous.get("fingerprint"),
                    },
                    "after": {
                        "severity": current["severity"],
                        "status": current["status"],
                        "verification": current["verification"]["status"],
                        "fingerprint": current.get("fingerprint"),
                    },
                }
            )
        else:
            unchanged.append(finding_id)

    before_coverage = {
        item["rule_id"]: {
            "status": item["status"],
            "reason": item.get("reason"),
            "finding_ids": item["finding_ids"],
        }
        for item in before["coverage"]
    }
    after_coverage = {
        item["rule_id"]: {
            "status": item["status"],
            "reason": item.get("reason"),
            "finding_ids": item["finding_ids"],
        }
        for item in after["coverage"]
    }
    coverage_changes = [
        {
            "rule_id": rule_id,
            "before": before_coverage.get(rule_id),
            "after": after_coverage.get(rule_id),
        }
        for rule_id in sorted(set(before_coverage) | set(after_coverage))
        if before_coverage.get(rule_id) != after_coverage.get(rule_id)
    ]
    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)
    return {
        "schema_version": "1.0",
        "subject": before_subject,
        "kind": before["review"]["kind"],
        "before": {
            "review_id": before["review"]["id"],
            "performed_at": before["review"]["performed_at"],
            "commit": before["review"].get("commit"),
        },
        "after": {
            "review_id": after["review"]["id"],
            "performed_at": after["review"]["performed_at"],
            "commit": after["review"].get("commit"),
        },
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(unchanged),
            "coverage_changed": len(coverage_changes),
        },
        "added": [
            {
                "id": finding_id,
                "severity": after_findings[finding_id]["severity"],
                "status": after_findings[finding_id]["status"],
                "verification": after_findings[finding_id]["verification"]["status"],
            }
            for finding_id in added
        ],
        "removed": [
            {
                "id": finding_id,
                "severity": before_findings[finding_id]["severity"],
                "status": before_findings[finding_id]["status"],
                "verification": before_findings[finding_id]["verification"]["status"],
            }
            for finding_id in removed
        ],
        "changed": changed,
        "unchanged": unchanged,
        "coverage_changes": coverage_changes,
    }


def review_bindings(project_root: Path, candidate_path: Path) -> dict[str, Any]:
    root = project_root.resolve()
    if not candidate_path.is_absolute():
        candidate_path = root / candidate_path
    candidate_path = require_within_root(root, candidate_path, "candidate review")
    profile_path = root / ".architecture" / "profile.yaml"
    profile = validate_file(profile_path, "project-profile.schema.json")
    candidate = validate_review(
        candidate_path,
        rule_pack_ids=profile["project"]["rule_packs"],
        strict_trust=True,
        repository_root=root,
        require_current_selection=True,
    )
    if candidate["review"]["verification_state"] != "candidates":
        raise ArchitectureError("Bindings require a candidate review")
    declared = [item["id"] for item in candidate["review"].get("rule_packs", [])]
    pack_ids = declared or [REVIEW_KIND_CORE_PACK[candidate["review"]["kind"]]]
    unknown = sorted(set(pack_ids) - set(profile["project"]["rule_packs"]))
    if unknown:
        raise ArchitectureError(
            "Candidate review declares packs absent from project profile: "
            + ", ".join(unknown)
        )
    packs = load_rule_packs(
        pack_ids,
        local_rule_pack_roots(root),
    )
    return {
        "repository_identity": profile["project"]["id"],
        "commit": current_git_commit(root),
        "dirty_tree": not git_is_clean(root),
        "profile": profile_path.relative_to(root).as_posix(),
        "profile_sha256": file_sha256(profile_path),
        "source_candidate": {
            "path": candidate_path.relative_to(root).as_posix(),
            "review_id": candidate["review"]["id"],
            "sha256": file_sha256(candidate_path),
        },
        "rule_packs": [
            {
                "id": pack_id,
                "version": record["payload"]["version"],
                "sha256": file_sha256(record["path"]),
            }
            for pack_id, record in sorted(packs.items())
        ],
        "finding_fingerprints": {
            finding["id"]: finding_fingerprint(
                candidate["review"]["subject"]["id"],
                finding,
            )
            for finding in candidate["findings"]
        },
    }


REVIEW_EXECUTION_IMPACTS = (
    "critical",
    "security",
    "public_contract",
    "migration",
)


def _declared_repository_paths(
    values: list[str],
    field: str,
    *,
    allow_empty: bool = False,
) -> list[str]:
    if not values and not allow_empty:
        raise ArchitectureError(f"Review execution plan requires explicit {field}")
    normalized: set[str] = set()
    for value in values:
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
            raise ArchitectureError(
                f"Review execution plan {field} escapes repository: {value}"
            )
        normalized.add(candidate.as_posix() or ".")
    return sorted(normalized)


def _path_in_scope(path: str, scope: list[str]) -> bool:
    return any(
        scoped == "." or path == scoped or path.startswith(scoped.rstrip("/") + "/")
        for scoped in scope
    )


def plan_review_execution(
    project_root: Path,
    review_path: Path,
    *,
    base_commit: str,
    scope: list[str],
) -> dict[str, Any]:
    """Build a deterministic, non-authoritative review execution payload.

    This payload binds execution inputs and records what must be reassessed. It
    deliberately does not claim architecture quality or promote a prior
    assessment to ``passed``; prior coverage is context only.
    """
    root = project_root.resolve()
    if not root.is_dir():
        raise ArchitectureError(f"Project directory does not exist: {root}")
    review_path = review_path if review_path.is_absolute() else root / review_path
    review_path = require_within_root(
        root,
        review_path,
        "review execution prior review",
    )
    profile_path = root / ".architecture" / "profile.yaml"
    profile = validate_file(profile_path, "project-profile.schema.json")
    validate_profile_review_requirements(profile, profile_path)
    review = validate_review(
        review_path,
        rule_pack_ids=profile["project"]["rule_packs"],
        strict_trust=True,
        repository_root=root,
        require_current_selection=True,
    )
    if review["review"]["verification_state"] != "verified":
        raise ArchitectureError(
            "Review execution planning requires a verified prior review"
        )
    reviewed_commit = review["review"].get("commit")
    if not reviewed_commit or reviewed_commit != base_commit:
        raise ArchitectureError(
            "Review execution plan base_commit must exactly match the prior "
            "verified review commit"
        )
    git_output(root, "cat-file", "-e", f"{base_commit}^{{commit}}")
    head = current_git_commit(root)
    if not git_is_ancestor(root, base_commit, head):
        raise ArchitectureError(
            f"Review execution plan base commit {base_commit} is not an ancestor "
            f"of HEAD {head}"
        )
    if not git_is_clean(root):
        raise ArchitectureError(
            "Review execution planning requires a clean working tree so paths "
            "bind to commits"
        )

    declared_scope = _declared_repository_paths(scope, "scope")
    declared_changed = sorted(git_changed_paths(root, base_commit, head))
    gate_policy = validate_file(
        root / ".architecture" / "gate-policy.yaml",
        "gate-policy.schema.json",
    )
    change_requirements = gate_policy["change_requirements"]
    impact_patterns = {
        "critical": change_requirements["critical_paths"],
        "security": change_requirements["security_paths"],
        "public_contract": change_requirements["public_contract_paths"],
        "migration": change_requirements["migration_paths"],
    }
    changed_set = set(declared_changed)
    impact_paths = {
        key: matching_paths(changed_set, impact_patterns[key])
        for key in REVIEW_EXECUTION_IMPACTS
    }
    impact = {key: bool(impact_paths[key]) for key in REVIEW_EXECUTION_IMPACTS}

    packs = load_rule_packs(
        profile["project"]["rule_packs"],
        local_rule_pack_roots(root),
    )
    rule_ids = sorted(expected_rules(packs))
    coverage = {item["rule_id"]: item for item in review["coverage"]}
    evidence_by_rule: dict[str, set[str]] = {}
    for finding in review["findings"]:
        evidence_by_rule.setdefault(finding["rule_id"], set()).update(
            path
            for item in finding["evidence"]
            if (path := evidence_path(item)) is not None
        )
    rule_execution = []
    for rule_id in rule_ids:
        prior = coverage.get(rule_id)
        overlap = sorted(evidence_by_rule.get(rule_id, set()) & set(declared_changed))
        rule_execution.append(
            {
                "id": rule_id,
                "prior_status": prior["status"] if prior else "unassessed",
                "changed_evidence_paths": overlap,
                "reuse": (
                    "context-only"
                    if prior and prior["status"] == "assessed"
                    else "none"
                ),
                "action": "reassess",
            }
        )

    critical_flows_path = require_within_root(
        root,
        root / profile["project"]["critical_flows_file"],
        "profile.project.critical_flows_file",
    )
    headings = re.findall(
        r"^## (?!Flow template\s*$)(.+?)\s*$",
        critical_flows_path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    prior_flows = {
        item["id"]: item for item in review.get("critical_flow_coverage", [])
    }
    critical_flow_execution = [
        {
            "id": flow_id,
            "prior_status": prior_flows.get(flow_id, {}).get("status", "unassessed"),
            "reuse": (
                "context-only"
                if prior_flows.get(flow_id, {}).get("status") == "assessed"
                else "none"
            ),
            "action": "reassess",
        }
        for flow_id in sorted({slugify(heading) for heading in headings})
    ]
    out_of_scope = sorted(
        path for path in declared_changed if not _path_in_scope(path, declared_scope)
    )
    if out_of_scope:
        raise ArchitectureError(
            "Review execution scope excludes changed paths: " + ", ".join(out_of_scope)
        )
    return {
        "runtime_payload_version": "1.0",
        "kind": "review-execution-plan",
        "repository": {
            "project_id": profile["project"]["id"],
            "base_commit": base_commit,
            "head_commit": head,
        },
        "prior_verified_review": {
            "path": review_path.relative_to(root).as_posix(),
            "id": review["review"]["id"],
            "commit": reviewed_commit,
            "sha256": file_sha256(review_path),
        },
        "changed_paths": declared_changed,
        "scope": declared_scope,
        "scope_exclusions": out_of_scope,
        "impact": {key: impact[key] for key in REVIEW_EXECUTION_IMPACTS},
        "impact_paths": {key: impact_paths[key] for key in REVIEW_EXECUTION_IMPACTS},
        "execution": {
            "architecture_quality_inferred": False,
            "rules": rule_execution,
            "critical_flows": critical_flow_execution,
            "guardrails": [
                "Prior assessments are context-only and never pass current execution.",
                "Every listed rule and critical flow requires explicit reassessment.",
                "Out-of-scope changed paths require explicit scope resolution.",
            ],
        },
    }


def benchmark_evidence_valid(fixture: Path, evidence: list[dict[str, Any]]) -> bool:
    if not evidence:
        return False
    fixture = fixture.resolve()
    for record in evidence:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            return False
        source = (fixture / relative).resolve()
        try:
            source.relative_to(fixture)
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError, ValueError):
            return False
        line_start = record["line_start"]
        line_end = record["line_end"]
        if line_end < line_start or line_end > len(lines):
            return False
        selected = "\n".join(lines[line_start - 1 : line_end])
        if record["excerpt"] not in selected:
            return False
    return True


def git_blob_bytes(root: Path, commit: str, path: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise ArchitectureError(
            f"Git provenance cannot resolve {path} at {commit}: {detail}"
        )
    return process.stdout


def git_tree_manifest(
    root: Path,
    commit: str,
    directory: str,
) -> tuple[str, int]:
    output = git_output(root, "ls-tree", "-r", "--name-only", commit, "--", directory)
    paths = [path for path in output.splitlines() if path]
    if not paths:
        raise ArchitectureError(
            f"Git provenance tree is empty at {commit}: {directory}"
        )
    prefix = directory.rstrip("/") + "/"
    records = []
    for path in paths:
        if not path.startswith(prefix):
            raise ArchitectureError(
                f"Git provenance tree path escaped {directory}: {path}"
            )
        records.append(
            {
                "path": path[len(prefix) :],
                "sha256": sha256_bytes(git_blob_bytes(root, commit, path)),
            }
        )
    return canonical_sha256(records), len(records)


def git_tree_bytes(root: Path, commit: str, directory: str) -> int:
    output = git_output(root, "ls-tree", "-r", "--name-only", commit, "--", directory)
    paths = [path for path in output.splitlines() if path]
    if not paths:
        raise ArchitectureError(
            f"Git provenance tree is empty at {commit}: {directory}"
        )
    return sum(len(git_blob_bytes(root, commit, path)) for path in paths)


def benchmark_context_text_parts(value: str, path: str) -> tuple[str, str]:
    if not path.endswith(".md") or not value.startswith("---\n"):
        return "", value
    closing = value.find("\n---\n", 4)
    if closing == -1:
        return "", value
    closing += len("\n---\n")
    return value[:closing], value[closing:]


def benchmark_context_treatment_map(
    context_manifest: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    treatments: dict[tuple[str, str], dict[str, Any]] = {}
    for treatment in context_manifest["treatments"]:
        key = (treatment["condition"], treatment["skill"])
        if key in treatments:
            raise ArchitectureError(
                "Benchmark context manifest repeats treatment " + "/".join(key)
            )
        treatments[key] = treatment
    return treatments


def validate_benchmark_ablation_contract(
    context_manifest: dict[str, Any],
    *,
    skills: set[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Require an unambiguous, comparable A/B/C treatment for every Skill."""
    treatments = benchmark_context_treatment_map(context_manifest)
    expected = {
        (condition, skill)
        for condition in BENCHMARK_TREATMENT_CONDITIONS
        for skill in skills
    }
    if set(treatments) != expected:
        raise ArchitectureError(
            "Benchmark context manifest must declare exactly one "
            "Base/Full/Compressed treatment for every benchmark Skill"
        )
    for skill in sorted(skills):
        base = treatments[("base", skill)]
        if base["knowledge_basis"] != "none" or any(
            base[field]
            for field in ("skill_metadata", "skill_body", "references", "knowledge")
        ):
            raise ArchitectureError(
                f"Benchmark Base treatment for {skill} must not load Skill, "
                "reference, or Knowledge content"
            )
        full = treatments[("full", skill)]
        compressed = treatments[("compressed", skill)]
        if (
            full["knowledge_basis"] != "workflow-required"
            or compressed["knowledge_basis"] != "workflow-required"
        ):
            raise ArchitectureError(
                f"Benchmark Full and Compressed treatments for {skill} must "
                "declare workflow-required Knowledge"
            )
        if full["knowledge"] != compressed["knowledge"]:
            raise ArchitectureError(
                f"Benchmark Full and Compressed treatments for {skill} must "
                "use identical Knowledge inputs"
            )
    return treatments


def validate_benchmark_context_budget(
    *,
    root: Path,
    commit: str,
    observed: dict[str, Any],
    provenance_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify a declared context proxy without interpreting it as token usage."""
    benchmark = observed["benchmark"]
    budget = benchmark["context_budget"]
    if budget["condition"] != benchmark["condition"]:
        raise ArchitectureError("Benchmark context condition does not match benchmark")
    by_role = {item["role"]: item for item in provenance_inputs}
    manifest_input = by_role.get("context-manifest")
    schema_input = by_role.get("benchmark-context-schema")
    if manifest_input is None or schema_input is None:
        raise ArchitectureError("Benchmark context proxy lacks manifest provenance")
    if (
        budget["manifest_path"] != manifest_input["path"]
        or budget["manifest_sha256"] != manifest_input["sha256"]
    ):
        raise ArchitectureError("Benchmark context manifest binding does not match")
    try:
        context_schema = json.loads(
            git_blob_bytes(root, commit, schema_input["path"]).decode("utf-8")
        )
        context_manifest = yaml.safe_load(
            git_blob_bytes(root, commit, manifest_input["path"]).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ArchitectureError(
            "Benchmark context manifest or schema is invalid"
        ) from exc
    if not isinstance(context_manifest, dict):
        raise ArchitectureError("Benchmark context manifest must be a mapping")
    errors = sorted(
        Draft202012Validator(context_schema).iter_errors(context_manifest),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ArchitectureError(
            "Benchmark context manifest fails its schema: " + errors[0].message
        )
    expected_by_role: dict[str, set[str]] = {
        "skill-metadata": set(),
        "skill-body": set(),
        "references": set(),
        "knowledge": set(),
        "tool-descriptions": set(),
        "artifact-input": set(),
    }
    treatments = validate_benchmark_ablation_contract(
        context_manifest,
        skills={str(case["skill"]) for case in observed["cases"]},
    )
    for case in observed["cases"]:
        key = (benchmark["condition"], case["skill"])
        treatment = treatments.get(key)
        if treatment is None:
            raise ArchitectureError(
                "Benchmark context manifest lacks treatment " + "/".join(key)
            )
        for source_key, target_key in (
            ("skill_metadata", "skill-metadata"),
            ("skill_body", "skill-body"),
            ("references", "references"),
            ("knowledge", "knowledge"),
            ("tool_descriptions", "tool-descriptions"),
        ):
            expected_by_role[target_key].update(treatment[source_key])
        if "$fixture-tree" in treatment["artifact_inputs"]:
            expected_by_role["artifact-input"].add(case["fixture"])
    actual_by_role: dict[str, set[str]] = {role: set() for role in expected_by_role}
    totals = {
        "skill_metadata_chars": 0,
        "skill_body_chars": 0,
        "reference_chars": 0,
        "knowledge_chars": 0,
        "tool_description_chars": 0,
        "artifact_input_bytes": 0,
    }
    total_by_role = {
        "skill-metadata": "skill_metadata_chars",
        "skill-body": "skill_body_chars",
        "references": "reference_chars",
        "knowledge": "knowledge_chars",
        "tool-descriptions": "tool_description_chars",
    }
    seen: set[tuple[str, str]] = set()
    for record in budget["inputs"]:
        role = record["role"]
        key = (role, record["path"])
        if key in seen:
            raise ArchitectureError(
                "Benchmark context proxy repeats input " + "/".join(key)
            )
        seen.add(key)
        if role not in expected_by_role:
            raise ArchitectureError(f"Benchmark context proxy has unknown role: {role}")
        actual_by_role[role].add(record["path"])
        if role == "artifact-input":
            digest, file_count = git_tree_manifest(root, commit, record["path"])
            byte_count = git_tree_bytes(root, commit, record["path"])
            if (
                record["sha256"] != digest
                or record.get("file_count") != file_count
                or record.get("bytes") != byte_count
            ):
                raise ArchitectureError(
                    f"Benchmark context artifact input is stale: {record['path']}"
                )
            totals["artifact_input_bytes"] += byte_count
            continue
        try:
            text = git_blob_bytes(root, commit, record["path"]).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArchitectureError(
                f"Benchmark context input is not UTF-8: {record['path']}"
            ) from exc
        if sha256_bytes(text.encode("utf-8")) != record["sha256"]:
            raise ArchitectureError(
                f"Benchmark context input hash mismatch: {record['path']}"
            )
        metadata, body = benchmark_context_text_parts(text, record["path"])
        characters = (
            len(metadata)
            if role == "skill-metadata"
            else len(body)
            if role == "skill-body"
            else len(text)
        )
        if record.get("characters") != characters:
            raise ArchitectureError(
                f"Benchmark context input character count is stale: {record['path']}"
            )
        totals[total_by_role[role]] += characters
    if actual_by_role != expected_by_role:
        raise ArchitectureError(
            "Benchmark context proxy inputs do not match the declared treatment"
        )
    for field, actual in totals.items():
        if budget[field] != actual:
            raise ArchitectureError(f"Benchmark context proxy total is stale: {field}")
    return {
        "metric_kind": budget["metric_kind"],
        "condition": budget["condition"],
        "scope": budget["scope"],
        "character_unit": budget["character_unit"],
        **totals,
    }


def validate_benchmark_provenance(
    *,
    root: Path,
    run_path: Path,
    observed: dict[str, Any],
    runtime_verification: str = "strict",
    artifact_commit: str | None = None,
) -> dict[str, Any] | None:
    if observed["schema_version"] not in {"1.3", "1.4", "1.5"}:
        return None
    extended_provenance = observed["schema_version"] in {"1.4", "1.5"}
    provenance = observed["benchmark"]["provenance"]
    source = provenance["source"]
    commit = source["commit"]
    git_output(root, "cat-file", "-e", f"{commit}^{{commit}}")
    archive_binding: dict[str, Any] | None = None
    if runtime_verification == "archived":
        if artifact_commit is None:
            raise ArchitectureError(
                "Archived runtime verification requires --artifact-commit"
            )
        git_output(root, "cat-file", "-e", f"{artifact_commit}^{{commit}}")
        try:
            relative_run = run_path.resolve().relative_to(root)
        except ValueError as exc:
            raise ArchitectureError(
                "Archived benchmark run must be inside the repository"
            ) from exc
        archived_run = git_blob_bytes(root, artifact_commit, relative_run.as_posix())
        if archived_run != run_path.read_bytes():
            raise ArchitectureError(
                "Benchmark run does not match the archived Git artifact"
            )
        archive_binding = {
            "commit": artifact_commit,
            "run_path": relative_run.as_posix(),
            "run_blob_sha": git_output(
                root,
                "rev-parse",
                f"{artifact_commit}:{relative_run.as_posix()}",
            ),
        }
    if source["dirty"]:
        raise ArchitectureError(
            "Benchmark release evidence must originate from clean relevant inputs"
        )

    required_roles = {
        "ground-truth",
        "benchmark-schema",
        "observation-schema",
        "dependency-lock",
        "knowledge-manifest",
    }
    if extended_provenance:
        required_roles.add("plugin-manifest")
    if observed["schema_version"] == "1.5":
        required_roles.update({"context-manifest", "benchmark-context-schema"})
    inputs = provenance["inputs"]
    roles = [item["role"] for item in inputs]
    if len(roles) != len(set(roles)):
        raise ArchitectureError("Benchmark provenance has duplicate input roles")
    if not required_roles.issubset(roles):
        missing = ", ".join(sorted(required_roles - set(roles)))
        raise ArchitectureError(f"Benchmark provenance is missing inputs: {missing}")
    for item in inputs:
        actual = sha256_bytes(git_blob_bytes(root, commit, item["path"]))
        if actual != item["sha256"]:
            raise ArchitectureError(
                f"Benchmark provenance input hash mismatch: {item['path']}"
            )
    if extended_provenance:
        manifest_item = next(
            item for item in inputs if item["role"] == "plugin-manifest"
        )
        try:
            manifest = json.loads(
                git_blob_bytes(root, commit, manifest_item["path"]).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchitectureError(
                "Benchmark provenance plugin manifest is invalid"
            ) from exc
        if manifest.get("version") != observed["benchmark"]["skill_version"]:
            raise ArchitectureError(
                "Benchmark Skill version does not match the source plugin manifest"
            )
        if provenance["model_request"] != observed["benchmark"]["model"]:
            raise ArchitectureError(
                "Benchmark model request does not match benchmark.model"
            )
        command_template = provenance["command_template"]
        if canonical_sha256(command_template) != provenance["command_template_sha256"]:
            raise ArchitectureError("Benchmark command template hash mismatch")
        if observed["schema_version"] == "1.5" and (
            not any("{condition}" in argument for argument in command_template)
            or not any(
                "{context_manifest}" in argument for argument in command_template
            )
        ):
            raise ArchitectureError(
                "Benchmark 1.5 command template must bind condition and "
                "context manifest"
            )

        runtimes = provenance["runtime_executables"]
        runtime_ids = [item["id"] for item in runtimes]
        if len(runtime_ids) != len(set(runtime_ids)):
            raise ArchitectureError("Benchmark provenance has duplicate runtime IDs")
        runtime_roles = {item["role"] for item in runtimes}
        if runtime_roles != {"command", "model"}:
            raise ArchitectureError(
                "Benchmark provenance requires command and model runtimes"
            )
        runtime_checks = []
        for runtime in runtimes:
            if (
                sha256_bytes(runtime["version_output"].encode("utf-8"))
                != runtime["version_output_sha256"]
            ):
                raise ArchitectureError(
                    f"Benchmark recorded runtime version hash mismatch: {runtime['id']}"
                )
            resolved_value = shutil.which(runtime["requested"])
            if resolved_value is None:
                runtime_checks.append(
                    {
                        "id": runtime["id"],
                        "current_host_match": False,
                        "reason": "unavailable",
                    }
                )
                continue
            resolved = Path(resolved_value).resolve()
            executable_match = (
                resolved.name == runtime["resolved_name"]
                and sha256_bytes(str(resolved).encode("utf-8"))
                == runtime["resolved_path_sha256"]
                and file_sha256(resolved) == runtime["executable_sha256"]
            )
            process = subprocess.run(
                [str(resolved), *runtime["version_arguments"]],
                check=False,
                capture_output=True,
                text=True,
            )
            version_output = "\n".join(
                part
                for part in (process.stdout.strip(), process.stderr.strip())
                if part
            )
            version_match = (
                process.returncode == 0
                and version_output == runtime["version_output"]
                and sha256_bytes(version_output.encode("utf-8"))
                == runtime["version_output_sha256"]
            )
            current_host_match = executable_match and version_match
            reason = (
                "matched"
                if current_host_match
                else (
                    "executable-mismatch"
                    if not executable_match
                    else "version-mismatch"
                )
            )
            runtime_checks.append(
                {
                    "id": runtime["id"],
                    "current_host_match": current_host_match,
                    "reason": reason,
                }
            )
        runtime_mismatches = [
            item for item in runtime_checks if not item["current_host_match"]
        ]
        if runtime_mismatches and runtime_verification == "strict":
            first = runtime_mismatches[0]
            if first["reason"] == "unavailable":
                raise ArchitectureError(
                    f"Benchmark runtime executable is unavailable: {first['id']}"
                )
            label = (
                "executable" if first["reason"] == "executable-mismatch" else "version"
            )
            raise ArchitectureError(
                f"Benchmark runtime {label} mismatch: {first['id']}"
            )
        model_versions = {
            item["version_output"] for item in runtimes if item["role"] == "model"
        }
        if observed["benchmark"]["surface"] not in model_versions:
            raise ArchitectureError(
                "Benchmark surface does not match a model runtime version"
            )
    context_budget = None
    if observed["schema_version"] == "1.5":
        context_budget = validate_benchmark_context_budget(
            root=root,
            commit=commit,
            observed=observed,
            provenance_inputs=inputs,
        )

    tools = provenance["tools"]
    tool_ids = [item["id"] for item in tools]
    if len(tool_ids) != len(set(tool_ids)):
        raise ArchitectureError("Benchmark provenance has duplicate tool IDs")
    if "benchmark-runner" not in tool_ids:
        raise ArchitectureError("Benchmark provenance is missing benchmark-runner")
    if "codex-benchmark-adapter" not in tool_ids:
        raise ArchitectureError(
            "Benchmark provenance is missing codex-benchmark-adapter"
        )
    for item in tools:
        actual = sha256_bytes(git_blob_bytes(root, commit, item["path"]))
        if actual != item["sha256"]:
            raise ArchitectureError(
                f"Benchmark provenance tool hash mismatch: {item['path']}"
            )

    fixture_records = provenance["fixtures"]
    fixture_ids = [item["case_id"] for item in fixture_records]
    case_ids = [item["id"] for item in observed["cases"]]
    if len(fixture_ids) != len(set(fixture_ids)) or set(fixture_ids) != set(case_ids):
        raise ArchitectureError(
            "Benchmark provenance fixtures do not match benchmark cases"
        )
    for item in fixture_records:
        digest, file_count = git_tree_manifest(root, commit, item["path"])
        if digest != item["sha256"] or file_count != item["file_count"]:
            raise ArchitectureError(
                f"Benchmark provenance fixture hash mismatch: {item['case_id']}"
            )

    log = provenance["execution_log"]
    relative_log = Path(log["path"])
    if relative_log.is_absolute() or ".." in relative_log.parts:
        raise ArchitectureError("Benchmark execution log path must be run-relative")
    log_path = require_within_root(
        run_path.parent,
        run_path.parent / relative_log,
        "benchmark execution log",
    )
    if file_sha256(log_path) != log["sha256"]:
        raise ArchitectureError("Benchmark execution log hash mismatch")
    if archive_binding is not None:
        if artifact_commit is None:
            raise ArchitectureError(
                "Archived benchmark verification requires an artifact commit"
            )
        relative_log_path = log_path.resolve().relative_to(root)
        archived_log = git_blob_bytes(
            root,
            artifact_commit,
            relative_log_path.as_posix(),
        )
        if archived_log != log_path.read_bytes():
            raise ArchitectureError(
                "Benchmark execution log does not match the archived Git artifact"
            )
        archive_binding["execution_log_path"] = relative_log_path.as_posix()
        archive_binding["execution_log_blob_sha"] = git_output(
            root,
            "rev-parse",
            f"{artifact_commit}:{relative_log_path.as_posix()}",
        )
    lines = log_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != log["records"]:
        raise ArchitectureError("Benchmark execution log record count mismatch")

    records: dict[tuple[str, int], tuple[dict[str, Any], str]] = {}
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArchitectureError(
                f"Benchmark execution log is invalid JSONL: {exc}"
            ) from exc
        key = (record.get("case_id"), record.get("trial_index"))
        if key in records:
            raise ArchitectureError(
                f"Benchmark execution log has duplicate record: {key}"
            )
        records[key] = (record, sha256_bytes(line.encode("utf-8")))

    expected_records = sum(len(case.get("trials", [])) for case in observed["cases"])
    if expected_records != len(records):
        raise ArchitectureError(
            "Benchmark execution log does not cover every preserved trial"
        )
    for case in observed["cases"]:
        for trial in case["trials"]:
            key = (case["id"], trial["index"])
            if key not in records:
                raise ArchitectureError(
                    f"Benchmark execution log is missing {case['id']} trial "
                    f"{trial['index']}"
                )
            record, record_sha256 = records[key]
            execution = trial["execution"]
            if record_sha256 != execution["log_record_sha256"]:
                raise ArchitectureError(
                    f"Benchmark log record hash mismatch: {case['id']} trial "
                    f"{trial['index']}"
                )
            for field in (
                "exit_code",
                "command_sha256",
                "stdout_sha256",
                "stderr_sha256",
            ):
                if record.get(field) != execution[field]:
                    raise ArchitectureError(
                        f"Benchmark execution {field} mismatch: {case['id']} "
                        f"trial {trial['index']}"
                    )
            if extended_provenance:
                command = record.get("command")
                if command != execution["command"]:
                    raise ArchitectureError(
                        f"Benchmark execution command mismatch: {case['id']} "
                        f"trial {trial['index']}"
                    )
                if canonical_sha256(command) != execution["command_sha256"]:
                    raise ArchitectureError(
                        f"Benchmark execution command hash mismatch: {case['id']} "
                        f"trial {trial['index']}"
                    )
                if observed["schema_version"] == "1.5":
                    if not any(
                        value == observed["benchmark"]["condition"]
                        or value.endswith("=" + observed["benchmark"]["condition"])
                        for value in command
                    ):
                        raise ArchitectureError(
                            f"Benchmark execution does not bind condition: "
                            f"{case['id']} trial {trial['index']}"
                        )
                    manifest_path = (
                        root / observed["benchmark"]["context_budget"]["manifest_path"]
                    ).resolve()
                    if not any(
                        Path(value.split("=", 1)[-1]).resolve() == manifest_path
                        for value in command
                    ):
                        raise ArchitectureError(
                            f"Benchmark execution does not bind context manifest: "
                            f"{case['id']} trial {trial['index']}"
                        )
            observation = record.get("observation")
            if not isinstance(observation, dict):
                raise ArchitectureError(
                    f"Benchmark execution observation is missing: {case['id']} "
                    f"trial {trial['index']}"
                )
            if canonical_sha256(observation) != execution["observation_sha256"]:
                raise ArchitectureError(
                    f"Benchmark observation hash mismatch: {case['id']} trial "
                    f"{trial['index']}"
                )
            normalized_findings = [
                {
                    "rule_id": finding["rule_id"],
                    "severity": finding["severity"],
                    "evidence": finding["evidence"],
                }
                for finding in trial["observed_findings"]
            ]
            if observation["observed_findings"] != normalized_findings:
                raise ArchitectureError(
                    f"Benchmark logged findings mismatch: {case['id']} trial "
                    f"{trial['index']}"
                )
            if (
                observation["observed_recommendations"]
                != trial["observed_recommendations"]
                or observation.get("observed_decision")
                != trial.get("observed_decision")
                or observation.get("usage") != trial.get("usage")
            ):
                raise ArchitectureError(
                    f"Benchmark logged observation mismatch: {case['id']} trial "
                    f"{trial['index']}"
                )
    return {
        "valid": True,
        "source_commit": commit,
        "source_dirty": source["dirty"],
        "execution_log_sha256": log["sha256"],
        "execution_log_records": log["records"],
        "environment": provenance["environment"],
        "runtime_executables": (
            provenance.get("runtime_executables") if extended_provenance else None
        ),
        "runtime_verification": (
            {
                "mode": runtime_verification,
                "current_host_match": all(
                    item["current_host_match"] for item in runtime_checks
                ),
                "checks": runtime_checks,
            }
            if extended_provenance
            else None
        ),
        "archive_binding": archive_binding,
        "context_budget_proxy": context_budget,
    }


def score_benchmark(
    ground_truth_path: Path,
    run_path: Path,
    *,
    runtime_verification: str = "strict",
    artifact_commit: str | None = None,
) -> dict[str, Any]:
    truth = validate_file(ground_truth_path, "benchmark.schema.json")
    observed = validate_file(run_path, "benchmark.schema.json")
    if truth["benchmark"]["kind"] != "ground-truth":
        raise ArchitectureError(f"{ground_truth_path} is not ground truth")
    if observed["benchmark"]["kind"] != "run":
        raise ArchitectureError(f"{run_path} is not a benchmark run")
    provenance_summary = validate_benchmark_provenance(
        root=ground_truth_path.parent.parent.resolve(),
        run_path=run_path.resolve(),
        observed=observed,
        runtime_verification=runtime_verification,
        artifact_commit=artifact_commit,
    )
    for field in ("id", "version"):
        if truth["benchmark"][field] != observed["benchmark"][field]:
            raise ArchitectureError(
                f"Benchmark run {field} does not match ground truth"
            )
    truth_case_ids = [case["id"] for case in truth["cases"]]
    run_case_ids = [case["id"] for case in observed["cases"]]
    if len(truth_case_ids) != len(set(truth_case_ids)):
        raise ArchitectureError("Ground truth has duplicate benchmark case IDs")
    if len(run_case_ids) != len(set(run_case_ids)):
        raise ArchitectureError("Benchmark run has duplicate case IDs")
    truth_cases = {case["id"]: case for case in truth["cases"]}
    run_cases = {case["id"]: case for case in observed["cases"]}
    if set(truth_cases) != set(run_cases):
        raise ArchitectureError("Benchmark run case IDs do not match ground truth")

    true_positive = 0
    false_positive = 0
    false_negative = 0
    severity_matches = 0
    severity_compared = 0
    valid_evidence = 0
    observed_evidence = 0
    forbidden_hits = 0
    total_trials = 0
    finding_stability_values: list[float] = []
    stable_severity = 0
    compared_severity_stability = 0
    durations: list[float] = []
    input_tokens = 0
    output_tokens = 0
    cost_usd = 0.0
    tool_calls = 0
    usage_trials = 0
    usage_field_trials = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0,
        "tool_calls": 0,
    }
    decision_trials = 0
    correct_decisions = 0
    overdesign_decisions = 0
    required_tradeoffs_seen = 0
    required_tradeoffs_total = 0
    valid_knowledge_citations = 0
    knowledge_citations = 0
    required_knowledge_seen = 0
    required_knowledge_total = 0
    rejection_explanation_values: list[float] = []
    migration_actionability_values: list[float] = []
    decision_stability_values: list[float] = []
    try:
        _, benchmark_knowledge = validate_knowledge_tree(
            KNOWLEDGE_ROOT,
            schema_root=SCHEMA_ROOT,
        )
    except KnowledgeError as exc:
        raise ArchitectureError(str(exc)) from exc
    for case_id, expected_case in truth_cases.items():
        run_case = run_cases[case_id]
        if run_case["fixture"] != expected_case["fixture"]:
            raise ArchitectureError(
                f"Benchmark case {case_id} fixture does not match ground truth"
            )
        expected_rule_ids = [
            item["rule_id"] for item in expected_case["expected_findings"]
        ]
        if len(expected_rule_ids) != len(set(expected_rule_ids)):
            raise ArchitectureError(
                f"Ground truth case {case_id} has duplicate rule IDs"
            )
        expected = {
            item["rule_id"]: item
            for item in expected_case["expected_findings"]
            if item["present"]
        }
        trials = run_case.get("trials") or [
            {
                "index": 1,
                "duration_seconds": 0.0,
                "observed_findings": run_case.get("observed_findings", []),
                "observed_recommendations": run_case.get(
                    "observed_recommendations",
                    [],
                ),
                "observed_decision": run_case.get("observed_decision"),
            }
        ]
        declared_repetitions = observed["benchmark"].get("repetitions")
        if declared_repetitions is not None and len(trials) != declared_repetitions:
            raise ArchitectureError(
                f"Benchmark case {case_id} trial count does not match repetitions"
            )
        if run_case.get("trials") and (
            run_case.get("observed_findings") != trials[0]["observed_findings"]
            or run_case.get("observed_recommendations")
            != trials[0]["observed_recommendations"]
        ):
            raise ArchitectureError(
                f"Benchmark case {case_id} summary does not match first trial"
            )
        if run_case.get("trials") and run_case.get("observed_decision") != trials[
            0
        ].get("observed_decision"):
            raise ArchitectureError(
                f"Benchmark case {case_id} decision summary does not match first trial"
            )
        total_trials += len(trials)
        trial_actuals: list[dict[str, dict[str, Any]]] = []
        trial_decisions: list[str] = []
        fixture = require_within_root(
            ground_truth_path.parent.parent,
            ground_truth_path.parent.parent / run_case["fixture"],
            f"benchmark case {case_id} fixture",
        )
        for trial in trials:
            observed_rule_ids = [item["rule_id"] for item in trial["observed_findings"]]
            if len(observed_rule_ids) != len(set(observed_rule_ids)):
                raise ArchitectureError(
                    f"Benchmark run case {case_id} trial {trial['index']} "
                    "has duplicate rule IDs"
                )
            actual = {item["rule_id"]: item for item in trial["observed_findings"]}
            trial_actuals.append(actual)
            true_positive += len(set(expected) & set(actual))
            false_negative += len(set(expected) - set(actual))
            false_positive += len(set(actual) - set(expected))
            for rule_id in set(expected) & set(actual):
                severity_compared += 1
                if expected[rule_id]["severity"] == actual[rule_id]["severity"]:
                    severity_matches += 1
            for item in actual.values():
                observed_evidence += 1
                computed_validity = benchmark_evidence_valid(
                    fixture,
                    item["evidence"],
                )
                if item["evidence_valid"] != computed_validity:
                    raise ArchitectureError(
                        f"Benchmark case {case_id} evidence_valid does not "
                        "match fixture evidence"
                    )
                valid_evidence += int(computed_validity)
            recommendations = {
                value.lower() for value in trial["observed_recommendations"]
            }
            forbidden_hits += sum(
                1
                for forbidden in expected_case["forbidden_recommendations"]
                if any(forbidden.lower() in value for value in recommendations)
            )
            durations.append(trial["duration_seconds"])
            usage = trial.get("usage")
            if usage is not None:
                usage_trials += 1
                if "input_tokens" in usage:
                    input_tokens += usage["input_tokens"]
                    usage_field_trials["input_tokens"] += 1
                if "output_tokens" in usage:
                    output_tokens += usage["output_tokens"]
                    usage_field_trials["output_tokens"] += 1
                if "cost_usd" in usage:
                    cost_usd += usage["cost_usd"]
                    usage_field_trials["cost_usd"] += 1
                if "tool_calls" in usage:
                    tool_calls += usage["tool_calls"]
                    usage_field_trials["tool_calls"] += 1
            expected_decision = expected_case.get("expected_decision")
            actual_decision = trial.get("observed_decision")
            if expected_decision is not None:
                decision_trials += 1
                if actual_decision is None:
                    trial_decisions.append("<missing>")
                    required_tradeoffs_total += len(
                        expected_decision["required_tradeoffs"]
                    )
                    required_knowledge_total += len(
                        expected_decision["required_knowledge_ids"]
                    )
                    rejection_explanation_values.append(0.0)
                    migration_actionability_values.append(
                        float(expected_decision["minimum_migration_slices"] == 0)
                    )
                    continue
                selected = actual_decision["selected_option"]
                trial_decisions.append(selected)
                accepted = {
                    expected_decision["selected_option"],
                    *expected_decision.get("acceptable_options", []),
                }
                correct_decisions += int(selected in accepted)
                overdesign_decisions += int(
                    selected in set(expected_decision["overdesign_options"])
                )
                expected_tradeoffs = set(expected_decision["required_tradeoffs"])
                actual_tradeoffs = set(actual_decision["compared_tradeoffs"])
                required_tradeoffs_total += len(expected_tradeoffs)
                required_tradeoffs_seen += len(expected_tradeoffs & actual_tradeoffs)
                cited_knowledge = set(actual_decision["knowledge_ids"])
                knowledge_citations += len(cited_knowledge)
                valid_knowledge_citations += len(
                    cited_knowledge & set(benchmark_knowledge)
                )
                expected_knowledge = set(expected_decision["required_knowledge_ids"])
                required_knowledge_total += len(expected_knowledge)
                required_knowledge_seen += len(expected_knowledge & cited_knowledge)
                minimum_rejections = expected_decision["minimum_rejected_options"]
                rejection_explanation_values.append(
                    min(
                        len(actual_decision["rejected_options"]) / minimum_rejections,
                        1.0,
                    )
                )
                minimum_slices = expected_decision["minimum_migration_slices"]
                migration_actionability_values.append(
                    min(
                        len(actual_decision["migration_slices"]) / minimum_slices,
                        1.0,
                    )
                    if minimum_slices
                    else 1.0
                )
        for left_index, left in enumerate(trial_actuals):
            for right in trial_actuals[left_index + 1 :]:
                union = set(left) | set(right)
                finding_stability_values.append(
                    len(set(left) & set(right)) / len(union) if union else 1.0
                )
                for rule_id in set(left) & set(right):
                    compared_severity_stability += 1
                    if left[rule_id]["severity"] == right[rule_id]["severity"]:
                        stable_severity += 1
        for left_index, left_decision in enumerate(trial_decisions):
            for right_decision in trial_decisions[left_index + 1 :]:
                decision_stability_values.append(float(left_decision == right_decision))

    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    result: dict[str, Any] = {
        "cases": len(truth_cases),
        "trials": total_trials,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": (
            true_positive / precision_denominator
            if precision_denominator
            else float(not recall_denominator)
        ),
        "recall": true_positive / recall_denominator if recall_denominator else 1.0,
        "severity_agreement": (
            severity_matches / severity_compared if severity_compared else 1.0
        ),
        "evidence_validity": (
            valid_evidence / observed_evidence if observed_evidence else 1.0
        ),
        "forbidden_recommendation_hits": forbidden_hits,
        "finding_stability": (
            sum(finding_stability_values) / len(finding_stability_values)
            if finding_stability_values
            else 1.0
        ),
        "severity_stability": (
            stable_severity / compared_severity_stability
            if compared_severity_stability
            else 1.0
        ),
        "mean_duration_seconds": (
            sum(durations) / len(durations) if durations else 0.0
        ),
        "usage_trials": usage_trials,
        "input_token_trials": usage_field_trials["input_tokens"],
        "output_token_trials": usage_field_trials["output_tokens"],
        "cost_trials": usage_field_trials["cost_usd"],
        "tool_call_trials": usage_field_trials["tool_calls"],
        "input_tokens": (input_tokens if usage_field_trials["input_tokens"] else None),
        "output_tokens": (
            output_tokens if usage_field_trials["output_tokens"] else None
        ),
        "cost_usd": cost_usd if usage_field_trials["cost_usd"] else None,
        "tool_calls": tool_calls if usage_field_trials["tool_calls"] else None,
        "decision_trials": decision_trials,
        "recommendation_accuracy": (
            correct_decisions / decision_trials if decision_trials else 1.0
        ),
        "overdesign_rate": (
            overdesign_decisions / decision_trials if decision_trials else 0.0
        ),
        "tradeoff_coverage": (
            required_tradeoffs_seen / required_tradeoffs_total
            if required_tradeoffs_total
            else 1.0
        ),
        "knowledge_citation_validity": (
            valid_knowledge_citations / knowledge_citations
            if knowledge_citations
            else float(not decision_trials)
        ),
        "required_knowledge_coverage": (
            required_knowledge_seen / required_knowledge_total
            if required_knowledge_total
            else 1.0
        ),
        "rejection_explanation_coverage": (
            sum(rejection_explanation_values) / len(rejection_explanation_values)
            if rejection_explanation_values
            else 1.0
        ),
        "migration_actionability": (
            sum(migration_actionability_values) / len(migration_actionability_values)
            if migration_actionability_values
            else 1.0
        ),
        "decision_stability": (
            sum(decision_stability_values) / len(decision_stability_values)
            if decision_stability_values
            else 1.0
        ),
    }
    if provenance_summary is not None:
        result["provenance"] = provenance_summary
        if provenance_summary.get("context_budget_proxy") is not None:
            result["context_budget_proxy"] = provenance_summary["context_budget_proxy"]
    return result


def gate_result_to_sarif(result: dict[str, Any]) -> dict[str, Any]:
    rules: dict[str, dict[str, Any]] = {}
    sarif_results: list[dict[str, Any]] = []
    for finding in result["blocking"]:
        rule_id = finding["id"]
        rules.setdefault(
            rule_id,
            {
                "id": rule_id,
                "shortDescription": {"text": finding["title"]},
                "properties": {"severity": finding["severity"]},
            },
        )
        sarif_results.append(
            {
                "ruleId": rule_id,
                "level": (
                    "error"
                    if finding["severity"] in {"critical", "high"}
                    else "warning"
                ),
                "message": {"text": finding["reason"]},
            }
        )
    for index, failure in enumerate(result["policy_failures"], start=1):
        rule_id = f"ARCH-POLICY-{index:03d}"
        rules[rule_id] = {
            "id": rule_id,
            "shortDescription": {"text": "Architecture policy failure"},
        }
        sarif_results.append(
            {
                "ruleId": rule_id,
                "level": "error",
                "message": {"text": failure},
            }
        )
    return {
        "version": "2.1.0",
        "$schema": ("https://json.schemastore.org/sarif-2.1.0.json"),
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Hengmu",
                        "version": TOOL_VERSION,
                        "rules": [rules[key] for key in sorted(rules)],
                    }
                },
                "results": sarif_results,
            }
        ],
    }


def append_repeatable(parser: argparse.ArgumentParser, flag: str, dest: str) -> None:
    parser.add_argument(flag, dest=dest, action="append", default=[])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage architecture profiles, reviews, and quality gates."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {TOOL_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_project = subparsers.add_parser(
        "prepare-project-audit",
        help=(
            "Create a facts-derived .architecture control plane when missing, "
            "or validate and reuse the existing one."
        ),
    )
    prepare_project.add_argument("--repo", default=".")
    prepare_project.add_argument("--name")
    prepare_project.add_argument("--id", dest="project_id")

    project = subparsers.add_parser(
        "init-project",
        help="Create .architecture configuration without overwriting existing files.",
    )
    project.add_argument("--repo", default=".")
    project.add_argument("--name")
    project.add_argument("--id", dest="project_id")
    project.add_argument(
        "--lifecycle",
        choices=["experimental", "active", "maintenance", "retiring"],
        default="active",
    )
    project.add_argument(
        "--criticality",
        choices=["low", "medium", "high", "mission-critical"],
        default="medium",
    )
    project.add_argument(
        "--data-classification",
        choices=["public", "internal", "confidential", "restricted", "mixed"],
        default="internal",
    )
    append_repeatable(project, "--type", "types")
    append_repeatable(project, "--owner", "owners")
    append_repeatable(project, "--quality", "qualities")
    append_repeatable(project, "--review", "reviews")
    append_repeatable(project, "--rule-pack", "rule_packs")

    inspect_parser = subparsers.add_parser(
        "inspect-repository",
        help="Write deterministic repository facts without architecture judgments.",
    )
    inspect_parser.add_argument("--repo", default=".")
    inspect_parser.add_argument("--output", required=True)
    append_repeatable(inspect_parser, "--scope", "scopes")
    inspect_parser.add_argument("--force", action="store_true")

    build_profile_parser = subparsers.add_parser(
        "build-profile",
        help="Build a sourced project profile from repository facts.",
    )
    build_profile_parser.add_argument("--facts", required=True)
    build_profile_parser.add_argument("--declared")
    build_profile_parser.add_argument("--output", required=True)
    build_profile_parser.add_argument("--force", action="store_true")

    select_parser = subparsers.add_parser(
        "select-knowledge",
        help="Select a bounded, explainable set of Markdown knowledge entries.",
    )
    select_parser.add_argument("--facts", required=True)
    select_parser.add_argument("--profile")
    select_parser.add_argument("--task", required=True)
    select_parser.add_argument("--skill", required=True)
    select_parser.add_argument("--max-entries", type=int, default=24)
    select_parser.add_argument(
        "--kind-budget",
        action="append",
        default=[],
        type=parse_kind_budget,
        metavar="KIND=LIMIT",
    )
    select_parser.add_argument("--maintainer", action="store_true")
    append_repeatable(select_parser, "--include", "includes")
    append_repeatable(select_parser, "--exclude", "excludes")
    append_repeatable(
        select_parser,
        "--decision-intent",
        "decision_intents",
    )
    select_parser.add_argument("--output", required=True)
    select_parser.add_argument(
        "--context-output",
        help="Also write the compact model-facing selected-Knowledge sidecar.",
    )
    select_parser.add_argument("--force", action="store_true")

    portfolio = subparsers.add_parser(
        "init-portfolio",
        help="Create .architecture-portfolio configuration.",
    )
    portfolio.add_argument("--root", default=".")
    portfolio.add_argument("--name")
    portfolio.add_argument("--id", dest="portfolio_id")
    portfolio.add_argument("--review-horizon-months", type=int, default=12)
    append_repeatable(portfolio, "--owner", "owners")

    validate_project_parser = subparsers.add_parser(
        "validate-project",
        help="Validate project configuration and review artifacts.",
    )
    validate_project_parser.add_argument("root", nargs="?", default=".")

    validate_portfolio_parser = subparsers.add_parser(
        "validate-portfolio",
        help="Validate portfolio configuration and review artifacts.",
    )
    validate_portfolio_parser.add_argument("root", nargs="?", default=".")

    validate_review_parser = subparsers.add_parser(
        "validate-review",
        help="Validate one candidate or verified review.",
    )
    validate_review_parser.add_argument("path")
    validate_review_parser.add_argument("--project")
    validate_review_parser.add_argument(
        "--historical",
        action="store_true",
        help=(
            "Validate an existing historical chain without allowing it to create "
            "new trusted downstream artifacts."
        ),
    )

    validate_selection_parser = subparsers.add_parser(
        "validate-knowledge-selection",
        help="Validate one selected-knowledge artifact and optional source bindings.",
    )
    validate_selection_parser.add_argument("path")
    validate_selection_parser.add_argument("--facts")
    validate_selection_parser.add_argument("--profile")
    validate_selection_parser.add_argument(
        "--read-only",
        action="store_true",
        help=(
            "Permit an unresolvable historical Runtime lock for inspection only; "
            "never use this mode for a trusted Review, Decision, or Gate."
        ),
    )
    validate_selection_parser.add_argument(
        "--require-current-runtime",
        action="store_true",
        help="Require deterministic replay by the current Selector Runtime.",
    )

    validate_context_parser = subparsers.add_parser(
        "validate-knowledge-context",
        help="Validate a compact context as the exact projection of its Selection.",
    )
    validate_context_parser.add_argument("path")
    validate_context_parser.add_argument("--selection", required=True)
    validate_context_parser.add_argument("--facts")
    validate_context_parser.add_argument("--profile")
    validate_context_parser.add_argument(
        "--historical",
        action="store_true",
        help="Permit a Git-verified archived Selection for historical inspection.",
    )

    validate_governance_run_parser = subparsers.add_parser(
        "validate-governance-run",
        help=(
            "Validate an informational high-risk governance run record; "
            "it is not gate evidence."
        ),
    )
    validate_governance_run_parser.add_argument("path")
    validate_governance_run_parser.add_argument(
        "--project",
        help="Repository root used only to check declared paths stay contained.",
    )

    coverage_parser = subparsers.add_parser(
        "validate-coverage",
        help="Validate trusted Rule Pack, critical-flow, and knowledge coverage.",
    )
    coverage_parser.add_argument("--project", required=True)
    coverage_parser.add_argument("--review", required=True)
    coverage_parser.add_argument("--allow-candidates", action="store_true")
    coverage_parser.add_argument("--historical", action="store_true")

    fingerprint_parser = subparsers.add_parser(
        "fingerprint-artifact",
        help="Print canonical, file, and Finding fingerprints.",
    )
    fingerprint_parser.add_argument("path")
    fingerprint_parser.add_argument("--subject-id")

    validate_plan_parser = subparsers.add_parser(
        "validate-plan",
        help="Validate one remediation plan.",
    )
    validate_plan_parser.add_argument("path")
    plan_source = validate_plan_parser.add_mutually_exclusive_group()
    plan_source.add_argument("--review")
    plan_source.add_argument("--design-brief")
    validate_plan_parser.add_argument("--decision")
    validate_plan_parser.add_argument(
        "--project",
        help="Repository root used to resolve completion evidence.",
    )
    validate_plan_parser.add_argument("--historical", action="store_true")

    validate_design_brief_parser = subparsers.add_parser(
        "validate-design-brief",
        help="Validate one Greenfield architecture design brief.",
    )
    validate_design_brief_parser.add_argument("path")
    validate_design_brief_parser.add_argument(
        "--project",
        help="Repository root used to verify approval authority and evidence.",
    )
    validate_design_brief_parser.add_argument("--historical", action="store_true")

    validate_decision_parser = subparsers.add_parser(
        "validate-decision",
        help="Validate a remediation or Greenfield architecture decision.",
    )
    validate_decision_parser.add_argument("path")
    decision_source = validate_decision_parser.add_mutually_exclusive_group()
    decision_source.add_argument("--review")
    decision_source.add_argument("--design-brief")
    validate_decision_parser.add_argument(
        "--project",
        help="Repository root used to validate Profile quality attributes.",
    )
    validate_decision_parser.add_argument("--require-accepted", action="store_true")
    validate_decision_parser.add_argument("--historical", action="store_true")

    history_parser = subparsers.add_parser(
        "validate-history-anchors",
        help="Require selector-source and reviewed implementation commits in HEAD.",
    )
    history_parser.add_argument("root", nargs="?", default=".")
    history_parser.add_argument("--review")

    validate_acceptance_parser = subparsers.add_parser(
        "validate-risk-acceptances",
        help="Validate a risk-acceptance registry.",
    )
    validate_acceptance_parser.add_argument("path")

    subparsers.add_parser(
        "validate-knowledge",
        help="Validate bundled knowledge, rules, providers, and freshness.",
    )

    provider_status_parser = subparsers.add_parser(
        "evidence-providers",
        help="List configured evidence providers, detection, and readiness.",
    )
    provider_status_parser.add_argument("--project", default=".")

    provider_run_parser = subparsers.add_parser(
        "run-evidence-provider",
        help="Run one enabled provider without a shell and bind its outputs.",
    )
    provider_run_parser.add_argument("--project", default=".")
    provider_run_parser.add_argument("--provider", required=True)
    provider_run_parser.add_argument("--output", type=Path)

    validate_provider_run_parser = subparsers.add_parser(
        "validate-evidence-run",
        help="Validate a provider run and its captured output hashes.",
    )
    validate_provider_run_parser.add_argument("path")
    validate_provider_run_parser.add_argument("--project", default=".")
    validate_provider_run_parser.add_argument(
        "--require-passed",
        action="store_true",
    )

    signature_parser = subparsers.add_parser(
        "verify-review-signature",
        help="Verify a trusted review's detached SSH signature against policy.",
    )
    signature_parser.add_argument("--project", default=".")
    signature_parser.add_argument("--review", required=True)

    verify_evidence_parser = subparsers.add_parser(
        "verify-evidence",
        help="Resolve confirmed review evidence against Git objects.",
    )
    verify_evidence_parser.add_argument("--repo", required=True)
    verify_evidence_parser.add_argument("--review", required=True)

    bindings_parser = subparsers.add_parser(
        "review-bindings",
        help="Print hashes and identities needed to bind a verified review.",
    )
    bindings_parser.add_argument("--project", required=True)
    bindings_parser.add_argument("--candidate", required=True)

    execution_plan_parser = subparsers.add_parser(
        "plan-review-execution",
        help=(
            "Build a deterministic review execution payload bound to a verified "
            "prior review and explicit change scope."
        ),
    )
    execution_plan_parser.add_argument("--project", required=True)
    execution_plan_parser.add_argument("--review", required=True)
    execution_plan_parser.add_argument("--base-commit", required=True)
    execution_plan_parser.add_argument("--scope", action="append", required=True)

    diff_parser = subparsers.add_parser(
        "review-diff",
        help="Compare findings and rule coverage between two valid reviews.",
    )
    diff_parser.add_argument("--before", required=True)
    diff_parser.add_argument("--after", required=True)
    diff_parser.add_argument(
        "--project",
        help="Strictly validate both reviews against this project.",
    )

    decision_bindings_parser = subparsers.add_parser(
        "decision-bindings",
        help="Print source-context and knowledge hashes for an architecture decision.",
    )
    decision_bindings_parser.add_argument("--project", required=True)
    binding_source = decision_bindings_parser.add_mutually_exclusive_group(
        required=True
    )
    binding_source.add_argument("--review")
    binding_source.add_argument("--design-brief")
    decision_bindings_parser.add_argument(
        "--knowledge-selection",
        help=(
            "Selection artifact for a 1.2 decision; defaults to the source "
            "review's selection."
        ),
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark-score",
        help="Score a behavior benchmark run against ground truth.",
    )
    benchmark_parser.add_argument("--ground-truth", required=True)
    benchmark_parser.add_argument("--run", required=True)
    benchmark_parser.add_argument(
        "--runtime-verification",
        choices=["strict", "archived"],
        default="strict",
        help=(
            "Require current-host runtime identity, or verify immutable archived "
            "run/log bytes while reporting current-host mismatches."
        ),
    )
    benchmark_parser.add_argument(
        "--artifact-commit",
        help="Git commit that immutably contains the run and execution log.",
    )
    benchmark_parser.add_argument(
        "--output",
        type=Path,
        help="Also write the score as deterministic UTF-8 JSON.",
    )

    gate = subparsers.add_parser(
        "gate",
        help="Evaluate a verified review against deterministic policy.",
    )
    gate_target = gate.add_mutually_exclusive_group()
    gate_target.add_argument("--project")
    gate_target.add_argument("--portfolio")
    gate_source = gate.add_mutually_exclusive_group()
    gate_source.add_argument("--review")
    gate_source.add_argument(
        "--decision",
        help="Gate a Greenfield Design Brief -> Decision -> Plan chain.",
    )
    gate.add_argument(
        "--base-commit",
        help="Classify the change from this ancestor to HEAD.",
    )
    gate.add_argument(
        "--stage",
        choices=["all", "contract", "finding", "change", "release"],
        default="all",
    )
    gate.add_argument("--json", action="store_true", dest="json_output")
    gate.add_argument(
        "--sarif-output",
        type=Path,
        help="Write GitHub-compatible SARIF 2.1.0 results to this path.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "prepare-project-audit":
        target, initialized = prepare_project_audit(args)
        action = "Initialized" if initialized else "Validated existing"
        print(f"{action} project architecture configuration: {target}")
        return 0
    if args.command == "init-project":
        target = init_project(args)
        print(f"Initialized project architecture configuration: {target}")
        return 0
    if args.command == "init-portfolio":
        if not 1 <= args.review_horizon_months <= 60:
            raise ArchitectureError("--review-horizon-months must be between 1 and 60")
        target = init_portfolio(args)
        print(f"Initialized portfolio architecture configuration: {target}")
        return 0
    if args.command == "inspect-repository":
        output = Path(args.output).expanduser().resolve()
        if output.exists() and not args.force:
            raise ArchitectureError(
                f"Refusing to overwrite existing output without --force: {output}"
            )
        payload = inspect_repository(
            Path(args.repo),
            scope_values=args.scopes or None,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(output, payload)
        print(f"Repository facts written: {output}")
        return 0
    if args.command == "build-profile":
        output = Path(args.output).expanduser().resolve()
        if output.exists() and not args.force:
            raise ArchitectureError(
                f"Refusing to overwrite existing output without --force: {output}"
            )
        payload = build_profile(
            Path(args.facts),
            declared_path=Path(args.declared) if args.declared else None,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(output, payload)
        print(f"Project profile written: {output}")
        return 0
    if args.command == "select-knowledge":
        output = Path(args.output).expanduser().resolve()
        if output.exists() and not args.force:
            raise ArchitectureError(
                f"Refusing to overwrite existing output without --force: {output}"
            )
        context_output = (
            Path(args.context_output).expanduser().resolve()
            if args.context_output
            else None
        )
        if context_output is not None and context_output.exists() and not args.force:
            raise ArchitectureError(
                "Refusing to overwrite existing context output without --force: "
                f"{context_output}"
            )
        kind_budgets: dict[str, int] = {}
        for kind, limit in args.kind_budget:
            if kind in kind_budgets:
                raise ArchitectureError(f"Duplicate knowledge kind budget: {kind}")
            kind_budgets[kind] = limit
        payload = select_knowledge(
            Path(args.facts),
            profile_path=Path(args.profile) if args.profile else None,
            task=args.task,
            skill=args.skill,
            maximum_entries=args.max_entries,
            includes=args.includes,
            excludes=args.excludes,
            kind_budgets=kind_budgets,
            maintainer_mode=args.maintainer,
            decision_intents=args.decision_intents,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(output, payload)
        if context_output is not None:
            context_output.parent.mkdir(parents=True, exist_ok=True)
            write_yaml(
                context_output,
                knowledge_context(
                    payload,
                    selection_lock_sha256=file_sha256(output),
                ),
            )
        print(
            f"Knowledge selection written: {output} "
            f"({payload['budget']['selected_entries']} entries)"
        )
        if context_output is not None:
            print(f"Knowledge context written: {context_output}")
        return 0
    if args.command == "validate-project":
        validated = validate_project(Path(args.root))
        print(f"Project architecture configuration is valid ({len(validated)} files).")
        return 0
    if args.command == "validate-portfolio":
        validated = validate_portfolio(Path(args.root))
        print(
            f"Portfolio architecture configuration is valid ({len(validated)} files)."
        )
        return 0
    if args.command == "validate-review":
        if args.project:
            project_root = Path(args.project).resolve()
            profile = validate_file(
                project_root / ".architecture" / "profile.yaml",
                "project-profile.schema.json",
            )
            validate_review(
                Path(args.path).resolve(),
                rule_pack_ids=profile["project"]["rule_packs"],
                strict_trust=True,
                repository_root=project_root,
                require_current_selection=not args.historical,
            )
        else:
            validate_review(Path(args.path).resolve())
        print("Architecture review is valid.")
        return 0
    if args.command == "validate-knowledge-selection":
        if args.read_only and args.require_current_runtime:
            raise ArchitectureError(
                "--read-only and --require-current-runtime are mutually exclusive"
            )
        validate_knowledge_selection_artifact(
            Path(args.path).resolve(),
            facts_path=Path(args.facts).resolve() if args.facts else None,
            profile_path=Path(args.profile).resolve() if args.profile else None,
            require_trusted_runtime=not args.read_only,
            require_current_runtime=args.require_current_runtime,
        )
        print("Knowledge selection is valid.")
        return 0
    if args.command == "validate-knowledge-context":
        validate_knowledge_context_artifact(
            Path(args.path).resolve(),
            Path(args.selection).resolve(),
            facts_path=Path(args.facts).resolve() if args.facts else None,
            profile_path=Path(args.profile).resolve() if args.profile else None,
            require_current_runtime=not args.historical,
        )
        print("Knowledge context is valid and exactly matches its Selection.")
        return 0
    if args.command == "validate-governance-run":
        validate_governance_run(
            Path(args.path).resolve(),
            project_root=(Path(args.project).resolve() if args.project else None),
        )
        print("Informational governance run is valid.")
        return 0
    if args.command == "validate-coverage":
        project_root = Path(args.project).resolve()
        profile = validate_file(
            project_root / ".architecture" / "profile.yaml",
            "project-profile.schema.json",
        )
        review_path = Path(args.review)
        if not review_path.is_absolute():
            review_path = project_root / review_path
        review = validate_review(
            review_path.resolve(),
            rule_pack_ids=profile["project"]["rule_packs"],
            strict_trust=True,
            repository_root=project_root,
            require_current_selection=not args.historical,
        )
        if (
            not args.allow_candidates
            and review["review"]["verification_state"] != "verified"
        ):
            raise ArchitectureError(
                f"{review_path} is a candidate review; verified coverage is required"
            )
        if not review.get("coverage_complete"):
            raise ArchitectureError(f"{review_path} declares incomplete coverage")
        print(
            "Architecture coverage is valid "
            f"({len(review['coverage'])} rules, "
            f"{len(review.get('critical_flow_coverage', []))} critical flows, "
            f"{len(review.get('selected_knowledge', []))} knowledge entries)."
        )
        return 0
    if args.command == "fingerprint-artifact":
        artifact_path = Path(args.path).resolve()
        payload = load_yaml(artifact_path)
        result: dict[str, Any] = {
            "path": str(artifact_path),
            "file_sha256": file_sha256(artifact_path),
            "canonical_sha256": canonical_sha256(payload),
        }
        if "review" in payload:
            subject = payload["review"]["subject"]["id"]
            result["kind"] = "review"
            result["artifact_id"] = payload["review"]["id"]
            result["findings"] = [
                {
                    "id": finding["id"],
                    "fingerprint": finding_fingerprint(subject, finding),
                    "evidence_fingerprint": canonical_sha256(finding["evidence"]),
                }
                for finding in payload["findings"]
            ]
        elif "decision" in payload:
            result["kind"] = "architecture-decision"
            result["artifact_id"] = payload["decision"]["id"]
        elif "plan" in payload:
            result["kind"] = "remediation-plan"
            result["artifact_id"] = payload["plan"]["id"]
        elif "id" in payload and "rule_id" in payload:
            if not args.subject_id:
                raise ArchitectureError("A standalone Finding requires --subject-id")
            result["kind"] = "finding"
            result["artifact_id"] = payload["id"]
            result["finding_fingerprint"] = finding_fingerprint(
                args.subject_id,
                payload,
            )
            result["evidence_fingerprint"] = canonical_sha256(payload["evidence"])
        else:
            result["kind"] = "generic-artifact"
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "validate-plan":
        plan_path = Path(args.path).resolve()
        if args.project is None and not args.historical:
            plan_payload = load_yaml(plan_path)
            if (
                plan_payload.get("schema_version") == "1.3"
                and plan_payload.get("plan", {}).get("plan_kind")
                == "greenfield-implementation"
            ):
                raise ArchitectureError(
                    "Current Greenfield Plan 1.3 validation requires --project "
                    "to verify project-relative source and Knowledge bindings"
                )
        validate_plan(
            plan_path,
            review_path=Path(args.review).resolve() if args.review else None,
            decision_path=Path(args.decision).resolve() if args.decision else None,
            design_brief_path=(
                Path(args.design_brief).resolve() if args.design_brief else None
            ),
            repository_root=(Path(args.project).resolve() if args.project else None),
            allow_unverifiable_historical=args.historical,
            require_current_selection=not args.historical,
        )
        print("Architecture remediation plan is valid.")
        return 0
    if args.command == "validate-design-brief":
        validate_design_brief(
            Path(args.path).resolve(),
            repository_root=(Path(args.project).resolve() if args.project else None),
            allow_unverifiable_historical=args.historical,
        )
        print("Architecture design brief is valid.")
        return 0
    if args.command == "validate-decision":
        decision_path = Path(args.path).resolve()
        if args.project is None and not args.historical:
            decision_payload = load_yaml(decision_path)
            if decision_payload.get("schema_version") == "1.4":
                raise ArchitectureError(
                    "Current Decision 1.4 validation requires --project to verify "
                    "project-relative Design Brief and Knowledge bindings"
                )
        validate_decision(
            decision_path,
            review_path=Path(args.review).resolve() if args.review else None,
            design_brief_path=(
                Path(args.design_brief).resolve() if args.design_brief else None
            ),
            require_accepted=args.require_accepted,
            repository_root=(Path(args.project).resolve() if args.project else None),
            allow_unverifiable_historical=args.historical,
            require_current_selection=not args.historical,
        )
        print("Architecture decision is valid.")
        return 0
    if args.command == "validate-history-anchors":
        result = validate_history_anchors(
            Path(args.root),
            Path(args.review) if args.review else None,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "validate-risk-acceptances":
        validate_risk_acceptances(Path(args.path).resolve())
        print("Architecture risk acceptances are valid.")
        return 0
    if args.command == "validate-knowledge":
        result = validate_knowledge()
        print(
            "Architecture knowledge is valid "
            f"({result['catalogs']} catalogs, {result['entries']} entries, "
            f"{result['rule_packs']} rule packs, {result['providers']} providers)."
        )
        return 0
    if args.command == "evidence-providers":
        provider_status = evidence_provider_status(Path(args.project))
        print(json.dumps(provider_status, indent=2, ensure_ascii=False))
        return 0
    if args.command == "run-evidence-provider":
        artifact_path, artifact = run_evidence_provider(
            Path(args.project),
            args.provider,
            output_path=args.output,
        )
        print(
            json.dumps(
                {
                    "artifact": str(artifact_path),
                    "run_id": artifact["run"]["id"],
                    "status": artifact["result"]["status"],
                    "exit_code": artifact["result"]["exit_code"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if artifact["result"]["status"] == "passed" else 1
    if args.command == "validate-evidence-run":
        validate_evidence_run(
            Path(args.path),
            Path(args.project),
            require_passed=args.require_passed,
        )
        print("Evidence provider run is valid.")
        return 0
    if args.command == "verify-review-signature":
        project_root = Path(args.project).resolve()
        review_path = Path(args.review)
        if not review_path.is_absolute():
            review_path = project_root / review_path
        profile = validate_file(
            project_root / ".architecture" / "profile.yaml",
            "project-profile.schema.json",
        )
        review = validate_review(
            review_path,
            rule_pack_ids=profile["project"]["rule_packs"],
            strict_trust=True,
            repository_root=project_root,
        )
        policy = validate_file(
            project_root / ".architecture" / "gate-policy.yaml",
            "gate-policy.schema.json",
        )
        if "artifact_signatures" not in policy:
            raise ArchitectureError(
                "Project policy has no artifact_signatures configuration"
            )
        verify_review_signature(
            review_path,
            review,
            project_root,
            policy["artifact_signatures"],
        )
        print("Review SSH signature is valid.")
        return 0
    if args.command == "verify-evidence":
        evidence_review = validate_review(Path(args.review).resolve())
        evidence_result = verify_review_evidence(evidence_review, Path(args.repo))
        print(json.dumps(evidence_result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "review-bindings":
        result = review_bindings(
            Path(args.project),
            Path(args.candidate),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "plan-review-execution":
        result = plan_review_execution(
            Path(args.project),
            Path(args.review),
            base_commit=args.base_commit,
            scope=args.scope,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "review-diff":
        result = review_diff(
            Path(args.before),
            Path(args.after),
            project_root=Path(args.project) if args.project else None,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "decision-bindings":
        project_root = Path(args.project).resolve()
        profile = validate_file(
            project_root / ".architecture" / "profile.yaml",
            "project-profile.schema.json",
        )
        decision_review: dict[str, Any] | None = None
        design_brief_path: Path | None = None
        decision_review_path: Path | None = None
        if args.review:
            decision_review_path = Path(args.review)
            if not decision_review_path.is_absolute():
                decision_review_path = project_root / decision_review_path
            decision_review_path = require_within_root(
                project_root,
                decision_review_path,
                "decision source review",
            )
            decision_review = validate_review(
                decision_review_path,
                rule_pack_ids=profile["project"]["rule_packs"],
                strict_trust=True,
                repository_root=project_root,
                require_current_selection=True,
            )
        else:
            design_brief_path = Path(args.design_brief)
            if not design_brief_path.is_absolute():
                design_brief_path = project_root / design_brief_path
            design_brief_path = require_within_root(
                project_root,
                design_brief_path,
                "decision source design brief",
            )
            brief = validate_design_brief(
                design_brief_path,
                repository_root=project_root,
            )
            if brief["brief"]["status"] != "approved":
                raise ArchitectureError(
                    "Greenfield decision bindings require an approved Design Brief"
                )
        if args.knowledge_selection:
            selection_path: Path | None = Path(args.knowledge_selection)
        elif decision_review is not None and decision_review["schema_version"] == "1.2":
            selection_path = Path(decision_review["knowledge_selection"]["path"])
        else:
            selection_path = None
        if design_brief_path is not None and selection_path is None:
            raise ArchitectureError(
                "Greenfield decision bindings require --knowledge-selection"
            )
        if selection_path is not None:
            if not selection_path.is_absolute():
                selection_path = project_root / selection_path
            selection_path = require_within_root(
                project_root,
                selection_path,
                "decision knowledge selection",
            )
            facts_binding = profile["project"].get("repository_facts")
            selection_facts_path = (
                require_within_root(
                    project_root,
                    project_root / facts_binding["path"],
                    "profile.project.repository_facts.path",
                )
                if facts_binding is not None
                else None
            )
            selection = validate_knowledge_selection_artifact(
                selection_path,
                facts_path=selection_facts_path,
                profile_path=project_root / ".architecture" / "profile.yaml",
                require_current_runtime=True,
            )
            binding_result = {
                "schema_version": (
                    "1.4"
                    if design_brief_path is not None
                    and brief["schema_version"] == "1.1"
                    else "1.3"
                    if design_brief_path is not None
                    else "1.2"
                ),
                "knowledge_selection_path": selection_path.relative_to(
                    project_root
                ).as_posix(),
                "knowledge_selection_sha256": file_sha256(selection_path),
                "knowledge_snapshot": [
                    {
                        "id": item["id"],
                        "version": item["version"],
                        "sha256": item["sha256"],
                    }
                    for item in selection["selection"]
                ],
            }
            if design_brief_path is not None:
                binding_result.update(
                    {
                        "decision_kind": "greenfield",
                        **(
                            {"architecture_intent": "target-architecture"}
                            if brief["schema_version"] == "1.1"
                            else {}
                        ),
                        "source_context": design_brief_path.relative_to(
                            project_root
                        ).as_posix(),
                        "source_context_sha256": file_sha256(design_brief_path),
                    }
                )
            else:
                if decision_review is None or decision_review_path is None:
                    raise ArchitectureError("Missing decision source review")
                binding_result.update(
                    {
                        "source_review": decision_review["review"]["id"],
                        "source_review_sha256": file_sha256(decision_review_path),
                    }
                )
        else:
            if decision_review is None or decision_review_path is None:
                raise ArchitectureError("Missing decision source review")
            binding_result = {
                "schema_version": "1.1",
                "source_review": decision_review["review"]["id"],
                "source_review_sha256": file_sha256(decision_review_path),
                "knowledge_snapshot": decision_knowledge_snapshot(),
            }
        print(
            json.dumps(
                binding_result,
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "benchmark-score":
        result = score_benchmark(
            Path(args.ground_truth).resolve(),
            Path(args.run).resolve(),
            runtime_verification=args.runtime_verification,
            artifact_commit=args.artifact_commit,
        )
        rendered = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(f"{rendered}\n", encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "gate":
        gate_review_path = Path(args.review) if args.review else None
        if args.decision and args.portfolio:
            raise ArchitectureError("--decision cannot be combined with --portfolio")
        if args.portfolio:
            result = gate_portfolio(
                Path(args.portfolio),
                gate_review_path,
                mode=args.stage,
                base_commit=args.base_commit,
            )
        elif args.decision:
            result = gate_greenfield(
                Path(args.project or "."),
                Path(args.decision),
                mode=args.stage,
                base_commit=args.base_commit,
            )
        else:
            result = gate_project(
                Path(args.project or "."),
                gate_review_path,
                mode=args.stage,
                base_commit=args.base_commit,
            )
        if args.json_output:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print_gate_result(result)
        if args.sarif_output:
            sarif_path = args.sarif_output.expanduser().resolve()
            sarif_path.parent.mkdir(parents=True, exist_ok=True)
            sarif_path.write_text(
                json.dumps(
                    gate_result_to_sarif(result),
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        return 0 if result["status"] == "pass" else 1
    raise ArchitectureError(f"Unknown command: {args.command}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except (
        ArchitectureError,
        InspectionError,
        KnowledgeError,
        ProfileBuildError,
        SelectionError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
