"""Explicit native consumers of owned disposable command allocations."""

import json
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


@pytest.mark.timeout(45)
@pytest.mark.parametrize("times_out", [False, True])
def test_real_failure_or_timeout_retains_only_bounded_diagnostics(
    producer_owner: ProducerOwner, times_out: bool,
) -> None:
    runner, owner, selected, _, ledger = producer_owner
    program = (
        "from pathlib import Path; import time; "
        "Path('private-payload.bin').write_bytes(b'not retained evidence'); "
        + ("time.sleep(30)" if times_out else "raise SystemExit(7)")
    )
    expected = 124 if times_out else 7
    assert runner.main([
        "--owner", owner, "--kind", "spike", "--timeout", "2" if times_out else "20",
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
