#!/usr/bin/env python3
"""Run benchmark cases through a caller-supplied agent command."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

DEFAULT_SKILL_VERSION = "1.3.0"
HARNESS_NAME = "hengmu-behavior-benchmark"
HARNESS_VERSION = "1.3.0"
MAX_ARTIFACT_TIMEOUT_SECONDS = 10 * 60 * 60
TREATMENT_CONDITIONS = ("base", "full", "compressed")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def relative_to_root(root: Path, path: Path, label: str) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} escapes repository root: {resolved}") from exc


def tree_manifest(path: Path) -> tuple[str, int]:
    records = []
    for child in (item for item in path.rglob("*") if item.is_file()):
        records.append(
            {
                "path": child.relative_to(path).as_posix(),
                "sha256": file_sha256(child),
            }
        )
    records.sort(key=lambda record: record["path"])
    return sha256_bytes(canonical_json(records).encode()), len(records)


def tree_bytes(path: Path) -> int:
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _text_parts(path: Path) -> tuple[str, str]:
    """Return the frontmatter and body characters of a Markdown skill asset."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".md" or not text.startswith("---\n"):
        return "", text
    closing = text.find("\n---\n", 4)
    if closing == -1:
        return "", text
    closing += len("\n---\n")
    return text[:closing], text[closing:]


def _within_root(root: Path, path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes repository root: {resolved}") from exc
    return resolved


def load_context_manifest(root: Path, path: Path) -> tuple[Path, dict[str, Any]]:
    candidate = path if path.is_absolute() else root / path
    resolved = _within_root(root, candidate, "context manifest")
    payload = load_yaml(resolved)
    schema_path = (
        root / "resources" / "schemas" / "benchmark-context-manifest.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validate(payload, schema, resolved)
    for treatment in payload["treatments"]:
        for category in (
            "skill_metadata",
            "skill_body",
            "references",
            "knowledge",
            "tool_descriptions",
        ):
            for relative in treatment[category]:
                candidate = _within_root(root, root / relative, f"context {category}")
                if not candidate.is_file():
                    raise ValueError(
                        f"Context manifest {category} input is missing: {relative}"
                    )
    treatment_map(payload)
    return resolved, payload


def treatment_map(
    manifest: dict[str, Any],
) -> dict[tuple[str, str, str | None], dict[str, Any]]:
    treatments: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for treatment in manifest["treatments"]:
        key = (
            treatment["condition"],
            treatment["skill"],
            treatment.get("case_id"),
        )
        if key in treatments:
            raise ValueError(
                "Context manifest repeats treatment "
                + "/".join(value or "default" for value in key)
            )
        treatments[key] = treatment
    return treatments


def treatment_for(
    manifest: dict[str, Any],
    *,
    condition: str,
    skill: str,
    case_id: str | None = None,
) -> dict[str, Any]:
    treatments = treatment_map(manifest)
    try:
        return (
            treatments.get((condition, skill, case_id))
            or treatments[(condition, skill, None)]
        )
    except KeyError as exc:
        raise ValueError(
            f"Context manifest has no {condition!r} treatment for skill {skill!r}"
        ) from exc


def validate_ablation_treatments(
    manifest: dict[str, Any],
    *,
    cases: list[dict[str, Any]],
) -> None:
    """Require one comparable Base/Full/Compressed treatment per benchmark Skill."""
    treatments = treatment_map(manifest)
    skills = {str(case["skill"]) for case in cases}
    defaults = {
        (condition, skill, None)
        for condition in TREATMENT_CONDITIONS
        for skill in skills
    }
    if not defaults.issubset(treatments):
        raise ValueError(
            "Context manifest must declare exactly one Base/Full/Compressed "
            "default treatment for every benchmark Skill"
        )
    corpus_skills = {str(case["id"]): str(case["skill"]) for case in cases}
    for _condition, skill, case_id in treatments:
        if case_id is None:
            if skill not in skills:
                raise ValueError(
                    f"Context manifest has unknown default Skill {skill!r}"
                )
            continue
        if case_id not in corpus_skills:
            raise ValueError(f"Context manifest override has unknown case {case_id!r}")
        if corpus_skills[case_id] != skill:
            raise ValueError(
                f"Context manifest override {case_id!r} does not match Skill {skill!r}"
            )
    for skill in sorted(skills):
        base = treatments[("base", skill, None)]
        if base["knowledge_basis"] != "none" or any(
            base[field]
            for field in ("skill_metadata", "skill_body", "references", "knowledge")
        ):
            raise ValueError(
                f"Context manifest Base treatment for {skill} must not load "
                "Skill, reference, or Knowledge content"
            )
        full = treatments[("full", skill, None)]
        compressed = treatments[("compressed", skill, None)]
        if (
            full["knowledge_basis"] != "workflow-required"
            or compressed["knowledge_basis"] != "workflow-required"
        ):
            raise ValueError(
                f"Context manifest Full and Compressed treatments for {skill} "
                "must declare workflow-required Knowledge"
            )
        if full["knowledge"] != compressed["knowledge"]:
            raise ValueError(
                f"Context manifest Full and Compressed treatments for {skill} "
                "must use identical Knowledge inputs"
            )
    for case in cases:
        case_id = str(case["id"])
        skill = str(case["skill"])
        resolved = {
            condition: treatment_for(
                manifest,
                condition=condition,
                skill=skill,
                case_id=case_id,
            )
            for condition in TREATMENT_CONDITIONS
        }
        base = resolved["base"]
        if base["knowledge_basis"] != "none" or any(
            base[field]
            for field in ("skill_metadata", "skill_body", "references", "knowledge")
        ):
            raise ValueError(
                f"Context manifest resolved Base treatment for {case_id} must be empty"
            )
        if resolved["full"]["knowledge"] != resolved["compressed"]["knowledge"]:
            raise ValueError(
                f"Context manifest resolved Full and Compressed treatments for "
                f"{case_id} must use identical Knowledge inputs"
            )


def collect_context_budget(
    *,
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    condition: str,
    corpus: dict[str, Any],
) -> dict[str, Any]:
    """Record a reproducible declared-input proxy, never model token usage."""
    totals = {
        "skill_metadata_chars": 0,
        "skill_body_chars": 0,
        "reference_chars": 0,
        "knowledge_chars": 0,
        "tool_description_chars": 0,
        "artifact_input_bytes": 0,
    }
    category_totals = {
        "skill_metadata": "skill_metadata_chars",
        "skill_body": "skill_body_chars",
        "references": "reference_chars",
        "knowledge": "knowledge_chars",
        "tool_descriptions": "tool_description_chars",
    }
    inputs: list[dict[str, Any]] = []
    seen_text: set[tuple[str, str]] = set()
    for case in corpus["cases"]:
        skill = str(case["skill"])
        case_id = str(case["id"])
        treatment = treatment_for(
            manifest,
            condition=condition,
            skill=skill,
            case_id=case_id,
        )
        for category, total_key in category_totals.items():
            for relative in treatment[category]:
                key = (category, relative)
                if key in seen_text:
                    continue
                seen_text.add(key)
                source = _within_root(root, root / relative, f"context {category}")
                metadata, body = _text_parts(source)
                if category == "skill_metadata":
                    characters = len(metadata)
                elif category == "skill_body":
                    characters = len(body)
                else:
                    characters = len(source.read_text(encoding="utf-8"))
                inputs.append(
                    {
                        "role": category.replace("_", "-"),
                        "path": relative,
                        "sha256": file_sha256(source),
                        "characters": characters,
                    }
                )
                totals[total_key] += characters

    fixture_paths = sorted(
        {
            str(case["fixture"])
            for case in corpus["cases"]
            if "$fixture-tree"
            in treatment_for(
                manifest,
                condition=condition,
                skill=str(case["skill"]),
                case_id=str(case["id"]),
            )["artifact_inputs"]
        }
    )
    for relative in fixture_paths:
        fixture = _within_root(root, root / relative, "context artifact input")
        if not fixture.is_dir():
            raise ValueError(f"Context artifact input is not a directory: {relative}")
        digest, file_count = tree_manifest(fixture)
        byte_count = tree_bytes(fixture)
        inputs.append(
            {
                "role": "artifact-input",
                "path": relative,
                "sha256": digest,
                "bytes": byte_count,
                "file_count": file_count,
            }
        )
        totals["artifact_input_bytes"] += byte_count

    result = {
        "condition": condition,
        "metric_kind": "declared-context-proxy-v1",
        "scope": "corpus",
        "character_unit": "unicode_code_points",
        "manifest_path": relative_to_root(root, manifest_path, "context manifest"),
        "manifest_sha256": file_sha256(manifest_path),
        **totals,
        "inputs": inputs,
    }
    validate_context_budget(result, root=root)
    return result


def validate_context_budget(payload: dict[str, Any], *, root: Path) -> None:
    character_totals = {
        "skill-metadata": "skill_metadata_chars",
        "skill-body": "skill_body_chars",
        "references": "reference_chars",
        "knowledge": "knowledge_chars",
        "tool-descriptions": "tool_description_chars",
    }
    calculated = dict.fromkeys(character_totals.values(), 0)
    calculated["artifact_input_bytes"] = 0
    seen: set[tuple[str, str]] = set()
    for record in payload["inputs"]:
        input_key = (record["role"], record["path"])
        if input_key in seen:
            raise ValueError("Context budget repeats input " + "/".join(input_key))
        seen.add(input_key)
        source = _within_root(root, root / record["path"], "context budget input")
        if record["role"] == "artifact-input":
            if not source.is_dir():
                raise ValueError(
                    f"Context artifact input is not a directory: {record['path']}"
                )
            digest, file_count = tree_manifest(source)
            if record["sha256"] != digest or record.get("file_count") != file_count:
                raise ValueError(
                    f"Context artifact input hash is stale: {record['path']}"
                )
            actual = tree_bytes(source)
            if record.get("bytes") != actual:
                raise ValueError(
                    f"Context artifact byte count is stale: {record['path']}"
                )
            calculated["artifact_input_bytes"] += actual
            continue
        if not source.is_file() or record["sha256"] != file_sha256(source):
            raise ValueError(f"Context input hash is stale: {record['path']}")
        metadata, body = _text_parts(source)
        if record["role"] == "skill-metadata":
            actual = len(metadata)
        elif record["role"] == "skill-body":
            actual = len(body)
        else:
            actual = len(source.read_text(encoding="utf-8"))
        if record.get("characters") != actual:
            raise ValueError(
                f"Context input character count is stale: {record['path']}"
            )
        calculated[character_totals[record["role"]]] += actual
    for total_name, actual in calculated.items():
        if payload.get(total_name) != actual:
            raise ValueError(f"Context budget total is stale: {total_name}")


def git_output(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise ValueError(process.stderr.strip() or "Git command failed")
    return process.stdout.strip()


def executable_provenance(
    *,
    runtime_id: str,
    role: str,
    requested: str,
) -> dict[str, Any]:
    resolved_value = shutil.which(requested)
    if resolved_value is None:
        raise ValueError(f"Benchmark runtime executable is unavailable: {requested}")
    resolved = Path(resolved_value).resolve()
    if not resolved.is_file():
        raise ValueError(f"Benchmark runtime is not a file: {resolved}")
    version_arguments = ["--version"]
    process = subprocess.run(
        [str(resolved), *version_arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise ValueError(
            f"Benchmark runtime version command failed for {requested}: {detail}"
        )
    version_output = "\n".join(
        part for part in (process.stdout.strip(), process.stderr.strip()) if part
    )
    if not version_output:
        raise ValueError(
            f"Benchmark runtime version command returned no output: {requested}"
        )
    return {
        "id": runtime_id,
        "role": role,
        "requested": requested,
        "resolved_name": resolved.name,
        "resolved_path_sha256": sha256_bytes(str(resolved).encode("utf-8")),
        "executable_sha256": file_sha256(resolved),
        "version_arguments": version_arguments,
        "version_output": version_output,
        "version_output_sha256": sha256_bytes(version_output.encode("utf-8")),
    }


def collect_runtime_provenance(
    command: list[str],
    declared_runtimes: list[str],
) -> list[dict[str, Any]]:
    requested = [("command-executable", "command", command[0])]
    requested.extend(
        (f"model-runtime-{index}", "model", executable)
        for index, executable in enumerate(declared_runtimes, start=1)
    )
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for runtime_id, role, executable in requested:
        key = (role, executable)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            executable_provenance(
                runtime_id=runtime_id,
                role=role,
                requested=executable,
            )
        )
    return records


def collect_provenance(
    *,
    root: Path,
    corpus_path: Path,
    corpus: dict[str, Any],
    command: list[str],
    skill_version: str,
    model: str,
    surface: str,
    declared_runtimes: list[str],
    context_manifest_path: Path | None = None,
    context_budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema_root = root / "resources" / "schemas"
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != skill_version:
        raise ValueError(
            "Declared benchmark Skill version does not match plugin manifest"
        )
    input_specs: list[tuple[str, Path]] = [
        ("ground-truth", corpus_path),
        ("benchmark-schema", schema_root / "benchmark.schema.json"),
        ("observation-schema", schema_root / "benchmark-observation.schema.json"),
        ("command-result-schema", schema_root / "benchmark-command-result.schema.json"),
        ("dependency-lock", root / "requirements-runtime.lock"),
        ("knowledge-manifest", root / "resources" / "knowledge" / "manifest.yaml"),
        ("plugin-manifest", manifest_path),
    ]
    if context_manifest_path is not None:
        input_specs.extend(
            [
                ("context-manifest", context_manifest_path),
                (
                    "benchmark-context-schema",
                    schema_root / "benchmark-context-manifest.schema.json",
                ),
            ]
        )
    inputs = [
        {
            "role": role,
            "path": relative_to_root(root, path, role),
            "sha256": file_sha256(path),
        }
        for role, path in input_specs
    ]

    runner = Path(__file__).resolve()
    tool_paths = [runner]
    for token in command:
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            is_file = candidate.is_file()
        except OSError:
            is_file = False
        if is_file:
            try:
                relative_to_root(root, candidate, "command tool")
            except ValueError:
                continue
            tool_paths.append(candidate.resolve())
    tools = []
    seen_paths: set[str] = set()
    for path in tool_paths:
        relative = relative_to_root(root, path, "tool")
        if relative in seen_paths:
            continue
        seen_paths.add(relative)
        tools.append(
            {
                "id": (
                    "benchmark-runner"
                    if path == runner
                    else path.stem.replace("_", "-")
                ),
                "path": relative,
                "sha256": file_sha256(path),
            }
        )

    fixtures = []
    for case in corpus["cases"]:
        fixture = (root / case["fixture"]).resolve()
        relative = relative_to_root(root, fixture, f"case {case['id']} fixture")
        digest, file_count = tree_manifest(fixture)
        fixtures.append(
            {
                "case_id": case["id"],
                "path": relative,
                "sha256": digest,
                "file_count": file_count,
            }
        )

    tracked_paths = [item["path"] for item in inputs]
    tracked_paths.extend(item["path"] for item in tools)
    tracked_paths.extend(item["path"] for item in fixtures)
    if context_budget is not None:
        # Context assets are execution inputs too. Include them in dirty-state
        # detection so an uncommitted compact prompt, Skill, reference, or
        # Knowledge entry cannot be mislabeled as clean release evidence.
        tracked_paths.extend(item["path"] for item in context_budget["inputs"])
    dirty = bool(
        git_output(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *tracked_paths,
        )
    )
    try:
        runtime_executables = collect_runtime_provenance(command, declared_runtimes)
    except ValueError:
        runtime_executables = []
    model_runtimes = [item for item in runtime_executables if item["role"] == "model"]
    del model_runtimes
    result = {
        "source": {
            "repository": ".",
            "commit": git_output(root, "rev-parse", "HEAD"),
            "dirty": dirty,
        },
        "environment": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
        "model_request": model,
        "command_template": command,
        "command_template_sha256": sha256_bytes(canonical_json(command).encode()),
        "inputs": inputs,
        "fixtures": fixtures,
        "tools": tools,
    }
    if runtime_executables:
        result["runtime_executables"] = runtime_executables
    return result


def harness_implementation_sha256(root: Path) -> str:
    paths = (
        root / "scripts" / "run_behavior_benchmark.py",
        root / "scripts" / "codex_benchmark_adapter.py",
        root / "resources" / "schemas" / "benchmark-observation.schema.json",
        root / "resources" / "schemas" / "benchmark-command-result.schema.json",
    )
    records = sorted(
        (
            {
                "path": relative_to_root(root, path, "harness input"),
                "sha256": file_sha256(path),
            }
            for path in paths
        ),
        key=lambda item: item["path"],
    )
    return sha256_bytes(canonical_json(records).encode("utf-8"))


def harness_config_sha256(
    *,
    requested_model: str,
    actual_model: str | None,
    surface: str,
    condition: str,
    profile: str,
    case_ids: list[str],
    repetitions: int,
    trial_timeout: float,
    artifact_timeout: float,
    command: list[str],
    context_manifest_sha256: str,
) -> str:
    payload = {
        "requested_model": requested_model,
        "actual_model": actual_model,
        "surface": surface,
        "condition": condition,
        "profile": profile,
        "selected_case_ids": case_ids,
        "repetitions": repetitions,
        "trial_timeout_seconds": trial_timeout,
        "artifact_timeout_seconds": artifact_timeout,
        "command_template": command,
        "context_manifest_sha256": context_manifest_sha256,
        "policies": {
            "generic_retries": 0,
            "evidence_correction_max": 1,
            "model_fallback": False,
        },
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")
    return payload


def validate(payload: dict[str, Any], schema: dict[str, Any], source: Path) -> None:
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(payload),
        key=lambda error: list(error.path),
    )
    if errors:
        detail = "; ".join(error.message for error in errors)
        raise ValueError(f"{source} is invalid: {detail}")


def render_command(
    template: list[str],
    *,
    skill: str,
    fixture: Path,
    prompt: str,
    condition: str = "full",
    context_manifest: Path | None = None,
    case_id: str = "",
) -> list[str]:
    values = {
        "skill": skill,
        "fixture": str(fixture),
        "prompt": prompt,
        "condition": condition,
        "context_manifest": str(context_manifest) if context_manifest else "",
        "case_id": case_id,
    }
    rendered: list[str] = []
    for part in template:
        for key, value in values.items():
            part = part.replace(f"{{{key}}}", value)
        rendered.append(part)
    return rendered


def evidence_is_valid(fixture: Path, evidence: object) -> bool:
    if not isinstance(evidence, list) or not evidence:
        return False
    fixture = fixture.resolve()
    for record in evidence:
        if not isinstance(record, dict):
            return False
        if set(record) != {"path", "line_start", "line_end", "excerpt"}:
            return False
        if not isinstance(record["path"], str):
            return False
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
        if (
            not isinstance(line_start, int)
            or not isinstance(line_end, int)
            or line_start < 1
            or line_end < line_start
            or line_end > len(lines)
        ):
            return False
        selected = "\n".join(lines[line_start - 1 : line_end])
        if not isinstance(record["excerpt"], str) or record["excerpt"] not in selected:
            return False
    return True


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root.resolve()
    repetitions = getattr(args, "repetitions", 1)
    condition = getattr(args, "condition", "full")
    trial_timeout = float(getattr(args, "timeout", 600))
    artifact_timeout = min(
        float(getattr(args, "artifact_timeout", MAX_ARTIFACT_TIMEOUT_SECONDS)),
        float(MAX_ARTIFACT_TIMEOUT_SECONDS),
    )
    if trial_timeout <= 0 or artifact_timeout <= 0:
        raise ValueError("Benchmark timeouts must be positive")
    profile = str(getattr(args, "profile", "default"))
    corpus_path = args.ground_truth.resolve()
    output_path = args.output.resolve()
    log_path = output_path.with_suffix(".log.jsonl")
    corpus = load_yaml(corpus_path)
    schema_root = root / "resources" / "schemas"
    schema_path = schema_root / "benchmark.schema.json"
    observation_schema_path = schema_root / "benchmark-observation.schema.json"
    command_result_schema_path = schema_root / "benchmark-command-result.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    observation_schema = json.loads(observation_schema_path.read_text(encoding="utf-8"))
    command_result_schema = json.loads(
        command_result_schema_path.read_text(encoding="utf-8")
    )
    validate(corpus, schema, corpus_path)
    if corpus["benchmark"]["kind"] != "ground-truth":
        raise ValueError("Benchmark input must be ground truth")
    if condition not in TREATMENT_CONDITIONS:
        raise ValueError(f"Unsupported benchmark condition: {condition}")

    requested_case_ids = list(getattr(args, "case_ids", []) or [])
    corpus_by_id = {str(case["id"]): case for case in corpus["cases"]}
    if len(corpus_by_id) != len(corpus["cases"]):
        raise ValueError("Ground truth contains duplicate case IDs")
    unknown = sorted(set(requested_case_ids) - set(corpus_by_id))
    if unknown:
        raise ValueError("Unknown benchmark case IDs: " + ", ".join(unknown))
    selected_cases = (
        [corpus_by_id[case_id] for case_id in requested_case_ids]
        if requested_case_ids
        else list(corpus["cases"])
    )
    selected_case_ids = [str(case["id"]) for case in selected_cases]
    selected_corpus = {**corpus, "cases": selected_cases}

    context_manifest_value = getattr(
        args,
        "context_manifest",
        root / "benchmarks" / "ablation" / "context-manifest.yaml",
    )
    context_manifest_path, context_manifest = load_context_manifest(
        root,
        Path(context_manifest_value),
    )
    validate_ablation_treatments(context_manifest, cases=list(corpus["cases"]))
    has_overrides = any(
        treatment.get("case_id") is not None
        for treatment in context_manifest["treatments"]
    )
    required_placeholders = ("condition", "context_manifest")
    for placeholder in required_placeholders:
        if not any(f"{{{placeholder}}}" in argument for argument in args.command):
            raise ValueError(
                f"Benchmark treatments require a {{{placeholder}}} command placeholder"
            )
    if has_overrides and not any("{case_id}" in argument for argument in args.command):
        raise ValueError(
            "Benchmark case overrides require a {case_id} command placeholder"
        )
    context_budget = collect_context_budget(
        root=root,
        manifest_path=context_manifest_path,
        manifest=context_manifest,
        condition=condition,
        corpus=selected_corpus,
    )
    provenance = collect_provenance(
        root=root,
        corpus_path=corpus_path,
        corpus=selected_corpus,
        command=args.command,
        skill_version=args.skill_version,
        model=args.model,
        surface=args.surface,
        declared_runtimes=getattr(args, "runtime_executables", []),
        context_manifest_path=context_manifest_path,
        context_budget=context_budget,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    deadline = time.monotonic() + artifact_timeout
    result_cases: list[dict[str, Any]] = []
    observed_actual_models: set[str] = set()

    for case in selected_cases:
        fixture = _within_root(root, root / case["fixture"], "benchmark fixture")
        if not fixture.is_dir():
            raise ValueError(f"Case {case['id']} fixture is missing: {fixture}")
        trials: list[dict[str, Any]] = []
        for trial_index in range(1, repetitions + 1):
            command = render_command(
                args.command,
                skill=case["skill"],
                fixture=fixture,
                prompt=case["prompt"],
                condition=condition,
                context_manifest=context_manifest_path,
                case_id=str(case["id"]),
            )
            command_sha256 = sha256_bytes(canonical_json(command).encode("utf-8"))
            started = time.monotonic()
            remaining = max(0.0, deadline - started)
            exit_code: int | None = None
            stdout: str | None = None
            stderr: str | None = None
            envelope: dict[str, Any] | None = None
            observed: dict[str, Any] | None = None
            status = "failed"
            failure_class: str | None = "timeout"

            if remaining > 0:
                try:
                    process = subprocess.run(
                        command,
                        cwd=root,
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=max(0.001, min(trial_timeout, remaining)),
                    )
                    exit_code = process.returncode
                    stdout = process.stdout
                    stderr = process.stderr
                    if exit_code != 0:
                        failure_class = "nonzero"
                    else:
                        try:
                            candidate = json.loads(stdout)
                            if not isinstance(candidate, dict):
                                raise ValueError("command result must be an object")
                            validate(
                                candidate,
                                command_result_schema,
                                command_result_schema_path,
                            )
                            if candidate["requested_model"] != args.model:
                                raise ValueError(
                                    "command result requested model mismatch"
                                )
                            envelope = candidate
                            if envelope["status"] == "failed":
                                failure_class = envelope["failure"]["class"]
                            else:
                                observed_candidate = envelope["observation"]
                                validate(
                                    observed_candidate,
                                    observation_schema,
                                    observation_schema_path,
                                )
                                observed = observed_candidate
                                status = "completed"
                                failure_class = None
                        except (json.JSONDecodeError, ValueError):
                            failure_class = "schema-invalid"
                except subprocess.TimeoutExpired as exc:
                    stdout = (
                        exc.stdout.decode("utf-8", errors="replace")
                        if isinstance(exc.stdout, bytes)
                        else exc.stdout or ""
                    )
                    stderr = (
                        exc.stderr.decode("utf-8", errors="replace")
                        if isinstance(exc.stderr, bytes)
                        else exc.stderr or ""
                    )
                    failure_class = "timeout"
                except OSError:
                    failure_class = "tool-error"

            duration_seconds = time.monotonic() - started
            adapter = (
                envelope["adapter"]
                if envelope is not None
                else {"name": "unavailable", "version": "unavailable"}
            )
            actual_model = (
                envelope.get("actual_model") if envelope is not None else None
            )
            actual_model_source = (
                envelope["actual_model_source"]
                if envelope is not None
                else "unavailable"
            )
            retries = (
                envelope["retries"]
                if envelope is not None
                else {
                    "generic": 0,
                    "evidence_correction_count": 0,
                    "evidence_correction_max": 1,
                }
            )
            usage = envelope.get("usage") if envelope is not None else None
            if isinstance(actual_model, str):
                observed_actual_models.add(actual_model)

            normalized_findings: list[dict[str, Any]] = []
            recommendations: list[str] = []
            if observed is not None:
                recommendations = list(observed["observed_recommendations"])
                for finding in observed["observed_findings"]:
                    evidence = finding["evidence"]
                    normalized_findings.append(
                        {
                            "rule_id": finding["rule_id"],
                            "severity": finding["severity"],
                            "evidence": evidence,
                            "evidence_valid": evidence_is_valid(fixture, evidence),
                        }
                    )

            stdout_sha256 = (
                sha256_bytes(stdout.encode("utf-8")) if stdout is not None else None
            )
            stderr_sha256 = (
                sha256_bytes(stderr.encode("utf-8")) if stderr is not None else None
            )
            observation_sha256 = (
                sha256_bytes(canonical_json(observed).encode("utf-8"))
                if observed is not None
                else None
            )
            command_result_sha256 = (
                sha256_bytes(canonical_json(envelope).encode("utf-8"))
                if envelope is not None
                else None
            )
            log_record = {
                "schema_version": "1.2",
                "case_id": case["id"],
                "trial_index": trial_index,
                "status": status,
                "failure_class": failure_class,
                "duration_seconds": duration_seconds,
                "exit_code": exit_code,
                "command": command,
                "command_sha256": command_sha256,
                "stdout_sha256": stdout_sha256,
                "stderr_sha256": stderr_sha256,
                "command_result": envelope,
                "observation": observed,
            }
            log_record_text = canonical_json(log_record)
            with log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(log_record_text + "\n")
            trial: dict[str, Any] = {
                "index": trial_index,
                "status": status,
                "duration_seconds": duration_seconds,
                "actual_model": actual_model,
                "actual_model_source": actual_model_source,
                "model_fallback": False,
                "adapter": adapter,
                "retries": retries,
                "usage": usage,
                "observed_findings": normalized_findings,
                "observed_recommendations": recommendations,
                "execution": {
                    "exit_code": exit_code,
                    "command": command,
                    "command_sha256": command_sha256,
                    "stdout_sha256": stdout_sha256,
                    "stderr_sha256": stderr_sha256,
                    "observation_sha256": observation_sha256,
                    "command_result_sha256": command_result_sha256,
                    "log_record_sha256": sha256_bytes(log_record_text.encode("utf-8")),
                },
            }
            if failure_class is not None:
                trial["failure_class"] = failure_class
            if observed is not None and "observed_decision" in observed:
                trial["observed_decision"] = observed["observed_decision"]
            trials.append(trial)
        result_cases.append(
            {
                "id": case["id"],
                "fixture": case["fixture"],
                "skill": case["skill"],
                "expected_findings": [],
                "forbidden_recommendations": [],
                "trials": trials,
            }
        )

    actual_model = (
        next(iter(observed_actual_models)) if len(observed_actual_models) == 1 else None
    )
    provenance["execution_log"] = {
        "path": log_path.name,
        "format": "jsonl",
        "sha256": file_sha256(log_path),
        "records": len(selected_cases) * repetitions,
    }
    result: dict[str, Any] = {
        "schema_version": "1.6",
        "benchmark": {
            "id": corpus["benchmark"]["id"],
            "version": corpus["benchmark"]["version"],
            "kind": "run",
            "model": args.model,
            "actual_model": actual_model,
            "surface": args.surface,
            "skill_version": args.skill_version,
            "condition": condition,
            "profile": profile,
            "context_budget": context_budget,
            "run_at": datetime.now(UTC).isoformat(),
            "repetitions": repetitions,
            "trial_timeout_seconds": trial_timeout,
            "artifact_timeout_seconds": artifact_timeout,
            "selected_case_ids": selected_case_ids,
            "harness": {
                "name": HARNESS_NAME,
                "version": HARNESS_VERSION,
                "implementation_sha256": harness_implementation_sha256(root),
                "config_sha256": harness_config_sha256(
                    requested_model=args.model,
                    actual_model=actual_model,
                    surface=args.surface,
                    condition=condition,
                    profile=profile,
                    case_ids=selected_case_ids,
                    repetitions=repetitions,
                    trial_timeout=trial_timeout,
                    artifact_timeout=artifact_timeout,
                    command=args.command,
                    context_manifest_sha256=file_sha256(context_manifest_path),
                ),
            },
            "provenance": provenance,
        },
        "cases": result_cases,
    }
    validate(result, schema, args.output)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run architecture benchmark cases through a command whose stdout "
            "is a JSON observation."
        )
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=Path("benchmarks/ground-truth.yaml"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--surface", required=True)
    parser.add_argument("--skill-version", default=DEFAULT_SKILL_VERSION)
    parser.add_argument(
        "--condition",
        choices=["base", "full", "compressed"],
        default="full",
        help="Declared Skill/context treatment; full preserves the current workflow.",
    )
    parser.add_argument(
        "--context-manifest",
        type=Path,
        default=Path("benchmarks/ablation/context-manifest.yaml"),
        help="Checked-in manifest that binds the declared context treatment.",
    )
    parser.add_argument(
        "--runtime-executable",
        action="append",
        default=[],
        dest="runtime_executables",
        help=(
            "External model/runtime executable to fingerprint. Repeat for "
            "multiple runtime boundaries."
        ),
    )
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--artifact-timeout",
        type=int,
        default=MAX_ARTIFACT_TIMEOUT_SECONDS,
        help="Per-artifact wall-clock cap; values above ten hours are clamped.",
    )
    parser.add_argument("--profile", default="default")
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        dest="case_ids",
        help="Run only this Ground Truth case. Repeat to select multiple cases.",
    )
    parser.add_argument("--repetitions", type=int, default=1, choices=range(1, 21))
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help=(
            "Command arguments after --. Use {skill}, {fixture}, {prompt}, "
            "{condition}, {context_manifest}, and {case_id} placeholders."
        ),
    )
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command template is required after --")
    return args


def main() -> None:
    args = parse_args()
    result = run_benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(result, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote benchmark run: {args.output}")


if __name__ == "__main__":
    main()
