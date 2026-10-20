"""Mechanical native-architecture guards, not real image qualification."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from test_gateway_role_tunnels import ENGINE, IMAGE, ROOT, RUN, SOURCE, WITNESS

SHA = "a" * 40


def test_default_native_selection_preserves_amd64(tmp_path: Path) -> None:
    engine = ENGINE.Engine(tmp_path, {})
    assert engine.architecture.name == "amd64"
    assert engine.architecture.platform == "linux/amd64"
    assert SOURCE.PROOF + "architecture.py" in SOURCE.REQUIRED


@pytest.mark.parametrize("selected", ["", "aarch64", "x86_64", "arm/v7", None, {}])
def test_invalid_selection_refuses_before_docker_or_directory_creation(
    tmp_path: Path, selected: object,
) -> None:
    with patch.object(ENGINE.subprocess, "run") as launch:
        with pytest.raises(SOURCE.ProofError, match="unsupported-native-architecture"):
            ENGINE.Engine(tmp_path, {}, selected)
    launch.assert_not_called()
    assert not (tmp_path / "cr-logs").exists()


@pytest.mark.parametrize("selected,observed", [
    ("amd64", "x86_64"), ("amd64", "amd64"),
    ("arm64", "aarch64"), ("arm64", "arm64"),
])
def test_explicit_native_engine_aliases_are_retained_in_receipt(
    tmp_path: Path, selected: str, observed: str,
) -> None:
    receipt = {}
    engine = ENGINE.Engine(tmp_path, receipt, selected)
    facts = {"os": "linux", "arch": observed, "version": "synthetic"}
    engine.call = Mock(return_value=json.dumps(facts))
    engine.preflight()
    assert receipt["engine"] == facts
    assert engine.architecture.name == selected
    assert engine.call.call_count == 1


@pytest.mark.parametrize("selected,facts", [
    ("amd64", {"os": "linux", "arch": "aarch64"}),
    ("arm64", {"os": "linux", "arch": "x86_64"}),
    ("amd64", {"os": "windows", "arch": "amd64"}),
    ("arm64", {"os": "linux", "arch": "armv7l"}),
    ("arm64", {"os": "linux", "arch": None}),
    ("amd64", {"os": "linux", "arch": {}}),
    ("arm64", {}), ("amd64", []), ("arm64", None),
])
def test_wrong_native_engine_refuses_before_build_or_network(
    tmp_path: Path, selected: str, facts: object,
) -> None:
    engine = ENGINE.Engine(tmp_path, {}, selected)
    engine.call = Mock(return_value=json.dumps(facts))
    with pytest.raises(SOURCE.ProofError, match="native-" + selected + "-engine-required"):
        engine.preflight()
    assert engine.call.call_count == 1
    assert engine.call.call_args.args[0] == "info"


@pytest.mark.parametrize("selected", ["amd64", "arm64"])
@pytest.mark.parametrize("changed", [None, "architecture", "os", "source"])
def test_selected_platform_and_exact_image_receipt(
    tmp_path: Path, selected: str, changed: str | None,
) -> None:
    receipt = {}
    engine = ENGINE.Engine(tmp_path, receipt, selected)
    metadata = {"Architecture": selected, "Os": "linux", "Config": {"Labels": {
        "org.opencontainers.image.revision": SHA,
    }}}
    if changed == "architecture":
        metadata["Architecture"] = "arm64" if selected == "amd64" else "amd64"
    elif changed == "os":
        metadata["Os"] = "windows"
    elif changed == "source":
        metadata["Config"]["Labels"]["org.opencontainers.image.revision"] = "b" * 40
    calls = []

    def call(*args: str, **kwargs: object) -> str:
        calls.append(args)
        if args[0] == "build":
            (tmp_path / "image-id").write_text(IMAGE)
            return ""
        assert args == ("image", "inspect", IMAGE)
        return json.dumps([metadata])

    engine.call = call
    if changed:
        with pytest.raises(SOURCE.ProofError, match="image-source-or-architecture-mismatch"):
            engine.build(SHA, 30)
        assert "architecture" not in receipt
    else:
        assert engine.build(SHA, 30) == IMAGE
        assert receipt["architecture"] == {
            "selected": selected, "image": selected, "execution": "UNRUN", "process": "UNRUN",
        }
    argv = calls[0]
    assert argv[argv.index("--platform") + 1] == "linux/" + selected
    assert argv[argv.index("--build-arg") + 1] == "SOURCE_COMMIT=" + SHA
    assert len(calls) == 2 and not any("network" in args for args in calls)


@pytest.mark.parametrize("selected,machine,platform,accepted", [
    ("amd64", "x86_64", "linux", True), ("amd64", "amd64", "linux", True),
    ("arm64", "aarch64", "linux", True), ("arm64", "arm64", "linux", True),
    ("amd64", "aarch64", "linux", False), ("arm64", "x86_64", "linux", False),
    ("arm64", "aarch64", "darwin", False), ("amd64", "x86_64", "windows", False),
    ("arm64", None, "linux", False), ("amd64", {}, "linux", False),
])
def test_installed_process_boundary_precedes_composition_and_native_claim(
    tmp_path: Path, selected: str, machine: object, platform: str, accepted: bool,
) -> None:
    receipt = {"architecture": {"selected": selected, "image": selected,
                                 "execution": "UNRUN", "process": "UNRUN"}}
    prepared = {"result": "pass", "platform": platform, "machine": machine}

    def start(*args: object, **kwargs: object) -> str:
        policy = tmp_path / "fixtures/gateway"
        policy.mkdir()
        (policy / "policy.json").write_text("{}")
        return json.dumps(prepared)

    engine = SimpleNamespace(
        root=tmp_path, architecture=ENGINE.NativeArchitecture(selected),
        create=Mock(return_value="f" * 64), start=start,
        call=Mock(return_value=json.dumps({"ExitCode": 0})), journal=Mock(),
        create_network=Mock(side_effect=SOURCE.ProofError("stop-after-native-boundary")),
    )
    report = RUN.report_template()
    witness = WITNESS.Witness(engine, IMAGE, report, receipt)
    expected = "stop-after-native-boundary" if accepted else "native-image-execution-not-observed"
    with pytest.raises(SOURCE.ProofError, match=expected):
        witness.run()
    assert engine.create.call_count == 1  # Only offline fixture preparation, never Gateway/service.
    if accepted:
        assert receipt["architecture"]["execution"] == "native-" + selected
        assert receipt["architecture"]["process"] == machine
        assert report["passed"] == 1
        engine.create_network.assert_called_once_with()
    else:
        assert receipt["architecture"]["execution"] == "UNRUN"
        assert receipt["architecture"]["process"] == "UNRUN"
        assert report["passed"] == 0
        engine.create_network.assert_not_called()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only runner entrypoint")
@pytest.mark.parametrize("selected", [None, "amd64", "arm64"])
def test_cli_preserves_default_and_forwards_explicit_selection(
    tmp_path: Path, selected: str | None,
) -> None:
    argv = ["--source-commit", SHA, "--results-root", str(tmp_path)]
    if selected is not None:
        argv.extend(["--architecture", selected])
    with patch.object(RUN, "private_root", return_value=tmp_path), \
            patch.object(RUN, "fresh_run", return_value=tmp_path), \
            patch.object(RUN, "run", return_value={"result": "pass", "cleanup": "complete"}) as run:
        assert RUN.main(argv) == 0
    assert run.call_args.args == (ROOT, SHA, tmp_path, 900, selected or "amd64")


@pytest.mark.parametrize("selected", ["aarch64", "arm/v7", "", "foreign"])
def test_cli_unknown_architecture_refuses_without_invoking_runner(
    tmp_path: Path, selected: str,
) -> None:
    with patch.object(RUN, "run") as run, pytest.raises(SystemExit) as refused:
        RUN.main(["--source-commit", SHA, "--results-root", str(tmp_path),
                  "--architecture", selected])
    assert refused.value.code == 2
    run.assert_not_called()
