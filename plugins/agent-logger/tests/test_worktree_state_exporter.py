"""Selected finite export commands preserve the owner's actual revision and provenance."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError

from agent_logger.sync import worktree_state_exporter as exporter
from agent_logger.sync.worktree_state import WorktreeStateError
from agent_logger.sync.worktree_state_archive import (
    collect_worktree_state,
    readback_worktree_state,
)
from agent_logger.sync.worktree_state_providers import ManagedPlacement, select_efforts_exporter

from .test_worktree_state import _PRODUCE, IDENTITY, Packet
from .test_worktree_state import packet as packet
from .test_worktree_state_providers import efforts_payload as efforts_payload


def placement(home: Path) -> ManagedPlacement:
    return ManagedPlacement(
        IDENTITY.worktree,
        IDENTITY.creation_nonce,
        IDENTITY.store_id,
        "main",
        1,
        home,
        "missing",
    )


@pytest.mark.contract("selected-efforts-export")
@pytest.mark.skipif(os.name != "posix", reason="POSIX collection adapter")
def test_actual_selected_export_and_readback_keep_complete_unknown_session_provenance(
    packet: Packet,
    efforts_payload: tuple[Path, Path, Path],
) -> None:
    cfg, target, plan, seed = packet
    _, root, script = efforts_payload
    home, initial = seed.parent / "actual-home", seed.parent / "seed-export"
    home.mkdir(mode=0o700)
    initial.mkdir(mode=0o700)
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _PRODUCE,
            str(script.parent),
            str(home),
            str(initial),
            IDENTITY.worktree,
            IDENTITY.store_id,
            "rich",
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    before = {path.name: path.read_bytes() for path in home.iterdir() if path.is_file()}
    directory = seed.parent / "selected-export"
    directory.mkdir(mode=0o700)
    client = exporter.EffortsExportClient(
        root,
        select_efforts_exporter(root),
        OwnedExecutionBudget.from_timeout(120, cleanup_reserve=5),
    )
    identity = client.export(directory, IDENTITY.repository, placement(home))
    assert identity == replace(IDENTITY, revision=5)
    receipt = collect_worktree_state(
        cfg,
        target,
        plan,
        directory,
        identity,
        (),
        client.verify,
        contributors=client.contributors,
    )
    assert (
        readback_worktree_state(
            cfg,
            target,
            plan,
            identity,
            client.verify,
            contributors=client.contributors,
        )
        == receipt
    )
    assert {path.name: path.read_bytes() for path in home.iterdir() if path.is_file()} == before
    provenance = json.loads(
        (target._root() / identity.relative_directory / "contributors.json").read_bytes(),
    )["contributors"]
    assert [(entry["actor"], entry["session"]) for entry in provenance] == [
        (None, None),
        ("writer/\u975eASCII", "../arbitrary session"),
    ]


def test_export_observes_revision_and_readback_pins_it_without_renewing_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = exporter.EffortsExporter("efforts@fixture", tmp_path / "export.py")
    budget = OwnedExecutionBudget.from_timeout(120, cleanup_reserve=5)
    calls: list[list[str]] = []
    selections = []

    def select(root: Path, **options: Any) -> exporter.EffortsExporter:
        assert root == tmp_path and options["budget"] is budget
        selections.append(root)
        return selected

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        assert options["budget"] is budget and options["binary"] is True
        assert options["cwd"] == tmp_path and options["max_stderr_bytes"] == 4096
        calls.append(argv)
        command = argv[6]
        field = "contributors" if command == "contributors" else "manifest"
        value = {
            "schema": "efforts.agenda-export-cli",
            "version": 1,
            "ok": True,
            field: {
                "schema": "efforts.agenda-export",
                "version": 1,
                "completeness": "complete",
                "worktree": IDENTITY.worktree,
                "store_id": IDENTITY.store_id,
                "revision": 23,
            },
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(value).encode(), b"")

    monkeypatch.setattr(exporter, "select_efforts_exporter", select)
    monkeypatch.setattr(exporter, "run_caller_owned", run)
    client = exporter.EffortsExportClient(tmp_path, selected, budget)
    identity = client.export(
        tmp_path / "export", IDENTITY.repository, placement(tmp_path / "home")
    )
    assert identity.revision == 23
    assert "--expected-revision" not in calls[0]
    client.verify(
        tmp_path / "export",
        worktree=identity.worktree,
        store_id=identity.store_id,
        expected_revision=identity.revision,
    )
    client.contributors(
        tmp_path / "export",
        worktree=identity.worktree,
        store_id=identity.store_id,
        expected_revision=identity.revision,
    )
    assert len(selections) == len(calls) == 3
    assert all(call[call.index("--expected-revision") + 1] == "23" for call in calls[1:])


@pytest.mark.parametrize(
    "kind", ["changed-provider", "refused", "malformed", "bad-revision", "wrong-store", "cleanup"]
)
def test_export_refusal_never_returns_an_invented_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    selected = exporter.EffortsExporter("efforts@fixture", tmp_path / "export.py")
    monkeypatch.setattr(
        exporter,
        "select_efforts_exporter",
        lambda *args, **kwargs: (
            replace(selected, source="efforts@other") if kind == "changed-provider" else selected
        ),
    )
    cleanup = OwnedCleanupError(None)
    calls = []

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        if kind == "cleanup":
            raise cleanup
        manifest = {
            "schema": "efforts.agenda-export",
            "version": 1,
            "completeness": "complete",
            "worktree": IDENTITY.worktree,
            "store_id": IDENTITY.store_id,
            "revision": True if kind == "bad-revision" else 7,
        }
        if kind == "wrong-store":
            manifest["store_id"] = "b" * 32
        value = {
            "schema": "efforts.agenda-export-cli",
            "version": 1,
            "ok": kind != "refused",
            "manifest": manifest,
        }
        return subprocess.CompletedProcess(
            argv,
            0,
            b"{" if kind == "malformed" else json.dumps(value).encode(),
            b"",
        )

    monkeypatch.setattr(exporter, "run_caller_owned", run)
    client = exporter.EffortsExportClient(
        tmp_path,
        selected,
        OwnedExecutionBudget.from_timeout(120, cleanup_reserve=5),
    )
    with pytest.raises(OwnedCleanupError if kind == "cleanup" else WorktreeStateError) as caught:
        client.export(tmp_path / "export", IDENTITY.repository, placement(tmp_path / "home"))
    if kind == "changed-provider":
        assert calls == []
    if kind == "cleanup":
        assert caught.value is cleanup


@pytest.mark.contract("selected-efforts-export-timeout")
@pytest.mark.skipif(
    sys.platform != "linux" or not hasattr(os, "pidfd_open"),
    reason="native Linux descendant-exit witness",
)
def test_actual_export_deadline_settles_its_console_descendant(
    efforts_payload: tuple[Path, Path, Path],
) -> None:
    _, root, script = efforts_payload
    marker = root / "descendant.json"
    info = {
        "schema": "efforts.agenda-export-cli",
        "version": 1,
        "ok": True,
        "capabilities": ["agenda-complete-export-v1"],
        "optional_capabilities": ["agenda-export-contributors-v1"],
    }
    script.write_text(
        "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
        f"if sys.argv[1]=='info': print({json.dumps(info)!r}); sys.exit(0)\n"
        "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"marker=Path({str(marker)!r}); prepared=marker.with_suffix('.prepared')\n"
        "prepared.write_text(json.dumps({'pid':child.pid,'group':os.getpgrp()})); "
        "prepared.rename(marker)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    client = exporter.EffortsExportClient(
        root,
        select_efforts_exporter(root),
        OwnedExecutionBudget.from_timeout(12, cleanup_reserve=3),
    )
    pin = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.export, root / "unused-export", IDENTITY.repository, placement(root)
        )
        deadline = time.monotonic() + 5
        while not marker.is_file() and time.monotonic() < deadline and not future.done():
            time.sleep(0.01)
        try:
            assert marker.is_file()
            child = json.loads(marker.read_bytes())
            pin = os.pidfd_open(child["pid"])
            assert os.getpgid(child["pid"]) == child["group"]
            with pytest.raises(WorktreeStateError):
                future.result(timeout=15)
            observer = select.poll()
            observer.register(pin, select.POLLIN)
            assert any(mask & select.POLLIN for _, mask in observer.poll(0))
        finally:
            if pin is not None:
                os.close(pin)
