#!/usr/bin/env python3
"""Run one benchmark case through Codex with a structured observation contract."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

import yaml
from jsonschema import Draft202012Validator, FormatChecker

CANONICAL_TRADEOFFS = (
    "availability",
    "client-complexity",
    "consistency",
    "cost",
    "delivery-semantics",
    "deployment-independence",
    "evaluation",
    "implementation-complexity",
    "latency",
    "maintainability",
    "migration-risk",
    "offline-capability",
    "operational-complexity",
    "recovery",
    "reliability",
    "reversibility",
    "routing-complexity",
    "safety",
    "security",
    "team-ownership",
)
ADAPTER_NAME = "hengmu-codex-benchmark-adapter"
ADAPTER_VERSION = "1.3.0"
# Codex CLI 0.150.1 has no documented JSONL event field that authoritatively
# identifies the actual model. Keep this map empty until a versioned CLI
# contract documents an event-type-specific field; requested strings, banners,
# and nested model/tool/MCP content are never identity evidence.
AUTHORITATIVE_MODEL_EVENT_FIELDS: dict[str, str] = {}
USAGE_EVENT_TYPES = {"turn.completed"}


class AdapterFailure(RuntimeError):
    def __init__(
        self,
        failure_class: str,
        *,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


def within(root: Path, value: Path, label: str) -> Path:
    resolved = value.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes repository root: {resolved}") from exc
    return resolved


def load_context_manifest(root: Path, value: Path) -> dict[str, Any]:
    path = within(
        root,
        value if value.is_absolute() else root / value,
        "context manifest",
    )
    if not path.is_file():
        raise ValueError(f"Context manifest is missing: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Context manifest must contain a mapping")
    schema_path = within(
        root,
        root / "resources" / "schemas" / "benchmark-context-manifest.schema.json",
        "context manifest schema",
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ValueError(
            "Context manifest is invalid: "
            + "; ".join(error.message for error in errors)
        )
    return payload


def treatment_for(
    manifest: dict[str, Any],
    *,
    condition: str,
    skill: str,
    case_id: str | None = None,
) -> dict[str, Any]:
    exact = [
        item
        for item in manifest["treatments"]
        if item["condition"] == condition
        and item["skill"] == skill
        and item.get("case_id") == case_id
    ]
    if len(exact) > 1:
        raise ValueError(
            f"Context manifest defines multiple exact {condition!r} treatments "
            f"for {skill!r}/{case_id!r}"
        )
    if len(exact) == 1:
        return cast(dict[str, Any], exact[0])
    defaults = [
        item
        for item in manifest["treatments"]
        if item["condition"] == condition
        and item["skill"] == skill
        and item.get("case_id") is None
    ]
    if len(defaults) > 1:
        raise ValueError(
            f"Context manifest defines multiple default {condition!r} treatments "
            f"for {skill!r}"
        )
    if not defaults:
        raise ValueError(
            f"Context manifest must define one {condition!r} treatment for {skill!r}"
        )
    return cast(dict[str, Any], defaults[0])


def resolve_treatment_paths(
    root: Path,
    treatment: dict[str, Any],
    category: str,
) -> list[Path]:
    result = []
    for relative in treatment[category]:
        path = within(root, root / relative, f"context {category}")
        if not path.is_file():
            raise ValueError(f"Context {category} input is missing: {relative}")
        result.append(path)
    return result


def build_prompt(
    *,
    skill_path: Path,
    knowledge_root: Path,
    fixture: Path,
    task: str,
    condition: str = "full",
    compact_skill_paths: list[Path] | None = None,
    reference_paths: list[Path] | None = None,
    knowledge_paths: list[Path] | None = None,
    tool_description_paths: list[Path] | None = None,
) -> str:
    solution_output = ""
    if skill_path.parent.name == "architecture-solution-advisor":
        tradeoffs = ", ".join(CANONICAL_TRADEOFFS)
        solution_output = f"""
This is a solution-advisor case. Also return observed_decision with:
- selected_option: the canonical selected knowledge ID without its kind prefix
  (style.web-queue-worker becomes web-queue-worker), or the exact slug of a
  Decision Guide option heading (Single agent with tools becomes
  single-agent-with-tools). Never add keep, retain, adopt, current, display,
  or another synonym;
- compared_tradeoffs: atomic IDs from this canonical vocabulary only:
  {tradeoffs}. Record each dimension separately; never combine dimensions into
  an A-vs-B identifier;
- knowledge_ids: only IDs from knowledge entries you actually used;
- rejected_options: at least two viable options with evidence-backed reasons;
- migration_slices: concrete reversible slices, even when retaining the design.
"""
    else:
        solution_output = """
This is not a solution-advisor case. Return observed_decision with
selected_option set to "not-applicable" and all four array fields empty.
"""
    if condition == "full":
        treatment_instructions = f"""Read and follow the Skill completely:
{skill_path}
"""
        if reference_paths:
            treatment_instructions += (
                "\nRead these declared references before reaching a conclusion:\n"
                + "\n".join(f"- {path}" for path in reference_paths)
                + "\n"
            )
        if knowledge_paths:
            treatment_instructions += (
                "\nUse only these declared knowledge entries when needed:\n"
                + "\n".join(f"- {path}" for path in knowledge_paths)
                + "\n"
            )
    elif condition == "compressed":
        compact_skill_paths = compact_skill_paths or []
        reference_paths = reference_paths or []
        knowledge_paths = knowledge_paths or []
        if not compact_skill_paths:
            raise ValueError("Compressed benchmark treatment requires a compact Skill")
        treatment_instructions = (
            "Read and follow these compact benchmark instructions:\n"
        )
        treatment_instructions += "\n".join(f"- {path}" for path in compact_skill_paths)
        if reference_paths:
            treatment_instructions += (
                "\n\nRead these declared references only when needed:\n"
            )
            treatment_instructions += "\n".join(f"- {path}" for path in reference_paths)
        if knowledge_paths:
            treatment_instructions += (
                "\n\nUse only these declared knowledge entries when needed:\n"
            )
            treatment_instructions += "\n".join(f"- {path}" for path in knowledge_paths)
    elif condition == "base":
        treatment_instructions = "Use only the fixture evidence and task below.\n"
    else:
        raise ValueError(f"Unsupported benchmark condition: {condition}")
    tool_text = "\n\n".join(
        path.read_text(encoding="utf-8").strip()
        for path in (tool_description_paths or [])
    )
    return f"""{treatment_instructions}

Inspect only this benchmark fixture and its files:
{fixture}

Task:
{task}

{tool_text}
{solution_output}
"""


def allowed_rule_ids(root: Path) -> list[str]:
    result: set[str] = set()
    for path in sorted((root / "resources" / "rules").glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        result.update(rule["id"] for rule in payload["rules"])
    if not result:
        raise RuntimeError("No machine Rule Pack IDs are available")
    return sorted(result)


def evidence_errors(observation: dict[str, Any], fixture: Path) -> list[str]:
    errors: list[str] = []
    fixture = fixture.resolve()
    for finding in observation["observed_findings"]:
        for index, evidence in enumerate(finding["evidence"], start=1):
            relative = Path(evidence["path"])
            label = f"{finding['rule_id']} evidence {index}"
            if relative.is_absolute() or ".." in relative.parts:
                errors.append(f"{label} path is not fixture-relative")
                continue
            source = (fixture / relative).resolve()
            try:
                source.relative_to(fixture)
                lines = source.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError, ValueError):
                errors.append(f"{label} path cannot be read")
                continue
            line_start = evidence["line_start"]
            line_end = evidence["line_end"]
            if line_end < line_start or line_end > len(lines):
                errors.append(f"{label} line range is invalid")
                continue
            selected = "\n".join(lines[line_start - 1 : line_end])
            if evidence["excerpt"] not in selected:
                errors.append(f"{label} excerpt is not verbatim in its line range")
    return errors


def validate_observation(observation: dict[str, Any], schema: dict[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(observation),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ValueError(
            "Codex observation failed schema validation: "
            + "; ".join(error.message for error in errors)
        )


def parse_cli_metadata(
    stdout: str, stderr: str
) -> tuple[str | None, str, dict[str, Any] | None]:
    """Read only CLI-emitted identity and telemetry, never model output metadata."""
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    model_candidates: list[str] = []
    usage_candidates: list[dict[str, Any]] = []
    for event in events:
        event_type = event.get("type")
        authoritative_field = (
            AUTHORITATIVE_MODEL_EVENT_FIELDS.get(event_type)
            if isinstance(event_type, str)
            else None
        )
        if authoritative_field is not None:
            value = event.get(authoritative_field)
            if isinstance(value, str) and value.strip():
                model_candidates.append(value.strip())
        usage = event.get("usage")
        if (
            event_type in USAGE_EVENT_TYPES
            and isinstance(usage, dict)
            and any(
                key in usage
                for key in ("input_tokens", "output_tokens", "cost_usd", "tool_calls")
            )
        ):
            usage_candidates.append(usage)
    actual_model = model_candidates[-1] if model_candidates else None
    source = "cli-json" if actual_model is not None else "unavailable"
    del stderr
    usage_result: dict[str, Any] | None = None
    if usage_candidates:
        latest = usage_candidates[-1]
        usage_result = {
            key: latest.get(key) if isinstance(latest.get(key), (int, float)) else None
            for key in ("input_tokens", "output_tokens", "cost_usd", "tool_calls")
        }
    return actual_model, source, usage_result


def command_envelope(
    *,
    requested_model: str,
    status: str,
    actual_model: str | None,
    actual_model_source: str,
    correction_count: int,
    usage: dict[str, Any] | None,
    observation: dict[str, Any] | None,
    failure_class: str | None = None,
    exit_code: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
        "status": status,
        "requested_model": requested_model,
        "actual_model": actual_model,
        "actual_model_source": actual_model_source,
        "model_fallback": False,
        "retries": {
            "generic": 0,
            "evidence_correction_count": correction_count,
            "evidence_correction_max": 1,
        },
        "usage": usage,
        "observation": observation,
    }
    if failure_class is not None:
        result["failure"] = {"class": failure_class, "exit_code": exit_code}
    return result


def execute_codex(
    *,
    codex: str,
    model: str,
    fixture: Path,
    schema_path: Path,
    output_path: Path,
    prompt: str,
    timeout: int,
) -> tuple[dict[str, Any], str | None, str, dict[str, Any] | None]:
    command = [
        codex,
        "exec",
        "--json",
        "--model",
        model,
        "--cd",
        str(fixture),
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        prompt,
    ]
    try:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterFailure("timeout") from exc
    except OSError as exc:
        raise AdapterFailure("tool-error") from exc
    if process.returncode != 0:
        raise AdapterFailure(
            "nonzero",
            exit_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )
    if not output_path.is_file():
        raise AdapterFailure(
            "tool-error",
            exit_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AdapterFailure("schema-invalid", exit_code=process.returncode) from exc
    if not isinstance(payload, dict):
        raise AdapterFailure("schema-invalid", exit_code=process.returncode)
    actual_model, source, usage = parse_cli_metadata(process.stdout, process.stderr)
    return payload, actual_model, source, usage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).parent.parent)
    parser.add_argument("--model", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--codex", default="codex")
    parser.add_argument(
        "--condition",
        choices=["base", "full", "compressed"],
        default="full",
    )
    parser.add_argument(
        "--context-manifest",
        type=Path,
        default=Path("benchmarks/ablation/context-manifest.yaml"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    fixture = within(root, args.fixture, "fixture")
    skill_path = within(
        root,
        root / "skills" / args.skill / "SKILL.md",
        "Skill",
    )
    if not fixture.is_dir():
        raise ValueError(f"Fixture is not a directory: {fixture}")
    if not skill_path.is_file():
        raise ValueError(f"Skill is missing: {skill_path}")
    context_manifest = load_context_manifest(root, args.context_manifest)
    treatment = treatment_for(
        context_manifest,
        condition=args.condition,
        skill=args.skill,
        case_id=args.case_id,
    )
    schema_path = within(
        root,
        root / "resources" / "schemas" / "benchmark-observation.schema.json",
        "observation schema",
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["observed_findings"]["items"]["properties"]["rule_id"] = {
        "type": "string",
        "enum": allowed_rule_ids(root),
    }
    codex = shutil.which(args.codex)
    if codex is None:
        print(
            json.dumps(
                command_envelope(
                    requested_model=args.model,
                    status="failed",
                    actual_model=None,
                    actual_model_source="unavailable",
                    correction_count=0,
                    usage=None,
                    observation=None,
                    failure_class="tool-error",
                    exit_code=None,
                )
            )
        )
        return 0
    prompt = build_prompt(
        skill_path=skill_path,
        knowledge_root=root / "resources" / "knowledge",
        fixture=fixture,
        task=args.prompt,
        condition=args.condition,
        compact_skill_paths=resolve_treatment_paths(root, treatment, "skill_body"),
        reference_paths=resolve_treatment_paths(root, treatment, "references"),
        knowledge_paths=resolve_treatment_paths(root, treatment, "knowledge"),
        tool_description_paths=resolve_treatment_paths(
            root,
            treatment,
            "tool_descriptions",
        ),
    )
    with tempfile.TemporaryDirectory(prefix="architecture-benchmark-") as temporary:
        temporary_root = Path(temporary)
        runtime_schema_path = temporary_root / "observation.schema.json"
        runtime_schema_path.write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        observation: dict[str, Any] | None = None
        actual_model: str | None = None
        actual_model_source = "unavailable"
        usage: dict[str, Any] | None = None
        correction_count = 0
        try:
            for attempt in range(2):
                output_path = temporary_root / f"observation-{attempt + 1}.json"
                observation, attempt_model, attempt_source, attempt_usage = (
                    execute_codex(
                        codex=codex,
                        model=args.model,
                        fixture=fixture,
                        schema_path=runtime_schema_path,
                        output_path=output_path,
                        prompt=prompt,
                        timeout=args.timeout,
                    )
                )
                if attempt_model is not None:
                    if actual_model is not None and attempt_model != actual_model:
                        raise AdapterFailure("tool-error")
                    actual_model = attempt_model
                    actual_model_source = attempt_source
                if attempt_usage is not None:
                    if usage is None:
                        usage = dict.fromkeys(attempt_usage, 0)
                    for key, value in attempt_usage.items():
                        if value is None or usage.get(key) is None:
                            usage[key] = None
                        else:
                            usage[key] += value
                try:
                    validate_observation(observation, schema)
                except ValueError as exc:
                    raise AdapterFailure("schema-invalid") from exc
                invalid_evidence = evidence_errors(observation, fixture)
                if not invalid_evidence:
                    break
                if attempt == 1:
                    raise AdapterFailure("schema-invalid")
                correction_count = 1
                prompt = f"""Reread the fixture and correct only the invalid evidence
in this prior observation:

{json.dumps(observation, ensure_ascii=False)}

Validation errors:
{chr(10).join(f"- {error}" for error in invalid_evidence)}

Return the complete JSON observation again. Preserve a finding only when a
fixture-relative, contiguous line range contains its excerpt byte-for-byte.
Prefer a one-line excerpt and copy leading indentation exactly.
"""
        except AdapterFailure as exc:
            failure_model, failure_source, failure_usage = parse_cli_metadata(
                exc.stdout,
                exc.stderr,
            )
            if failure_model is not None:
                actual_model = failure_model
                actual_model_source = failure_source
            if usage is None:
                usage = failure_usage
            print(
                json.dumps(
                    command_envelope(
                        requested_model=args.model,
                        status="failed",
                        actual_model=actual_model,
                        actual_model_source=actual_model_source,
                        correction_count=correction_count,
                        usage=usage,
                        observation=None,
                        failure_class=exc.failure_class,
                        exit_code=exc.exit_code,
                    ),
                    ensure_ascii=False,
                )
            )
            return 0
    print(
        json.dumps(
            command_envelope(
                requested_model=args.model,
                status="completed",
                actual_model=actual_model,
                actual_model_source=actual_model_source,
                correction_count=correction_count,
                usage=usage,
                observation=observation,
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
