from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "publish_release.py"
SPEC = importlib.util.spec_from_file_location("publish_release", SCRIPT)
assert SPEC and SPEC.loader
publish_release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publish_release
SPEC.loader.exec_module(publish_release)


class FakeReleaseClient:
    def __init__(
        self,
        state=None,
        *,
        verify_results: list[bool] | None = None,
    ) -> None:
        self.state = state
        self.verify_results = list(verify_results or [True])
        self.calls: list[tuple[object, ...]] = []

    def get_release(self, repository: str, tag: str):
        self.calls.append(("get", repository, tag))
        return self.state

    def create_draft(self, repository: str, tag: str, title: str) -> None:
        self.calls.append(("create", tag, title))
        self.state = publish_release.ReleaseState(draft=True, assets=())

    def upload_asset(
        self,
        repository: str,
        tag: str,
        path: Path,
        *,
        clobber: bool,
    ) -> None:
        self.calls.append(("upload", path.name, clobber))
        local = publish_release.LocalAsset(
            path=path,
            name=path.name,
            size=path.stat().st_size,
            digest=publish_release.sha256(path),
        )
        retained = tuple(
            asset for asset in self.state.assets if asset.name != local.name
        )
        self.state = publish_release.ReleaseState(
            draft=True,
            assets=(*retained, remote(local)),
        )

    def publish(self, repository: str, tag: str) -> None:
        self.calls.append(("publish", tag))
        self.state = publish_release.ReleaseState(
            draft=False,
            assets=self.state.assets,
        )

    def verify_release(self, repository: str, tag: str) -> bool:
        self.calls.append(("verify-release", tag))
        if len(self.verify_results) > 1:
            return self.verify_results.pop(0)
        return self.verify_results[0]

    def verify_asset(self, repository: str, tag: str, path: Path) -> bool:
        self.calls.append(("verify-asset", path.name))
        return True


def remote(local):
    return publish_release.RemoteAsset(
        name=local.name,
        size=local.size,
        digest=f"sha256:{local.digest}",
    )


class ReleasePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.dist = Path(self.temporary.name)
        version = "1.1.0"
        for name in (
            f"hengmu-{version}.zip",
            f"hengmu-{version}.zip.sha256",
            f"hengmu-{version}-agent-plugins.zip",
            f"hengmu-{version}-agent-plugins.zip.sha256",
            f"hengmu-{version}.spdx.json",
            f"hengmu-{version}-agent-plugins.spdx.json",
        ):
            (self.dist / name).write_bytes(f"asset:{name}\n".encode())
        self.assets = publish_release.expected_assets(self.dist, "v1.1.0")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_absent_release_creates_draft_uploads_exact_assets_and_publishes(
        self,
    ) -> None:
        client = FakeReleaseClient()
        outcome = publish_release.publish_release(
            "owner/repo",
            "v1.1.0",
            self.dist,
            client=client,
            sleep=lambda _: None,
        )
        self.assertEqual(outcome, "published")
        self.assertEqual(sum(call[0] == "create" for call in client.calls), 1)
        uploads = [call for call in client.calls if call[0] == "upload"]
        self.assertEqual(len(uploads), 6)
        self.assertTrue(all(call[2] is False for call in uploads))
        self.assertEqual(sum(call[0] == "publish" for call in client.calls), 1)

    def test_partial_draft_resumes_and_clobbers_only_mismatched_expected_asset(
        self,
    ) -> None:
        matching = remote(self.assets[0])
        mismatched = publish_release.RemoteAsset(
            name=self.assets[1].name,
            size=1,
            digest="sha256:" + "0" * 64,
        )
        client = FakeReleaseClient(
            publish_release.ReleaseState(
                draft=True,
                assets=(matching, mismatched),
            )
        )
        publish_release.publish_release(
            "owner/repo",
            "v1.1.0",
            self.dist,
            client=client,
            sleep=lambda _: None,
        )
        uploads = [call for call in client.calls if call[0] == "upload"]
        self.assertNotIn(("upload", self.assets[0].name, False), uploads)
        self.assertIn(("upload", self.assets[1].name, True), uploads)
        self.assertEqual(len(uploads), 5)

    def test_unexpected_draft_asset_fails_without_mutation(self) -> None:
        state = publish_release.ReleaseState(
            draft=True,
            assets=(
                publish_release.RemoteAsset(
                    name="unexpected.txt",
                    size=1,
                    digest="sha256:" + "0" * 64,
                ),
            ),
        )
        client = FakeReleaseClient(state)
        with self.assertRaisesRegex(publish_release.ReleaseError, "unexpected"):
            publish_release.publish_release(
                "owner/repo",
                "v1.1.0",
                self.dist,
                client=client,
                sleep=lambda _: None,
            )
        self.assertFalse(any(call[0] in {"upload", "publish"} for call in client.calls))

    def test_published_release_is_read_only_and_idempotent(self) -> None:
        state = publish_release.ReleaseState(
            draft=False,
            assets=tuple(remote(asset) for asset in self.assets),
        )
        client = FakeReleaseClient(state)
        outcome = publish_release.publish_release(
            "owner/repo",
            "v1.1.0",
            self.dist,
            client=client,
            sleep=lambda _: None,
        )
        self.assertEqual(outcome, "verified-existing")
        self.assertFalse(
            any(call[0] in {"create", "upload", "publish"} for call in client.calls)
        )

    def test_post_publish_verification_retries_five_times_then_requires_patch(
        self,
    ) -> None:
        client = FakeReleaseClient(verify_results=[False] * 5)
        sleeps: list[float] = []
        with self.assertRaisesRegex(publish_release.ReleaseError, "patch release"):
            publish_release.publish_release(
                "owner/repo",
                "v1.1.0",
                self.dist,
                client=client,
                sleep=sleeps.append,
            )
        self.assertEqual(sleeps, [10.0] * 4)
        self.assertEqual(
            sum(call[0] == "verify-release" for call in client.calls),
            5,
        )
        self.assertEqual(sum(call[0] == "publish" for call in client.calls), 1)

    def test_missing_local_asset_fails_before_remote_access(self) -> None:
        (self.dist / self.assets[-1].name).unlink()
        client = FakeReleaseClient()
        with self.assertRaisesRegex(publish_release.ReleaseError, "missing"):
            publish_release.publish_release(
                "owner/repo",
                "v1.1.0",
                self.dist,
                client=client,
            )
        self.assertEqual(client.calls, [])

    def test_gh_asset_verification_is_bound_to_requested_tag(self) -> None:
        client = publish_release.GhReleaseClient()
        asset = self.dist / self.assets[0].name
        completed = CompletedProcess([], 0, "", "")
        with patch.object(client, "_run", return_value=completed) as run:
            self.assertTrue(client.verify_asset("owner/repo", "v1.1.0", asset))
        run.assert_called_once_with(
            [
                "release",
                "verify-asset",
                "v1.1.0",
                str(asset),
                "--repo",
                "owner/repo",
            ],
            allow_failure=True,
        )

    def test_gh_release_lookup_treats_only_http_404_as_absent(self) -> None:
        client = publish_release.GhReleaseClient()
        not_found = CompletedProcess([], 1, "", "gh: Not Found (HTTP 404)\n")
        with patch.object(client, "_run", return_value=not_found):
            self.assertIsNone(client.get_release("owner/repo", "v1.1.0"))

        unauthorized = CompletedProcess([], 1, "", "authentication failed\n")
        with (
            patch.object(client, "_run", return_value=unauthorized),
            self.assertRaisesRegex(publish_release.ReleaseError, "authentication"),
        ):
            client.get_release("owner/repo", "v1.1.0")


if __name__ == "__main__":
    unittest.main()
