"""Explicit native consumers of owned disposable command allocations."""

import errno
import json
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
import secrets
import shutil
import sys
from types import ModuleType, TracebackType
from typing import Literal
import zipfile

import pytest

from agent_worktrees import artifact_scratch, project_state, tracking
from agent_worktrees.config import Config
from agent_worktrees.tracking_claims import format_claim_ref
from test_resource_producer_finalize import _final_owner_seed as _final_owner_seed
from test_resource_producer_finalize import final_owner as final_owner

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]

ProducerOwner = tuple[ModuleType, str, Path, Path, Path]


def _require_child_payload(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, name: str, payload: bytes,
) -> None:
    from owned_producer import OwnedProducerSandbox

    class StartedScope(OwnedProducerSandbox):
        def __exit__(
            self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> Literal[False]:
            assert self.allocation is not None
            assert (Path(self.allocation["path"]) / "work" / name).read_bytes() == payload
            return super().__exit__(exc_type, exc_value, traceback)

    monkeypatch.setattr(runner, "OwnedProducerSandbox", StartedScope)


@pytest.fixture
def producer_owner(
    final_owner: tuple[Path, Path, Config], tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> ProducerOwner:
    root, ledger, config = final_owner
    initial = tracking.load_record(ledger)
    initial.creation_nonce = secrets.token_hex(16)
    tracking.save_record(initial, ledger)
    repository = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repository / "tools"))
    import owned_producer

    owner = format_claim_ref(config.machine, config.repo_name, "owner")
    selected = tmp_path_factory.mktemp("producer path with spaces") / "private"
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(selected))
    monkeypatch.setattr(project_state, "canonical_repository_id", lambda *a: "repo-fixture")
    return owned_producer, owner, selected, root, ledger


_CONSUMERS = {
    "spike": """
result = subprocess.run(
    [sys.executable, "-m", "ast", str(inputs / "app" / "__main__.py")],
    capture_output=True, text=True, check=True, **flags,
)
assert "FunctionDef" in result.stdout
consumed = result.stdout
""",
    "build": """
archive = work / "application.pyz"
subprocess.run(
    [sys.executable, "-m", "zipapp", str(inputs / "app"), "-o", str(archive)],
    check=True, **flags,
)
result = subprocess.run(
    [sys.executable, str(archive)], capture_output=True, text=True, check=True, **flags,
)
assert result.stdout == "result: 42\\n"
consumed = result.stdout
""",
    "archive-extraction": """
expanded = work / "expanded"
subprocess.run(
    [sys.executable, "-m", "zipfile", "-e", str(inputs / "input.zip"), str(expanded)],
    check=True, **flags,
)
consumed = (expanded / "payload.txt").read_text(encoding="utf-8")
assert consumed == "archive fixture"
""",
    "report": """
profile = work / "profile.pstats"
subprocess.run(
    [sys.executable, "-m", "cProfile", "-o", str(profile), str(inputs / "app" / "__main__.py")],
    check=True, **flags,
)
output = io.StringIO()
pstats.Stats(str(profile), stream=output).strip_dirs().sort_stats("calls").print_stats()
report = work / "report.txt"
report.write_text(output.getvalue(), encoding="utf-8")
consumed = report.read_text(encoding="utf-8")
assert "function calls" in consumed
""",
    "source-worker": """
image = work / "source.zip"
image.write_bytes(subprocess.run(
    [sys.argv[3], "-C", str(inputs / "anchor"), "archive", "--format=zip", "HEAD"],
    capture_output=True, check=True, **flags,
).stdout)
expanded = work / "qualification-image"
subprocess.run(
    [sys.executable, "-m", "zipfile", "-e", str(image), str(expanded)],
    check=True, **flags,
)
consumed = (expanded / "base.txt").read_text(encoding="utf-8")
assert consumed == "base\\n" and not (expanded / ".git").exists()
""",
}


@pytest.mark.timeout(120)
@pytest.mark.parametrize("kind", list(_CONSUMERS))
def test_real_selected_producer_consumes_output_and_closes_allocation(
    producer_owner: ProducerOwner, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    from owned_producer import OwnedProducerSandbox

    runner, owner, selected, root, ledger = producer_owner
    inputs = root / "consumer inputs"
    inputs.mkdir()
    app = inputs / "app"
    app.mkdir()
    (app / "__main__.py").write_text(
        'def value():\n    return 42\nprint(f"result: {value()}")\n', encoding="utf-8",
    )
    with zipfile.ZipFile(inputs / "input.zip", "w") as archive:
        archive.writestr("payload.txt", "archive fixture")
    if kind == "source-worker":
        inputs = root
    cache = root / "retained-cache"
    cache.mkdir()
    sentinel = cache / "sentinel.bin"
    sentinel.write_bytes(b"not disposable scratch")
    observed_paths: list[Path] = []
    consumed: list[str] = []

    class ObservedScope(OwnedProducerSandbox):
        def __exit__(
            self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> Literal[False]:
            assert self.allocation is not None
            allocation = Path(self.allocation["path"])
            observed_paths.append(allocation)
            consumed.append((allocation / "work" / "consumed.txt").read_text(encoding="utf-8"))
            return super().__exit__(exc_type, exc_value, traceback)

    monkeypatch.setattr(runner, "OwnedProducerSandbox", ObservedScope)
    monkeypatch.setenv("GH_TOKEN", "synthetic-credential-must-not-reach-child")
    git = shutil.which("git")
    assert git is not None
    program = """
import io, os, pstats, subprocess, sys
from pathlib import Path
work, inputs = Path(sys.argv[1]), Path(sys.argv[2])
assert work == Path.cwd() == Path(os.environ["OWNED_PRODUCER_SCRATCH"])
assert "GH_TOKEN" not in os.environ
assert Path(os.environ["TEMP"]).is_relative_to(work.parent)
flags = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
""" + _CONSUMERS[kind] + """
(work / "consumed.txt").write_text(consumed, encoding="utf-8")
"""
    assert runner.main([
        "--owner", owner, "--kind", kind, "--timeout", "60",
        "--", sys.executable, "-c", program, "{scratch}", str(inputs), git,
    ]) == 0
    claim, = tracking.load_record(ledger).resources
    producer = claim.producer
    assert isinstance(producer, dict)
    assert producer["phase"] == "closed" and not claim.is_unsettled
    assert consumed[0] and not observed_paths[0].exists()
    assert sentinel.read_bytes() == b"not disposable scratch"
    assert not list(selected.glob("*/work"))


@pytest.mark.timeout(60)
@pytest.mark.parametrize("times_out", [False, True])
def test_real_failure_or_timeout_retains_only_bounded_diagnostics(
    producer_owner: ProducerOwner, monkeypatch: pytest.MonkeyPatch, times_out: bool,
) -> None:
    runner, owner, selected, _, ledger = producer_owner
    _require_child_payload(runner, monkeypatch, "private-payload.bin", b"not retained evidence")
    program = (
        "from pathlib import Path; import time; "
        "Path('private-payload.bin').write_bytes(b'not retained evidence'); "
        + ("time.sleep(30)" if times_out else "raise SystemExit(7)")
    )
    expected = 124 if times_out else 7
    assert runner.main([
        "--owner", owner, "--kind", "spike", "--timeout", "5" if times_out else "20",
        "--", sys.executable, "-c", program,
    ]) == expected
    claim, = tracking.load_record(ledger).resources
    producer = claim.producer
    assert isinstance(producer, dict)
    allocation = selected / artifact_scratch._name(producer["recovery"])
    assert producer["phase"] == "retained" and not claim.is_unsettled
    assert {entry.name for entry in allocation.iterdir()} == {"scratch-owner.json", "runner-diagnostic.txt"}
    diagnostic = json.loads((allocation / "runner-diagnostic.txt").read_bytes())
    assert diagnostic["schema"] == "copilot-extensions.owned-producer-diagnostic/v1"
    assert diagnostic["results_tail"] == [{"group": 0, "exit_code": expected}]
    assert b"private-payload" not in (allocation / "runner-diagnostic.txt").read_bytes()


def test_unknown_cleanup_preserves_payload_and_open_responsibility(
    producer_owner: ProducerOwner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin_test_containment import UnreapedProcessError

    runner, owner, selected, _, ledger = producer_owner

    def unknown(*args: object, cwd: Path, **kwargs: object) -> int:
        (cwd / "unsettled.bin").write_bytes(b"keep")
        raise UnreapedProcessError("fixture-owned unknown cleanup")

    monkeypatch.setattr(runner, "run_contained", unknown)
    assert runner.main([
        "--owner", owner, "--kind", "source-worker",
        "--", sys.executable, "-c", "pass",
    ]) == 125
    claim, = tracking.load_record(ledger).resources
    producer = claim.producer
    assert isinstance(producer, dict)
    allocation = selected / artifact_scratch._name(producer["recovery"])
    assert producer["phase"] == "bound" and claim.is_unsettled
    assert (allocation / "work" / "unsettled.bin").read_bytes() == b"keep"
    assert not (allocation / "runner-diagnostic.txt").exists()


@pytest.mark.timeout(60)
@pytest.mark.parametrize("disposition", ["success", "failure", "timeout", "unknown"])
def test_target_disposition_preserves_an_unrelated_sibling_allocation(
    producer_owner: ProducerOwner, monkeypatch: pytest.MonkeyPatch, disposition: str,
) -> None:
    from owned_producer import OwnedProducerSandbox
    from plugin_test_containment import UnreapedProcessError

    runner, owner, selected, _, ledger = producer_owner
    repository = Path(__file__).resolve().parents[3]
    sibling = OwnedProducerSandbox(owner, repository, stage="sibling-control")
    if disposition == "unknown":
        def unknown(*args: object, cwd: Path, **kwargs: object) -> int:
            (cwd / "unsettled.bin").write_bytes(b"target")
            raise UnreapedProcessError("fixture-only unknown target cleanup")

        monkeypatch.setattr(runner, "run_contained", unknown)
    else:
        _require_child_payload(runner, monkeypatch, "payload.bin", b"target")
    expected = {"success": 0, "failure": 7, "timeout": 124, "unknown": 125}[disposition]
    program = "from pathlib import Path; import time; Path('payload.bin').write_bytes(b'target'); "
    program += "time.sleep(30)" if disposition == "timeout" else f"raise SystemExit({7 if disposition == 'failure' else 0})"
    with (pytest.raises(
        UnreapedProcessError, match=r"\Apreserve the enclosing scope while target cleanup is unknown\Z",
    ) if disposition == "unknown" else nullcontext()):
        with sibling as raw_sibling:
            sibling_path = Path(raw_sibling)
            sentinel = sibling_path / "sibling.bin"
            sentinel.write_bytes(b"sibling-owned")
            original = sibling_path.stat()
            marker = (sibling_path / "scratch-owner.json").read_bytes()
            assert sibling.allocation is not None
            sibling_token = sibling.allocation["token"]
            bound_ref = sibling.allocation["ref"]
            bound_producer = deepcopy(sibling.allocation["producer"])
            initial_claim, = tracking.load_record(ledger).resources
            assert initial_claim.ref == bound_ref and initial_claim.producer == bound_producer
            assert runner.main([
                "--owner", owner, "--kind", "spike",
                "--timeout", "5" if disposition == "timeout" else "20",
                "--", sys.executable, "-c", program,
            ]) == expected
            current = sibling_path.stat()
            assert (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)
            assert sentinel.read_bytes() == b"sibling-owned"
            assert (sibling_path / "scratch-owner.json").read_bytes() == marker
            claims = tracking.load_record(ledger).resources
            assert len(claims) == 2
            states = [claim.producer for claim in claims]
            assert all(isinstance(state, dict) for state in states)
            sibling_claim = next(claim for claim in claims if isinstance(claim.producer, dict)
                                 and claim.producer["token"] == sibling_token)
            target_claim = next(claim for claim in claims if claim is not sibling_claim)
            sibling_state, target_state = sibling_claim.producer, target_claim.producer
            assert isinstance(sibling_state, dict) and isinstance(target_state, dict)
            assert sibling_claim.ref == bound_ref and sibling_state == bound_producer
            assert sibling_state["phase"] == "bound" and sibling_claim.is_unsettled
            assert target_state["phase"] == {
                "success": "closed", "failure": "retained", "timeout": "retained", "unknown": "bound",
            }[disposition]
            assert target_claim.is_unsettled == (disposition == "unknown")
            target_path = selected / artifact_scratch._name(target_state["recovery"])
            if disposition == "success":
                assert not target_path.exists()
            elif disposition == "unknown":
                assert (target_path / "work" / "unsettled.bin").read_bytes() == b"target"
                raise UnreapedProcessError("preserve the enclosing scope while target cleanup is unknown")
            else:
                assert {entry.name for entry in target_path.iterdir()} == {
                    "scratch-owner.json", "runner-diagnostic.txt",
                }
    if disposition == "unknown":
        assert sibling_path.exists() and sentinel.read_bytes() == b"sibling-owned"
    else:
        assert not sibling_path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows component-length boundary")
@pytest.mark.timeout(60)
@pytest.mark.parametrize("stage", ["root", "child"])
def test_native_windows_overlong_path_refuses_without_losing_custody(
    producer_owner: ProducerOwner, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], stage: str,
) -> None:
    runner, owner, selected, _, ledger = producer_owner
    if stage == "root":
        valid = selected.parent / ("r" * 240)
        valid.mkdir()
        valid.rmdir()
        overlong = selected.parent / ("r" * 256)
        with pytest.raises(OSError) as failure:
            overlong.mkdir()
        native_error = getattr(failure.value, "winerror", None)
        assert native_error in {123, 206}
        assert failure.value.errno in {errno.EINVAL, errno.ENAMETOOLONG}
        monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(overlong))

        def unexpected(*args: object, **kwargs: object) -> int:
            raise AssertionError("an invalid root must refuse before child execution")

        monkeypatch.setattr(runner, "run_contained", unexpected)
        assert runner.main([
            "--owner", owner, "--kind", "spike", "--", sys.executable, "-c", "pass",
        ]) == 125
        assert str(native_error) in capsys.readouterr().err
        assert tracking.load_record(ledger).resources == []
        assert overlong.name not in {entry.name for entry in selected.parent.iterdir()}
        return

    valid_component = "v" * 240
    _require_child_payload(runner, monkeypatch, valid_component, b"child started")
    program = """
import errno
from pathlib import Path
Path("v" * 240).write_bytes(b"child started")
try:
    Path("x" * 256).write_bytes(b"must refuse")
except OSError as error:
    assert error.errno in {errno.EINVAL, errno.ENAMETOOLONG}, error
    assert getattr(error, "winerror", None) in {None, 123, 206}, error
    raise SystemExit(7)
raise AssertionError("the native overlong component unexpectedly succeeded")
"""
    assert runner.main([
        "--owner", owner, "--kind", "spike", "--timeout", "20",
        "--", sys.executable, "-c", program,
    ]) == 7
    claim, = tracking.load_record(ledger).resources
    producer = claim.producer
    assert isinstance(producer, dict)
    allocation = selected / artifact_scratch._name(producer["recovery"])
    assert producer["phase"] == "retained" and not claim.is_unsettled
    assert {entry.name for entry in allocation.iterdir()} == {
        "scratch-owner.json", "runner-diagnostic.txt",
    }
    diagnostic = json.loads((allocation / "runner-diagnostic.txt").read_bytes())
    assert diagnostic["results_tail"] == [{"group": 0, "exit_code": 7}]
