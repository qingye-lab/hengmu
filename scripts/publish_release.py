#!/usr/bin/env python3
"""Prepare or publish one immutable GitHub Release with six exact assets."""

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
    immutable: bool
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


def require_published_state(
    state: ReleaseState | None,
    assets: Sequence[LocalAsset],
) -> ReleaseState:
    if state is None or state.draft:
        raise ReleaseError(
            "Release is not a complete draft; run the prepare phase first"
        )
    if not state.immutable:
        raise ReleaseError("Published Release is not immutable")
    validate_inventory(state, assets)
    return state


def prepare_release(
    repository: str,
    tag: str,
    dist: Path,
    *,
    client: ReleaseClient,
) -> str:
    assets = expected_assets(dist.resolve(), tag)
    state = client.get_release(repository, tag)
    if state is not None and not state.draft:
        require_published_state(state, assets)
        return "already-published"

    if state is None:
        client.create_draft(repository, tag, f"Hengmu {tag}")
        state = ReleaseState(draft=True, immutable=False, assets=())
    if len({asset.name for asset in state.assets}) != len(state.assets):
        raise ReleaseError("Draft Release contains duplicate asset names")
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
        raise ReleaseError("Release changed state before draft validation")
    validate_inventory(ready, assets)
    return "draft-prepared"


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
        require_published_state(state, assets)
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
        raise ReleaseError("Release draft is absent; run the prepare phase first")
    validate_inventory(state, assets)
    client.publish(repository, tag)
    published = client.get_release(repository, tag)
    require_published_state(published, assets)
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

    @staticmethod
    def _load_json(process: subprocess.CompletedProcess[str], message: str) -> Any:
        try:
            return json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise ReleaseError(message) from exc

    @staticmethod
    def _parse_release_state(
        payload: Any,
        *,
        expected_tag: str | None = None,
        expected_id: int | None = None,
    ) -> ReleaseState:
        if not isinstance(payload, dict):
            raise ReleaseError("GitHub Release response is malformed")
        if expected_tag is not None and payload.get("tag_name") != expected_tag:
            raise ReleaseError("GitHub Release tag identity is malformed")
        if expected_id is not None:
            actual_id = payload.get("id")
            if (
                not isinstance(actual_id, int)
                or isinstance(actual_id, bool)
                or actual_id != expected_id
            ):
                raise ReleaseError("GitHub Release numeric identity is malformed")
        if not isinstance(payload.get("draft"), bool) or not isinstance(
            payload.get("immutable"), bool
        ):
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
            if (
                not isinstance(name, str)
                or not isinstance(size, int)
                or isinstance(size, bool)
            ):
                raise ReleaseError("GitHub Release asset identity is malformed")
            if digest is not None and not isinstance(digest, str):
                raise ReleaseError("GitHub Release asset digest is malformed")
            assets.append(RemoteAsset(name=name, size=size, digest=digest))
        return ReleaseState(
            draft=payload["draft"],
            immutable=payload["immutable"],
            assets=tuple(assets),
        )

    def _find_release_id(self, repository: str, tag: str) -> int | None:
        process = self._run(
            [
                "api",
                "--paginate",
                "--slurp",
                "-H",
                "X-GitHub-Api-Version: 2026-03-10",
                f"repos/{repository}/releases?per_page=100",
            ]
        )
        pages = self._load_json(process, "GitHub Release list response is malformed")
        if not isinstance(pages, list):
            raise ReleaseError("GitHub Release list response is malformed")
        matches: list[int] = []
        for page in pages:
            if not isinstance(page, list):
                raise ReleaseError("GitHub Release list response is malformed")
            for record in page:
                if not isinstance(record, dict):
                    raise ReleaseError("GitHub Release list entry is malformed")
                release_id = record.get("id")
                tag_name = record.get("tag_name")
                if (
                    not isinstance(release_id, int)
                    or isinstance(release_id, bool)
                    or release_id <= 0
                    or not isinstance(tag_name, str)
                ):
                    raise ReleaseError("GitHub Release list identity is malformed")
                if tag_name == tag:
                    matches.append(release_id)
        if not matches:
            return None
        if len(matches) != 1:
            raise ReleaseError("GitHub Release list contains duplicate exact tags")
        return matches[0]

    def get_release(self, repository: str, tag: str) -> ReleaseState | None:
        process = self._run(
            [
                "api",
                "-H",
                "X-GitHub-Api-Version: 2026-03-10",
                f"repos/{repository}/releases/tags/{tag}",
            ],
            allow_failure=True,
        )
        if process.returncode != 0:
            if "(HTTP 404)" not in process.stderr:
                raise ReleaseError(
                    process.stderr.strip() or "GitHub Release lookup failed"
                )
            release_id = self._find_release_id(repository, tag)
            if release_id is None:
                return None
            authoritative = self._run(
                [
                    "api",
                    "-H",
                    "X-GitHub-Api-Version: 2026-03-10",
                    f"repos/{repository}/releases/{release_id}",
                ]
            )
            payload = self._load_json(
                authoritative,
                "GitHub Release response is malformed",
            )
            return self._parse_release_state(
                payload,
                expected_tag=tag,
                expected_id=release_id,
            )
        payload = self._load_json(process, "GitHub Release response is malformed")
        return self._parse_release_state(payload, expected_tag=tag)

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
    parser.add_argument("mode", choices=("prepare", "publish"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        client = GhReleaseClient()
        if args.mode == "prepare":
            outcome = prepare_release(
                args.repository,
                args.tag,
                args.dist,
                client=client,
            )
        else:
            outcome = publish_release(
                args.repository,
                args.tag,
                args.dist,
                client=client,
            )
    except (ReleaseError, OSError, json.JSONDecodeError) as exc:
        print(f"Release publication failed: {exc}", file=sys.stderr)
        return 2
    print(f"Release {args.mode} state: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
