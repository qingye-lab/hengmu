#!/usr/bin/env python3
"""Read and validate Markdown architecture knowledge packs."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

SAFE_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

_KNOWLEDGE_TREE_CACHE: dict[
    tuple[Path, Path, str, str],
    tuple[dict[str, Any], dict[str, KnowledgeEntry]],
] = {}
_SCHEMA_CACHE: dict[tuple[Path, str], dict[str, Any]] = {}
_SCHEMA_VALIDATOR_CACHE: dict[tuple[Path, str], Draft202012Validator] = {}

SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
REQUIRED_SECTIONS = (
    "Problem and intent",
    "Mechanism",
    "Fit when",
    "Avoid when",
    "Required capabilities",
    "Benefits",
    "Costs and liabilities",
    "Failure modes",
    "Alternatives",
    "Migration and exit",
    "Evidence to inspect",
    "Evidence that changes the recommendation",
    "Quality trade-offs",
    "Volatile facts",
)
FORBIDDEN_MARKERS = ("TODO", "TBD", "placeholder", "fill this")
GOLDEN_KIND_SECTIONS = {
    "decision-guide": ("Options",),
    "technology-profile": ("Operating model", "Capability boundaries"),
    "reference-architecture": (
        "Components and responsibilities",
        "Data flow",
    ),
    "architecture-style": ("Operating model",),
    "pattern": ("Operating model",),
}
OPTION_FIELD_RE = re.compile(
    r"^- (Fit|Avoid|Cost|Failure):\s+\S",
    flags=re.MULTILINE,
)
CLAIM_RE = re.compile(r"^- (?P<id>[A-Z][A-Z0-9-]{1,31}):\s+\S", re.MULTILINE)


class KnowledgeError(RuntimeError):
    """Invalid knowledge contract or content."""


@dataclass(frozen=True)
class KnowledgeEntry:
    """One validated Markdown knowledge entry."""

    path: Path
    metadata: dict[str, Any]
    body: str
    sha256: str

    @property
    def id(self) -> str:
        return str(self.metadata["id"])


def _load_json(path: Path) -> dict[str, Any]:
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise KnowledgeError(f"Missing schema: {path}") from exc
    digest = hashlib.sha256(content).hexdigest()
    cache_key = (path, digest)
    cached = _SCHEMA_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        value = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise KnowledgeError(f"Invalid JSON schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise KnowledgeError(f"Expected object schema in {path}")
    _SCHEMA_CACHE[cache_key] = value
    return value


def _content_fingerprint(records: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for name, content in records:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _knowledge_input_fingerprint(
    knowledge_root: Path,
    schema_root: Path,
) -> str | None:
    """Hash every byte that contributes to a Markdown tree validation.

    The directory is intentionally scanned on every call.  A process-local
    cache must notice edits, additions, removals, and schema changes made
    after an earlier validation; filesystem timestamps are not sufficient for
    that trust boundary.
    """
    manifest_path = knowledge_root / "manifest.yaml"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = yaml.load(manifest_bytes.decode("utf-8"), Loader=SAFE_YAML_LOADER)
        if not isinstance(manifest, dict) or not isinstance(
            manifest.get("packs"), list
        ):
            return None
        records: list[tuple[str, bytes]] = [
            ("manifest.yaml", manifest_bytes),
        ]
        for schema_name in (
            "knowledge-manifest.schema.json",
            "knowledge-entry.schema.json",
        ):
            schema_path = schema_root / schema_name
            records.append((f"schema/{schema_name}", schema_path.read_bytes()))
        for pack in manifest["packs"]:
            if not isinstance(pack, dict) or not isinstance(pack.get("path"), str):
                return None
            pack_root = (knowledge_root / pack["path"]).resolve()
            try:
                pack_root.relative_to(knowledge_root)
            except ValueError:
                return None
            paths = sorted(pack_root.rglob("*.md")) if pack_root.is_dir() else []
            for path in paths:
                records.append(
                    (
                        path.relative_to(knowledge_root).as_posix(),
                        path.read_bytes(),
                    )
                )
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return _content_fingerprint(records)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=SAFE_YAML_LOADER)
    except FileNotFoundError as exc:
        raise KnowledgeError(f"Missing YAML file: {path}") from exc
    except yaml.YAMLError as exc:
        raise KnowledgeError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise KnowledgeError(f"Expected YAML mapping in {path}")
    return value


def _validate_schema(
    value: dict[str, Any],
    schema_path: Path,
    source: Path,
) -> None:
    try:
        schema_content = schema_path.read_bytes()
    except FileNotFoundError as exc:
        raise KnowledgeError(f"Missing schema: {schema_path}") from exc
    schema_digest = hashlib.sha256(schema_content).hexdigest()
    cache_key = (schema_path, schema_digest)
    validator = _SCHEMA_VALIDATOR_CACHE.get(cache_key)
    if validator is None:
        schema = _load_json(schema_path)
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
        _SCHEMA_VALIDATOR_CACHE[cache_key] = validator
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if not errors:
        return
    details = []
    for error in errors[:12]:
        location = ".".join(str(item) for item in error.absolute_path) or "<root>"
        details.append(f"{location}: {error.message}")
    if len(errors) > len(details):
        details.append(f"... and {len(errors) - len(details)} more")
    raise KnowledgeError(f"{source} failed schema validation: {'; '.join(details)}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_markdown_entry(path: Path) -> tuple[dict[str, Any], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise KnowledgeError(f"Missing knowledge entry: {path}") from exc
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise KnowledgeError(f"{path} must start with YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise KnowledgeError(f"{path} has no closing frontmatter delimiter") from exc
    try:
        metadata = yaml.load("\n".join(lines[1:closing]), Loader=SAFE_YAML_LOADER)
    except yaml.YAMLError as exc:
        raise KnowledgeError(f"Invalid frontmatter in {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise KnowledgeError(f"{path} frontmatter must be a mapping")
    body = "\n".join(lines[closing + 1 :]).strip()
    if not body:
        raise KnowledgeError(f"{path} has no Markdown body")
    return metadata, body


def _section_content(body: str, heading: str) -> str | None:
    pattern = re.compile(
        rf"^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(body)
    if match is None:
        return None
    return match.group("body").strip()


def validate_markdown_entry(
    path: Path,
    *,
    schema_root: Path,
    expected_kind: str,
    today: date,
) -> KnowledgeEntry:
    metadata, body = parse_markdown_entry(path)
    _validate_schema(
        metadata,
        schema_root / "knowledge-entry.schema.json",
        path,
    )
    if metadata["kind"] != expected_kind:
        raise KnowledgeError(
            f"{path} declares kind {metadata['kind']!r}; expected {expected_kind!r}"
        )
    if not body.startswith("# "):
        raise KnowledgeError(f"{path} body must start with one level-one title")
    for section in REQUIRED_SECTIONS:
        content = _section_content(body, section)
        if content is None:
            raise KnowledgeError(f"{path} is missing section '## {section}'")
        if len(content) < 3:
            raise KnowledgeError(f"{path} section '## {section}' is too shallow")
    lowered = body.lower()
    for marker in FORBIDDEN_MARKERS:
        if marker.lower() in lowered:
            raise KnowledgeError(f"{path} contains forbidden marker {marker!r}")
    reviewed = metadata["last_reviewed"]
    if isinstance(reviewed, str):
        reviewed_on = date.fromisoformat(reviewed)
    elif isinstance(reviewed, date):
        reviewed_on = reviewed
    else:
        raise KnowledgeError(f"{path} last_reviewed must be a date")
    if reviewed_on > today:
        raise KnowledgeError(
            f"{path} last_reviewed {reviewed_on.isoformat()} is in the future"
        )
    age = (today - reviewed_on).days
    if age > metadata["review_after_days"]:
        raise KnowledgeError(
            f"{path} is stale by {age - metadata['review_after_days']} day(s)"
        )
    for source in metadata["sources"]:
        if not source["url"].startswith("https://"):
            raise KnowledgeError(f"{path} source must use HTTPS: {source['url']}")
    if (
        metadata.get("status") == "active"
        and metadata.get("curation", {}).get("method") == "generated"
    ):
        raise KnowledgeError(f"{path} generated content cannot be active")
    if metadata.get("maturity") == "golden":
        for section in GOLDEN_KIND_SECTIONS.get(metadata["kind"], ()):
            content = _section_content(body, section)
            if content is None:
                raise KnowledgeError(
                    f"{path} golden {metadata['kind']} is missing section "
                    f"'## {section}'"
                )
        if metadata["kind"] == "decision-guide":
            options = _section_content(body, "Options") or ""
            option_blocks = re.split(r"^### ", options, flags=re.MULTILINE)[1:]
            if len(option_blocks) < 2:
                raise KnowledgeError(
                    f"{path} golden decision guide requires at least two named options"
                )
            for option in option_blocks:
                fields = {match.group(1) for match in OPTION_FIELD_RE.finditer(option)}
                missing_fields = sorted({"Fit", "Avoid", "Cost", "Failure"} - fields)
                if missing_fields:
                    title = option.splitlines()[0].strip()
                    raise KnowledgeError(
                        f"{path} option {title!r} is missing: "
                        + ", ".join(missing_fields)
                    )
        claim_content = _section_content(body, "Claim map")
        if claim_content is None:
            raise KnowledgeError(f"{path} golden entry is missing '## Claim map'")
        claim_ids = {match.group("id") for match in CLAIM_RE.finditer(claim_content)}
        if len(claim_ids) < 2:
            raise KnowledgeError(
                f"{path} golden entry requires at least two mapped claims"
            )
        supported: set[str] = set()
        for source in metadata["sources"]:
            source_claims = source.get("supports")
            if not source_claims:
                raise KnowledgeError(
                    f"{path} golden source {source['title']!r} has no supports mapping"
                )
            unknown_claims = sorted(set(source_claims) - claim_ids)
            if unknown_claims:
                raise KnowledgeError(
                    f"{path} source {source['title']!r} references unknown claims: "
                    + ", ".join(unknown_claims)
                )
            supported.update(source_claims)
        unsupported = sorted(claim_ids - supported)
        if unsupported:
            raise KnowledgeError(
                f"{path} has claims with no supporting source: "
                + ", ".join(unsupported)
            )
    return KnowledgeEntry(
        path=path,
        metadata=metadata,
        body=body,
        sha256=sha256_file(path),
    )


def validate_knowledge_tree(
    knowledge_root: Path,
    *,
    schema_root: Path,
    today: date | None = None,
) -> tuple[dict[str, Any], dict[str, KnowledgeEntry]]:
    knowledge_root = knowledge_root.resolve()
    schema_root = schema_root.resolve()
    evaluation_date = today or datetime.now(UTC).date()
    input_sha256 = _knowledge_input_fingerprint(knowledge_root, schema_root)
    cache_key = (
        knowledge_root,
        schema_root,
        evaluation_date.isoformat(),
        input_sha256 or "<unreadable-input>",
    )
    cached = _KNOWLEDGE_TREE_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    manifest_path = knowledge_root / "manifest.yaml"
    manifest = _load_yaml(manifest_path)
    _validate_schema(
        manifest,
        schema_root / "knowledge-manifest.schema.json",
        manifest_path,
    )
    pack_ids: set[str] = set()
    pack_paths: set[str] = set()
    entries: dict[str, KnowledgeEntry] = {}
    aliases: dict[str, str] = {}
    counts: dict[str, int] = {}
    for pack in manifest["packs"]:
        pack_id = pack["id"]
        pack_path = pack["path"]
        if pack_id in pack_ids:
            raise KnowledgeError(f"Duplicate knowledge pack ID {pack_id}")
        if pack_path in pack_paths:
            raise KnowledgeError(f"Duplicate knowledge pack path {pack_path}")
        pack_ids.add(pack_id)
        pack_paths.add(pack_path)
        root = (knowledge_root / pack_path).resolve()
        try:
            root.relative_to(knowledge_root)
        except ValueError as exc:
            raise KnowledgeError(
                f"Knowledge pack {pack_id} escapes the knowledge root"
            ) from exc
        paths = sorted(root.rglob("*.md")) if root.is_dir() else []
        if pack["required"] and not paths:
            raise KnowledgeError(f"Required knowledge pack {pack_id} has no entries")
        counts[pack_id] = len(paths)
        for path in paths:
            entry = validate_markdown_entry(
                path,
                schema_root=schema_root,
                expected_kind=pack["kind"],
                today=evaluation_date,
            )
            if entry.id in entries:
                raise KnowledgeError(
                    f"Duplicate knowledge ID {entry.id} in "
                    f"{entries[entry.id].path} and {path}"
                )
            entries[entry.id] = entry
            for alias in entry.metadata.get("legacy_ids", []):
                if alias in aliases:
                    raise KnowledgeError(
                        f"Knowledge alias {alias!r} maps to both "
                        f"{aliases[alias]} and {entry.id}"
                    )
                aliases[alias] = entry.id
    for entry in entries.values():
        unknown = sorted(set(entry.metadata["related"]) - set(entries))
        if unknown:
            raise KnowledgeError(
                f"{entry.path} references unknown related IDs: " + ", ".join(unknown)
            )
    golden = [
        (entry, re.findall(r"[a-z0-9][a-z0-9-]*", entry.body.lower()))
        for entry in entries.values()
        if entry.metadata.get("maturity") == "golden"
    ]
    for index, (left, left_tokens) in enumerate(golden):
        for right, right_tokens in golden[index + 1 :]:
            matcher = SequenceMatcher(
                None,
                left_tokens,
                right_tokens,
                autojunk=False,
            )
            # This upper bound can only rule out a match. Keep the exact ratio
            # and threshold authoritative for every remaining pair.
            if matcher.quick_ratio() < 0.88:
                continue
            similarity = matcher.ratio()
            if similarity >= 0.88:
                raise KnowledgeError(
                    f"Golden entries {left.id} and {right.id} are too similar "
                    f"({similarity:.3f}); replace shared template prose with "
                    "decision-specific mechanisms"
                )
    manifest["_validated_counts"] = counts
    manifest["_validated_at"] = evaluation_date.isoformat()
    result = (manifest, entries)
    if input_sha256 is not None:
        _KNOWLEDGE_TREE_CACHE[cache_key] = copy.deepcopy(result)
    return result


def knowledge_snapshot(entries: list[KnowledgeEntry]) -> list[dict[str, str]]:
    return [
        {
            "id": entry.id,
            "version": str(entry.metadata["version"]),
            "sha256": entry.sha256,
        }
        for entry in sorted(entries, key=lambda item: item.id)
    ]
