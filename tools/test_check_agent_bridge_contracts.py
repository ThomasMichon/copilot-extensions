"""Regression tests for the agent-bridge contract registry checker."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-agent-bridge-contracts.py"
EVIDENCE_MODULE = Path(__file__).resolve().parent / "agent_bridge_contract_git.py"
SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "agent-bridge"
    / "contract"
    / "registry.schema.json"
)
SOURCE = "plugins/agent-bridge/src/agent_bridge/protocol.py"
FIXTURE = "plugins/agent-bridge/contract/fixtures/http/current/health.json"
HOST_SOURCE = "plugins/agent-bridge/src/agent_bridge/session_host/protocol.py"
HOST_FIXTURE = (
    "plugins/agent-bridge/contract/fixtures/session-host/current/messages.json"
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repo: Path, relative: str, value: str | dict[str, Any]) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2) + "\n" if isinstance(value, dict) else value
    path.write_text(text, encoding="utf-8")


def _sha256(repo: Path, relative: str) -> str:
    data = (repo / relative).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        canonical = data
    else:
        canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_agent_bridge_contracts", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry(repo: Path, commit: str, blob: str) -> dict[str, Any]:
    http_contract = {
        "id": "agent-bridge.http-wire",
        "authority": "agent-bridge/http-wire",
        "owner": "agent-bridge",
        "kind": "wire-protocol",
        "normative": True,
        "declared_range": {
            "current": 12,
            "minimum": 1,
            "previous_generation": None,
            "previous_absent_reason": "Synthetic test registry has one generation.",
        },
        "evidence_window": {
            "generations": [12],
            "runtimes": ["1.0.0"],
        },
        "capability_versions": {
            "relay_interrupt": 2,
            "failed_acp_handshake": 3,
            "container_recreate": 4,
            "machine_metadata": 5,
            "result_snapshot": 6,
            "represented_result_snapshot": 7,
            "provider_target_refresh": 8,
            "at_rest_projection": 9,
            "attention_wait": 10,
            "remote_operations": 11,
            "conditional_idle_end": 12,
            "dispatch_task_session": 12,
            "plugin_readiness": 12,
        },
        "durable_records": [],
        "source_paths": [
            {
                "path": SOURCE,
                "sha256": _sha256(repo, SOURCE),
                "semantic": True,
                "non_semantic_reason": None,
            }
        ],
        "fixtures": [
            {
                "path": FIXTURE,
                "sha256": _sha256(repo, FIXTURE),
                "role": "current health",
                "generation": 12,
            }
        ],
        "provenance": [
            {
                "commit": commit,
                "plugin_version": "1.0.0",
                "generation": 12,
                "source_path": SOURCE,
                "source_git_blob": blob,
                "capture_method": "Read exact committed source.",
            }
        ],
        "support_window": "Generations 1 through 10.",
        "bridge_contract_rollback_window": "Retain generation 9.",
        "mixed_version_scenarios": ["old-client_new-daemon"],
        "removal_gate": "Prove zero references.",
    }
    host_blob = _git(repo, "rev-parse", f"{commit}:{HOST_SOURCE}")
    host_contract = {
        "id": "agent-bridge.session-host-wire",
        "authority": "agent-bridge/session-host-wire",
        "owner": "agent-bridge",
        "kind": "wire-protocol",
        "normative": True,
        "declared_range": {
            "current": 1,
            "minimum": 1,
            "previous_generation": None,
            "previous_absent_reason": "Generation 1 is the first envelope.",
        },
        "evidence_window": {
            "generations": [1],
            "runtimes": ["1.0.0"],
        },
        "capability_versions": {"length_prefixed_envelope": 1},
        "durable_records": [],
        "source_paths": [
            {
                "path": HOST_SOURCE,
                "sha256": _sha256(repo, HOST_SOURCE),
                "semantic": True,
                "non_semantic_reason": None,
            }
        ],
        "fixtures": [
            {
                "path": HOST_FIXTURE,
                "sha256": _sha256(repo, HOST_FIXTURE),
                "role": "current messages",
                "generation": 1,
            }
        ],
        "provenance": [
            {
                "commit": commit,
                "plugin_version": "1.0.0",
                "generation": 1,
                "source_path": HOST_SOURCE,
                "source_git_blob": host_blob,
                "capture_method": "Read exact committed source.",
            }
        ],
        "support_window": "Generation 1.",
        "bridge_contract_rollback_window": "Retain generation 1.",
        "mixed_version_scenarios": ["new-frontend_H1-host"],
        "removal_gate": "Prove zero references.",
    }
    return {
        "schema_version": 1,
        "contracts": [http_contract, host_contract],
        "deferred_contracts": [],
    }


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    (root / "tools" / EVIDENCE_MODULE.name).write_bytes(EVIDENCE_MODULE.read_bytes())
    for name in ("agent_bridge_contract_registry.py", "agent_bridge_contract_paths.py"):
        (root / "tools" / name).write_bytes((SCRIPT.parent / name).read_bytes())
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "config", "core.longpaths", "true")
    _git(root, "checkout", "-q", "-b", "main")

    _write(
        root,
        SOURCE,
        "\n".join(
            [
                "HTTP_PROTOCOL_VERSION = 12",
                "HTTP_PROTOCOL_MIN_SUPPORTED = 1",
                "RELAY_INTERRUPT_PROTOCOL_VERSION = 2",
                "FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION = 3",
                "CONTAINER_RECREATE_PROTOCOL_VERSION = 4",
                "MACHINE_METADATA_PROTOCOL_VERSION = 5",
                "RESULT_SNAPSHOT_PROTOCOL_VERSION = 6",
                "REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION = 7",
                "PROVIDER_TARGET_REFRESH_PROTOCOL_VERSION = 8",
                "AT_REST_PROJECTION_PROTOCOL_VERSION = 9",
                "ATTENTION_WAIT_PROTOCOL_VERSION = 10",
                "REMOTE_OPERATIONS_PROTOCOL_VERSION = 11",
                "CONDITIONAL_IDLE_END_PROTOCOL_VERSION = 12",
                "DISPATCH_TASK_SESSION_PROTOCOL_VERSION = 12",
                "PLUGIN_READINESS_PROTOCOL_VERSION = 12",
                "",
            ]
        ),
    )
    _write(
        root,
        "plugins/agent-bridge/plugin.json",
        {"name": "agent-bridge", "version": "1.0.0"},
    )
    _write(root, HOST_SOURCE, "PROTOCOL_VERSION = 1\n")
    fixture = {
        "captured_from": {
            "commit": "pending",
            "plugin_version": "1.0.0",
            "protocol_generation": 12,
        },
        "response": {"status_code": 200},
    }
    _write(root, FIXTURE, fixture)
    host_fixture = {
        "captured_from": {
            "commit": "pending",
            "plugin_version": "1.0.0",
            "protocol_generation": 1,
        },
        "messages": {},
    }
    _write(root, HOST_FIXTURE, host_fixture)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline source")
    commit = _git(root, "rev-parse", "HEAD")
    blob = _git(root, "rev-parse", f"{commit}:{SOURCE}")

    fixture["captured_from"]["commit"] = commit
    fixture["captured_from"]["source_path"] = SOURCE
    fixture["captured_from"]["source_git_blob"] = blob
    fixture["captured_from"]["source_sha256"] = _sha256(root, SOURCE)
    _write(root, FIXTURE, fixture)
    host_blob = _git(root, "rev-parse", f"{commit}:{HOST_SOURCE}")
    host_fixture["captured_from"].update(
        {
            "commit": commit,
            "source_path": HOST_SOURCE,
            "source_git_blob": host_blob,
            "source_sha256": _sha256(root, HOST_SOURCE),
        }
    )
    _write(root, HOST_FIXTURE, host_fixture)
    schema_path = root / "plugins/agent-bridge/contract/registry.schema.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_bytes(SCHEMA.read_bytes())
    _write(
        root,
        "plugins/agent-bridge/contract/registry.json",
        _registry(root, commit, blob),
    )
    return root


def _mutate_registry(
    repo: Path,
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    path = repo / "plugins/agent-bridge/contract/registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    _write(repo, "plugins/agent-bridge/contract/registry.json", data)


def test_valid_registry_passes(repo: Path) -> None:
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    assert "OK (2 contracts, 2 fixtures)" in result.stdout


def test_checker_validates_complete_chunked_history_without_rewriting(repo: Path) -> None:
    import agent_bridge_contract_registry as codec

    original = codec.load(repo)
    updated = copy.deepcopy(original.materialized)
    updated["contracts"][0]["provenance"] *= 5000
    outputs = codec.plan(repo, original, updated)
    assert len(outputs) > 2
    assert all(len(data) <= codec.MAX_ARTIFACT_BYTES for data in outputs.values())
    for relative, data in outputs.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    assert codec.load(repo).materialized == updated
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    assert "OK (2 contracts, 2 fixtures)" in result.stdout
    assert all((repo / path).read_bytes() == data for path, data in outputs.items())


def test_ci_checks_registry_once_and_keeps_pr_source_coverage() -> None:
    import yaml

    workflow = yaml.safe_load(
        (SCRIPT.parent.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    steps = [
        step for step in workflow["jobs"]["checks"]["steps"]
        if SCRIPT.name in step.get("run", "")
    ]
    assert len(steps) == 2
    impact = "steps.code-impact.outputs.run == 'true' && "
    assert all(step["if"].startswith(impact) for step in steps)
    commands = {step["if"].removeprefix(impact): step["run"] for step in steps}
    assert commands == {
        "github.event_name != 'pull_request'": "python tools/check-agent-bridge-contracts.py",
        "github.event_name == 'pull_request'": (
            "python tools/check-agent-bridge-contracts.py "
            "--base ${{ github.event.pull_request.base.sha }}"
        ),
    }
    for event in ("pull_request", "push", "workflow_dispatch", "future_event", ""):
        selected = [
            command for condition, command in commands.items()
            if (
                condition == "github.event_name == 'pull_request'"
                and event == "pull_request"
            ) or (
                condition == "github.event_name != 'pull_request'"
                and event != "pull_request"
            )
        ]
        assert len(selected) == 1
        assert ("--base " in selected[0]) is (event == "pull_request")


@pytest.mark.parametrize("corruption", ["schema", "fixture", "provenance"])
def test_diff_mode_retains_full_registry_validation(repo: Path, corruption: str) -> None:
    if corruption == "schema":
        _mutate_registry(repo, lambda data: data.update(schema_version=3))
    elif corruption == "fixture":
        _write(repo, FIXTURE, {"corrupted": True})
    else:
        _mutate_registry(
            repo,
            lambda data: data["contracts"][0]["provenance"][0].update(
                plugin_version="9.9.9"
            ),
        )
    full = _run(repo)
    diff = _run(repo, "--base", "HEAD")
    assert full.returncode == diff.returncode == 1
    assert full.stderr == diff.stderr
    assert full.stdout == diff.stdout == ""


@pytest.mark.parametrize("base", ["", "missing-contract-base"])
def test_diff_mode_rejects_unavailable_base(repo: Path, base: str) -> None:
    result = _run(repo, "--base", base)
    assert result.returncode == 1
    assert "diff:" in result.stderr


def test_unresolvable_commit_does_not_fail_validation(repo: Path) -> None:
    """Regression test for copilot-extensions#5050 (recurrence of #2230): a
    squash-merge discards every intermediate commit on a feature branch, so a
    provenance/``captured_from`` entry's ``commit`` becomes permanently
    unreachable through no fault of its content. The content-addressed
    ``source_git_blob``/``source_sha256`` fields are the durable evidence;
    an unresolvable commit must be a silently-skipped opportunistic check,
    never a hard failure, as long as those fields are present and
    well-formed."""
    fake_commit = "f" * 40

    def orphan_commit(data: dict[str, Any]) -> None:
        for contract in data["contracts"]:
            for provenance in contract["provenance"]:
                provenance["commit"] = fake_commit

    _mutate_registry(repo, orphan_commit)
    for relative in (FIXTURE, HOST_FIXTURE):
        path = repo / relative
        fixture_data = json.loads(path.read_text(encoding="utf-8"))
        fixture_data["captured_from"]["commit"] = fake_commit
        _write(repo, relative, fixture_data)

    def refresh_fixture_hashes(data: dict[str, Any]) -> None:
        for contract in data["contracts"]:
            for fixture_entry in contract["fixtures"]:
                fixture_entry["sha256"] = _sha256(repo, fixture_entry["path"])

    _mutate_registry(repo, refresh_fixture_hashes)

    result = _run(repo)
    assert result.returncode == 0, result.stderr
    assert "OK (2 contracts, 2 fixtures)" in result.stdout


def test_unresolvable_commit_with_mismatched_sha256_still_fails(repo: Path) -> None:
    """Closes the gap flagged on #5062's review: an unresolvable commit must
    not reduce ``source_sha256`` verification to format-only validation.
    ``source_git_blob`` remains resolvable directly (by its own object id,
    independent of the orphaned commit), so the checker must fall back to
    hashing that blob's content and catch a ``source_sha256`` that disagrees
    with it -- exactly the "arbitrary content hash passes" scenario the
    review called out."""
    fake_commit = "f" * 40

    def orphan_commit_keep_blob(data: dict[str, Any]) -> None:
        for contract in data["contracts"]:
            for provenance in contract["provenance"]:
                provenance["commit"] = fake_commit

    _mutate_registry(repo, orphan_commit_keep_blob)

    path = repo / FIXTURE
    fixture_data = json.loads(path.read_text(encoding="utf-8"))
    fixture_data["captured_from"]["commit"] = fake_commit
    # source_git_blob is left pointing at the real, resolvable blob; only
    # source_sha256 is corrupted -- this must still be caught via the
    # direct-blob-resolution fallback, not silently skipped.
    fixture_data["captured_from"]["source_sha256"] = "0" * 64
    _write(repo, FIXTURE, fixture_data)

    host_path = repo / HOST_FIXTURE
    host_fixture_data = json.loads(host_path.read_text(encoding="utf-8"))
    host_fixture_data["captured_from"]["commit"] = fake_commit
    _write(repo, HOST_FIXTURE, host_fixture_data)

    def refresh_fixture_hashes(data: dict[str, Any]) -> None:
        for contract in data["contracts"]:
            for fixture_entry in contract["fixtures"]:
                fixture_entry["sha256"] = _sha256(repo, fixture_entry["path"])

    _mutate_registry(repo, refresh_fixture_hashes)

    result = _run(repo)
    assert result.returncode == 1
    assert "source_sha256 is " + "0" * 64 in result.stderr
    assert "direct blob" in result.stderr


def test_unresolvable_commit_and_unresolvable_blob_skips_not_fails(repo: Path) -> None:
    """When *neither* the commit nor the blob can be resolved, evidence has
    genuinely run out -- this must stay a silent skip, not a hard failure.
    This is a real, legitimate state for old registry entries (confirmed in
    production: a historical blob can itself become unreachable once the
    source file changes again after capture, even though the commit-is-
    orphaned case alone is expected and already tolerated) -- the direct-
    blob fallback must not regress that tolerance into a new hard failure."""
    fake_commit = "f" * 40
    fake_blob = "e" * 40

    def orphan_commit_and_blob(data: dict[str, Any]) -> None:
        for contract in data["contracts"]:
            for provenance in contract["provenance"]:
                provenance["commit"] = fake_commit
                provenance["source_git_blob"] = fake_blob

    _mutate_registry(repo, orphan_commit_and_blob)
    for relative in (FIXTURE, HOST_FIXTURE):
        path = repo / relative
        fixture_data = json.loads(path.read_text(encoding="utf-8"))
        fixture_data["captured_from"]["commit"] = fake_commit
        fixture_data["captured_from"]["source_git_blob"] = fake_blob
        _write(repo, relative, fixture_data)

    def refresh_fixture_hashes(data: dict[str, Any]) -> None:
        for contract in data["contracts"]:
            for fixture_entry in contract["fixtures"]:
                fixture_entry["sha256"] = _sha256(repo, fixture_entry["path"])

    _mutate_registry(repo, refresh_fixture_hashes)

    result = _run(repo)
    assert result.returncode == 0, result.stderr
    assert "OK (2 contracts, 2 fixtures)" in result.stdout


def test_resolvable_commit_still_catches_real_mismatch(repo: Path) -> None:
    """The opportunistic cross-check must still catch a genuine content
    mismatch when the commit *is* resolvable -- the fix for #5050 relaxes
    unresolvable history, not resolvable-but-wrong history."""

    def mutation(data: dict[str, Any]) -> None:
        http_contract = next(
            c for c in data["contracts"] if c["id"] == "agent-bridge.http-wire"
        )
        http_contract["provenance"][0]["plugin_version"] = "9.9.9"

    _mutate_registry(repo, mutation)
    result = _run(repo)
    assert result.returncode == 1
    assert "plugin_version is 9.9.9, actual 1.0.0" in result.stderr


@pytest.mark.parametrize("capability", ["dispatch_task_session", "plugin_readiness"])
def test_capability_constant_mismatch_fails(repo: Path, capability: str) -> None:
    """A capability mapping must enforce its actual production constant."""

    def mutation(data: dict[str, Any]) -> None:
        http_contract = next(
            c for c in data["contracts"] if c["id"] == "agent-bridge.http-wire"
        )
        http_contract["capability_versions"][capability] = 999

    _mutate_registry(repo, mutation)
    result = _run(repo)
    assert result.returncode != 0
    assert f"capability {capability} does not match" in result.stderr


@pytest.mark.parametrize("available", [False, True])
def test_provenance_commit_validation_is_local_only(
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
) -> None:
    checker = _load_checker()
    commit = "a" * 40
    calls: list[tuple[str, ...]] = []

    def fake_git(*args: str):
        calls.append(args)
        if args[:2] == ("cat-file", "-e"):
            return subprocess.CompletedProcess(args, 0 if available else 1, "", "")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(checker._eg, "git", fake_git)
    assert checker._ensure_commit_available(commit) is available
    assert checker._ensure_commit_available(commit) is available
    assert calls == [("cat-file", "-e", f"{commit}^{{commit}}")] * 2


def test_local_git_probe_is_bounded_headless_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_checker()
    checker._eg.git.cache_clear()
    monkeypatch.setattr(checker._eg, "require_local_object_reads", lambda: None)
    observed = {}

    def timed_out(args, **kwargs):
        observed.update(kwargs)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(checker._eg.subprocess, "run", timed_out)
    with pytest.raises(subprocess.TimeoutExpired):
        checker._ensure_commit_available("a" * 40)
    assert observed["timeout"] == 10
    assert observed["env"]["GIT_NO_LAZY_FETCH"] == "1"
    assert observed["creationflags"] == (
        subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    )


def test_repeated_local_evidence_is_read_once_per_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_checker()
    checker._eg.git.cache_clear()
    monkeypatch.setattr(checker._eg, "require_local_object_reads", lambda: None)
    calls = []

    def read(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(checker._eg.subprocess, "run", read)
    for _ in range(20):
        assert checker._ensure_commit_available("b" * 40)
    assert len(calls) == 1
    checker._eg.git.cache_clear()


@pytest.mark.parametrize("version", ["2.44.9", "2.45.0", "2.55.0.windows.5"])
def test_local_only_guard_refuses_unsupported_git_before_object_reads(
    monkeypatch: pytest.MonkeyPatch, version: str,
) -> None:
    checker = _load_checker()
    checker._eg.require_local_object_reads.cache_clear()
    checker._eg.git.cache_clear()
    calls = []

    def read(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, f"git version {version}\n", "")

    monkeypatch.setattr(checker._eg.subprocess, "run", read)
    if version.startswith("2.44"):
        with pytest.raises(RuntimeError, match="Git 2.45"):
            checker._eg.git("cat-file", "-e", "a" * 40)
        assert calls == [["git", "--version"]]
    else:
        checker._eg.git("cat-file", "-e", "a" * 40)
        assert len(calls) == 2
    checker._eg.require_local_object_reads.cache_clear()
    checker._eg.git.cache_clear()


@pytest.mark.parametrize("corrupt_hash", [False, True])
def test_ci_preserves_orphan_blob_validation_outside_fast_pre_push(
    repo: Path, corrupt_hash: bool,
) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("production pre-push hook requires bash")
    hook = SCRIPT.parent / "hooks" / "pre-push"
    _write(repo, "tools/hooks/pre-push", hook.read_text(encoding="utf-8"))
    (repo / "tools" / "hooks" / "pre-push").chmod(0o755)
    owner = "plugins/agent-worktrees/src/agent_worktrees/pre_push.sh"
    (repo / owner).parent.mkdir(parents=True, exist_ok=True)
    (repo / owner).write_bytes((SCRIPT.parent.parent / owner).read_bytes())
    # Isolate the changed gate while retaining Git's real hook/stdin protocol.
    for line in hook.read_text(encoding="utf-8").splitlines():
        if '"$ROOT/tools/' not in line:
            continue
        name = line.split('"$ROOT/tools/', 1)[1].split('"', 1)[0]
        if name != SCRIPT.name:
            _write(repo, f"tools/{name}", "raise SystemExit(0)\n")

    def orphan(data: dict[str, Any]) -> None:
        for contract in data["contracts"]:
            for provenance in contract["provenance"]:
                provenance["commit"] = "f" * 40

    _mutate_registry(repo, orphan)
    for relative in (FIXTURE, HOST_FIXTURE):
        fixture = json.loads((repo / relative).read_text(encoding="utf-8"))
        fixture["captured_from"]["commit"] = "f" * 40
        if corrupt_hash and relative == FIXTURE:
            fixture["captured_from"]["source_sha256"] = "0" * 64
        _write(repo, relative, fixture)
    _mutate_registry(repo, lambda data: [
        entry.update(sha256=_sha256(repo, entry["path"]))
        for contract in data["contracts"] for entry in contract["fixtures"]
    ])
    remote = repo.parent / "remote.git"
    _git(repo, "init", "--bare", "-q", str(remote))
    _git(repo, "--git-dir", str(remote), "config", "core.longpaths", "true")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "contract evidence")
    _git(repo, "config", "core.hooksPath", "tools/hooks")
    validation = _run(repo, "--base", "origin/dev")
    assert (validation.returncode == 0) is not corrupt_hash, validation.stderr
    result = subprocess.run(
        ["git", "push", "origin", "HEAD:refs/heads/validated"],
        cwd=repo, capture_output=True, text=True, timeout=30,
        env={**os.environ, **{"GIT_TERMINAL_PROMPT": "0"}},
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert subprocess.run(
        ["git", "show-ref", "--verify", "refs/remotes/origin/main"],
        cwd=repo, capture_output=True, timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ).returncode != 0
    if corrupt_hash:
        assert "direct blob" in validation.stderr
        assert subprocess.run(
            ["git", "--git-dir", str(remote), "show-ref", "--verify",
             "refs/heads/validated"], capture_output=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        ).returncode == 0
    else:
        assert "OK (2 contracts, 2 fixtures)" in validation.stdout


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda data: data.__setitem__("schema_version", 3), "only versions 1 and 2"),
        (
            lambda data: data["contracts"][0].pop("owner"),
            "missing fields: owner",
        ),
        (
            lambda data: data["contracts"][0].__setitem__("owner", None),
            "owner: must be a non-empty string",
        ),
        (
            lambda data: data["contracts"][0]["fixtures"][0].__setitem__(
                "sha256", "not-a-hash"
            ),
            "sha256 must be 64 lowercase",
        ),
        (
            lambda data: data["contracts"][0].__setitem__("provenance", []),
            "provenance: must be a non-empty array",
        ),
    ],
)
def test_malformed_registry_fails_deterministically(
    repo: Path,
    mutation: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    _mutate_registry(repo, mutation)
    first = _run(repo)
    second = _run(repo)
    assert first.returncode == 1
    assert first.stderr == second.stderr
    assert expected in first.stderr


def test_duplicate_id_and_authority_fail(repo: Path) -> None:
    def duplicate(data: dict[str, Any]) -> None:
        data["contracts"].append(copy.deepcopy(data["contracts"][0]))

    _mutate_registry(repo, duplicate)
    result = _run(repo)
    assert result.returncode == 1
    assert "duplicate contract id" in result.stderr
    assert "duplicate authority" in result.stderr


def test_invalid_required_contract_id_cannot_bypass_checks(repo: Path) -> None:
    def rename(data: dict[str, Any]) -> None:
        data["contracts"][0]["id"] = "INVALID ID"

    _mutate_registry(repo, rename)
    result = _run(repo)
    assert result.returncode == 1
    assert "must match ^[a-z0-9][a-z0-9.-]+$" in result.stderr
    assert "missing required protocol contracts: agent-bridge.http-wire" in result.stderr


def test_external_reference_requires_source_and_hash(repo: Path) -> None:
    def add_reference(data: dict[str, Any]) -> None:
        data["deferred_contracts"].append(
            {
                "id": "external.example",
                "owner": "#1",
                "classification": "externally-owned",
                "tracked_issue": "https://example.com/issues/1",
                "reason": "Owned elsewhere.",
            }
        )

    _mutate_registry(repo, add_reference)
    result = _run(repo)
    assert result.returncode == 1
    assert "externally-owned references require" in result.stderr


def test_deep_schema_corruption_fails(repo: Path) -> None:
    path = repo / "plugins/agent-bridge/contract/registry.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["$defs"]["fixture"]["required"].remove("generation")
    _write(repo, "plugins/agent-bridge/contract/registry.schema.json", schema)

    result = _run(repo)
    assert result.returncode == 1
    assert "schema.$defs.fixture: required fields do not match" in result.stderr


def test_missing_fixture_fails(repo: Path) -> None:
    (repo / FIXTURE).unlink()
    result = _run(repo)
    assert result.returncode == 1
    assert f"missing file {FIXTURE}" in result.stderr


def test_fixture_path_escape_fails(repo: Path) -> None:
    _write(repo, "outside.json", {"value": 1})

    def escape(data: dict[str, Any]) -> None:
        fixture = data["contracts"][0]["fixtures"][0]
        fixture["path"] = "outside.json"
        fixture["sha256"] = _sha256(repo, "outside.json")

    _mutate_registry(repo, escape)
    result = _run(repo)
    assert result.returncode == 1
    assert "path escapes plugins/agent-bridge/contract" in result.stderr


def test_changed_registered_source_requires_registry_update(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "registry baseline")
    base = _git(repo, "rev-parse", "HEAD")
    source = repo / SOURCE
    source.write_text(source.read_text(encoding="utf-8") + "NEW_FIELD = 11\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change contract source only")

    result = _run(repo, "--base", base)
    assert result.returncode == 1
    assert "registered contract source changed without updating" in result.stderr
    assert SOURCE in result.stderr


@pytest.mark.parametrize("fault", [None, "changed", "unregistered", "present", "escape"])
def test_source_relocation_preserves_immutable_provenance(repo: Path, fault: str | None) -> None:
    relocated = "libs/canonical/protocol.py"
    historical_path = "libs/shadow/protocol.py"
    original = (repo / SOURCE).read_text(encoding="utf-8")
    before_fixture = (repo / FIXTURE).read_bytes()
    _write(repo, historical_path, original)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "capture historical source")
    commit = _git(repo, "rev-parse", "HEAD")
    blob = _git(repo, "rev-parse", f"{commit}:{historical_path}")
    _write(repo, relocated, original)
    if fault != "present":
        (repo / historical_path).unlink()

    def relocate(data: dict[str, Any]) -> None:
        contract = data["contracts"][0]
        canonical = dict(
            contract["source_paths"][0], path=relocated,
            semantic=False, non_semantic_reason="Byte-identical canonical relocation.",
        )
        historical = dict(canonical, path=historical_path)
        contract["source_paths"].append(historical)
        if fault != "unregistered":
            contract["source_paths"].append(canonical)
        historical["current_path"] = "../outside.py" if fault == "escape" else relocated
        contract["provenance"].append(dict(
            contract["provenance"][0], commit=commit,
            source_path=historical_path, source_git_blob=blob,
        ))

    _mutate_registry(repo, relocate)
    if fault == "changed":
        _write(repo, relocated, original + "CHANGED = True\n")
    result = _run(repo)
    if fault is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1
        assert "relocation requires" in result.stderr or "stale sha256" in result.stderr
    assert (repo / FIXTURE).read_bytes() == before_fixture


def test_git_reads_ignore_contaminated_ambient_environment(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_DIR", str(repo / "wrong.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo / "wrong-worktree"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(repo / "missing-global-config"))

    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "check-agent-bridge-contracts: OK (2 contracts, 2 fixtures)\n"


def test_semantic_source_hash_cannot_advance_without_fixture(repo: Path) -> None:
    source = repo / SOURCE
    source.write_text(
        source.read_text(encoding="utf-8") + "NEW_FIELD = 11\n",
        encoding="utf-8",
    )

    def refresh_hash_only(data: dict[str, Any]) -> None:
        data["contracts"][0]["source_paths"][0]["sha256"] = _sha256(repo, SOURCE)

    _mutate_registry(repo, refresh_hash_only)
    result = _run(repo)
    assert result.returncode == 1
    assert "semantic current sources lack matching current fixtures" in result.stderr
    assert SOURCE in result.stderr


@pytest.mark.parametrize(
    ("refresh_hash", "reason", "expected_error"),
    [
        (True, "Internal flow refactor; wire packets and capability floors are unchanged.", None),
        (False, "Internal flow refactor; wire packets and capability floors are unchanged.", "stale sha256"),
        (True, None, "non_semantic_reason is required"),
    ],
)
def test_implementation_only_update_keeps_every_fixture_unchanged(
    repo: Path, refresh_hash: bool, reason: str | None, expected_error: str | None,
) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "registry baseline")
    base = _git(repo, "rev-parse", "HEAD")
    before = {path: (repo / path).read_bytes() for path in (FIXTURE, HOST_FIXTURE)}
    source = repo / SOURCE
    _write(
        repo, SOURCE,
        source.read_text(encoding="utf-8")
        + "\ndef _flow(value):\n    result = value\n    return result\n",
    )

    def classify_update(data: dict[str, Any]) -> None:
        record = data["contracts"][0]["source_paths"][0]
        record.update(semantic=False, non_semantic_reason=reason)
        if refresh_hash:
            record["sha256"] = _sha256(repo, SOURCE)

    _mutate_registry(repo, classify_update)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "implementation-only flow update")
    result = _run(repo, "--base", base)
    assert {path: (repo / path).read_bytes() for path in before} == before
    assert set(_git(repo, "diff", "--name-only", base, "HEAD").splitlines()) == {
        SOURCE, "plugins/agent-bridge/contract/registry.json",
    }
    if expected_error is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode == 1
        assert expected_error in result.stderr


def test_one_semantic_capture_does_not_require_recapturing_its_peers(repo: Path) -> None:
    registry_path = "plugins/agent-bridge/contract/registry.json"
    peer_path = FIXTURE.replace("health.json", "peer.json")
    peer = json.loads((repo / FIXTURE).read_text(encoding="utf-8"))
    _write(repo, peer_path, peer)

    def register_peer(data: dict[str, Any]) -> None:
        data["contracts"][0]["fixtures"].append({
            "path": peer_path, "sha256": _sha256(repo, peer_path),
            "role": "independent peer capture", "generation": 12,
        })

    _mutate_registry(repo, register_peer)
    before = {path: (repo / path).read_bytes() for path in (FIXTURE, peer_path, HOST_FIXTURE)}
    source = repo / SOURCE
    _write(repo, SOURCE, source.read_text(encoding="utf-8") + "NEW_FIELD = 11\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "updated semantic source")
    commit = _git(repo, "rev-parse", "HEAD")
    blob = _git(repo, "rev-parse", f"{commit}:{SOURCE}")
    refreshed = copy.deepcopy(peer)
    refreshed["captured_from"].update(
        commit=commit, source_git_blob=blob, source_sha256=_sha256(repo, SOURCE),
    )
    refreshed_path = FIXTURE.replace("health.json", "health-refreshed.json")
    _write(repo, refreshed_path, refreshed)
    registry = json.loads((repo / registry_path).read_text(encoding="utf-8"))
    contract = registry["contracts"][0]
    contract["source_paths"][0]["sha256"] = _sha256(repo, SOURCE)
    contract["fixtures"].append({
        "path": refreshed_path, "sha256": _sha256(repo, refreshed_path),
        "role": "updated source capture", "generation": 12,
    })
    contract["provenance"].append({
        **contract["provenance"][0], "commit": commit, "source_git_blob": blob,
    })
    _write(repo, registry_path, registry)

    result = _run(repo)
    assert result.returncode == 0, result.stderr
    assert {path: (repo / path).read_bytes() for path in before} == before
    assert refreshed["response"] == peer["response"]
    assert refreshed["captured_from"]["commit"] != peer["captured_from"]["commit"]

    refreshed["captured_from"]["source_sha256"] = "0" * 64
    _write(repo, refreshed_path, refreshed)
    contract["fixtures"][-1]["sha256"] = _sha256(repo, refreshed_path)
    _write(repo, registry_path, registry)
    corrupted = _run(repo)
    assert corrupted.returncode == 1
    assert "historical source" in corrupted.stderr


def test_nonsemantic_classification_does_not_relax_packet_hashes(repo: Path) -> None:
    def classify_update(data: dict[str, Any]) -> None:
        data["contracts"][0]["source_paths"][0].update(
            semantic=False, non_semantic_reason="Implementation-only maintenance.",
        )

    _mutate_registry(repo, classify_update)
    fixture = json.loads((repo / FIXTURE).read_text(encoding="utf-8"))
    fixture["response"]["status_code"] = 500
    _write(repo, FIXTURE, fixture)
    result = _run(repo)
    assert result.returncode == 1
    assert "fixtures[0]: stale sha256" in result.stderr
