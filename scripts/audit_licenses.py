#!/usr/bin/env python3
"""Require an approved SPDX license for every locked runtime dependency."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

PACKAGE_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s\\]+)")
DENIED_LICENSES = {
    "AGPL-3.0",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "GPL-3.0",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
}


class LicenseAuditError(RuntimeError):
    """License policy or lock mismatch."""


def normalize_name(value: str) -> str:
    return value.lower().replace("_", "-")


def locked_packages(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PACKAGE_RE.match(line.strip())
        if match is None:
            continue
        name = normalize_name(match.group("name"))
        version = match.group("version")
        if name in result and result[name] != version:
            raise LicenseAuditError(f"{path} pins {name} more than once")
        result[name] = version
    if not result:
        raise LicenseAuditError(f"{path} contains no exact package pins")
    return result


def audit(lock_path: Path, policy_path: Path) -> dict[str, Any]:
    locked = locked_packages(lock_path)
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        packages = policy["packages"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
        raise LicenseAuditError(
            f"Cannot read license policy {policy_path}: {exc}"
        ) from exc
    if policy.get("schema_version") != "1.0" or not isinstance(packages, dict):
        raise LicenseAuditError(f"{policy_path} is not a 1.0 license policy")
    policy_names = {normalize_name(name) for name in packages}
    missing = sorted(set(locked) - policy_names)
    extra = sorted(policy_names - set(locked))
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("not locked " + ", ".join(extra))
        raise LicenseAuditError(
            "License policy coverage mismatch: " + "; ".join(detail)
        )
    audited = []
    for name, version in sorted(locked.items()):
        record = packages[name]
        if not isinstance(record, dict):
            raise LicenseAuditError(f"License policy entry {name} must be an object")
        license_id = record.get("license")
        source = record.get("source")
        if (
            not isinstance(license_id, str)
            or not license_id
            or license_id == "NOASSERTION"
        ):
            raise LicenseAuditError(f"License policy entry {name} is unapproved")
        if license_id in DENIED_LICENSES:
            raise LicenseAuditError(
                f"License policy entry {name} uses denied license {license_id}"
            )
        if not isinstance(source, str) or not source.startswith("https://"):
            raise LicenseAuditError(
                f"License policy entry {name} requires an HTTPS source"
            )
        audited.append(
            {
                "name": name,
                "version": version,
                "license": license_id,
                "source": source,
            }
        )
    return {
        "status": "pass",
        "reviewed_on": policy.get("reviewed_on"),
        "packages": audited,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock",
        type=Path,
        default=Path("requirements-runtime.lock"),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("resources/supply-chain/runtime-licenses.json"),
    )
    args = parser.parse_args()
    try:
        result = audit(args.lock, args.policy)
    except (OSError, LicenseAuditError) as exc:
        print(f"License audit failed: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
