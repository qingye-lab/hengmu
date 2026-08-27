from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        immutable_error: str | None = None,
    ) -> None:
        self.state = state
        self.verify_results = list(verify_results or [True])
        self.immutable_error = immutable_error
        self.calls: list[tuple[object, ...]] = []

    def require_immutable_releases(self, repository: str) -> None:
        self.calls.append(("immutable", repository))
        if self.immutable_error is not None:
            raise publish_release.ReleaseError(self.immutable_error)

    def get_release(self, repository: str, tag: str):
        self.calls.append(("get", repository, tag))
        return self.state

    def create_draft(self, repository: str, tag: str, title: str) -> None:
        self.calls.append(("create", tag, title))
        self.state = publish_release.ReleaseState(
            draft=True,
            immutable=False,
            assets=(),
        )

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
            immutable=False,
            assets=(*retained, remote(local)),
        )

    def publish(self, repository: str, tag: str) -> None:
        self.calls.append(("publish", tag))
        self.state = publish_release.ReleaseState(
            draft=False,
            immutable=True,
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

    def test_prepare_absent_release_creates_draft_and_uploads_exact_assets(
        self,
    ) -> None:
        client = FakeReleaseClient()
        outcome = publish_release.prepare_release(
            "owner/repo",
            "v1.1.0",
            self.dist,
            client=client,
        )
        self.assertEqual(outcome, "draft-prepared")
        self.assertEqual(client.calls[0], ("get", "owner/repo", "v1.1.0"))
        self.assertEqual(sum(call[0] == "create" for call in client.calls), 1)
        uploads = [call for call in client.calls if call[0] == "upload"]
        self.assertEqual(len(uploads), 6)
        self.assertTrue(all(call[2] is False for call in uploads))
        self.assertFalse(
            any(call[0] in {"immutable", "publish"} for call in client.calls)
        )

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
                immutable=False,
                assets=(matching, mismatched),
            )
        )
        publish_release.prepare_release(
            "owner/repo",
            "v1.1.0",
            self.dist,
            client=client,
        )
        uploads = [call for call in client.calls if call[0] == "upload"]
        self.assertNotIn(("upload", self.assets[0].name, False), uploads)
        self.assertIn(("upload", self.assets[1].name, True), uploads)
        self.assertEqual(len(uploads), 5)

    def test_unexpected_draft_asset_fails_without_mutation(self) -> None:
        state = publish_release.ReleaseState(
            draft=True,
            immutable=False,
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
            publish_release.prepare_release(
                "owner/repo",
                "v1.1.0",
                self.dist,
                client=client,
            )
        self.assertFalse(
            any(call[0] in {"immutable", "upload", "publish"} for call in client.calls)
        )

    def test_published_release_is_read_only_and_idempotent(self) -> None:
        state = publish_release.ReleaseState(
            draft=False,
            immutable=True,
            assets=tuple(remote(asset) for asset in self.assets),
        )
        client = FakeReleaseClient(state)
        outcome = publish_release.prepare_release(
            "owner/repo",
            "v1.1.0",
            self.dist,
            client=client,
        )
        self.assertEqual(outcome, "already-published")
        self.assertFalse(
            any(
                call[0] in {"immutable", "create", "upload", "publish"}
                for call in client.calls
            )
        )

    def test_prepare_rejects_mutable_published_release_without_mutation(self) -> None:
        state = publish_release.ReleaseState(
            draft=False,
            immutable=False,
            assets=tuple(remote(asset) for asset in self.assets),
        )
        client = FakeReleaseClient(state)
        with self.assertRaisesRegex(publish_release.ReleaseError, "not immutable"):
            publish_release.prepare_release(
                "owner/repo",
                "v1.1.0",
                self.dist,
                client=client,
            )
        self.assertEqual(client.calls, [("get", "owner/repo", "v1.1.0")])

    def test_publish_accepts_only_a_complete_exact_draft(self) -> None:
        unsafe_states = (
            (
                None,
                "absent",
            ),
            (
                publish_release.ReleaseState(
                    draft=True,
                    immutable=False,
                    assets=tuple(remote(asset) for asset in self.assets[:-1]),
                ),
                "missing",
            ),
            (
                publish_release.ReleaseState(
                    draft=True,
                    immutable=False,
                    assets=(
                        *(remote(asset) for asset in self.assets),
                        publish_release.RemoteAsset(
                            name="unexpected.txt",
                            size=1,
                            digest="sha256:" + "0" * 64,
                        ),
                    ),
                ),
                "unexpected",
            ),
            (
                publish_release.ReleaseState(
                    draft=True,
                    immutable=False,
                    assets=(remote(self.assets[0]), remote(self.assets[0])),
                ),
                "duplicate",
            ),
        )
        for state, message in unsafe_states:
            with self.subTest(message=message):
                client = FakeReleaseClient(state)
                with self.assertRaisesRegex(publish_release.ReleaseError, message):
                    publish_release.publish_release(
                        "owner/repo",
                        "v1.1.0",
                        self.dist,
                        client=client,
                    )
                self.assertEqual(
                    client.calls[:2],
                    [("immutable", "owner/repo"), ("get", "owner/repo", "v1.1.0")],
                )
                self.assertFalse(
                    any(
                        call[0] in {"create", "upload", "publish"}
                        for call in client.calls
                    )
                )

    def test_publish_complete_draft_publishes_once_then_verifies(self) -> None:
        state = publish_release.ReleaseState(
            draft=True,
            immutable=False,
            assets=tuple(remote(asset) for asset in self.assets),
        )
        client = FakeReleaseClient(state)
        outcome = publish_release.publish_release(
            "owner/repo",
            "v1.1.0",
            self.dist,
            client=client,
        )
        self.assertEqual(outcome, "published")
        self.assertEqual(
            client.calls[:4],
            [
                ("immutable", "owner/repo"),
                ("get", "owner/repo", "v1.1.0"),
                ("publish", "v1.1.0"),
                ("get", "owner/repo", "v1.1.0"),
            ],
        )
        self.assertEqual(sum(call[0] == "publish" for call in client.calls), 1)
        self.assertEqual(sum(call[0] == "verify-release" for call in client.calls), 1)
        self.assertEqual(sum(call[0] == "verify-asset" for call in client.calls), 6)

    def test_publish_existing_release_is_read_only_and_requires_immutable(self) -> None:
        for immutable, message in ((True, None), (False, "not immutable")):
            with self.subTest(immutable=immutable):
                state = publish_release.ReleaseState(
                    draft=False,
                    immutable=immutable,
                    assets=tuple(remote(asset) for asset in self.assets),
                )
                client = FakeReleaseClient(state)
                if message is None:
                    outcome = publish_release.publish_release(
                        "owner/repo",
                        "v1.1.0",
                        self.dist,
                        client=client,
                    )
                    self.assertEqual(outcome, "verified-existing")
                else:
                    with self.assertRaisesRegex(publish_release.ReleaseError, message):
                        publish_release.publish_release(
                            "owner/repo",
                            "v1.1.0",
                            self.dist,
                            client=client,
                        )
                self.assertFalse(
                    any(
                        call[0] in {"create", "upload", "publish"}
                        for call in client.calls
                    )
                )

    def test_post_publish_verification_retries_five_times_then_requires_patch(
        self,
    ) -> None:
        state = publish_release.ReleaseState(
            draft=True,
            immutable=False,
            assets=tuple(remote(asset) for asset in self.assets),
        )
        client = FakeReleaseClient(state, verify_results=[False] * 5)
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

    def test_post_publish_readback_must_report_immutable_before_verification(
        self,
    ) -> None:
        class MutableAfterPublishClient(FakeReleaseClient):
            def publish(self, repository: str, tag: str) -> None:
                self.calls.append(("publish", tag))
                self.state = publish_release.ReleaseState(
                    draft=False,
                    immutable=False,
                    assets=self.state.assets,
                )

        state = publish_release.ReleaseState(
            draft=True,
            immutable=False,
            assets=tuple(remote(asset) for asset in self.assets),
        )
        client = MutableAfterPublishClient(state)
        with self.assertRaisesRegex(publish_release.ReleaseError, "not immutable"):
            publish_release.publish_release(
                "owner/repo",
                "v1.1.0",
                self.dist,
                client=client,
            )
        self.assertEqual(sum(call[0] == "publish" for call in client.calls), 1)
        self.assertFalse(any(call[0].startswith("verify") for call in client.calls))

    def test_missing_local_asset_fails_before_remote_access(self) -> None:
        (self.dist / self.assets[-1].name).unlink()
        client = FakeReleaseClient()
        with self.assertRaisesRegex(publish_release.ReleaseError, "missing"):
            publish_release.prepare_release(
                "owner/repo",
                "v1.1.0",
                self.dist,
                client=client,
            )
        self.assertEqual(client.calls, [])

    def test_immutable_release_preflight_failure_has_zero_remote_mutation(
        self,
    ) -> None:
        for failure in (
            "immutable releases are disabled",
            "immutable-release endpoint returned 404",
            "immutable-release response is malformed",
            "authentication failed",
            "GitHub API failed",
            "GitHub CLI failed",
        ):
            with self.subTest(failure=failure):
                client = FakeReleaseClient(immutable_error=failure)
                with self.assertRaisesRegex(
                    publish_release.ReleaseError,
                    failure,
                ):
                    publish_release.publish_release(
                        "owner/repo",
                        "v1.1.0",
                        self.dist,
                        client=client,
                    )
                self.assertEqual(client.calls, [("immutable", "owner/repo")])

    def test_gh_immutable_release_preflight_is_read_only_and_fail_closed(
        self,
    ) -> None:
        client = publish_release.GhReleaseClient()
        enabled = CompletedProcess(
            [],
            0,
            json.dumps({"enabled": True, "enforced_by_owner": False}),
            "",
        )
        with patch.object(client, "_run", return_value=enabled) as run:
            client.require_immutable_releases("owner/repo")
        run.assert_called_once_with(
            [
                "api",
                "-H",
                "X-GitHub-Api-Version: 2026-03-10",
                "repos/owner/repo/immutable-releases",
            ],
            allow_failure=True,
        )

        failures = (
            (
                CompletedProcess([], 1, "", "gh: Not Found (HTTP 404)\n"),
                "not enabled",
            ),
            (CompletedProcess([], 0, '{"enabled": false}', ""), "not enabled"),
            (CompletedProcess([], 0, "not-json", ""), "malformed"),
            (CompletedProcess([], 0, '{"enabled": "yes"}', ""), "malformed"),
            (
                CompletedProcess(
                    [],
                    0,
                    '{"enabled": true, "enforced_by_owner": "yes"}',
                    "",
                ),
                "malformed",
            ),
            (CompletedProcess([], 1, "", "authentication failed\n"), "authentication"),
        )
        for completed, message in failures:
            with (
                self.subTest(completed=completed, message=message),
                patch.object(client, "_run", return_value=completed),
                self.assertRaisesRegex(publish_release.ReleaseError, message),
            ):
                client.require_immutable_releases("owner/repo")

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
        with patch.object(client, "_run", return_value=not_found) as run:
            self.assertIsNone(client.get_release("owner/repo", "v1.1.0"))
        run.assert_called_once_with(
            [
                "api",
                "-H",
                "X-GitHub-Api-Version: 2026-03-10",
                "repos/owner/repo/releases/tags/v1.1.0",
            ],
            allow_failure=True,
        )

        unauthorized = CompletedProcess([], 1, "", "authentication failed\n")
        with (
            patch.object(client, "_run", return_value=unauthorized),
            self.assertRaisesRegex(publish_release.ReleaseError, "authentication"),
        ):
            client.get_release("owner/repo", "v1.1.0")

    def test_gh_client_parses_inventory_and_issues_mutation_commands(self) -> None:
        client = publish_release.GhReleaseClient()
        payload = {
            "draft": True,
            "immutable": False,
            "assets": [
                {
                    "name": self.assets[0].name,
                    "size": self.assets[0].size,
                    "digest": f"sha256:{self.assets[0].digest}",
                }
            ],
        }
        completed = CompletedProcess([], 0, json.dumps(payload), "")
        with patch.object(publish_release.subprocess, "run", return_value=completed):
            state = client.get_release("owner/repo", "v1.1.0")
        self.assertIsNotNone(state)
        assert state is not None
        self.assertTrue(state.draft)
        self.assertFalse(state.immutable)
        self.assertEqual(state.assets[0].name, self.assets[0].name)

        ok = CompletedProcess([], 0, "", "")
        with patch.object(client, "_run", return_value=ok) as run:
            client.create_draft("owner/repo", "v1.1.0", "Hengmu v1.1.0")
            client.upload_asset(
                "owner/repo",
                "v1.1.0",
                self.assets[0].path,
                clobber=False,
            )
            client.upload_asset(
                "owner/repo",
                "v1.1.0",
                self.assets[0].path,
                clobber=True,
            )
            client.publish("owner/repo", "v1.1.0")
            self.assertTrue(client.verify_release("owner/repo", "v1.1.0"))
        self.assertEqual(run.call_count, 5)
        self.assertNotIn("--clobber", run.call_args_list[1].args[0])
        self.assertIn("--clobber", run.call_args_list[2].args[0])

    def test_gh_command_failure_and_invalid_inventory_fail_closed(self) -> None:
        client = publish_release.GhReleaseClient()
        failed = CompletedProcess([], 1, "", "command failed\n")
        with (
            patch.object(publish_release.subprocess, "run", return_value=failed),
            self.assertRaisesRegex(publish_release.ReleaseError, "command failed"),
        ):
            client._run(["release", "view", "v1.1.0"])
        with (
            patch.object(
                publish_release.subprocess,
                "run",
                side_effect=OSError("gh is unavailable"),
            ),
            self.assertRaisesRegex(
                publish_release.ReleaseError,
                "GitHub CLI invocation failed",
            ),
        ):
            client.require_immutable_releases("owner/repo")

        with self.assertRaisesRegex(publish_release.ReleaseError, "start with v"):
            publish_release.expected_assets(self.dist, "1.1.0")
        duplicate = publish_release.ReleaseState(
            draft=True,
            immutable=False,
            assets=(remote(self.assets[0]), remote(self.assets[0])),
        )
        with self.assertRaisesRegex(publish_release.ReleaseError, "duplicate"):
            publish_release.validate_inventory(duplicate, self.assets)
        with self.assertRaisesRegex(publish_release.ReleaseError, "missing"):
            publish_release.validate_inventory(
                publish_release.ReleaseState(
                    draft=True,
                    immutable=False,
                    assets=(),
                ),
                self.assets,
            )
        mismatched = publish_release.ReleaseState(
            draft=True,
            immutable=False,
            assets=(
                publish_release.RemoteAsset(
                    name=self.assets[0].name,
                    size=0,
                    digest=f"sha256:{self.assets[0].digest}",
                ),
                *(remote(asset) for asset in self.assets[1:]),
            ),
        )
        with self.assertRaisesRegex(publish_release.ReleaseError, "mismatch"):
            publish_release.validate_inventory(mismatched, self.assets)

        malformed_release_payloads = (
            {"draft": True, "assets": []},
            {"draft": True, "immutable": "yes", "assets": []},
            {"draft": True, "immutable": False, "assets": "invalid"},
        )
        for payload in malformed_release_payloads:
            with (
                self.subTest(payload=payload),
                patch.object(
                    client,
                    "_run",
                    return_value=CompletedProcess([], 0, json.dumps(payload), ""),
                ),
                self.assertRaisesRegex(publish_release.ReleaseError, "malformed"),
            ):
                client.get_release("owner/repo", "v1.1.0")
        with (
            patch.object(
                client,
                "_run",
                return_value=CompletedProcess([], 0, "not-json", ""),
            ),
            self.assertRaisesRegex(publish_release.ReleaseError, "malformed"),
        ):
            client.get_release("owner/repo", "v1.1.0")

    def test_main_reports_success_and_release_errors(self) -> None:
        arguments = [
            str(SCRIPT),
            "publish",
            "--repository",
            "owner/repo",
            "--tag",
            "v1.1.0",
            "--dist",
            str(self.dist),
        ]
        stdout = io.StringIO()
        with (
            patch.object(sys, "argv", arguments),
            patch.object(
                publish_release,
                "publish_release",
                return_value="verified-existing",
            ),
            redirect_stdout(stdout),
        ):
            self.assertEqual(publish_release.main(), 0)
        self.assertIn("verified-existing", stdout.getvalue())

        prepare_arguments = [*arguments]
        prepare_arguments[1] = "prepare"
        with (
            patch.object(sys, "argv", prepare_arguments),
            patch.object(
                publish_release,
                "prepare_release",
                return_value="draft-prepared",
            ) as prepare,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(publish_release.main(), 0)
        prepare.assert_called_once()

        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", arguments),
            patch.object(
                publish_release,
                "publish_release",
                side_effect=publish_release.ReleaseError("unsafe state"),
            ),
            redirect_stderr(stderr),
        ):
            self.assertEqual(publish_release.main(), 2)
        self.assertIn("unsafe state", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
