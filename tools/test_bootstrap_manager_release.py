"""Current-footprint preparation shares release verification, not layout export."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from unittest.mock import Mock

import pytest

import bootstrap_manager_release as release
import artifact_release_discovery as discovery
import local_install_source as source
from promotion_source_history import PIPELINE_STATE_PATH


@pytest.fixture(autouse=True)
def published_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        source.preparation, "_git",
        lambda *_args, **_kwargs: PIPELINE_STATE_PATH,
    )


def test_source_before_wheel_channel_preserves_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source.preparation, "_git", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        discovery, "discover_promoted_source",
        lambda *_args, **_kwargs: pytest.fail("no release provenance"),
    )
    with pytest.raises(release.ReleaseInputsUnavailable, match="predates"):
        release.prepare_snapshot(
            tmp_path, "a" * 40, "example/repository", tmp_path / "output",
            tmp_path / "uv", time.monotonic() + 60, component="worktree-manager",
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("artifacts", [None, []])
def test_missing_release_inputs_are_typed_before_source_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifacts: list[dict[str, Any]] | None,
) -> None:
    monkeypatch.setattr(
        discovery, "discover_promoted_source",
        lambda *_args, **_kwargs: None if artifacts is None else ({"artifacts": artifacts}, {}),
    )
    monkeypatch.setattr(source, "compose", lambda *_args, **_kwargs: pytest.fail("no artifact"))
    with pytest.raises(release.ReleaseInputsUnavailable):
        release.prepare_snapshot(
            tmp_path, "a" * 40, "example/repository", tmp_path / "output",
            tmp_path / "uv", time.monotonic() + 60, component="worktree-manager",
        )


def test_ambiguous_release_is_failure_not_unavailability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discovery, "discover_promoted_source", lambda *_args, **_kwargs: (
            {"artifacts": [{"component": "worktree-manager"}] * 2}, {},
        ),
    )
    with pytest.raises(ValueError, match="ambiguous") as caught:
        release.prepare_snapshot(
            tmp_path, "a" * 40, "example/repository", tmp_path / "output",
            tmp_path / "uv", time.monotonic() + 60, component="worktree-manager",
        )
    assert not isinstance(caught.value, release.ReleaseInputsUnavailable)


@pytest.mark.parametrize("failure", [None, "absent", "verification"])
def test_snapshot_entry_does_not_export_or_hide_verification_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    failure: str | None,
) -> None:
    deadline = time.monotonic() + 60
    snapshot = tmp_path / "output/profile/snapshot"
    calls: list[str] = []

    def prepare(
        checkout: Path, commit: str, repository: str, output: Path, uv: Path,
        budget: float, **options: Any,
    ) -> Path:
        assert (checkout, commit, repository, output, uv, budget) == (
            tmp_path, "a" * 40, "example/repository", tmp_path / "output", tmp_path / "uv", deadline,
        )
        assert options == {"component": "worktree-manager", "extras": ()}
        calls.append("snapshot")
        if failure:
            error = release.ReleaseInputsUnavailable if failure == "absent" else ValueError
            raise error("controlled release outcome")
        return snapshot

    monkeypatch.setattr(release, "prepare_snapshot", prepare)
    monkeypatch.setattr(release, "prepare", lambda *_args, **_kwargs: pytest.fail("no footprint export"))
    arguments = [
        "--checkout", str(tmp_path), "--commit", "a" * 40,
        "--repository", "example/repository", "--output", str(tmp_path / "output"),
        "--component", "worktree-manager", "--snapshot",
        "--uv", str(tmp_path / "uv"), "--deadline", str(deadline),
    ]
    if failure == "verification":
        with pytest.raises(ValueError, match="controlled release outcome"):
            release.main(arguments)
    else:
        assert release.main(arguments) == (3 if failure else 0)
        output = capsys.readouterr()
        if failure:
            assert output.out == "" and "unavailable" in output.err
        else:
            assert json.loads(output.out) == {"snapshot": str(snapshot)}
    assert calls == ["snapshot"]


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("warning", [
    "could not mint the configured account token", "using ambient auth",
])
def test_scoped_github_reader_refuses_ambient_account_fallback(
    monkeypatch: pytest.MonkeyPatch, binary: bool, stream: str, warning: str,
) -> None:
    seen: list[tuple[list[str], bool]] = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[Any]:
        seen.append((command, options["text"]))
        output = warning if stream == "stdout" else "private result"
        error = warning if stream == "stderr" else ""
        return subprocess.CompletedProcess(
            command, 1 if stream == "stdout" else 0,
            output.encode("utf-8") if binary else output,
            error.encode("utf-8") if binary else error,
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="Repository-scoped GitHub authentication is unavailable"):
        reader = release._github_runner(["scoped-engine", "repos", "gh", "example/repository", "--"])
        if binary:
            reader(["api", "user"], binary=True)
        else:
            reader(["api", "user"])
    assert seen == [
        (["scoped-engine", "repos", "gh", "example/repository", "--", "api", "user"], not binary),
    ]


def test_scoped_binary_reader_does_not_decode_or_inspect_the_asset_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for padding in (0, 128 << 10):
        asset = b"PK\x03\x04" + b"\0" * padding + b"using ambient auth\xff"
        monkeypatch.setattr(
            subprocess, "run",
            lambda command, **_options: subprocess.CompletedProcess(command, 0, asset, b""),
        )
        reader = release._github_runner(["scoped-engine", "repos", "gh", "example/repository", "--"])
        assert reader(["api", "asset"], binary=True).stdout == asset


def test_scoped_text_reader_preserves_successful_response_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = json.dumps({"description": "could not mint; using ambient auth"})
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **_options: subprocess.CompletedProcess(command, 0, content, ""),
    )
    reader = release._github_runner(["scoped-engine", "repos", "gh", "example/repository", "--"])
    assert reader(["api", "metadata"]).stdout == content


def test_release_discovery_and_attestation_share_scoped_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Mock()
    seen: list[Path] = []

    def discover(_directory: Path, **options: Any) -> None:
        assert options["runner"] is client
        options["verify_subject"](tmp_path / "subject", repository="example/repository")
        return None

    def verify(path: Path, **options: Any) -> None:
        assert options == {"repository": "example/repository", "runner": client}
        seen.append(path)

    monkeypatch.setattr(discovery, "discover_promoted_source", discover)
    monkeypatch.setattr(release, "verify_attestation_subject", verify)
    with pytest.raises(release.ReleaseInputsUnavailable):
        release.prepare_snapshot(
            tmp_path, "a" * 40, "example/repository", tmp_path / "output",
            tmp_path / "uv", time.monotonic() + 60, component="worktree-manager", runner=client,
        )
    assert seen == [tmp_path / "subject"]


def test_copied_snapshot_entry_recognizes_source_before_wheel_channel(tmp_path: Path) -> None:
    from materialize_launch_wrapper_assets import materialize_into

    root = Path(__file__).resolve().parents[1]
    package = tmp_path / "package"
    package.mkdir()
    report = materialize_into(
        source_consumer_dir=root / "worktree-manager",
        dest_consumer_dir=package / "_runtime_assets", canonical_root=root, dest_root=package,
    )
    assert report and all(row.startswith("OK ") for row in report), report
    checkout = tmp_path / "source"
    checkout.mkdir()
    (checkout / "README.md").write_text("Legacy source fixture\n")

    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(checkout), *arguments],
            check=True, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.strip()

    git("init", "--quiet")
    git("add", "README.md")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Legacy source")
    entry = package / "_runtime_assets/scripts/runtime-input-provider/tools/bootstrap_manager_release.py"
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    result = subprocess.run(
        [
            sys.executable, "-I", "-B", str(entry),
            "--checkout", str(checkout), "--commit", git("rev-parse", "HEAD"),
            "--repository", "example/repository", "--component", "worktree-manager",
            "--snapshot", "--output", str(tmp_path / "output"),
            "--uv", str(tmp_path / "unused-uv"), "--deadline", str(time.monotonic() + 30),
        ],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert result.stdout == "" and "predates the published wheel channel" in result.stderr
    assert not (tmp_path / "output").exists()
