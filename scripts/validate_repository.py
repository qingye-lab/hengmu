#!/usr/bin/env python3
"""Validate the plugin repository's static contracts."""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

EXPECTED_SKILLS = (
    "ai-agent-architecture-audit",
    "architecture-finding-verifier",
    "architecture-quality-gate",
    "architecture-remediation-planner",
    "architecture-solution-advisor",
    "hengmu",
    "mobile-architecture-audit",
    "portfolio-architecture-audit",
    "project-architecture-audit",
)
EVAL_KINDS = ("direct", "indirect", "incomplete", "negative", "edge")
REQUIRED_FILES = (
    ".codex-plugin/plugin.json",
    "plugin.json",
    ".architecture/baseline.yaml",
    ".architecture/constraints.md",
    ".architecture/critical-flows.md",
    ".architecture/evidence-providers.yaml",
    ".architecture/gate-policy.yaml",
    ".architecture/profile.yaml",
    ".architecture/reviews/README.md",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "LICENSE",
    "maintainer/skills/architecture-knowledge-curator/SKILL.md",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
    "docs/evaluation.md",
    "docs/governance-modes.md",
    "docs/assurance-model.md",
    "docs/compatibility.md",
    "docs/host-compatibility.md",
    "docs/comprehensive-review-implementation.md",
    "docs/knowledge-authoring.md",
    "docs/releasing.md",
    "docs/migrating-to-0.2.md",
    "docs/migrating-to-0.3.md",
    "docs/migrating-to-0.4.md",
    "docs/migrating-to-0.4.2.md",
    "docs/migrating-to-1.0.md",
    "docs/target-architecture.md",
    "docs/target-architecture-implementation.md",
    "resources/templates/evolution-assessment.md",
    "evals/cases.yaml",
    "evals/artifact-validity.yaml",
    "evals/decision-quality.yaml",
    "evals/false-positive.yaml",
    "evals/knowledge-selection.yaml",
    "evals/routing.yaml",
    "benchmarks/ground-truth.yaml",
    "benchmarks/run-template.yaml",
    "benchmarks/ablation/context-manifest.yaml",
    "benchmarks/ablation/tool-description.md",
    "benchmarks/ablation/skills/ai-agent-architecture-audit.md",
    "benchmarks/ablation/skills/architecture-finding-verifier.md",
    "benchmarks/ablation/skills/architecture-solution-advisor.md",
    "benchmarks/ablation/skills/project-architecture-audit.md",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements-dev.lock",
    "requirements-runtime.lock",
    "requirements.txt",
    "scripts/generate_sbom.py",
    "scripts/check_changed_coverage.py",
    "scripts/evaluate_ci_gate.py",
    "scripts/publish_release.py",
    "scripts/smoke_test_package.py",
    "scripts/curate_golden_knowledge.py",
    "scripts/codex_benchmark_adapter.py",
    "scripts/audit_licenses.py",
    "scripts/run_behavior_benchmark.py",
    "scripts/verify_checksum.py",
    "resources/knowledge/manifest.yaml",
    "resources/schemas/knowledge-entry.schema.json",
    "resources/schemas/architecture-design-brief.schema.json",
    "resources/schemas/benchmark.schema.json",
    "resources/schemas/benchmark-context-manifest.schema.json",
    "resources/schemas/benchmark-observation.schema.json",
    "resources/schemas/knowledge-context.schema.json",
    "resources/schemas/knowledge-manifest.schema.json",
    "resources/schemas/knowledge-selection.schema.json",
    "resources/schemas/repository-facts.schema.json",
    "resources/schemas/selector-source.schema.json",
    "resources/schemas/governance-run-manifest.schema.json",
    "resources/selector-source.json",
    "resources/scripts/build_project_profile.py",
    "resources/scripts/artifact_types.py",
    "resources/scripts/fingerprint_artifact.py",
    "resources/scripts/inspect_repository.py",
    "resources/scripts/knowledge_model.py",
    "resources/scripts/migrate_artifacts.py",
    "resources/scripts/select_knowledge.py",
    "resources/scripts/validate_coverage.py",
    "resources/scripts/validate_knowledge.py",
    "resources/templates/governance-run-manifest.yaml",
    "resources/templates/knowledge-context.yaml",
    "third_party/PAAD-MIT.txt",
    ".github/dependabot.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    ".github/workflows/knowledge-freshness.yml",
    ".github/workflows/pages.yml",
    ".github/workflows/release.yml",
    "index.html",
    "robots.txt",
    "sitemap.xml",
    "site/i18n.json",
    "site/main.js",
    "site/styles.css",
)
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\."
    r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
MARKDOWN_LINK_RE = re.compile(
    r"!?\[[^\]]*]\("
    r"(?P<target><[^>]+>|[^)\s]+)"
    r"(?:\s+[\"'][^\"']*[\"'])?"
    r"\)"
)
RESOURCE_REF_RE = re.compile(r"`(?P<target>\.\./\.\./resources/[^`\s]+)`")
REFERENCE_REF_RE = re.compile(
    r"`(?P<target>\.\./(?:schemas|scripts|templates)/[^`\s]+)`"
)
GITHUB_ACTION_USE_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<action>[^@\s#]+)@(?P<ref>[^\s#]+)",
    re.MULTILINE,
)
HTML_RESOURCE_RE = re.compile(
    r"\b(?:href|src|data-image-en|data-image-zh)=[\"'](?P<target>[^\"']+)[\"']"
)
HTML_SCRIPT_RE = re.compile(r"<script\b[^>]*\bsrc=[\"'](?P<target>[^\"']+)[\"']")
HTML_I18N_RE = re.compile(
    r"\bdata-i18n(?:-aria|-alt|-content)?=[\"'](?P<key>[^\"']+)[\"']"
)
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SITE_URL = "https://qingye-lab.github.io/hengmu/"
AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PORTABLE_MANIFEST_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
FORBIDDEN_MARKERS = (
    "[" + "TODO:",
    "OWNER" + "/REPOSITORY",
    "Local " + "developer",
)
TEXT_SUFFIXES = {
    ".json",
    ".css",
    ".html",
    ".js",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except FileNotFoundError:
        errors.append(f"missing JSON file: {path}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON in {path}: {exc}")
        return None
    except ValueError as exc:
        errors.append(f"invalid JSON in {path}: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"expected a JSON object in {path}")
        return None
    return payload


def load_yaml(path: Path, errors: list[str]) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing YAML file: {path}")
    except yaml.YAMLError as exc:
        errors.append(f"invalid YAML in {path}: {exc}")
    return None


def require_string(
    payload: dict[str, Any],
    key: str,
    source: str,
    errors: list[str],
) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{source}.{key} must be a non-empty string")
        return None
    return value


def validate_manifest(root: Path, errors: list[str]) -> dict[str, Any] | None:
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = load_json(manifest_path, errors)
    if manifest is None:
        return None

    require_string(manifest, "name", "plugin", errors)
    version = require_string(manifest, "version", "plugin", errors)
    require_string(manifest, "description", "plugin", errors)
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        errors.append(f"plugin.version {version!r} is not strict Semantic Versioning")
    if manifest.get("skills") != "./skills/":
        errors.append("plugin.skills must be './skills/'")
    if manifest.get("license") != "MIT":
        errors.append("plugin.license must be 'MIT'")

    author = manifest.get("author")
    if not isinstance(author, dict):
        errors.append("plugin.author must be an object")
    else:
        author_name = require_string(author, "name", "plugin.author", errors)
        if author_name in {"Your Name", "Local " + "developer"}:
            errors.append("plugin.author.name must identify a real maintainer group")

    keywords = manifest.get("keywords")
    if (
        not isinstance(keywords, list)
        or len(keywords) < 3
        or not all(isinstance(item, str) and item.strip() for item in keywords)
    ):
        errors.append("plugin.keywords must contain at least three non-empty strings")

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append("plugin.interface must be an object")
        return manifest
    for key in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
    ):
        require_string(interface, key, "plugin.interface", errors)

    capabilities = interface.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(isinstance(item, str) and item.strip() for item in capabilities)
    ):
        errors.append("plugin.interface.capabilities must be a non-empty string array")

    prompts = interface.get("defaultPrompt")
    if not isinstance(prompts, list) or not 1 <= len(prompts) <= 3:
        errors.append(
            "plugin.interface.defaultPrompt must contain one to three prompts"
        )
    else:
        for index, prompt in enumerate(prompts):
            if not isinstance(prompt, str) or not prompt.strip():
                errors.append(
                    "plugin.interface.defaultPrompt"
                    f"[{index}] must be a non-empty string"
                )
            elif len(prompt) > 128:
                errors.append(
                    f"plugin.interface.defaultPrompt[{index}] exceeds 128 characters"
                )
    return manifest


def validate_portable_manifest(
    root: Path,
    codex_manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    """Validate the checked-in Agent Plugins discovery and identity contract."""

    manifest_path = root / "plugin.json"
    manifest = load_json(manifest_path, errors)
    if manifest is None:
        return
    unknown = sorted(set(manifest) - PORTABLE_MANIFEST_FIELDS)
    if unknown:
        errors.append(
            "portable plugin.json has unknown top-level fields: " + ", ".join(unknown)
        )
    if manifest.get("$schema") != AGENT_PLUGINS_SCHEMA:
        errors.append("portable plugin.json has the wrong Agent Plugins $schema")
    version = require_string(manifest, "version", "portable plugin", errors)
    description = require_string(manifest, "description", "portable plugin", errors)
    require_string(manifest, "name", "portable plugin", errors)
    if version is not None and SEMVER_RE.fullmatch(version) is None:
        errors.append(
            f"portable plugin.version {version!r} is not strict Semantic Versioning"
        )
    if description is not None and "for Codex" in description:
        errors.append("portable plugin.description must be host-neutral")
    keywords = manifest.get("keywords")
    if (
        not isinstance(keywords, list)
        or "agent-plugins" not in keywords
        or not all(isinstance(item, str) and item.strip() for item in keywords)
    ):
        errors.append(
            "portable plugin.keywords must contain agent-plugins and only "
            "non-empty strings"
        )
    if "skills" in manifest or "interface" in manifest:
        errors.append(
            "portable plugin.json must use fixed Agent Plugins discovery, not "
            "Codex-only skills or interface fields"
        )
    if codex_manifest is not None:
        for key in (
            "name",
            "version",
            "author",
            "homepage",
            "repository",
            "license",
        ):
            if manifest.get(key) != codex_manifest.get(key):
                errors.append(
                    f"portable plugin.{key} must match .codex-plugin/plugin.json"
                )


def validate_selector_source(
    root: Path,
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    source_path = root / "resources" / "selector-source.json"
    schema_path = root / "resources" / "schemas" / "selector-source.schema.json"
    source = load_json(source_path, errors)
    schema = load_json(schema_path, errors)
    if source is None or schema is None:
        return
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )
    for error in validator.iter_errors(source):
        location = ".".join(str(part) for part in error.absolute_path)
        errors.append(
            f"{source_path} does not match selector-source.schema.json"
            f"{f' at {location}' if location else ''}: {error.message}"
        )
    if manifest is not None and (
        source.get("repository") != manifest.get("repository")
        or source.get("plugin_version") != manifest.get("version")
    ):
        errors.append(
            "resources/selector-source.json repository/version must match plugin.json"
        )


def split_frontmatter(path: Path, errors: list[str]) -> tuple[Any, str] | None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        errors.append(f"{path} must start with YAML frontmatter")
        return None
    try:
        closing = lines.index("---", 1)
    except ValueError:
        errors.append(f"{path} has unclosed YAML frontmatter")
        return None
    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as exc:
        errors.append(f"invalid frontmatter in {path}: {exc}")
        return None
    return frontmatter, "\n".join(lines[closing + 1 :]).strip()


def validate_skill(root: Path, name: str, errors: list[str]) -> None:
    skill_root = root / "skills" / name
    skill_path = skill_root / "SKILL.md"
    if not skill_path.is_file():
        errors.append(f"missing Skill instructions: {skill_path}")
        return

    line_count = len(skill_path.read_text(encoding="utf-8").splitlines())
    if line_count >= 500:
        errors.append(f"{skill_path} has {line_count} lines; it must stay below 500")

    parsed = split_frontmatter(skill_path, errors)
    if parsed is None:
        return
    frontmatter, body = parsed
    if not isinstance(frontmatter, dict):
        errors.append(f"{skill_path} frontmatter must be a mapping")
    else:
        if set(frontmatter) != {"name", "description"}:
            errors.append(
                f"{skill_path} frontmatter keys must be exactly name and description"
            )
        if frontmatter.get("name") != name:
            errors.append(f"{skill_path} name must match its directory")
        description = frontmatter.get("description")
        if not isinstance(description, str) or len(description.strip()) < 80:
            errors.append(f"{skill_path} description is too short to route reliably")
        elif len(description) > 1024:
            errors.append(f"{skill_path} description exceeds 1,024 characters")
    if not body:
        errors.append(f"{skill_path} has no instruction body")

    ui_path = skill_root / "agents" / "openai.yaml"
    ui = load_yaml(ui_path, errors)
    if not isinstance(ui, dict) or not isinstance(ui.get("interface"), dict):
        errors.append(f"{ui_path} must contain an interface mapping")
    else:
        interface = ui["interface"]
        display_name = interface.get("display_name")
        short_description = interface.get("short_description")
        default_prompt = interface.get("default_prompt")
        if not isinstance(display_name, str) or not display_name.strip():
            errors.append(f"{ui_path} interface.display_name is required")
        if (
            not isinstance(short_description, str)
            or not 25 <= len(short_description) <= 64
        ):
            errors.append(
                f"{ui_path} interface.short_description must be 25-64 characters"
            )
        if not isinstance(default_prompt, str) or f"${name}" not in default_prompt:
            errors.append(f"{ui_path} interface.default_prompt must mention ${name}")

    for match in RESOURCE_REF_RE.finditer(skill_path.read_text(encoding="utf-8")):
        referenced = (skill_root / match.group("target")).resolve()
        try:
            referenced.relative_to(root.resolve())
        except ValueError:
            errors.append(f"{skill_path} resource escapes the plugin: {referenced}")
            continue
        if not referenced.is_file():
            errors.append(f"{skill_path} references missing resource: {referenced}")


def validate_skills(root: Path, errors: list[str]) -> None:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        errors.append(f"missing skills directory: {skills_root}")
        return
    actual = sorted(path.name for path in skills_root.iterdir() if path.is_dir())
    expected = sorted(EXPECTED_SKILLS)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing:
            errors.append("missing Skills: " + ", ".join(missing))
        if extra:
            errors.append("unexpected directories under skills/: " + ", ".join(extra))
    for name in EXPECTED_SKILLS:
        validate_skill(root, name, errors)


def validate_reference_paths(root: Path, errors: list[str]) -> None:
    references_root = root / "resources" / "references"
    for path in sorted(references_root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in REFERENCE_REF_RE.finditer(text):
            referenced = (path.parent / match.group("target")).resolve()
            try:
                referenced.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{path} resource escapes the plugin: {referenced}")
                continue
            if not referenced.is_file():
                errors.append(f"{path} references missing resource: {referenced}")


def validate_markdown_links(root: Path, errors: list[str]) -> None:
    excluded = {".git", ".pytest_cache", ".venv", "dist"}
    for path in sorted(root.rglob("*.md")):
        if excluded.intersection(path.relative_to(root).parts):
            continue
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            raw_target = match.group("target")
            target = raw_target[1:-1] if raw_target.startswith("<") else raw_target
            parsed = urlparse(target)
            if parsed.scheme or target.startswith("#"):
                continue
            local_text = unquote(target.split("#", 1)[0])
            if not local_text:
                continue
            local_path = (path.parent / local_text).resolve()
            try:
                local_path.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{path} link escapes repository: {target}")
                continue
            if not local_path.exists():
                errors.append(f"{path} has broken local link: {target}")


def validate_schemas_and_yaml(root: Path, errors: list[str]) -> None:
    schemas_root = root / "resources" / "schemas"
    schema_paths = sorted(schemas_root.glob("*.schema.json"))
    if not schema_paths:
        errors.append(f"no JSON Schemas found in {schemas_root}")
    for path in schema_paths:
        payload = load_json(path, errors)
        if payload is None:
            continue
        try:
            Draft202012Validator.check_schema(payload)
        except SchemaError as exc:
            errors.append(f"invalid JSON Schema {path}: {exc.message}")

    yaml_roots = (
        root / ".architecture",
        root / "resources" / "templates",
        root / "resources" / "knowledge",
        root / "resources" / "rules",
        root / "resources" / "evidence-providers",
        root / "skills",
        root / "evals",
        root / "benchmarks",
        root / ".github",
    )
    for yaml_root in yaml_roots:
        if not yaml_root.exists():
            continue
        for pattern in ("*.yaml", "*.yml"):
            for path in sorted(yaml_root.rglob(pattern)):
                load_yaml(path, errors)

    instance_groups = (
        (
            root / "resources" / "knowledge",
            "*.yaml",
            "knowledge-catalog.schema.json",
        ),
        (
            root / "resources" / "rules",
            "*.yaml",
            "rule-pack.schema.json",
        ),
        (
            root / "resources" / "evidence-providers",
            "*.yaml",
            "evidence-provider.schema.json",
        ),
        (
            root / "benchmarks",
            "*.yaml",
            "benchmark.schema.json",
        ),
    )
    for instance_root, pattern, schema_name in instance_groups:
        schema = load_json(schemas_root / schema_name, errors)
        if schema is None:
            continue
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
        for path in sorted(instance_root.glob(pattern)):
            if (
                schema_name == "knowledge-catalog.schema.json"
                and path.name == "manifest.yaml"
            ):
                continue
            payload = load_yaml(path, errors)
            if payload is None:
                continue
            for error in sorted(
                validator.iter_errors(payload),
                key=lambda item: list(item.path),
            ):
                location = ".".join(str(part) for part in error.absolute_path)
                errors.append(
                    f"{path} does not match {schema_name}"
                    f"{f' at {location}' if location else ''}: {error.message}"
                )

    template_schemas = {
        "architecture-design-brief.yaml": "architecture-design-brief.schema.json",
        "architecture-decision.yaml": "architecture-decision.schema.json",
        "assetkeeper-profile.example.yaml": "project-profile.schema.json",
        "baseline.yaml": "baseline.schema.json",
        "cognera-profile.example.yaml": "project-profile.schema.json",
        "dependency-map.yaml": "dependency-map.schema.json",
        "evidence-providers.yaml": "evidence-provider-config.schema.json",
        "gate-policy.yaml": "gate-policy.schema.json",
        "governance-run-manifest.yaml": "governance-run-manifest.schema.json",
        "knowledge-context.yaml": "knowledge-context.schema.json",
        "knowledge-selection.yaml": "knowledge-selection.schema.json",
        "portfolio-gate-policy.yaml": "gate-policy.schema.json",
        "portfolio.yaml": "portfolio.schema.json",
        "profile.yaml": "project-profile.schema.json",
        "remediation-plan.yaml": "remediation-plan.schema.json",
        "review.yaml": "review.schema.json",
        "risk-acceptances.yaml": "risk-acceptance.schema.json",
        "shared-capabilities.yaml": "shared-capabilities.schema.json",
        "technology-catalog.yaml": "technology-catalog.schema.json",
    }
    for template_name, schema_name in template_schemas.items():
        template_path = root / "resources" / "templates" / template_name
        payload = load_yaml(template_path, errors)
        schema = load_json(schemas_root / schema_name, errors)
        if payload is None or schema is None:
            continue
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
        for error in validator.iter_errors(payload):
            errors.append(
                f"{template_path} does not match {schema_name}: {error.message}"
            )

    manifest_path = root / "resources" / "knowledge" / "manifest.yaml"
    manifest = load_yaml(manifest_path, errors)
    manifest_schema = load_json(
        schemas_root / "knowledge-manifest.schema.json",
        errors,
    )
    if isinstance(manifest, dict) and manifest_schema is not None:
        validator = Draft202012Validator(
            manifest_schema,
            format_checker=FormatChecker(),
        )
        for error in validator.iter_errors(manifest):
            errors.append(
                f"{manifest_path} does not match knowledge-manifest.schema.json: "
                f"{error.message}"
            )

    benchmark = load_yaml(root / "benchmarks" / "ground-truth.yaml", errors)
    if isinstance(benchmark, dict):
        for case in benchmark.get("cases", []):
            fixture = root / case.get("fixture", "")
            try:
                fixture.resolve().relative_to(root.resolve())
            except ValueError:
                errors.append(f"benchmark fixture escapes repository: {fixture}")
                continue
            if not fixture.is_dir():
                errors.append(f"benchmark fixture is missing: {fixture}")


def validate_benchmark_ablation(root: Path, errors: list[str]) -> None:
    """Check that the three benchmark treatments remain comparable by design."""
    manifest_path = root / "benchmarks" / "ablation" / "context-manifest.yaml"
    schema_path = (
        root / "resources" / "schemas" / "benchmark-context-manifest.schema.json"
    )
    manifest = load_yaml(manifest_path, errors)
    schema = load_json(schema_path, errors)
    if not isinstance(manifest, dict) or schema is None:
        return
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for error in sorted(
        validator.iter_errors(manifest),
        key=lambda item: list(item.path),
    ):
        location = ".".join(str(part) for part in error.absolute_path)
        errors.append(
            f"{manifest_path} does not match benchmark-context-manifest.schema.json"
            f"{f' at {location}' if location else ''}: {error.message}"
        )
    treatments = manifest.get("treatments")
    truth = load_yaml(root / "benchmarks" / "ground-truth.yaml", errors)
    if not isinstance(treatments, list) or not isinstance(truth, dict):
        return
    benchmark_skills: set[str] = set()
    for case in truth.get("cases", []):
        if not isinstance(case, dict):
            continue
        skill = case.get("skill")
        if isinstance(skill, str):
            benchmark_skills.add(skill)
    expected = {
        (condition, skill)
        for condition in ("base", "full", "compressed")
        for skill in benchmark_skills
    }
    actual = {
        (item.get("condition"), item.get("skill"))
        for item in treatments
        if isinstance(item, dict)
    }
    if len(actual) != len(treatments) or actual != expected:
        errors.append(
            f"{manifest_path} must declare exactly one Base/Full/Compressed "
            "treatment for every benchmark Skill"
        )
        return
    by_key = {
        (item["condition"], item["skill"]): item
        for item in treatments
        if isinstance(item, dict)
    }
    for skill in sorted(benchmark_skills):
        base = by_key[("base", skill)]
        if base["knowledge_basis"] != "none" or any(
            base[field]
            for field in ("skill_metadata", "skill_body", "references", "knowledge")
        ):
            errors.append(
                f"{manifest_path} Base treatment for {skill} must not load "
                "Skill, reference, or Knowledge content"
            )
        full = by_key[("full", skill)]
        compressed = by_key[("compressed", skill)]
        if (
            full["knowledge_basis"] != "workflow-required"
            or compressed["knowledge_basis"] != "workflow-required"
        ):
            errors.append(
                f"{manifest_path} Full and Compressed treatments for {skill} "
                "must declare workflow-required Knowledge"
            )
        if full["knowledge"] != compressed["knowledge"]:
            errors.append(
                f"{manifest_path} Full and Compressed treatments for {skill} "
                "must use identical Knowledge inputs"
            )


def validate_evals(root: Path, errors: list[str]) -> None:
    path = root / "evals" / "cases.yaml"
    payload = load_yaml(path, errors)
    if not isinstance(payload, dict):
        errors.append(f"{path} must contain a mapping")
        return
    if payload.get("schema_version") != "1.0":
        errors.append(f"{path} schema_version must be '1.0'")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        errors.append(f"{path} cases must be an array")
        return

    ids: set[str] = set()
    coverage: dict[tuple[str, str], int] = {}
    for index, case in enumerate(cases):
        source = f"{path} cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{source} must be a mapping")
            continue
        case_id = case.get("id")
        skill = case.get("skill")
        kind = case.get("kind")
        prompt = case.get("prompt")
        expected = case.get("expected")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{source}.id must be a non-empty string")
        elif case_id in ids:
            errors.append(f"{source}.id is duplicated: {case_id}")
        else:
            ids.add(case_id)
        if skill not in EXPECTED_SKILLS:
            errors.append(f"{source}.skill is unknown: {skill!r}")
        if kind not in EVAL_KINDS:
            errors.append(f"{source}.kind is unknown: {kind!r}")
        if isinstance(skill, str) and isinstance(kind, str):
            key = (skill, kind)
            coverage[key] = coverage.get(key, 0) + 1
        if not isinstance(prompt, str) or len(prompt.strip()) < 20:
            errors.append(f"{source}.prompt must be a realistic prompt")
        if not isinstance(expected, dict):
            errors.append(f"{source}.expected must be a mapping")
            continue
        activates = expected.get("activates")
        outcome = expected.get("outcome")
        if not isinstance(activates, bool):
            errors.append(f"{source}.expected.activates must be a boolean")
        if not isinstance(outcome, str) or len(outcome.strip()) < 20:
            errors.append(
                f"{source}.expected.outcome must describe observable behavior"
            )
        if kind == "negative" and activates is not False:
            errors.append(f"{source} negative cases must not activate")
        if kind in {"direct", "indirect"} and activates is not True:
            errors.append(f"{source} direct and indirect cases must activate")

    expected_pairs = {(skill, kind) for skill in EXPECTED_SKILLS for kind in EVAL_KINDS}
    actual_pairs = set(coverage)
    missing = sorted(expected_pairs - actual_pairs)
    extra = sorted(actual_pairs - expected_pairs)
    duplicates = sorted(pair for pair, count in coverage.items() if count != 1)
    if missing:
        errors.append(
            "eval coverage is missing: "
            + ", ".join(f"{skill}/{kind}" for skill, kind in missing)
        )
    if extra:
        errors.append(
            "eval coverage has unknown pairs: "
            + ", ".join(f"{skill}/{kind}" for skill, kind in extra)
        )
    if duplicates:
        errors.append(
            "eval coverage must have exactly one case per pair: "
            + ", ".join(f"{skill}/{kind}" for skill, kind in duplicates)
        )


def validate_supplemental_evals(root: Path, errors: list[str]) -> None:
    names = (
        "routing.yaml",
        "knowledge-selection.yaml",
        "decision-quality.yaml",
        "false-positive.yaml",
        "artifact-validity.yaml",
    )
    payloads: dict[str, dict[str, Any]] = {}
    for name in names:
        path = root / "evals" / name
        payload = load_yaml(path, errors)
        if not isinstance(payload, dict):
            errors.append(f"{path} must contain a mapping")
            continue
        payloads[name] = payload
        if payload.get("schema_version") != "1.0":
            errors.append(f"{path} schema_version must be '1.0'")
        cases = payload.get("cases")
        if not isinstance(cases, list) or not cases:
            errors.append(f"{path} cases must be a non-empty array")
            continue
        case_ids = [
            case.get("id")
            for case in cases
            if isinstance(case, dict) and isinstance(case.get("id"), str)
        ]
        if len(case_ids) != len(cases):
            errors.append(f"{path} every case requires a string id")
        if len(case_ids) != len(set(case_ids)):
            errors.append(f"{path} contains duplicate case IDs")

    base = load_yaml(root / "evals" / "cases.yaml", errors)
    routing = payloads.get("routing.yaml")
    if isinstance(base, dict) and isinstance(routing, dict):
        base_skills = {
            case["id"]: case["skill"]
            for case in base.get("cases", [])
            if isinstance(case, dict)
            and isinstance(case.get("id"), str)
            and isinstance(case.get("skill"), str)
        }
        for case in routing.get("cases", []):
            if not isinstance(case, dict):
                continue
            case_id = case.get("id")
            expected_skill = case.get("expected_skill")
            if case_id not in base_skills:
                errors.append(
                    f"evals/routing.yaml references unknown base case {case_id!r}"
                )
            elif expected_skill != base_skills[case_id]:
                errors.append(
                    f"evals/routing.yaml {case_id!r} expected_skill does not "
                    "match evals/cases.yaml"
                )

    selection = payloads.get("knowledge-selection.yaml")
    if isinstance(selection, dict):
        for case in selection.get("cases", []):
            if not isinstance(case, dict):
                continue
            includes = set(case.get("must_include", []))
            excludes = set(case.get("must_exclude", []))
            overlap = sorted(includes & excludes)
            if overlap:
                errors.append(
                    f"evals/knowledge-selection.yaml {case.get('id')!r} "
                    "both includes and excludes: " + ", ".join(overlap)
                )


def validate_dependency_locks(root: Path, errors: list[str]) -> None:
    for name in ("requirements-runtime.lock", "requirements-dev.lock"):
        path = root / name
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        if "pypi.tuna.tsinghua.edu.cn" in text:
            errors.append(f"{path} contains a maintainer-local package index")
        for line in text.splitlines():
            if line.startswith("--index-url") and "pypi.org/simple" not in line:
                errors.append(f"{path} must use the public PyPI index")
        package_lines = [
            line
            for line in text.splitlines()
            if line and not line[0].isspace() and "==" in line
        ]
        if not package_lines:
            errors.append(f"{path} contains no exact package pins")
        if "--hash=sha256:" not in text:
            errors.append(f"{path} contains no package hashes")
        for line in package_lines:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+==[^\s\\]+(?: \\)?", line):
                errors.append(f"{path} has a non-exact package line: {line}")


def validate_github_action_pins(root: Path, errors: list[str]) -> None:
    paths = sorted((root / ".github" / "workflows").glob("*.yml"))
    paths.extend(sorted((root / ".github" / "workflows").glob("*.yaml")))
    template = root / "resources" / "templates" / "github-architecture-gate.yml"
    if template.is_file():
        paths.append(template)
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in GITHUB_ACTION_USE_RE.finditer(text):
            action = match.group("action")
            action_ref = match.group("ref")
            if action.startswith("./") or action.startswith("docker://"):
                continue
            if action.split("/", 1)[0] not in {"actions", "github"}:
                errors.append(f"{path} must use a GitHub-owned action; found {action}")
            if COMMIT_SHA_RE.fullmatch(action_ref) is None:
                errors.append(
                    f"{path} must pin {action} to a 40-character commit SHA; "
                    f"found {action_ref!r}"
                )


def validate_site(root: Path, errors: list[str]) -> None:
    index_path = root / "index.html"
    i18n_path = root / "site" / "i18n.json"
    script_path = root / "site" / "main.js"
    workflow_path = root / ".github" / "workflows" / "pages.yml"
    try:
        index_text = index_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append("site is missing index.html")
        return

    if f'<link rel="canonical" href="{SITE_URL}">' not in index_text:
        errors.append("site canonical URL must use the production GitHub Pages origin")
    if f'<meta property="og:url" content="{SITE_URL}">' not in index_text:
        errors.append("site Open Graph URL must use the production GitHub Pages origin")

    scripts = HTML_SCRIPT_RE.findall(index_text)
    if scripts != ["./site/main.js"]:
        external = [target for target in scripts if target != "./site/main.js"]
        if external:
            errors.append(
                "site must not load an external script: " + ", ".join(external)
            )
        else:
            errors.append("site must load exactly one local script: ./site/main.js")

    for match in HTML_RESOURCE_RE.finditer(index_text):
        target = match.group("target")
        parsed = urlparse(target)
        if parsed.scheme or target.startswith("#"):
            continue
        relative = unquote(target.split("#", 1)[0]).removeprefix("./")
        if not relative:
            continue
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            errors.append(f"site resource escapes repository: {target}")
            continue
        if not path.is_file():
            errors.append(f"site references missing local resource: {target}")

    payload = load_json(i18n_path, errors)
    if payload is not None:
        if set(payload) != {"en", "zh-CN"}:
            errors.append("site/i18n.json must contain exactly en and zh-CN")
        else:
            english = payload["en"]
            chinese = payload["zh-CN"]
            if not isinstance(english, dict) or not isinstance(chinese, dict):
                errors.append("site locale entries must be JSON objects")
            else:
                english_keys = set(english)
                chinese_keys = set(chinese)
                if english_keys != chinese_keys:
                    errors.append("site locale keys differ between en and zh-CN")
                required_keys = set(HTML_I18N_RE.findall(index_text))
                required_keys.update(
                    {
                        "closeMenu",
                        "copied",
                        "copiedStatus",
                        "copyFailed",
                        "openMenu",
                        "pageDescription",
                        "pageTitle",
                    }
                )
                missing = sorted(required_keys - english_keys)
                if missing:
                    errors.append(
                        "site/i18n.json is missing required keys: " + ", ".join(missing)
                    )
                empty = sorted(
                    key
                    for key in english_keys & chinese_keys
                    if not isinstance(english[key], str)
                    or not english[key].strip()
                    or not isinstance(chinese[key], str)
                    or not chinese[key].strip()
                )
                if empty:
                    errors.append(
                        "site/i18n.json contains empty locale values: "
                        + ", ".join(empty)
                    )

    try:
        script_text = script_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append("site is missing site/main.js")
    else:
        for forbidden in ("innerHTML", "document.write", "eval("):
            if forbidden in script_text:
                errors.append(
                    f"site/main.js contains forbidden DOM primitive {forbidden}"
                )

    try:
        workflow_text = workflow_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append("site is missing .github/workflows/pages.yml")
    else:
        for required in (
            "actions/configure-pages@",
            "actions/upload-pages-artifact@",
            "actions/deploy-pages@",
            "id-token: write",
            "pages: write",
            "touch _site/.nojekyll",
            "GitHub Pages artifact must not contain symlinks",
        ):
            if required not in workflow_text:
                errors.append(
                    f"Pages workflow is missing required contract: {required}"
                )


def validate_repository_hygiene(root: Path, errors: list[str]) -> None:
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing required repository file: {relative}")

    runtime_roots = (
        root / ".codex-plugin",
        root / "resources",
        root / "skills",
    )
    for runtime_root in runtime_roots:
        if not runtime_root.exists():
            continue
        for path in runtime_root.rglob("*"):
            if path.is_symlink():
                errors.append(f"runtime tree must not contain symlinks: {path}")

    excluded = {".git", ".pytest_cache", ".venv", "dist", "third_party"}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if excluded.intersection(path.relative_to(root).parts):
            continue
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                errors.append(f"{path} contains forbidden placeholder {marker!r}")


def validate_changelog(
    root: Path,
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    if manifest is None:
        return
    version = manifest.get("version")
    if not isinstance(version, str):
        return
    changelog_path = root / "CHANGELOG.md"
    try:
        changelog = changelog_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    if f"## [{version}]" not in changelog:
        errors.append(f"CHANGELOG.md has no section for plugin version {version}")


def validate_tool_version(
    root: Path,
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    if manifest is None or not isinstance(manifest.get("version"), str):
        return
    path = root / "resources" / "scripts" / "architecture_tool.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (FileNotFoundError, SyntaxError) as exc:
        errors.append(f"cannot inspect architecture tool version: {exc}")
        return
    tool_version: str | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == "TOOL_VERSION"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            tool_version = node.value.value
            break
    if tool_version is None:
        errors.append(f"{path} must define a string TOOL_VERSION")
    elif tool_version != manifest["version"]:
        errors.append(
            f"architecture tool version {tool_version!r} does not match "
            f"plugin version {manifest['version']!r}"
        )


def validate_repository(root: Path) -> list[str]:
    root = root.expanduser().resolve()
    errors: list[str] = []
    validate_repository_hygiene(root, errors)
    manifest = validate_manifest(root, errors)
    validate_portable_manifest(root, manifest, errors)
    validate_selector_source(root, manifest, errors)
    validate_skills(root, errors)
    validate_reference_paths(root, errors)
    validate_markdown_links(root, errors)
    validate_schemas_and_yaml(root, errors)
    validate_benchmark_ablation(root, errors)
    validate_evals(root, errors)
    validate_supplemental_evals(root, errors)
    validate_dependency_locks(root, errors)
    validate_github_action_pins(root, errors)
    validate_site(root, errors)
    validate_changelog(root, manifest, errors)
    validate_tool_version(root, manifest, errors)
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Hengmu repository contracts."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repository root; defaults to the parent of scripts/.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.expanduser().resolve()
    errors = validate_repository(root)
    if errors:
        print(f"Repository validation failed with {len(errors)} error(s):")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    schema_count = len(list((root / "resources" / "schemas").glob("*.json")))
    template_count = len(list((root / "resources" / "templates").glob("*.yaml")))
    eval_payload = yaml.safe_load(
        (root / "evals" / "cases.yaml").read_text(encoding="utf-8")
    )
    print(
        "Repository validation passed: "
        f"{len(EXPECTED_SKILLS)} public Skills, "
        f"{len(eval_payload['cases'])} eval cases, "
        f"{schema_count} schemas, and {template_count} templates."
    )


if __name__ == "__main__":
    main()
