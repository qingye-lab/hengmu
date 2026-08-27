#!/usr/bin/env python3
"""Publish or verify one immutable GitHub Release with six exact assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ReleaseError(RuntimeError):
    """The release state or remote verification is unsafe."""


@dataclass(frozen=True)
class LocalAsset:
    path: Path
    name: str
    size: int
    digest: str


@dataclass(frozen=True)
class RemoteAsset:
    name: str
    size: int
    digest: str | None


@dataclass(frozen=True)
class ReleaseState:
    draft: bool
    assets: tuple[RemoteAsset, ...]


class ReleaseClient(Protocol):
    def require_immutable_releases(self, repository: str) -> None: ...

    def get_release(self, repository: str, tag: str) -> ReleaseState | None: ...

    def create_draft(self, repository: str, tag: str, title: str) -> None: ...

    def upload_asset(
        self,
        repository: str,
        tag: str,
        path: Path,
        *,
        clobber: bool,
    ) -> None: ...

    def publish(self, repository: str, tag: str) -> None: ...

    def verify_release(self, repository: str, tag: str) -> bool: ...

    def verify_asset(self, repository: str, tag: str, path: Path) -> bool: ...


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_assets(dist: Path, tag: str) -> tuple[LocalAsset, ...]:
    if not tag.startswith("v") or len(tag) == 1:
        raise ReleaseError("Release tag must start with v and include a version")
    version = tag[1:]
    names = (
        f"hengmu-{version}.zip",
        f"hengmu-{version}.zip.sha256",
        f"hengmu-{version}-agent-plugins.zip",
        f"hengmu-{version}-agent-plugins.zip.sha256",
        f"hengmu-{version}.spdx.json",
        f"hengmu-{version}-agent-plugins.spdx.json",
    )
    assets: list[LocalAsset] = []
    for name in names:
        path = (dist / name).resolve()
        if not path.is_file():
            raise ReleaseError(f"Expected release asset is missing: {path}")
        assets.append(
            LocalAsset(
                path=path,
                name=name,
                size=path.stat().st_size,
                digest=sha256(path),
            )
        )
    return tuple(assets)


def validate_inventory(
    state: ReleaseState,
    assets: Sequence[LocalAsset],
) -> None:
    expected = {asset.name: asset for asset in assets}
    remote = {asset.name: asset for asset in state.assets}
    if len(remote) != len(state.assets):
        raise ReleaseError("Release contains duplicate asset names")
    unexpected = sorted(set(remote) - set(expected))
    missing = sorted(set(expected) - set(remote))
    if unexpected or missing:
        details = []
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        if missing:
            details.append("missing: " + ", ".join(missing))
        raise ReleaseError(
            "Release asset inventory mismatch (" + "; ".join(details) + ")"
        )
    for name, local in expected.items():
        uploaded = remote[name]
        if uploaded.size != local.size or uploaded.digest != f"sha256:{local.digest}":
            raise ReleaseError(f"Release asset digest or size mismatch: {name}")


def verify_with_retry(
    client: ReleaseClient,
    repository: str,
    tag: str,
    assets: Sequence[LocalAsset],
    *,
    sleep: Callable[[float], None],
    attempts: int,
    delay_seconds: float,
) -> None:
    for attempt in range(1, attempts + 1):
        release_ok = client.verify_release(repository, tag)
        assets_ok = release_ok and all(
            client.verify_asset(repository, tag, asset.path) for asset in assets
        )
        if release_ok and assets_ok:
            return
        if attempt < attempts:
            sleep(delay_seconds)
    raise ReleaseError(
        "Published Release verification failed; do not mutate it, "
        "publish a patch release"
    )


def publish_release(
    repository: str,
    tag: str,
    dist: Path,
    *,
    client: ReleaseClient,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 5,
    delay_seconds: float = 10.0,
) -> str:
    assets = expected_assets(dist.resolve(), tag)
    client.require_immutable_releases(repository)
    state = client.get_release(repository, tag)
    if state is not None and not state.draft:
        validate_inventory(state, assets)
        verify_with_retry(
            client,
            repository,
            tag,
            assets,
            sleep=sleep,
            attempts=attempts,
            delay_seconds=delay_seconds,
        )
        return "verified-existing"

    if state is None:
        client.create_draft(repository, tag, f"Hengmu {tag}")
        state = ReleaseState(draft=True, assets=())
    expected_names = {asset.name for asset in assets}
    unexpected = sorted(
        asset.name for asset in state.assets if asset.name not in expected_names
    )
    if unexpected:
        raise ReleaseError(
            "Draft Release contains unexpected assets: " + ", ".join(unexpected)
        )
    remote_by_name = {asset.name: asset for asset in state.assets}
    for asset in assets:
        remote = remote_by_name.get(asset.name)
        matches = remote is not None and (
            remote.size == asset.size and remote.digest == f"sha256:{asset.digest}"
        )
        if not matches:
            client.upload_asset(
                repository,
                tag,
                asset.path,
                clobber=remote is not None,
            )

    ready = client.get_release(repository, tag)
    if ready is None or not ready.draft:
        raise ReleaseError("Release changed state before publish preflight")
    validate_inventory(ready, assets)
    client.publish(repository, tag)
    verify_with_retry(
        client,
        repository,
        tag,
        assets,
        sleep=sleep,
        attempts=attempts,
        delay_seconds=delay_seconds,
    )
    return "published"


class GhReleaseClient:
    def _run(
        self,
        arguments: Sequence[str],
        *,
        allow_failure: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        try:
            process = subprocess.run(
                ["gh", *arguments],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise ReleaseError(f"GitHub CLI invocation failed: {exc}") from exc
        if process.returncode != 0 and not allow_failure:
            raise ReleaseError(process.stderr.strip() or "GitHub CLI command failed")
        return process

    def require_immutable_releases(self, repository: str) -> None:
        process = self._run(
            [
                "api",
                "-H",
                "X-GitHub-Api-Version: 2026-03-10",
                f"repos/{repository}/immutable-releases",
            ],
            allow_failure=True,
        )
        if process.returncode != 0:
            if "(HTTP 404)" in process.stderr:
                raise ReleaseError(
                    "GitHub immutable releases are not enabled for the repository"
                )
            raise ReleaseError(
                process.stderr.strip() or "GitHub immutable-release preflight failed"
            )
        try:
            payload: Any = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise ReleaseError(
                "GitHub immutable-release response is malformed"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("enabled"), bool
        ):
            raise ReleaseError("GitHub immutable-release response is malformed")
        enforced_by_owner = payload.get("enforced_by_owner")
        if enforced_by_owner is not None and not isinstance(enforced_by_owner, bool):
            raise ReleaseError("GitHub immutable-release response is malformed")
        if not payload["enabled"]:
            raise ReleaseError(
                "GitHub immutable releases are not enabled for the repository"
            )

    def get_release(self, repository: str, tag: str) -> ReleaseState | None:
        process = self._run(
            ["api", f"repos/{repository}/releases/tags/{tag}"],
            allow_failure=True,
        )
        if process.returncode != 0:
            if "(HTTP 404)" in process.stderr:
                return None
            raise ReleaseError(process.stderr.strip() or "GitHub Release lookup failed")
        payload: Any = json.loads(process.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("draft"), bool):
            raise ReleaseError("GitHub Release response is malformed")
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            raise ReleaseError("GitHub Release asset response is malformed")
        assets: list[RemoteAsset] = []
        for record in raw_assets:
            if not isinstance(record, dict):
                raise ReleaseError("GitHub Release asset entry is malformed")
            name = record.get("name")
            size = record.get("size")
            digest = record.get("digest")
            if not isinstance(name, str) or not isinstance(size, int):
                raise ReleaseError("GitHub Release asset identity is malformed")
            if digest is not None and not isinstance(digest, str):
                raise ReleaseError("GitHub Release asset digest is malformed")
            assets.append(RemoteAsset(name=name, size=size, digest=digest))
        return ReleaseState(draft=payload["draft"], assets=tuple(assets))

    def create_draft(self, repository: str, tag: str, title: str) -> None:
        self._run(
            [
                "release",
                "create",
                tag,
                "--repo",
                repository,
                "--draft",
                "--generate-notes",
                "--title",
                title,
            ]
        )

    def upload_asset(
        self,
        repository: str,
        tag: str,
        path: Path,
        *,
        clobber: bool,
    ) -> None:
        arguments = ["release", "upload", tag, str(path), "--repo", repository]
        if clobber:
            arguments.append("--clobber")
        self._run(arguments)

    def publish(self, repository: str, tag: str) -> None:
        self._run(["release", "edit", tag, "--repo", repository, "--draft=false"])

    def verify_release(self, repository: str, tag: str) -> bool:
        return (
            self._run(
                ["release", "verify", tag, "--repo", repository],
                allow_failure=True,
            ).returncode
            == 0
        )

    def verify_asset(self, repository: str, tag: str, path: Path) -> bool:
        return (
            self._run(
                [
                    "release",
                    "verify-asset",
                    tag,
                    str(path),
                    "--repo",
                    repository,
                ],
                allow_failure=True,
            ).returncode
            == 0
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        outcome = publish_release(
            args.repository,
            args.tag,
            args.dist,
            client=GhReleaseClient(),
        )
    except (ReleaseError, OSError, json.JSONDecodeError) as exc:
        print(f"Release publication failed: {exc}", file=sys.stderr)
        return 2
    print(f"Release publication state: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
