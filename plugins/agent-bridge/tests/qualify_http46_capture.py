"""Qualify the current capture owner against a locally retained HTTP-46 cohort."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "libs/agent-procutil/src"))
from agent_procutil.owned_process import run_caller_owned  # noqa: E402
import agent_bridge_contract_git as evidence  # noqa: E402

TESTS = "plugins/agent-bridge/tests/"
MARKER = ".http46-capture-qualification.json"
OVERLAYS = (
    "tools/agent_bridge_contract_git.py",
    "tools/agent_bridge_contract_paths.py",
    "tools/agent_bridge_contract_registry.py",
    "tools/agent_bridge_contract_publication.py",
    "plugins/agent-bridge/contract/capture_http46.py",
    TESTS + "test_http46_startup_evidence.py",
    TESTS + "test_http46_capture_roundtrip.py",
    TESTS + "http46_qualification_runtime.py",
)


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env=evidence.clean_git_environment(),
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ).stdout


def extract(archive: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or ":" in member.name
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError(f"unsafe historical archive entry: {member.name}")
        source.extractall(destination, filter="data")


def scratch_root() -> Path:
    override = os.environ.get("AGENT_SCRATCH_ROOT")
    root = (
        Path(override)
        if override
        else Path(tempfile.gettempdir()) / f"agent-scratch-{os.getuid()}"
    )
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        info = root.lstat()
        if not (
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700
        ):
            raise ValueError("qualification scratch root is not a private owned directory")
    return root


def prepare(historical: str, destination: Path) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", historical):
        raise ValueError("an available full historical commit is required")
    evidence.require_local_object_reads()
    if git(ROOT, "cat-file", "-t", historical).strip() != b"commit":
        raise ValueError("historical source must be a locally available commit")
    protocol = git(
        ROOT,
        "show",
        f"{historical}:plugins/agent-bridge/src/agent_bridge/protocol.py",
    )
    constants = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(protocol).body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    if constants.get("HTTP_PROTOCOL_VERSION") != 46:
        raise ValueError("retained source must actually declare HTTP-46")
    extract(git(ROOT, "archive", historical), destination)
    runtime = {
        path.relative_to(destination).as_posix(): evidence.sha256_bytes(path.read_bytes())
        for base in (destination / "libs", destination / "plugins/agent-bridge/src")
        for path in base.rglob("*.py")
    }
    for relative in OVERLAYS:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    conftest = destination / TESTS / "conftest.py"
    with conftest.open("a", encoding="utf-8") as stream:
        stream.write('\npytest_plugins = ["http46_qualification_runtime"]\n')
    marker = {"historical_source": historical, "runtime_sha256": runtime}
    (destination / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    git(destination, "init", "-q", "-b", "qualification")
    git(destination, "add", ".")
    git(
        destination,
        "-c",
        "user.name=Contract qualification",
        "-c",
        "user.email=qualification@example.invalid",
        "commit",
        "-qm",
        "Compose historical HTTP-46 capture qualification fixture\n\n"
        "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
    )
    return marker


def qualification_receipt(output: str) -> dict:
    matches = re.findall(r"^HTTP46_CAPTURE_QUALIFIED (\{[^\n]+\})$", output, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("qualification requires exactly one positive receipt")
    receipt = json.loads(matches[0])
    cases = receipt.get("cases", [])
    if (
        receipt.get("composed_fixture_not_original_capture") is not True
        or not re.fullmatch(r"[0-9a-f]{40}", receipt.get("historical_source", ""))
        or len(cases) != 2
        or {case.get("representation") for case in cases} != {"inline", "chunked"}
        or any(
            case.get("write") is not True
            or case.get("no_write") is not True
            or not re.fullmatch(r"[0-9a-f]{40}", case.get("source", ""))
            for case in cases
        )
        or not any(case.get("retained_chunks", 0) > 0 for case in cases)
    ):
        raise ValueError("qualification did not complete both actual representation cases")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-source", required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("this contained historical qualification currently requires Linux")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(
        prefix=f"http46-capture-{stamp}-",
        dir=scratch_root(),
    ) as directory:
        fixture = Path(directory)
        prepare(args.historical_source, fixture)
        result = run_caller_owned(
            [
                sys.executable,
                str(fixture / "tools/run_tests_in_devcontainer.py"),
                "agent-bridge",
                "--allow-explicit-tiers",
                "-k",
                "http46_capture_actual_inline_and_chunked_roundtrip",
                "--timeout",
                "540",
                "--plugin-timeout",
                "540",
                "--test-timeout",
                "420",
            ],
            timeout=600,
            max_stdout_bytes=8 * 1024 * 1024,
            max_stderr_bytes=2 * 1024 * 1024,
        )
        print(result.stdout or "", end="")
        print(result.stderr or "", end="", file=sys.stderr)
        if result.returncode:
            raise SystemExit(result.returncode)
        qualification_receipt(result.stdout or "")


if __name__ == "__main__":
    main()
