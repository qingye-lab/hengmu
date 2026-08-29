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

DEFAULT_SKILL_VERSION = "1.1.3"
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


def treatment_map(manifest: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    treatments: dict[tuple[str, str], dict[str, Any]] = {}
    for treatment in manifest["treatments"]:
        key = (treatment["condition"], treatment["skill"])
        if key in treatments:
            raise ValueError("Context manifest repeats treatment " + "/".join(key))
        treatments[key] = treatment
    return treatments


def treatment_for(
    manifest: dict[str, Any],
    *,
    condition: str,
    skill: str,
) -> dict[str, Any]:
    try:
        return treatment_map(manifest)[(condition, skill)]
    except KeyError as exc:
        raise ValueError(
            f"Context manifest has no {condition!r} treatment for skill {skill!r}"
        ) from exc


def validate_ablation_treatments(
    manifest: dict[str, Any],
    *,
    skills: set[str],
) -> None:
    """Require one comparable Base/Full/Compressed treatment per benchmark Skill."""
    treatments = treatment_map(manifest)
    expected = {
        (condition, skill) for condition in TREATMENT_CONDITIONS for skill in skills
    }
    if set(treatments) != expected:
        raise ValueError(
            "Context manifest must declare exactly one Base/Full/Compressed "
            "treatment for every benchmark Skill"
        )
    for skill in sorted(skills):
        base = treatments[("base", skill)]
        if base["knowledge_basis"] != "none" or any(
            base[field]
            for field in ("skill_metadata", "skill_body", "references", "knowledge")
        ):
            raise ValueError(
                f"Context manifest Base treatment for {skill} must not load "
                "Skill, reference, or Knowledge content"
            )
        full = treatments[("full", skill)]
        compressed = treatments[("compressed", skill)]
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
    skills = sorted({str(case["skill"]) for case in corpus["cases"]})
    for skill in skills:
        treatment = treatment_for(manifest, condition=condition, skill=skill)
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
        if candidate.is_file():
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
    runtime_executables = collect_runtime_provenance(command, declared_runtimes)
    model_runtimes = [item for item in runtime_executables if item["role"] == "model"]
    if model_runtimes and not any(
        item["version_output"] == surface for item in model_runtimes
    ):
        raise ValueError(
            "Declared benchmark surface does not match a model runtime version"
        )
    return {
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
        "runtime_executables": runtime_executables,
        "inputs": inputs,
        "fixtures": fixtures,
        "tools": tools,
    }


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
) -> list[str]:
    values = {
        "skill": skill,
        "fixture": str(fixture),
        "prompt": prompt,
        "condition": condition,
        "context_manifest": str(context_manifest) if context_manifest else "",
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
    corpus_path = args.ground_truth.resolve()
    output_path = args.output.resolve()
    log_path = output_path.with_suffix(".log.jsonl")
    corpus = load_yaml(corpus_path)
    schema_path = root / "resources" / "schemas" / "benchmark.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    observation_schema_path = (
        root / "resources" / "schemas" / "benchmark-observation.schema.json"
    )
    observation_schema = json.loads(observation_schema_path.read_text(encoding="utf-8"))
    # Keep the standalone observation schema usable by model surfaces while
    # avoiding network or resolver behavior during local validation.
    observation_schema["properties"]["usage"] = schema["$defs"]["trial"]["properties"][
        "usage"
    ]
    validate(corpus, schema, corpus_path)
    if corpus["benchmark"]["kind"] != "ground-truth":
        raise ValueError("Benchmark input must be ground truth")
    condition = getattr(args, "condition", "full")
    if condition not in {"base", "full", "compressed"}:
        raise ValueError(f"Unsupported benchmark condition: {condition}")
    context_manifest_value = getattr(
        args,
        "context_manifest",
        root / "benchmarks" / "ablation" / "context-manifest.yaml",
    )
    context_manifest_path, context_manifest = load_context_manifest(
        root,
        Path(context_manifest_value),
    )
    validate_ablation_treatments(
        context_manifest,
        skills={str(case["skill"]) for case in corpus["cases"]},
    )
    context_budget = collect_context_budget(
        root=root,
        manifest_path=context_manifest_path,
        manifest=context_manifest,
        condition=condition,
        corpus=corpus,
    )
    if not any("{condition}" in argument for argument in args.command):
        raise ValueError(
            "Benchmark treatments require a {condition} command placeholder"
        )
    if not any("{context_manifest}" in argument for argument in args.command):
        raise ValueError(
            "Benchmark treatments require a {context_manifest} command placeholder"
        )
    provenance = collect_provenance(
        root=root,
        corpus_path=corpus_path,
        corpus=corpus,
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
    log_records = 0

    result: dict[str, Any] = {
        "schema_version": "1.5",
        "benchmark": {
            "id": corpus["benchmark"]["id"],
            "version": corpus["benchmark"]["version"],
            "kind": "run",
            "model": args.model,
            "surface": args.surface,
            "skill_version": args.skill_version,
            "condition": condition,
            "context_budget": context_budget,
            "run_at": datetime.now(UTC).isoformat(),
            "repetitions": repetitions,
            "provenance": provenance,
        },
        "cases": [],
    }
    for case in corpus["cases"]:
        fixture = (root / case["fixture"]).resolve()
        try:
            fixture.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Case {case['id']} fixture escapes benchmark root"
            ) from exc
        if not fixture.is_dir():
            raise ValueError(f"Case {case['id']} fixture is missing: {fixture}")
        trials = []
        for trial_index in range(1, repetitions + 1):
            command = render_command(
                args.command,
                skill=case["skill"],
                fixture=fixture,
                prompt=case["prompt"],
                condition=condition,
                context_manifest=context_manifest_path,
            )
            started = time.monotonic()
            process = subprocess.run(
                command,
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                timeout=args.timeout,
            )
            duration_seconds = time.monotonic() - started
            command_sha256 = sha256_bytes(canonical_json(command).encode("utf-8"))
            stdout_sha256 = sha256_bytes(process.stdout.encode("utf-8"))
            stderr_sha256 = sha256_bytes(process.stderr.encode("utf-8"))
            if process.returncode != 0:
                failed_record = {
                    "schema_version": "1.1",
                    "case_id": case["id"],
                    "trial_index": trial_index,
                    "duration_seconds": duration_seconds,
                    "exit_code": process.returncode,
                    "command": command,
                    "command_sha256": command_sha256,
                    "stdout_sha256": stdout_sha256,
                    "stderr_sha256": stderr_sha256,
                    "observation": None,
                }
                with log_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(canonical_json(failed_record) + "\n")
                raise RuntimeError(
                    f"Case {case['id']} trial {trial_index} failed "
                    f"({process.returncode}): {process.stderr.strip()}"
                )
            try:
                observed = json.loads(process.stdout)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Case {case['id']} trial {trial_index} did not return JSON: {exc}"
                ) from exc
            if not isinstance(observed, dict):
                raise ValueError(
                    f"Case {case['id']} trial {trial_index} output must be "
                    "a JSON object"
                )
            validate(observed, observation_schema, observation_schema_path)
            log_record = {
                "schema_version": "1.1",
                "case_id": case["id"],
                "trial_index": trial_index,
                "duration_seconds": duration_seconds,
                "exit_code": process.returncode,
                "command": command,
                "command_sha256": command_sha256,
                "stdout_sha256": stdout_sha256,
                "stderr_sha256": stderr_sha256,
                "observation": observed,
            }
            log_record_text = canonical_json(log_record)
            with log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(log_record_text + "\n")
            log_records += 1
            observed_findings = observed.get("observed_findings", [])
            observed_recommendations = observed.get(
                "observed_recommendations",
                [],
            )
            if not isinstance(observed_findings, list):
                raise ValueError(
                    f"Case {case['id']} observed_findings must be an array"
                )
            if not isinstance(observed_recommendations, list):
                raise ValueError(
                    f"Case {case['id']} observed_recommendations must be an array"
                )
            normalized_findings = []
            for index, finding in enumerate(observed_findings):
                if not isinstance(finding, dict):
                    raise ValueError(
                        f"Case {case['id']} finding {index} must be an object"
                    )
                evidence = finding.get("evidence", [])
                normalized_findings.append(
                    {
                        "rule_id": finding.get("rule_id"),
                        "severity": finding.get("severity"),
                        "evidence": evidence,
                        "evidence_valid": evidence_is_valid(fixture, evidence),
                    }
                )
            trial = {
                "index": trial_index,
                "duration_seconds": duration_seconds,
                "observed_findings": normalized_findings,
                "observed_recommendations": observed_recommendations,
                "execution": {
                    "exit_code": process.returncode,
                    "command": command,
                    "command_sha256": command_sha256,
                    "stdout_sha256": stdout_sha256,
                    "stderr_sha256": stderr_sha256,
                    "observation_sha256": sha256_bytes(
                        canonical_json(observed).encode("utf-8")
                    ),
                    "log_record_sha256": sha256_bytes(log_record_text.encode("utf-8")),
                },
            }
            observed_decision = observed.get("observed_decision")
            if case.get("expected_decision") is not None:
                if observed_decision is None:
                    raise ValueError(
                        f"Case {case['id']} requires observed_decision output"
                    )
                trial["observed_decision"] = observed_decision
            elif observed_decision is not None:
                trial["observed_decision"] = observed_decision
            usage = observed.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise ValueError(f"Case {case['id']} usage must be an object")
                trial["usage"] = usage
            trials.append(trial)
        first_trial = trials[0]
        result_case = {
            "id": case["id"],
            "fixture": case["fixture"],
            "skill": case["skill"],
            "expected_findings": [],
            "forbidden_recommendations": [],
            "observed_findings": first_trial["observed_findings"],
            "observed_recommendations": first_trial["observed_recommendations"],
            "trials": trials,
        }
        if "observed_decision" in first_trial:
            result_case["observed_decision"] = first_trial["observed_decision"]
        result["cases"].append(result_case)
    provenance["execution_log"] = {
        "path": log_path.name,
        "format": "jsonl",
        "sha256": file_sha256(log_path),
        "records": log_records,
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
    parser.add_argument("--repetitions", type=int, default=1, choices=range(1, 21))
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help=(
            "Command arguments after --. Use {skill}, {fixture}, {prompt}, "
            "{condition}, and {context_manifest} placeholders."
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
