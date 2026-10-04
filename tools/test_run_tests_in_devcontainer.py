"""Focused unit tests for the devcontainer test-isolation wrapper.

These tests never invoke Docker or the real ``devcontainer`` CLI, but
several DO invoke the real ``git`` CLI against small, throwaway repositories
built in ``tmp_path`` (the ``_materialized_git_dir`` tests) -- that function
makes several sequential `git bundle`/`clone`/`fetch` calls whose real
behavior is the point being tested, not something subprocess mocking could
meaningfully stand in for. Everything else (argument parsing,
git-environment scrubbing, the tracked-file selection, the bounded-volume
and per-instance config/volume rewrite, the privileged workspace population,
and the Docker/devcontainer-CLI invocation shape) is exercised via
subprocess mocking, matching the style of ``test_run_plugin_tests.py``. A
real, Docker-backed end-to-end run is exercised manually (see the effort
README's Phase 1 journal), not in the repository's default test portfolio,
since it requires a working Docker daemon and network access to pull a base
image -- neither of which this repo's unit-test tier guarantees.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import signal
import subprocess as real_subprocess
import sys
import tarfile
import uuid
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent / "run_tests_in_devcontainer.py"
_previous_path = sys.path.copy()
sys.path.insert(0, str(SCRIPT.parent))
try:
    _spec = importlib.util.spec_from_file_location("run_tests_in_devcontainer", SCRIPT)
    assert _spec and _spec.loader
    wrapper = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = wrapper
    _spec.loader.exec_module(wrapper)
finally:
    sys.path[:] = _previous_path


def test_scrubbed_git_env_removes_repository_context_variables(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: [])
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere/else")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.foo")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/somewhere/else/.gitconfig")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/somewhere/else/gitconfig")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "0")
    # Behaviorful variables with no bearing on repository SELECTION, which
    # a narrower, name-by-name allowlist could miss entirely -- the
    # blanket `GIT_*` strip must catch these too, not just the ones this
    # wrapper happens to have anticipated.
    monkeypatch.setenv("GIT_TRACE", "/somewhere/else/trace.log")
    monkeypatch.setenv("GIT_EXEC_PATH", "/somewhere/else/git-core")
    monkeypatch.setenv("UNRELATED_VAR", "kept")
    env = wrapper._scrubbed_git_env()
    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_TRACE" not in env
    assert "GIT_EXEC_PATH" not in env
    # The caller's own injected `GIT_CONFIG_KEY_0` is stripped -- but the
    # wrapper forces its OWN `GIT_CONFIG_KEY_0=core.fsmonitor` afterward
    # (see below), so the key is present again with the wrapper's value,
    # never the caller's.
    assert env["GIT_CONFIG_KEY_0"] != "core.foo"
    # `GIT_CONFIG_SYSTEM` is stripped (no forced replacement needed --
    # `GIT_CONFIG_NOSYSTEM=1` below already disables system config
    # entirely). `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_NOSYSTEM` are instead
    # FORCED to safe values (not merely stripped), since the caller's own
    # global/system config -- including a configured `core.fsmonitor`
    # hook -- would otherwise still load and execute as part of an
    # ostensibly read-only probe against the REAL host checkout.
    assert "GIT_CONFIG_SYSTEM" not in env
    assert env["GIT_CONFIG_GLOBAL"] == wrapper.os.devnull
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env.get("UNRELATED_VAR") == "kept"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    # Without this, even a nominally read-only `git status` against the
    # real host checkout (`_warn_about_dirty_tracked_files`) can refresh
    # and rewrite the index, violating the wrapper's read-only-host
    # guarantee.
    assert env["GIT_OPTIONAL_LOCKS"] == "0"
    # Forces `core.fsmonitor=false` via the env-override mechanism --
    # without it, a configured fsmonitor hook (global, system, or the
    # repo's own local config) would still execute arbitrary host code
    # as part of this same read-only probe.
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "core.fsmonitor"
    assert env["GIT_CONFIG_VALUE_0"] == "false"


def test_scrubbed_git_env_appends_discovered_clean_filter_overrides(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: ["lfs", "custom"])
    env = wrapper._scrubbed_git_env()
    assert env["GIT_CONFIG_COUNT"] == "5"
    assert env["GIT_CONFIG_KEY_0"] == "core.fsmonitor"
    assert env["GIT_CONFIG_KEY_1"] == "filter.lfs.clean"
    assert env["GIT_CONFIG_VALUE_1"] == "cat"
    # `filter.<name>.process` is a separate, higher-precedence protocol
    # that still executes host code even with `.clean` neutralized --
    # confirmed live -- so it must also be forced (to empty) for every
    # discovered filter name, not just `.clean`.
    assert env["GIT_CONFIG_KEY_2"] == "filter.lfs.process"
    assert env["GIT_CONFIG_VALUE_2"] == ""
    assert env["GIT_CONFIG_KEY_3"] == "filter.custom.clean"
    assert env["GIT_CONFIG_VALUE_3"] == "cat"
    assert env["GIT_CONFIG_KEY_4"] == "filter.custom.process"
    assert env["GIT_CONFIG_VALUE_4"] == ""


def test_discover_configured_clean_filters_finds_assigned_filter(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".gitattributes").write_text("secret.bin filter=redact\n")
    (repo / "secret.bin").write_text("hello")
    (repo / "plain.txt").write_text("hi")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    monkeypatch.setattr(wrapper, "REPO", repo)
    names = wrapper._discover_configured_clean_filters()
    assert names == ["redact"]


def test_discover_configured_clean_filters_empty_on_no_assignments(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "plain.txt").write_text("hi")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    monkeypatch.setattr(wrapper, "REPO", repo)
    assert wrapper._discover_configured_clean_filters() == []


def test_discover_configured_clean_filters_honors_uncommitted_attributes_edit(tmp_path, monkeypatch) -> None:
    # `--cached` would only ever see the COMMITTED `.gitattributes`
    # assignment, silently missing an uncommitted edit that assigns a NEW
    # filter -- confirmed live. Discovery must honor the actual
    # WORKING-TREE `.gitattributes`, the same file a real `git status`/
    # `diff` probe itself consults.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".gitattributes").write_text("secret.bin filter=committed\n")
    (repo / "secret.bin").write_text("hello")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    # Uncommitted edit -- reassigns the filter, never staged or committed.
    (repo / ".gitattributes").write_text("secret.bin filter=uncommitted\n")
    monkeypatch.setattr(wrapper, "REPO", repo)
    names = wrapper._discover_configured_clean_filters()
    assert names == ["uncommitted"]


def test_discover_configured_clean_filters_fails_closed_on_subprocess_error(monkeypatch) -> None:
    # Degrading a discovery failure to "no known filters to neutralize"
    # would be exactly the false safety this probe exists to prevent --
    # must fail loudly instead.
    fake_result = mock.Mock(returncode=128, stdout=b"", stderr=b"fatal: not a git repository")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        try:
            wrapper._discover_configured_clean_filters()
        except SystemExit:
            pass
        else:
            raise AssertionError("expected SystemExit on a failed discovery subprocess")


def test_clean_filter_neutralized_during_status_probe(tmp_path, monkeypatch) -> None:
    # Reproduces the live finding: a configured `filter.<name>.clean`
    # command executes during an ordinary `git status` once a same-size
    # content edit forces git to actually re-hash rather than trust
    # stat/size alone. `_scrubbed_git_env()` must neutralize it (override
    # to `cat`) while `git status` still correctly reports the file as
    # modified.
    repo = tmp_path / "repo"
    _init_repo(repo)
    marker = tmp_path / "filter-ran.marker"
    script = tmp_path / "fake-clean-filter.sh"
    script.write_text(f'#!/bin/sh\ntouch "{marker}"\ncat\n')
    script.chmod(0o755)
    (repo / ".gitattributes").write_text("watched.txt filter=probe\n")
    (repo / "watched.txt").write_text("aaaa")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    _run_git(["git", "-C", str(repo), "config", "filter.probe.clean", str(script)], check=True)
    # Same-size edit -- forces git to re-hash content rather than trust
    # the cached stat/size comparison.
    (repo / "watched.txt").write_text("bbbb")
    monkeypatch.setattr(wrapper, "REPO", repo)
    env = wrapper._scrubbed_git_env()
    assert not marker.exists()
    status = real_subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, env=env,
    )
    assert "watched.txt" in status.stdout
    assert not marker.exists(), "clean filter ran despite the override -- neutralization failed"


def test_process_filter_neutralized_during_status_probe(tmp_path, monkeypatch) -> None:
    # A configured `filter.<name>.process` takes precedence over `.clean`
    # and uses a separate (pkt-line) protocol -- confirmed live that it
    # still executes host code during an ordinary `git status` even with
    # `.clean` neutralized. `_scrubbed_git_env()` must force `.process`
    # empty too.
    repo = tmp_path / "repo"
    _init_repo(repo)
    marker = tmp_path / "process-ran.marker"
    script = tmp_path / "fake-process-filter.sh"
    script.write_text(f'#!/bin/bash\ntouch "{marker}"\nexit 1\n')
    script.chmod(0o755)
    (repo / ".gitattributes").write_text("watched.txt filter=probe\n")
    (repo / "watched.txt").write_text("aaaa")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    _run_git(["git", "-C", str(repo), "config", "filter.probe.process", str(script)], check=True)
    (repo / "watched.txt").write_text("bbbb")
    monkeypatch.setattr(wrapper, "REPO", repo)
    env = wrapper._scrubbed_git_env()
    assert not marker.exists()
    status = real_subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, env=env,
    )
    assert "watched.txt" in status.stdout
    assert status.returncode == 0
    assert not marker.exists(), "process filter ran despite the override -- neutralization failed"


def test_tracked_paths_defaults_to_cached_only_and_filters_excluded_prefixes(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: [])
    fake_result = mock.Mock(
        returncode=0,
        stdout=b"TESTING.md\0.test-venvs/linux/foo\0.devcontainer/devcontainer.json\0tools/x.py\0",
        stderr=b"",
    )
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result) as run:
        paths = wrapper._tracked_paths(include_untracked=False)
    # `.devcontainer` is deliberately NOT excluded -- excluding it while
    # rebuilding the index from the full HEAD tree (which still lists it)
    # would make every in-container checkout appear dirty.
    assert paths == ["TESTING.md", ".devcontainer/devcontainer.json", "tools/x.py"]
    args, kwargs = run.call_args
    assert args[0] == ["git", "-C", str(wrapper.REPO), "ls-files", "-z", "--cached"]
    # Must use the scrubbed environment, not the ambient one.
    assert kwargs["env"] == wrapper._scrubbed_git_env()


def test_tracked_paths_include_untracked_adds_others_exclude_standard() -> None:
    fake_result = mock.Mock(returncode=0, stdout=b"TESTING.md\0", stderr=b"")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result) as run:
        wrapper._tracked_paths(include_untracked=True)
    args = run.call_args.args[0]
    assert args == ["git", "-C", str(wrapper.REPO), "ls-files", "-z",
                     "--cached", "--others", "--exclude-standard"]


def test_tracked_paths_decodes_non_utf8_bytes_via_surrogateescape() -> None:
    # A git-tracked path on Linux is arbitrary bytes -- a plain UTF-8
    # `.decode()` would raise `UnicodeDecodeError` outright for a valid
    # tracked filename that isn't valid UTF-8, aborting the whole snapshot.
    # `os.fsdecode` (surrogate-escape) must handle it instead.
    non_utf8_name = b"weird-\xff-name.txt"
    fake_result = mock.Mock(returncode=0, stdout=non_utf8_name + b"\0", stderr=b"")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        paths = wrapper._tracked_paths(include_untracked=False)
    assert len(paths) == 1
    # Round-trips back to the original bytes via `os.fsencode`.
    assert os.fsencode(paths[0]) == non_utf8_name


def test_tracked_paths_raises_on_git_failure(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: [])
    fake_result = mock.Mock(returncode=128, stdout=b"", stderr=b"not a git repository")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        try:
            wrapper._tracked_paths(include_untracked=False)
        except SystemExit as exc:
            assert "not a git repository" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_create_bounded_volume_invokes_tmpfs_backed_docker_volume_create() -> None:
    fake_result = mock.Mock(returncode=0, stderr="")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result) as run:
        wrapper._create_bounded_volume("fake-volume")
    args = run.call_args.args[0]
    assert args[:3] == ["docker", "volume", "create"]
    assert "fake-volume" in args
    assert f"o=size={wrapper.WORKSPACE_VOLUME_SIZE}" in args
    assert "type=tmpfs" in args


def test_create_bounded_volume_raises_on_failure() -> None:
    fake_result = mock.Mock(returncode=1, stderr="volume already exists")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        try:
            wrapper._create_bounded_volume("fake-volume")
        except SystemExit as exc:
            assert "volume already exists" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_per_instance_config_rewrites_volume_name_uniquely() -> None:
    config_path, volume_name = wrapper._per_instance_config("abc123def456")
    try:
        text = config_path.read_text()
        assert volume_name == f"{wrapper.BASE_VOLUME_NAME}-abc123def456"
        assert volume_name in text
        # The base (unsuffixed) name must not remain anywhere in the
        # rewritten config, or `devcontainer up` would still target the
        # shared, non-unique volume.
        assert wrapper.BASE_VOLUME_NAME + "," not in text
    finally:
        config_path.unlink(missing_ok=True)


def test_per_instance_config_raises_if_base_volume_name_missing(tmp_path: Path, monkeypatch) -> None:
    bogus = tmp_path / "devcontainer.json"
    bogus.write_text("{}")
    monkeypatch.setattr(wrapper, "DEVCONTAINER_CONFIG", bogus)
    try:
        wrapper._per_instance_config("instance-label")
    except SystemExit as exc:
        assert wrapper.BASE_VOLUME_NAME in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_bring_up_parses_container_id_from_devcontainer_up_output(tmp_path: Path) -> None:
    fake_result = mock.Mock(
        returncode=0,
        stdout='{"outcome":"success","containerId":"abc123"}\n',
        stderr="",
    )
    config_path = tmp_path / "devcontainer.json"
    config_path.write_text("{}")
    with mock.patch.object(wrapper.shutil, "which", return_value="/usr/bin/devcontainer"), \
         mock.patch.object(wrapper.subprocess, "run", return_value=fake_result) as run:
        container_id = wrapper._bring_up("instance-label", config_path)
    assert container_id == "abc123"
    args = run.call_args.args[0]
    assert args[0] == "/usr/bin/devcontainer"
    assert "up" in args
    assert "--config" in args
    assert str(config_path) in args


def test_bring_up_raises_when_devcontainer_cli_missing(tmp_path: Path) -> None:
    with mock.patch.object(wrapper.shutil, "which", return_value=None):
        try:
            wrapper._bring_up("instance-label", tmp_path / "devcontainer.json")
        except SystemExit as exc:
            assert "devcontainer CLI not found" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_bring_up_raises_when_container_id_missing_from_output(tmp_path: Path) -> None:
    fake_result = mock.Mock(returncode=0, stdout="no json here\n", stderr="")
    with mock.patch.object(wrapper.shutil, "which", return_value="/usr/bin/devcontainer"), \
         mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        try:
            wrapper._bring_up("instance-label", tmp_path / "devcontainer.json")
        except SystemExit as exc:
            assert "containerId" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_reject_resource_overrides_allows_defaults() -> None:
    # No overrides at all -- the inner runner's own defaults (4096 MiB
    # memory, 2048 MiB temp) must always be safe.
    wrapper._reject_resource_overrides_exceeding_container_ceilings([])


def test_reject_resource_overrides_rejects_memory_mb_alone_above_combined_budget() -> None:
    # The exact finding this closes: `--max-memory-mb 16000` is valid to
    # `run-plugin-tests.py` but would be silently preempted by the
    # container's own fixed `--memory=14g` ceiling regardless of what the
    # inner runner believes it has.
    try:
        wrapper._reject_resource_overrides_exceeding_container_ceilings(
            ["--max-memory-mb", "16000"]
        )
    except SystemExit as exc:
        assert "--max-memory-mb" in str(exc)
        assert "16000" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_reject_resource_overrides_rejects_memory_and_temp_combination_exceeding_shared_cgroup() -> None:
    # The exact regression this closes: each flag ALONE sitting right at
    # its own raw outer ceiling (14336 MiB memory, 6144 MiB temp) still
    # passed the previous, independent-only check -- but `/tmp` is
    # memory-backed tmpfs, so both draw from the SAME `--memory` cgroup,
    # and the combination would still be OOM-killed. Confirmed live by
    # inspection of the container's own `runArgs`.
    try:
        wrapper._reject_resource_overrides_exceeding_container_ceilings(
            ["--max-memory-mb", str(wrapper._CONTAINER_MEMORY_MB_CEILING),
             "--max-temp-mb", str(wrapper._CONTAINER_TMP_MB_CEILING)]
        )
    except SystemExit as exc:
        assert "combined" in str(exc)
    else:
        raise AssertionError("expected SystemExit for the combined memory+temp budget")


def test_reject_resource_overrides_allows_a_safe_combined_memory_and_temp_budget() -> None:
    # A combination comfortably within the shared cgroup budget (well
    # under the reserved-overhead-adjusted ceiling) must still pass.
    wrapper._reject_resource_overrides_exceeding_container_ceilings(
        ["--max-memory-mb", "8000", "--max-temp-mb", "2000"]
    )


def test_reject_resource_overrides_rejects_processes_above_effective_pid_budget() -> None:
    try:
        wrapper._reject_resource_overrides_exceeding_container_ceilings(
            ["--max-processes", "600"]
        )
    except SystemExit as exc:
        assert "--max-processes" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_reject_resource_overrides_rejects_temp_mb_above_physical_tmpfs_ceiling() -> None:
    try:
        wrapper._reject_resource_overrides_exceeding_container_ceilings(
            ["--max-temp-mb=7000"]
        )
    except SystemExit as exc:
        assert "--max-temp-mb" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_reject_resource_overrides_recognizes_abbreviated_flag() -> None:
    try:
        wrapper._reject_resource_overrides_exceeding_container_ceilings(
            ["--max-memory", "20000"]
        )
    except SystemExit as exc:
        assert "--max-memory-mb" in str(exc)
    else:
        raise AssertionError("expected SystemExit for an abbreviated flag")


def test_reject_resource_overrides_ignores_non_integer_value() -> None:
    # A non-integer value is left for the inner runner's own argparse to
    # reject -- this check must not itself crash on one.
    wrapper._reject_resource_overrides_exceeding_container_ceilings(
        ["--max-memory-mb", "not-a-number"]
    )


def test_reject_resource_overrides_uses_the_last_repeated_occurrence() -> None:
    # The exact regression this closes: argparse itself would use only
    # the LAST `--max-memory-mb` value for a repeated flag -- an earlier,
    # unsafe value superseded by a later, safe one must never be wrongly
    # rejected (confirmed this was previously broken: the check raised
    # on the FIRST occurrence it saw, before ever reaching the later,
    # safe one).
    wrapper._reject_resource_overrides_exceeding_container_ceilings(
        ["--max-memory-mb", "20000", "--max-memory-mb", "4096"]
    )


def test_resolve_base_ref_defaults_to_origin_main() -> None:
    assert wrapper._resolve_base_ref(["agent-worktrees"]) == "origin/main"
    assert wrapper._resolve_base_ref([]) == "origin/main"


def test_resolve_base_ref_extracts_space_separated_form() -> None:
    assert wrapper._resolve_base_ref(["--changed", "--base", "origin/dev"]) == "origin/dev"


def test_resolve_base_ref_extracts_equals_form() -> None:
    assert wrapper._resolve_base_ref(["--changed", "--base=origin/dev"]) == "origin/dev"


def test_resolve_base_ref_honors_last_of_repeated_flag() -> None:
    # Mirrors argparse's own last-occurrence-wins behavior for a repeated
    # flag -- the snapshot and the in-container runner must agree on which
    # `--base` is actually in effect.
    assert wrapper._resolve_base_ref(
        ["--base", "origin/main", "--base", "origin/dev"]
    ) == "origin/dev"
    assert wrapper._resolve_base_ref(
        ["--base=origin/main", "--base=origin/dev"]
    ) == "origin/dev"


def test_rewrite_base_to_resolved_sha_replaces_space_and_equals_forms(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_git_rev_parse", lambda ref: "deadbeef" * 5)
    assert wrapper._rewrite_base_to_resolved_sha(
        ["--changed", "--base", "origin/main~1"]
    ) == ["--changed", "--base", "deadbeef" * 5]
    assert wrapper._rewrite_base_to_resolved_sha(
        ["--changed", "--base=origin/dev~1"]
    ) == ["--changed", f"--base={'deadbeef' * 5}"]


def test_rewrite_base_to_resolved_sha_recognizes_abbreviated_flag(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_git_rev_parse", lambda ref: "deadbeef" * 5)
    assert wrapper._rewrite_base_to_resolved_sha(
        ["--changed", "--bas", "origin/main~1"]
    ) == ["--changed", "--bas", "deadbeef" * 5]


def test_rewrite_base_to_resolved_sha_leaves_passthrough_unchanged_when_unresolvable(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_git_rev_parse", lambda ref: None)
    original = ["--changed", "--base", "origin/nonexistent"]
    assert wrapper._rewrite_base_to_resolved_sha(original) == original


def test_rewrite_base_to_resolved_sha_leaves_passthrough_unchanged_when_base_absent(monkeypatch) -> None:
    # The default (`origin/main`) doesn't resolve in a throwaway test repo
    # with no such remote -- nothing to rewrite, passthrough is untouched.
    monkeypatch.setattr(wrapper, "_git_rev_parse", lambda ref: None)
    original = ["agent-worktrees"]
    assert wrapper._rewrite_base_to_resolved_sha(original) == original


def test_rewrite_base_to_resolved_sha_appends_base_when_absent_and_changed_mode_active(monkeypatch) -> None:
    # The exact regression this closes: the single MOST COMMON invocation
    # (no --all, no plugin names, no explicit --base) has no --base TOKEN
    # at all to rewrite -- without appending one explicitly, the
    # in-container command would fall back to run-plugin-tests.py's own
    # implicit default (origin/main), a remote-tracking ref the bundle
    # clone does not preserve as a named ref, silently running no suites.
    monkeypatch.setattr(wrapper, "_git_rev_parse", lambda ref: "deadbeef" * 5)
    assert wrapper._rewrite_base_to_resolved_sha([]) == ["--base", "deadbeef" * 5]
    assert wrapper._rewrite_base_to_resolved_sha(["--changed"]) == [
        "--changed", "--base", "deadbeef" * 5
    ]


def test_rewrite_base_to_resolved_sha_does_not_append_base_when_not_changed_mode(monkeypatch) -> None:
    # An --all run or an explicit plugin name never consults --base at
    # all, so appending it there would be pointless noise.
    monkeypatch.setattr(wrapper, "_git_rev_parse", lambda ref: "deadbeef" * 5)
    assert wrapper._rewrite_base_to_resolved_sha(["--all"]) == ["--all"]
    assert wrapper._rewrite_base_to_resolved_sha(["agent-worktrees"]) == ["agent-worktrees"]


def test_changed_mode_active_by_default_with_no_args() -> None:
    # Mirrors run-plugin-tests.py's own `else: targets =
    # changed_plugins(args.base)` fallback -- no --all, no plugin names.
    assert wrapper._changed_mode_active([]) is True
    assert wrapper._changed_mode_active(["--changed"]) is True
    assert wrapper._changed_mode_active(["-k", "some_filter"]) is True


def test_changed_mode_not_active_with_all_flag() -> None:
    assert wrapper._changed_mode_active(["--all"]) is False


def test_changed_mode_not_active_with_explicit_plugin_name() -> None:
    assert wrapper._changed_mode_active(["agent-worktrees"]) is False
    assert wrapper._changed_mode_active(["--base", "origin/dev", "agent-worktrees"]) is False


def test_canonicalize_flag_resolves_unambiguous_abbreviations() -> None:
    # `run-plugin-tests.py`'s own argparse silently accepts any unambiguous
    # prefix of a long flag -- `--base` is the only known flag starting
    # with `--b`, so `--b`/`--ba`/`--bas` must all canonicalize to it.
    for abbrev in ("--b", "--ba", "--bas", "--base"):
        assert wrapper._canonicalize_flag(abbrev) == "--base"


def test_canonicalize_flag_leaves_ambiguous_or_unknown_tokens_unchanged() -> None:
    assert wrapper._canonicalize_flag("--max") == "--max"  # ambiguous: 3 --max-* flags
    assert wrapper._canonicalize_flag("--nope") == "--nope"
    assert wrapper._canonicalize_flag("-k") == "-k"


def test_resolve_base_ref_recognizes_abbreviated_base_flag() -> None:
    # The exact gap an abbreviated `--base` would otherwise open: silently
    # keeping the wrong (`origin/main`) default instead of the ref the
    # in-container `run-plugin-tests.py` invocation will actually use.
    assert wrapper._resolve_base_ref(["--changed", "--bas", "origin/dev"]) == "origin/dev"
    assert wrapper._resolve_base_ref(["--changed", "--bas=origin/dev"]) == "origin/dev"


def test_changed_mode_active_with_abbreviated_base_flag_does_not_misclassify_value() -> None:
    # Without abbreviation-awareness, `--bas`'s value token
    # (`origin/dev`) would be misclassified as a positional plugin name,
    # wrongly reporting changed-mode as NOT active.
    assert wrapper._changed_mode_active(["--bas", "origin/dev"]) is True
    assert wrapper._changed_mode_active(["--bas=origin/dev"]) is True


def test_git_rev_parse_returns_sha_on_success(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: [])
    fake_result = mock.Mock(returncode=0, stdout="deadbeef\n", stderr="")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result) as run:
        result = wrapper._git_rev_parse("origin/dev")
    assert result == "deadbeef"
    args, kwargs = run.call_args
    assert args[0] == [
        "git", "-C", str(wrapper.REPO), "rev-parse", "--verify",
        "--end-of-options", "origin/dev^{commit}",
    ]
    assert kwargs["env"] == wrapper._scrubbed_git_env()


def test_git_rev_parse_returns_none_when_unresolvable(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: [])
    fake_result = mock.Mock(returncode=128, stdout="", stderr="unknown revision")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        assert wrapper._git_rev_parse("no-such-ref") is None


def test_git_rev_parse_rejects_a_non_commit_object(tmp_path: Path, monkeypatch) -> None:
    # Plain `rev-parse --verify` accepts ANY object type -- a tree/blob
    # expression resolves fine there, but the downstream `git diff
    # <base>...HEAD` needs a commit-ish, so letting a non-commit object
    # pass here would bypass this function's own resolvability contract.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "only commit"], check=True)
    tree_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    monkeypatch.setattr(wrapper, "REPO", repo)
    assert wrapper._git_rev_parse(tree_sha) is None
    head_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert wrapper._git_rev_parse("HEAD") == head_sha


def _run_git(args: list[str], **kwargs):
    """Run a real ``git`` subprocess for test setup/assertions, always
    through the scrubbed environment the production code itself uses
    (``wrapper._scrubbed_git_env()``) -- without it, an ambient
    ``GIT_DIR``/``GIT_WORK_TREE``/``GIT_INDEX_FILE`` could redirect even
    these real-git calls to target or mutate the CALLER's repository
    instead of the throwaway one under ``tmp_path``."""
    return real_subprocess.run(args, env=wrapper._scrubbed_git_env(), **kwargs)


def _init_repo(path: Path) -> None:
    _run_git(["git", "init", "-q", "-b", "main", str(path)], check=True)
    _run_git(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    _run_git(["git", "-C", str(path), "config", "user.email", "t@example.com"], check=True)


def test_materialized_git_dir_bundles_only_head_and_base_closure(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
    base_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    _run_git(["git", "-C", str(repo), "branch", "base-branch"], check=True)

    (repo / "tracked.txt").write_text("v2\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "head"], check=True)
    head_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()

    # A sibling branch with unique, placeholder-secret-shaped content that
    # is NEVER an ancestor of HEAD or the base ref -- this must NOT survive
    # into the materialized copy, proving the bundle closure is genuinely
    # minimal (not the whole repository's history).
    _run_git(["git", "-C", str(repo), "checkout", "-q", "-b", "secret-branch", base_sha],
                         check=True)
    (repo / "secret.txt").write_text("not-a-real-secret-placeholder\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "secret"], check=True)
    secret_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    _run_git(["git", "-C", str(repo), "checkout", "-q", head_sha], check=True)

    # A placeholder-credential-shaped remote URL in the real config --
    # must not survive into the materialized copy.
    placeholder_remote = "https://" + "not-a-real-credential" + "@example.com/repo.git"
    _run_git(
        ["git", "-C", str(repo), "config", "remote.origin.url", placeholder_remote],
        check=True,
    )

    monkeypatch.setattr(wrapper, "REPO", repo)
    with contextlib.ExitStack() as stack:
        merged = wrapper._materialized_git_dir(stack, ["--base", "base-branch"])

        rp = _run_git(
            ["git", f"--git-dir={merged}", "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        assert rp.returncode == 0
        assert rp.stdout.strip() == head_sha

        diff = _run_git(
            ["git", f"--git-dir={merged}", "diff", "--name-only", "base-branch", "HEAD"],
            capture_output=True, text=True,
        )
        assert diff.returncode == 0
        assert "tracked.txt" in diff.stdout

        # The secret branch's commit must be UNRESOLVABLE in the
        # materialized copy -- its object is simply not present.
        secret_lookup = _run_git(
            ["git", f"--git-dir={merged}", "cat-file", "-e", secret_sha],
            capture_output=True, text=True,
        )
        assert secret_lookup.returncode != 0

        config_text = merged.joinpath("config").read_text()
        assert config_text == wrapper._MINIMAL_GIT_CONFIG
        assert "not-a-real-credential" not in config_text
        assert not (merged / "hooks").exists()

        # The rebuilt index (`git read-tree HEAD`) must exactly match
        # HEAD's tree -- no spurious staged differences.
        status = _run_git(
            ["git", f"--git-dir={merged}", f"--work-tree={repo}", "status", "--short"],
            capture_output=True, text=True,
        )
        assert status.returncode == 0
        assert status.stdout == ""
    assert not merged.parent.exists()


def test_rewrite_base_to_resolved_sha_fixes_a_ref_relative_expression_end_to_end(
    tmp_path: Path, monkeypatch,
) -> None:
    # The regression this closes: a REF-RELATIVE `--base` expression (e.g.
    # `origin/dev~1`) resolves fine on the host, but `git bundle create`
    # does not preserve a REMOTE-TRACKING ref (`refs/remotes/origin/...`)
    # as a named ref in the resulting clone (unlike a plain local branch
    # name, which it does preserve) -- so the unchanged expression would
    # fail to resolve again inside the materialized bundle clone, even
    # though the underlying commit object is present. Rewriting `--base`
    # to its resolved SHA sidesteps this entirely: a bare SHA resolves
    # against any clone containing its object, no named ref required.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)

    (repo / "tracked.txt").write_text("v2\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "intermediate"], check=True)

    (repo / "tracked.txt").write_text("v3\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "head"], check=True)
    head_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Set up a genuine remote-tracking ref (not a plain local branch) so
    # `origin/dev~1` is actually a ref-relative expression over a
    # `refs/remotes/...` ref -- the specific case `git bundle create`
    # does not preserve as a named ref in its resulting clone.
    _run_git(["git", "-C", str(repo), "remote", "add", "origin", str(repo)], check=True)
    _run_git(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True)

    monkeypatch.setattr(wrapper, "REPO", repo)
    passthrough = wrapper._rewrite_base_to_resolved_sha(["--changed", "--base", "origin/main~1"])
    assert passthrough[-1] != "origin/main~1"  # genuinely rewritten, not left as-is

    with contextlib.ExitStack() as stack:
        merged = wrapper._materialized_git_dir(stack, passthrough)

        rp = _run_git(
            ["git", f"--git-dir={merged}", "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        assert rp.returncode == 0
        assert rp.stdout.strip() == head_sha

        # The rewritten (resolved-SHA) base must resolve AND diff
        # correctly inside the bundle clone -- the exact two operations
        # that would have failed against the unrewritten ref-relative
        # expression.
        resolved_base = passthrough[-1]
        base_rp = _run_git(
            ["git", f"--git-dir={merged}", "rev-parse", resolved_base],
            capture_output=True, text=True,
        )
        assert base_rp.returncode == 0

        diff = _run_git(
            ["git", f"--git-dir={merged}", "diff", "--name-only", resolved_base, "HEAD"],
            capture_output=True, text=True,
        )
        assert diff.returncode == 0
        assert "tracked.txt" in diff.stdout


def test_materialized_git_dir_handles_staged_uncommitted_change_at_snapshot_time(
    tmp_path: Path, monkeypatch,
) -> None:
    # A staged (but not yet committed) new file's blob is reachable from
    # neither HEAD nor the base ref -- copying the real index verbatim
    # would reference that now-missing blob and break `git diff`/`status`
    # outright. Rebuilding the index from HEAD instead must not crash, even
    # though the staged state itself is not preserved as "staged".

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "only commit"], check=True)

    # Stage a brand-new file whose blob is genuinely unreachable from HEAD.
    (repo / "staged-new.txt").write_text("staged content\n")
    _run_git(["git", "-C", str(repo), "add", "staged-new.txt"], check=True)

    monkeypatch.setattr(wrapper, "REPO", repo)
    with contextlib.ExitStack() as stack:
        # An explicit plugin name keeps changed-selection mode inactive,
        # so the unresolvable default "origin/main" base in this tiny repo
        # doesn't trigger the fail-closed guard this test isn't about.
        merged = wrapper._materialized_git_dir(stack, ["agent-worktrees"])
        status = _run_git(
            ["git", f"--git-dir={merged}", f"--work-tree={repo}", "status", "--short"],
            capture_output=True, text=True,
        )
        # Must not crash (a copied-index approach referencing the missing
        # staged blob would fail here). The staged-new file's actual content
        # is still visible -- just reported as an ordinary untracked file
        # rather than "staged", since the rebuilt index exactly matches
        # HEAD (no entry for it) instead of preserving the real staging
        # state.
        assert status.returncode == 0
        assert status.stdout == "?? staged-new.txt\n"


def test_materialized_git_dir_skips_base_closure_when_base_unresolvable_and_not_changed_mode(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "only commit"], check=True)
    head_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()

    monkeypatch.setattr(wrapper, "REPO", repo)
    with contextlib.ExitStack() as stack:
        # "origin/main" (the default) does not exist in this tiny repo, but
        # an explicit plugin name means changed-selection mode is NOT
        # active -- must degrade gracefully (HEAD alone), not raise.
        merged = wrapper._materialized_git_dir(stack, ["agent-worktrees"])
        rp = _run_git(
            ["git", f"--git-dir={merged}", "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        assert rp.returncode == 0
        assert rp.stdout.strip() == head_sha


def test_materialized_git_dir_excludes_base_closure_when_base_resolves_but_not_changed_mode(
    tmp_path: Path, monkeypatch,
) -> None:
    # A RESOLVABLE base must still be excluded from the bundle when
    # changed-selection isn't active (`--all`/an explicit plugin name) --
    # otherwise a divergent `origin/main` would needlessly widen the
    # minimal-history boundary with unrelated commits/trees/blobs reachable
    # from it but having nothing to do with the plugin suite being run.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
    _run_git(["git", "-C", str(repo), "branch", "origin/main"], check=True)

    # A divergent commit on "origin/main" that is NOT an ancestor of HEAD
    # and carries unique, placeholder-secret-shaped content -- this must
    # NOT survive into the materialized copy when changed-selection is
    # inactive, proving the base closure genuinely was never bundled.
    _run_git(["git", "-C", str(repo), "checkout", "-q", "origin/main"], check=True)
    (repo / "divergent-secret.txt").write_text("not-a-real-secret-placeholder\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "divergent"], check=True)
    divergent_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    _run_git(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)
    head_sha = _run_git(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()

    monkeypatch.setattr(wrapper, "REPO", repo)
    with contextlib.ExitStack() as stack:
        # "origin/main" genuinely resolves here, but an explicit plugin
        # name means changed-selection mode is NOT active.
        merged = wrapper._materialized_git_dir(stack, ["agent-worktrees"])
        rp = _run_git(
            ["git", f"--git-dir={merged}", "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        assert rp.returncode == 0
        assert rp.stdout.strip() == head_sha

        divergent_lookup = _run_git(
            ["git", f"--git-dir={merged}", "cat-file", "-e", divergent_sha],
            capture_output=True, text=True,
        )
        assert divergent_lookup.returncode != 0


def test_materialized_git_dir_raises_when_changed_mode_active_and_base_unresolvable(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "only commit"], check=True)

    monkeypatch.setattr(wrapper, "REPO", repo)
    with contextlib.ExitStack() as stack:
        # No --all, no plugin names -- changed-selection mode is active by
        # `run-plugin-tests.py`'s own default -- and "origin/main" doesn't
        # resolve in this tiny repo, so this must fail loudly rather than
        # silently building a snapshot that would make the in-container
        # run report "no plugin suites to run" for the wrong reason.
        try:
            wrapper._materialized_git_dir(stack, [])
        except SystemExit as exc:
            assert "origin/main" in str(exc)
            assert "does not resolve" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_materialized_git_dir_raises_when_base_resolves_but_shares_no_merge_base(
    tmp_path: Path, monkeypatch,
) -> None:
    # A base that RESOLVES (to some commit) can still have no common
    # ancestor with HEAD (an orphan/unrelated-history branch) -- the
    # downstream three-dot diff would fail with "no merge base," which
    # `run-plugin-tests.py` ignores the same way it ignores an
    # unresolvable ref, silently reporting zero changed plugins. This
    # must fail loudly instead.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "main commit"], check=True)

    _run_git(["git", "-C", str(repo), "checkout", "-q", "--orphan", "unrelated"], check=True)
    _run_git(["git", "-C", str(repo), "rm", "-rq", "--cached", "."], check=True)
    (repo / "other.txt").write_text("unrelated history\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "unrelated root"], check=True)
    _run_git(["git", "-C", str(repo), "branch", "origin/main", "unrelated"], check=True)
    _run_git(["git", "-C", str(repo), "checkout", "-q", "main"], check=True)

    monkeypatch.setattr(wrapper, "REPO", repo)
    with contextlib.ExitStack() as stack:
        try:
            wrapper._materialized_git_dir(stack, [])
        except SystemExit as exc:
            assert "no merge base" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_write_tar_of_repo_includes_materialized_git_dir_and_tracked_paths(tmp_path: Path, monkeypatch) -> None:
    fake_git_dir = tmp_path / "fake-git"
    fake_git_dir.mkdir()
    (fake_git_dir / "HEAD").write_text("ref: refs/heads/main\n")

    real_file = tmp_path / "tracked.txt"
    real_file.write_text("hello\n")

    monkeypatch.setattr(wrapper, "_materialized_git_dir", lambda stack, passthrough: fake_git_dir)
    monkeypatch.setattr(wrapper, "_tracked_paths", lambda *, include_untracked: ["tracked.txt"])
    monkeypatch.setattr(wrapper, "REPO", tmp_path)
    monkeypatch.setattr(wrapper, "_warn_about_dirty_tracked_files", lambda: None)
    monkeypatch.setattr(wrapper, "_warn_about_hidden_tracked_file_flags", lambda: None)

    dest = tmp_path / "out.tar"
    wrapper._write_tar_of_repo(dest, ["agent-worktrees"], include_untracked=False)
    with tarfile.open(dest) as tar:
        names = set(tar.getnames())
    assert ".git/HEAD" in names
    assert "tracked.txt" in names


def test_write_tar_of_repo_skips_tracked_path_deleted_from_working_tree(tmp_path: Path, monkeypatch) -> None:
    # `git ls-files --cached` still lists a path for an unstaged deletion --
    # the index entry exists even though the working-tree file is gone.
    # `_write_tar_of_repo` must skip it (`os.path.lexists`) rather than
    # letting `tarfile.add` raise `FileNotFoundError`.
    fake_git_dir = tmp_path / "fake-git"
    fake_git_dir.mkdir()
    (fake_git_dir / "HEAD").write_text("ref: refs/heads/main\n")

    present_file = tmp_path / "present.txt"
    present_file.write_text("still here\n")
    # "deleted.txt" is deliberately NOT created on disk.

    monkeypatch.setattr(wrapper, "_materialized_git_dir", lambda stack, passthrough: fake_git_dir)
    monkeypatch.setattr(wrapper, "_tracked_paths",
                         lambda *, include_untracked: ["present.txt", "deleted.txt"])
    monkeypatch.setattr(wrapper, "REPO", tmp_path)
    monkeypatch.setattr(wrapper, "_warn_about_dirty_tracked_files", lambda: None)
    monkeypatch.setattr(wrapper, "_warn_about_hidden_tracked_file_flags", lambda: None)

    dest = tmp_path / "out.tar"
    wrapper._write_tar_of_repo(dest, [], include_untracked=False)
    with tarfile.open(dest) as tar:
        names = set(tar.getnames())
    assert "present.txt" in names
    assert "deleted.txt" not in names


def test_write_tar_of_repo_includes_a_tracked_dangling_symlink(tmp_path: Path, monkeypatch) -> None:
    # A tracked symlink whose target doesn't exist on disk must still be
    # archived (`os.path.lexists` reports True for a dangling symlink,
    # unlike a symlink-following `Path.exists()`) -- this is the
    # complementary half of the symlink-handling fix: the SNAPSHOT still
    # includes a dangling symlink entry as-is (never dereferenced), while
    # the separate `_populate_workspace` permission pass must not try to
    # `chmod` it (regression coverage for that lives in the
    # `_populate_workspace` tests, which assert the `find` invocation
    # excludes symlink entries entirely).
    fake_git_dir = tmp_path / "fake-git"
    fake_git_dir.mkdir()
    (fake_git_dir / "HEAD").write_text("ref: refs/heads/main\n")

    dangling_link = tmp_path / "dangling-link.txt"
    dangling_link.symlink_to(tmp_path / "does-not-exist.txt")

    monkeypatch.setattr(wrapper, "_materialized_git_dir", lambda stack, passthrough: fake_git_dir)
    monkeypatch.setattr(wrapper, "_tracked_paths",
                         lambda *, include_untracked: ["dangling-link.txt"])
    monkeypatch.setattr(wrapper, "REPO", tmp_path)
    monkeypatch.setattr(wrapper, "_warn_about_dirty_tracked_files", lambda: None)
    monkeypatch.setattr(wrapper, "_warn_about_hidden_tracked_file_flags", lambda: None)

    dest = tmp_path / "out.tar"
    wrapper._write_tar_of_repo(dest, [], include_untracked=False)
    with tarfile.open(dest) as tar:
        member = tar.getmember("dangling-link.txt")
    assert member.issym()


def test_write_tar_of_repo_rejects_a_symlinked_ancestor_directory(tmp_path: Path, monkeypatch) -> None:
    # `git ls-files` lists a path relative to the recorded tree structure
    # -- if a tracked path's ANCESTOR directory has since been replaced on
    # disk with a symlink pointing OUTSIDE the repo, `os.path.lexists` on
    # the full path still reports True (it only checks the FINAL
    # component's own link status; the OS transparently follows the
    # symlinked ancestor to reach a REAL file living elsewhere) --
    # confirmed live that this would otherwise silently archive an
    # external file's real bytes under the tracked path's name. Must fail
    # closed instead.
    fake_git_dir = tmp_path / "fake-git"
    fake_git_dir.mkdir()
    (fake_git_dir / "HEAD").write_text("ref: refs/heads/main\n")

    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not part of the checkout\n")
    # "dir" is recorded in git history as a real tracked directory, but on
    # THIS disk it has been replaced with a symlink pointing outside.
    (repo / "dir").symlink_to(outside)

    monkeypatch.setattr(wrapper, "_materialized_git_dir", lambda stack, passthrough: fake_git_dir)
    monkeypatch.setattr(wrapper, "_tracked_paths",
                         lambda *, include_untracked: ["dir/secret.txt"])
    monkeypatch.setattr(wrapper, "REPO", repo)
    monkeypatch.setattr(wrapper, "_warn_about_dirty_tracked_files", lambda: None)
    monkeypatch.setattr(wrapper, "_warn_about_hidden_tracked_file_flags", lambda: None)

    dest = tmp_path / "out.tar"
    try:
        wrapper._write_tar_of_repo(dest, [], include_untracked=False)
    except SystemExit as exc:
        assert "dir/secret.txt" in str(exc)
        assert "outside the repository root" in str(exc)
    else:
        raise AssertionError("expected SystemExit for a symlinked ancestor directory")


def test_write_tar_of_repo_does_not_recurse_into_submodule_directory(tmp_path: Path, monkeypatch) -> None:
    # `git ls-files` lists an initialized submodule as a single path that
    # happens to be a real DIRECTORY on disk. `tarfile.add` recursively
    # archives directories by default -- that would copy the submodule's
    # entire working tree (including its own untracked/ignored files and
    # `.git` metadata) wholesale, defeating the tracked-files-only
    # boundary. `recursive=False` must keep the directory entry itself
    # from being expanded.
    fake_git_dir = tmp_path / "fake-git"
    fake_git_dir.mkdir()
    (fake_git_dir / "HEAD").write_text("ref: refs/heads/main\n")

    submodule_dir = tmp_path / "vendor" / "some-submodule"
    submodule_dir.mkdir(parents=True)
    (submodule_dir / "secret-inside-submodule.txt").write_text("should not be archived\n")

    monkeypatch.setattr(wrapper, "_materialized_git_dir", lambda stack, passthrough: fake_git_dir)
    monkeypatch.setattr(wrapper, "_tracked_paths",
                         lambda *, include_untracked: ["vendor/some-submodule"])
    monkeypatch.setattr(wrapper, "REPO", tmp_path)
    monkeypatch.setattr(wrapper, "_warn_about_dirty_tracked_files", lambda: None)
    monkeypatch.setattr(wrapper, "_warn_about_hidden_tracked_file_flags", lambda: None)

    dest = tmp_path / "out.tar"
    wrapper._write_tar_of_repo(dest, [], include_untracked=False)
    with tarfile.open(dest) as tar:
        names = set(tar.getnames())
    assert "vendor/some-submodule" in names
    assert "vendor/some-submodule/secret-inside-submodule.txt" not in names


def test_warn_about_dirty_tracked_files_reports_modified_tracked_paths(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)

    # An uncommitted modification to an already-tracked file.
    (repo / "tracked.txt").write_text("v2 -- locally modified\n")

    monkeypatch.setattr(wrapper, "REPO", repo)
    wrapper._warn_about_dirty_tracked_files()
    err = capsys.readouterr().err
    assert "tracked.txt" in err
    assert "warning" in err.lower()


def test_scrubbed_git_env_strips_an_otherwise_unlisted_git_variable(monkeypatch) -> None:
    # The exact regression this closes: a narrower, name-by-name
    # allowlist of variables to strip can only ever anticipate the ones
    # someone thought of -- the blanket `GIT_*` strip must catch a
    # completely made-up, never-enumerated-anywhere variable too.
    monkeypatch.setenv("GIT_TOTALLY_MADE_UP_VARIABLE_NOBODY_LISTED", "host-value")
    env = wrapper._scrubbed_git_env()
    assert "GIT_TOTALLY_MADE_UP_VARIABLE_NOBODY_LISTED" not in env


def test_scrubbed_git_env_prevents_a_configured_fsmonitor_hook_from_running(
    tmp_path: Path, monkeypatch,
) -> None:
    # The exact regression this closes: a configured `core.fsmonitor` hook
    # runs arbitrary host code as part of an ostensibly READ-ONLY `git
    # status` probe against the REAL host checkout, unless local, global,
    # AND system config are all neutralized for it.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)

    marker = tmp_path / "fsmonitor-ran.marker"
    hook = tmp_path / "fake-fsmonitor-hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nprintf '1\\n'\n")
    hook.chmod(0o755)
    _run_git(["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True)

    monkeypatch.setattr(wrapper, "REPO", repo)
    real_subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=no"],
        env=wrapper._scrubbed_git_env(), capture_output=True, timeout=30,
    )
    assert not marker.exists(), "a configured core.fsmonitor hook executed despite the scrubbed environment"


def test_warn_about_dirty_tracked_files_silent_when_clean(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)

    monkeypatch.setattr(wrapper, "REPO", repo)
    wrapper._warn_about_dirty_tracked_files()
    assert capsys.readouterr().err == ""


def test_warn_about_dirty_tracked_files_does_not_warn_about_untracked_files(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    # A brand-new untracked file is a DIFFERENT (already-covered) concern
    # -- `--untracked-files=no` means this function must stay silent about
    # it, not conflate the two.
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tracked.txt").write_text("v1\n")
    _run_git(["git", "-C", str(repo), "add", "."], check=True)
    _run_git(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    (repo / "new-untracked.txt").write_text("brand new\n")

    monkeypatch.setattr(wrapper, "REPO", repo)
    wrapper._warn_about_dirty_tracked_files()
    assert capsys.readouterr().err == ""


def test_warn_about_dirty_tracked_files_ignores_submodules(monkeypatch) -> None:
    # `git status` recursively inspects an initialized submodule by
    # default, which would consult a SUBMODULE-specific
    # `filter.<name>.clean`/`.process` assignment that
    # `_discover_configured_clean_filters` (superproject tracked paths
    # only) never covers -- an ostensibly read-only probe executing
    # unneutralized filter code. `--ignore-submodules=all` must always be
    # passed; the snapshot never copies submodule contents anyway (see
    # `_write_tar_of_repo`), so there's nothing useful to report there
    # regardless.
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: [])
    fake_result = mock.Mock(returncode=0, stdout=b"")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result) as run:
        wrapper._warn_about_dirty_tracked_files()
    args = run.call_args.args[0]
    assert "--ignore-submodules=all" in args


def test_warn_about_dirty_tracked_files_fails_closed_when_status_itself_fails(monkeypatch) -> None:
    # A failed `git status` must never be silently treated as "clean" --
    # this check is the runtime mitigation for accidental secret exposure,
    # so an unknown dirty state must abort the snapshot, not proceed.
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: [])
    fake_result = mock.Mock(returncode=128, stdout=b"", stderr=b"not a git repository")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        try:
            wrapper._warn_about_dirty_tracked_files()
        except SystemExit as exc:
            assert "not a git repository" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_warn_about_hidden_tracked_file_flags_reports_assume_unchanged_and_skip_worktree(capsys) -> None:
    # `H` is an ordinary cached entry (no flag); a lowercase letter means
    # assume-unchanged, and `S` means skip-worktree -- both suppress `git
    # status`'s own on-disk-modification reporting for that path, while
    # the snapshot still archives its real current content regardless.
    fake_result = mock.Mock(
        returncode=0,
        stdout=b"H normal.txt\nh assumed-unchanged.txt\nS skip-worktree.txt\n",
        stderr=b"",
    )
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        wrapper._warn_about_hidden_tracked_file_flags()
    err = capsys.readouterr().err
    assert "assumed-unchanged.txt" in err
    assert "skip-worktree.txt" in err
    assert "normal.txt" not in err


def test_warn_about_hidden_tracked_file_flags_silent_when_none_flagged() -> None:
    fake_result = mock.Mock(returncode=0, stdout=b"H normal.txt\n", stderr=b"")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        wrapper._warn_about_hidden_tracked_file_flags()


def test_warn_about_hidden_tracked_file_flags_fails_closed_when_ls_files_fails(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_discover_configured_clean_filters", lambda: [])
    fake_result = mock.Mock(returncode=128, stdout=b"", stderr=b"not a git repository")
    with mock.patch.object(wrapper.subprocess, "run", return_value=fake_result):
        try:
            wrapper._warn_about_hidden_tracked_file_flags()
        except SystemExit as exc:
            assert "not a git repository" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_populate_workspace_streams_tar_file_as_stdin_then_chmod(monkeypatch) -> None:
    written_paths: list[Path] = []
    written_passthrough: list[list[str]] = []
    written_include_untracked: list[bool] = []

    def fake_write_tar(dest: Path, passthrough: list[str], *, include_untracked: bool) -> None:
        written_paths.append(dest)
        written_passthrough.append(passthrough)
        written_include_untracked.append(include_untracked)
        dest.write_bytes(b"not-empty")

    monkeypatch.setattr(wrapper, "_write_tar_of_repo", fake_write_tar)
    chmod_root_result = mock.Mock(returncode=0, stderr="")
    tar_result = mock.Mock(returncode=0, stderr=b"")
    chmod_result = mock.Mock(returncode=0, stderr="")
    with mock.patch.object(wrapper.subprocess, "run",
                            side_effect=[chmod_root_result, tar_result, chmod_result]) as run:
        wrapper._populate_workspace("container-9", ["--changed"], include_untracked=True)
    assert run.call_count == 3
    assert len(written_paths) == 1
    assert written_passthrough == [["--changed"]]
    assert written_include_untracked == [True]

    chmod_root_call = run.call_args_list[0]
    chmod_root_args = chmod_root_call.args[0]
    assert chmod_root_args[:4] == ["docker", "exec", "-u", "root"]
    assert "chmod" in chmod_root_args
    assert wrapper.CONTAINER_WORKSPACE in chmod_root_args

    tar_call = run.call_args_list[1]
    tar_args = tar_call.args[0]
    assert tar_args[:5] == ["docker", "exec", "-i", "-u", wrapper.REMOTE_USER]
    assert "container-9" in tar_args
    assert "tar" in tar_args
    # Extraction runs AS the non-root remote user, not root -- every
    # extracted file is then natively owned by that user with no chown
    # step needed (and none would be possible: `--no-same-owner` is no
    # longer necessary or present once extraction itself isn't root).
    assert "--no-same-owner" not in tar_args
    # Streamed via `stdin=`, never buffered as an `input=` bytes payload.
    assert "stdin" in tar_call.kwargs
    assert "input" not in tar_call.kwargs

    chmod_args = run.call_args_list[2].args[0]
    assert chmod_args[:4] == ["docker", "exec", "-u", wrapper.REMOTE_USER]
    assert "find" in chmod_args
    assert "chmod" in chmod_args
    assert wrapper.CONTAINER_WORKSPACE in chmod_args
    # Only regular files and directories are chmod'd -- `chmod` on a
    # symlink PATH dereferences it and would either fail outright (a
    # dangling symlink) or affect whatever a LIVE symlink points at,
    # possibly outside the workspace tree entirely.
    type_values = [
        chmod_args[i + 1] for i, arg in enumerate(chmod_args) if arg == "-type"
    ]
    assert set(type_values) == {"f", "d"}


def test_populate_workspace_raises_when_opening_up_the_empty_volume_fails(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_write_tar_of_repo",
                         lambda dest, passthrough, *, include_untracked: dest.write_bytes(b""))
    chmod_root_result = mock.Mock(returncode=1, stderr="chmod: operation not permitted")
    with mock.patch.object(wrapper.subprocess, "run", return_value=chmod_root_result):
        try:
            wrapper._populate_workspace("container-9", [], include_untracked=False)
        except SystemExit as exc:
            assert "operation not permitted" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_populate_workspace_raises_when_tar_extraction_fails(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_write_tar_of_repo",
                         lambda dest, passthrough, *, include_untracked: dest.write_bytes(b""))
    chmod_root_result = mock.Mock(returncode=0, stderr="")
    tar_result = mock.Mock(returncode=1, stderr=b"tar: permission denied")
    with mock.patch.object(wrapper.subprocess, "run",
                            side_effect=[chmod_root_result, tar_result]):
        try:
            wrapper._populate_workspace("container-9", [], include_untracked=False)
        except SystemExit as exc:
            assert "permission denied" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_populate_workspace_raises_when_chmod_fails(monkeypatch) -> None:
    monkeypatch.setattr(wrapper, "_write_tar_of_repo",
                         lambda dest, passthrough, *, include_untracked: dest.write_bytes(b""))
    chmod_root_result = mock.Mock(returncode=0, stderr="")
    tar_result = mock.Mock(returncode=0, stderr=b"")
    chmod_result = mock.Mock(returncode=1, stderr="chmod: operation not permitted")
    with mock.patch.object(wrapper.subprocess, "run",
                            side_effect=[chmod_root_result, tar_result, chmod_result]):
        try:
            wrapper._populate_workspace("container-9", [], include_untracked=False)
        except SystemExit as exc:
            assert "operation not permitted" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_run_tests_invokes_devcontainer_exec_with_full_passthrough_args(tmp_path: Path) -> None:
    fake_result = mock.Mock(returncode=3)
    config_path = tmp_path / "devcontainer.json"
    config_path.write_text("{}")
    with mock.patch.object(wrapper.shutil, "which", return_value="/usr/bin/devcontainer"), \
         mock.patch.object(wrapper.subprocess, "run", return_value=fake_result) as run:
        code = wrapper._run_tests("abc123", config_path, ["agent-worktrees", "-k", "foo"])
    assert code == 3
    args = run.call_args.args[0]
    assert args[:2] == ["/usr/bin/devcontainer", "exec"]
    assert "--container-id" in args
    assert str(config_path) in args
    # The complete expected suffix, not a looser subset -- a regression
    # that drops the final passthrough argument must fail this assertion.
    assert args[-5:] == ["python", "tools/run-plugin-tests.py", "agent-worktrees", "-k", "foo"]


def test_is_list_only_detects_bare_list_flag() -> None:
    assert wrapper._net_scope.is_list_only(["--list"], wrapper._canonicalize_flag) is True


def test_is_list_only_detects_abbreviated_list_flag() -> None:
    assert wrapper._net_scope.is_list_only(["--lis"], wrapper._canonicalize_flag) is True


def test_is_list_only_false_for_other_passthrough() -> None:
    canon = wrapper._canonicalize_flag
    assert wrapper._net_scope.is_list_only(["agent-worktrees"], canon) is False
    assert wrapper._net_scope.is_list_only(["--collect-only"], canon) is False
    assert wrapper._net_scope.is_list_only([], canon) is False


def test_prepare_dependencies_appends_prepare_only_when_absent(tmp_path: Path) -> None:
    fake_result = mock.Mock(returncode=0)
    config_path = tmp_path / "devcontainer.json"
    config_path.write_text("{}")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=fake_result) as run:
        wrapper._net_scope.prepare_dependencies(
            "/usr/bin/devcontainer", wrapper.REPO, "abc123", config_path,
            ["agent-worktrees"], wrapper._canonicalize_flag,
        )
    args = run.call_args.args[0]
    assert args[-3:] == ["tools/run-plugin-tests.py", "agent-worktrees", "--prepare-only"]


def test_prepare_dependencies_does_not_duplicate_an_explicit_prepare_only(tmp_path: Path) -> None:
    fake_result = mock.Mock(returncode=0)
    config_path = tmp_path / "devcontainer.json"
    config_path.write_text("{}")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=fake_result) as run:
        wrapper._net_scope.prepare_dependencies(
            "/usr/bin/devcontainer", wrapper.REPO, "abc123", config_path,
            ["agent-worktrees", "--prepare-only"], wrapper._canonicalize_flag,
        )
    args = run.call_args.args[0]
    assert args.count("--prepare-only") == 1


def test_prepare_dependencies_raises_on_nonzero_exit(tmp_path: Path) -> None:
    fake_result = mock.Mock(returncode=1)
    config_path = tmp_path / "devcontainer.json"
    config_path.write_text("{}")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=fake_result):
        try:
            wrapper._net_scope.prepare_dependencies(
                "/usr/bin/devcontainer", wrapper.REPO, "abc123", config_path,
                ["agent-worktrees"], wrapper._canonicalize_flag,
            )
        except SystemExit as exc:
            assert "dependency-preparation" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_strip_reinstall_removes_the_flag() -> None:
    canon = wrapper._canonicalize_flag
    result = wrapper._net_scope.strip_reinstall(["agent-worktrees", "--reinstall"], canon)
    assert result == ["agent-worktrees"]


def test_strip_reinstall_recognizes_abbreviated_flag() -> None:
    canon = wrapper._canonicalize_flag
    result = wrapper._net_scope.strip_reinstall(["agent-worktrees", "--reinst"], canon)
    assert result == ["agent-worktrees"]


def test_strip_reinstall_leaves_other_flags_untouched() -> None:
    canon = wrapper._canonicalize_flag
    passthrough = ["agent-worktrees", "--all", "-k", "foo"]
    assert wrapper._net_scope.strip_reinstall(passthrough, canon) == passthrough


def test_disconnect_container_networks_disconnects_every_attached_network() -> None:
    inspect_result = mock.Mock(
        returncode=0, stdout='bridge\t{"bridge": {}, "test-isolation-net": {}}', stderr="",
    )
    disconnect_result = mock.Mock(returncode=0, stdout="", stderr="")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return inspect_result if args[1] == "inspect" else disconnect_result

    with mock.patch.object(wrapper._net_scope.subprocess, "run", side_effect=fake_run):
        wrapper._net_scope.disconnect_container_networks("abc123")
    disconnect_calls = [c for c in calls if c[1] == "network"]
    assert len(disconnect_calls) == 2
    disconnected_networks = {c[4] for c in disconnect_calls}
    assert disconnected_networks == {"bridge", "test-isolation-net"}
    for c in disconnect_calls:
        assert c[:2] == ["docker", "network"]
        assert "-f" in c


def test_disconnect_container_networks_raises_on_inspect_failure() -> None:
    inspect_result = mock.Mock(returncode=1, stdout="", stderr="no such container")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=inspect_result):
        try:
            wrapper._net_scope.disconnect_container_networks("abc123")
        except SystemExit as exc:
            assert "no such container" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_disconnect_container_networks_raises_on_malformed_inspect_output() -> None:
    inspect_result = mock.Mock(returncode=0, stdout="bridge\tnot json", stderr="")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=inspect_result):
        try:
            wrapper._net_scope.disconnect_container_networks("abc123")
        except SystemExit as exc:
            assert "could not parse" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_disconnect_container_networks_raises_on_disconnect_failure() -> None:
    inspect_result = mock.Mock(returncode=0, stdout='bridge\t{"bridge": {}}', stderr="")
    disconnect_result = mock.Mock(returncode=1, stdout="", stderr="not attached")

    def fake_run(args, **kwargs):
        return inspect_result if args[1] == "inspect" else disconnect_result

    with mock.patch.object(wrapper._net_scope.subprocess, "run", side_effect=fake_run):
        try:
            wrapper._net_scope.disconnect_container_networks("abc123")
        except SystemExit as exc:
            assert "not attached" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_disconnect_container_networks_fails_closed_on_host_network_mode() -> None:
    inspect_result = mock.Mock(returncode=0, stdout="host\t{}", stderr="")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=inspect_result):
        try:
            wrapper._net_scope.disconnect_container_networks("abc123")
        except SystemExit as exc:
            assert "host" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_disconnect_container_networks_fails_closed_on_shared_container_namespace() -> None:
    inspect_result = mock.Mock(returncode=0, stdout="container:other-id\t{}", stderr="")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=inspect_result):
        try:
            wrapper._net_scope.disconnect_container_networks("abc123")
        except SystemExit as exc:
            assert "container:other-id" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_disconnect_container_networks_fails_closed_on_empty_map_with_non_none_mode() -> None:
    # Invariant: an empty NetworkSettings.Networks map must never be read
    # as "already isolated" for a mode other than literal "none" (Docker's
    # real shape for --network none is one entry keyed "none" -> {}).
    inspect_result = mock.Mock(returncode=0, stdout="bridge\t{}", stderr="")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=inspect_result):
        try:
            wrapper._net_scope.disconnect_container_networks("abc123")
        except SystemExit as exc:
            assert "no inspectable attached networks" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_disconnect_container_networks_no_op_for_none_mode() -> None:
    inspect_result = mock.Mock(returncode=0, stdout='none\t{"none": {}}', stderr="")
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=inspect_result) as run:
        wrapper._net_scope.disconnect_container_networks("abc123")
    assert run.call_count == 1  # only the inspect call -- nothing to disconnect


def test_disconnect_container_networks_no_op_for_none_mode_with_real_endpoint_metadata() -> None:
    # Only the NETWORK KEY is the isolation invariant -- a legitimate
    # `--network none` container's single "none" entry still carries real
    # (non-empty) EndpointSettings metadata; comparing the whole value to
    # `{}` would wrongly reject this common, safe shape.
    inspect_result = mock.Mock(
        returncode=0,
        stdout='none\t{"none": {"NetworkID": "abc", "EndpointID": "def"}}',
        stderr="",
    )
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=inspect_result) as run:
        wrapper._net_scope.disconnect_container_networks("abc123")
    assert run.call_count == 1


def test_disconnect_container_networks_fails_closed_for_none_mode_with_extra_network() -> None:
    # NetworkMode reflects CREATION-time config, not live state -- a later
    # `docker network connect` can attach a real network to a
    # "none"-mode container while this field stays frozen at "none".
    inspect_result = mock.Mock(
        returncode=0, stdout='none\t{"none": {}, "bridge": {}}', stderr="",
    )
    with mock.patch.object(wrapper._net_scope.subprocess, "run", return_value=inspect_result):
        try:
            wrapper._net_scope.disconnect_container_networks("abc123")
        except SystemExit as exc:
            assert "not just 'none'" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_admission_needs_admission_false_only_for_list() -> None:
    # `--list` returns before `run-plugin-tests.py` ever reaches
    # `_ensure_venv()`, so it is the only mode that never touches the
    # shared venv and is therefore exempt from the host-wide lease.
    canon = wrapper._canonicalize_flag
    assert wrapper._admission.needs_admission(["--list"], canon) is False


def test_admission_needs_admission_true_for_a_real_run() -> None:
    canon = wrapper._canonicalize_flag
    assert wrapper._admission.needs_admission(["agent-worktrees"], canon) is True
    assert wrapper._admission.needs_admission([], canon) is True


def test_admission_needs_admission_true_for_venv_mutating_modes() -> None:
    # `--guards`, `--collect-only`, and `--prepare-only` all reach
    # `_ensure_venv()` and so can rebuild/delete the SHARED on-disk venv
    # (via `--reinstall` or a drifted dependency fingerprint) a
    # concurrent admitted run may be relying on mid-execution -- none of
    # them are exempt.
    canon = wrapper._canonicalize_flag
    assert wrapper._admission.needs_admission(["--guards"], canon) is True
    assert wrapper._admission.needs_admission(["--collect-only"], canon) is True
    assert wrapper._admission.needs_admission(["--prepare-only"], canon) is True


def test_admission_resolve_wait_defaults_to_zero() -> None:
    assert wrapper._admission.resolve_admission_wait(["agent-worktrees"], wrapper._canonicalize_flag) == 0.0


def test_admission_resolve_wait_extracts_space_and_equals_forms() -> None:
    canon = wrapper._canonicalize_flag
    assert wrapper._admission.resolve_admission_wait(["--admission-wait", "30"], canon) == 30.0
    assert wrapper._admission.resolve_admission_wait(["--admission-wait=45"], canon) == 45.0


def test_admission_resolve_wait_honors_last_repeated_occurrence() -> None:
    canon = wrapper._canonicalize_flag
    passthrough = ["--admission-wait", "10", "--admission-wait", "20"]
    assert wrapper._admission.resolve_admission_wait(passthrough, canon) == 20.0


def test_admission_resolve_wait_rejects_malformed_value_immediately() -> None:
    # Matches argparse's own semantics: each occurrence is validated as
    # it's seen, not just the FINAL resolved value -- a malformed earlier
    # occurrence must not be silently masked by a later valid override,
    # since argparse itself would reject it the moment it's parsed.
    canon = wrapper._canonicalize_flag
    try:
        wrapper._admission.resolve_admission_wait(["--admission-wait", "bad"], canon)
    except SystemExit as exc:
        assert "bad" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_admission_resolve_wait_rejects_malformed_earlier_occurrence_even_with_later_valid_one() -> None:
    canon = wrapper._canonicalize_flag
    passthrough = ["--admission-wait", "bad", "--admission-wait", "20"]
    try:
        wrapper._admission.resolve_admission_wait(passthrough, canon)
    except SystemExit as exc:
        assert "bad" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_admission_acquire_rejects_negative_wait_with_system_exit() -> None:
    try:
        wrapper._admission.acquire(-1.0)
    except SystemExit as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_admission_acquire_succeeds_when_uncontested(tmp_path: Path) -> None:
    with mock.patch.object(wrapper._admission, "admission_dir", return_value=tmp_path):
        lease = wrapper._admission.acquire(0.0)
    try:
        assert lease.held
    finally:
        lease.release()


def test_admission_acquire_fails_fast_when_busy_and_wait_is_zero(tmp_path: Path) -> None:
    with mock.patch.object(wrapper._admission, "admission_dir", return_value=tmp_path):
        holder = wrapper._admission.acquire(0.0)
        try:
            try:
                wrapper._admission.acquire(0.0)
            except SystemExit as exc:
                assert "BUSY" in str(exc)
            else:
                raise AssertionError("expected SystemExit")
        finally:
            holder.release()


def test_admission_acquire_waits_then_succeeds_once_released(tmp_path: Path) -> None:
    import threading

    with mock.patch.object(wrapper._admission, "admission_dir", return_value=tmp_path):
        holder = wrapper._admission.acquire(0.0)

        def release_soon() -> None:
            import time
            time.sleep(0.1)
            holder.release()

        threading.Thread(target=release_soon).start()
        lease = wrapper._admission.acquire(2.0)
    try:
        assert lease.held
    finally:
        lease.release()


def test_main_acquires_and_releases_admission_lease_around_the_run(monkeypatch, tmp_path: Path) -> None:
    order: list[str] = []
    fake_lease = mock.Mock()
    fake_lease.release = mock.Mock(side_effect=lambda: order.append("release"))

    config_path = tmp_path / "cfgdir-admission" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")
    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: order.append("bring-up") or "container-admission")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper._net_scope, "is_list_only", lambda passthrough, canonicalize: True)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: order.append("run") or 0)
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: True)
    monkeypatch.setattr(wrapper._admission, "acquire", lambda wait: order.append("acquire") or fake_lease)

    rc = wrapper.main(["agent-worktrees"])
    assert rc == 0
    assert order == ["acquire", "bring-up", "run", "release"]


def test_main_releases_admission_lease_even_when_per_instance_config_fails(monkeypatch) -> None:
    # Invariant: once the host-wide lease is acquired, it must be released
    # on EVERY exit path -- including a failure as early as
    # `_per_instance_config` itself, not just the common success path.
    release_calls: list[str] = []
    fake_lease = mock.Mock()
    fake_lease.release = mock.Mock(side_effect=lambda: release_calls.append("release"))

    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: True)
    monkeypatch.setattr(wrapper._admission, "acquire", lambda wait: fake_lease)

    def failing_per_instance_config(label: str):
        raise SystemExit("devcontainer config write failed: boom")

    monkeypatch.setattr(wrapper, "_per_instance_config", failing_per_instance_config)

    try:
        wrapper.main(["agent-worktrees"])
    except SystemExit as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected the original SystemExit to propagate")
    assert release_calls == ["release"]


def test_main_skips_admission_for_guards(monkeypatch, tmp_path: Path) -> None:
    acquire_calls: list[float] = []
    config_path = tmp_path / "cfgdir-guards" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")
    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-guards")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 0)
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)
    monkeypatch.setattr(wrapper._admission, "acquire", lambda wait: acquire_calls.append(wait))

    rc = wrapper.main(["agent-worktrees", "--guards"])
    assert rc == 0
    assert acquire_calls == []


def test_tear_down_removes_container_then_volume_on_success() -> None:
    container_result = mock.Mock(returncode=0, stderr="")
    volume_result = mock.Mock(returncode=0, stderr="")
    with mock.patch.object(wrapper.subprocess, "run",
                            side_effect=[container_result, volume_result]) as run:
        wrapper._tear_down("container-5", "volume-5")
    assert run.call_count == 2
    assert run.call_args_list[0].args[0] == ["docker", "rm", "-f", "container-5"]
    assert run.call_args_list[1].args[0] == ["docker", "volume", "rm", "volume-5"]
    # A terminal Ctrl-C delivers SIGINT to the WHOLE foreground process
    # group -- without `start_new_session=True`, these children retain
    # the default handler and can die mid-removal despite
    # `_cleanup_signals_deferred` protecting the Python parent.
    assert run.call_args_list[0].kwargs["start_new_session"] is True
    assert run.call_args_list[1].kwargs["start_new_session"] is True


def test_tear_down_raises_when_container_removal_fails_but_still_attempts_volume() -> None:
    container_result = mock.Mock(returncode=1, stderr="container busy")
    volume_result = mock.Mock(returncode=0, stderr="")
    with mock.patch.object(wrapper.subprocess, "run",
                            side_effect=[container_result, volume_result]) as run:
        try:
            wrapper._tear_down("container-5", "volume-5")
        except SystemExit as exc:
            assert "container busy" in str(exc)
        else:
            raise AssertionError("expected SystemExit")
    # The volume removal must still be attempted even though the container
    # removal already failed -- a failed container removal must not skip
    # cleaning up the volume too.
    assert run.call_count == 2


def test_tear_down_raises_when_volume_removal_fails() -> None:
    container_result = mock.Mock(returncode=0, stderr="")
    volume_result = mock.Mock(returncode=1, stderr="volume in use")
    with mock.patch.object(wrapper.subprocess, "run",
                            side_effect=[container_result, volume_result]):
        try:
            wrapper._tear_down("container-5", "volume-5")
        except SystemExit as exc:
            assert "volume in use" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_cleanup_orphan_removes_containers_found_by_label_then_volume() -> None:
    find_result = mock.Mock(returncode=0, stdout="cid-a cid-b\n", stderr="")
    rm_a = mock.Mock(returncode=0)
    rm_b = mock.Mock(returncode=0)
    vol_result = mock.Mock(returncode=0)
    with mock.patch.object(wrapper.subprocess, "run",
                            side_effect=[find_result, rm_a, rm_b, vol_result]) as run:
        wrapper._cleanup_orphan("instance-label", "fake-volume")
    assert run.call_count == 4
    find_args = run.call_args_list[0].args[0]
    assert find_args[:2] == ["docker", "ps"]
    assert "label=devcontainer-test-isolation.instance=instance-label" in find_args
    assert run.call_args_list[1].args[0] == ["docker", "rm", "-f", "cid-a"]
    assert run.call_args_list[2].args[0] == ["docker", "rm", "-f", "cid-b"]
    assert run.call_args_list[3].args[0] == ["docker", "volume", "rm", "fake-volume"]
    # Same process-group isolation `_tear_down` applies -- see its own
    # docstring for why a bare terminal SIGINT would otherwise still
    # reach these children directly.
    assert all(call.kwargs["start_new_session"] is True for call in run.call_args_list)


def test_cleanup_orphan_warns_but_does_not_raise_on_nonzero_results(capsys) -> None:
    find_result = mock.Mock(returncode=1, stdout="", stderr="docker ps failed")
    vol_result = mock.Mock(returncode=1, stderr="volume busy")
    with mock.patch.object(wrapper.subprocess, "run", side_effect=[find_result, vol_result]):
        # Must not raise -- a failed `docker ps` is reported, never
        # silently treated as "no orphan exists".
        wrapper._cleanup_orphan("instance-label", "fake-volume")
    err = capsys.readouterr().err
    assert "docker ps failed" in err
    assert "volume busy" in err


def test_cleanup_orphan_warns_but_does_not_raise_if_container_removal_fails(capsys) -> None:
    find_result = mock.Mock(returncode=0, stdout="cid-a\n", stderr="")
    rm_result = mock.Mock(returncode=1, stderr="container busy")
    vol_result = mock.Mock(returncode=0, stderr="")
    with mock.patch.object(wrapper.subprocess, "run",
                            side_effect=[find_result, rm_result, vol_result]):
        wrapper._cleanup_orphan("instance-label", "fake-volume")
    assert "container busy" in capsys.readouterr().err


def test_cleanup_orphan_never_raises_when_every_subprocess_call_itself_raises(capsys) -> None:
    import subprocess as real_subprocess

    def always_times_out(*args, **kwargs):
        raise real_subprocess.TimeoutExpired(cmd=args[0] if args else "docker", timeout=30)

    with mock.patch.object(wrapper.subprocess, "run", side_effect=always_times_out):
        # Must not raise -- this runs while an already-failing startup
        # error is propagating, and that original error must surface, not
        # a secondary cleanup failure (not even a raised TimeoutExpired
        # from one of the cleanup's own subprocess calls).
        wrapper._cleanup_orphan("instance-label", "fake-volume")
    # Still reported, just not raised.
    assert "warning" in capsys.readouterr().err.lower()


def test_raise_on_sigterm_raises_termination_requested() -> None:
    try:
        wrapper._raise_on_sigterm(signal.SIGTERM, None)
    except wrapper._TerminationRequested as exc:
        assert "SIGTERM" in str(exc) or str(int(signal.SIGTERM)) in str(exc)
    else:
        raise AssertionError("expected _TerminationRequested")


def test_main_installs_a_sigterm_handler(monkeypatch) -> None:
    # The default SIGTERM action terminates the process immediately,
    # bypassing every `finally` block (container/volume teardown
    # included) -- `main` must convert it into a normal raised exception
    # instead, so an outer timeout or CI cancellation can't leak a
    # container/volume with no later run able to find it.
    config_path = None

    def fake_per_instance_config(label: str):
        nonlocal config_path
        import tempfile as _tempfile
        d = _tempfile.mkdtemp()
        config_path = Path(d) / "devcontainer.json"
        config_path.write_text("{}")
        return config_path, "fake-volume"

    monkeypatch.setattr(wrapper, "_per_instance_config", fake_per_instance_config)
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda instance_label, config_path: "container-1")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace",
                         lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper, "_run_tests",
                         lambda container_id, config_path, passthrough: 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)

    signal_calls: list[tuple] = []
    with mock.patch.object(wrapper.signal, "signal",
                            side_effect=lambda *a: signal_calls.append(a)) as signal_mock:
        wrapper.main([])
    # `_cleanup_signals_deferred` (wrapping the `_tear_down` call below) also
    # calls `signal.signal` -- assert the INITIAL handler registration
    # specifically, not a total call count.
    assert signal_mock.call_count >= 1
    registered_signum, registered_handler = signal_calls[0]
    assert registered_signum == wrapper.signal.SIGTERM
    assert registered_handler is wrapper._raise_on_sigterm


def test_main_restores_the_previous_sigterm_handler_after_returning(monkeypatch, tmp_path: Path) -> None:
    # `main` is also invoked in-process by this module's own test suite
    # (and any other programmatic caller) -- permanently replacing the
    # process-wide SIGTERM handler without restoring it would leak
    # `_raise_on_sigterm` into every later in-process call.
    config_path = tmp_path / "cfgdir-restore" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")
    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-restore")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)

    sentinel_handler = lambda signum, frame: None
    previous = signal.signal(signal.SIGTERM, sentinel_handler)
    try:
        signal.signal(signal.SIGTERM, sentinel_handler)
        wrapper.main(["agent-worktrees"])
        assert signal.getsignal(signal.SIGTERM) is sentinel_handler
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_main_installs_and_restores_a_sighup_handler(monkeypatch, tmp_path: Path) -> None:
    # SIGHUP also terminates the process by default on Linux (e.g. a
    # closed SSH/terminal session), bypassing every `finally` block just
    # like the unhandled-SIGTERM case this wrapper already closed --
    # confirmed this was previously missing entirely.
    config_path = tmp_path / "cfgdir-sighup" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")
    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-sighup")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)

    sentinel_handler = lambda signum, frame: None
    previous = signal.signal(signal.SIGHUP, sentinel_handler)
    try:
        wrapper.main(["agent-worktrees"])
        assert signal.getsignal(signal.SIGHUP) is sentinel_handler
    finally:
        signal.signal(signal.SIGHUP, previous)


def test_raise_on_sighup_raises_termination_requested() -> None:
    try:
        wrapper._raise_on_sigterm(signal.SIGHUP, None)
    except wrapper._TerminationRequested as exc:
        assert "SIGHUP" in str(exc) or str(int(signal.SIGHUP)) in str(exc)
    else:
        raise AssertionError("expected _TerminationRequested")


def test_cleanup_signals_deferred_records_signals_instead_of_ignoring_them(monkeypatch) -> None:
    # `SIG_IGN` would DISCARD a signal outright (nothing delivered or
    # queued later) -- this context manager must instead install a
    # handler that RECORDS receipt, so a signal arriving mid-cleanup can
    # still be acted on (re-raised) once cleanup finishes, rather than
    # silently vanishing.
    sentinel_handler = object()
    installed_handlers: dict[int, object] = {}

    def fake_signal(sig, handler):
        previous = installed_handlers.get(sig, sentinel_handler)
        installed_handlers[sig] = handler
        return previous

    with mock.patch.object(wrapper.signal, "signal", side_effect=fake_signal):
        with wrapper._cleanup_signals_deferred():
            sigterm_handler = installed_handlers[wrapper.signal.SIGTERM]
            sigint_handler = installed_handlers[wrapper.signal.SIGINT]
            assert sigterm_handler is not wrapper.signal.SIG_IGN
            assert sigint_handler is not wrapper.signal.SIG_IGN
            assert callable(sigterm_handler) and callable(sigint_handler)
    # No signal was delivered -- the context must exit cleanly, restoring
    # the previous (sentinel) handlers, with nothing raised.
    assert installed_handlers[wrapper.signal.SIGTERM] is sentinel_handler
    assert installed_handlers[wrapper.signal.SIGINT] is sentinel_handler


def test_cleanup_signals_deferred_blocks_signals_atomically_during_handler_swap() -> None:
    # Installing TWO handlers (SIGINT then SIGTERM) is not itself atomic
    # -- confirmed live that a signal landing between the two
    # `signal.signal()` calls would still hit whichever OLD handler is
    # still active for the second one, aborting entry before cleanup even
    # starts. `pthread_sigmask` must block BOTH signals for the install
    # swap, restoring the EXACT prior mask via `SIG_SETMASK` afterward
    # (never an unconditional `SIG_UNBLOCK`, which would silently unblock
    # a signal the caller had deliberately kept blocked -- confirmed
    # live). On exit: a brief `SIG_UNBLOCK`/`SIG_SETMASK` flush (while
    # `_record` is still installed) precedes the restore swap, which is
    # itself bracketed the same way as the install swap.
    calls: list[tuple] = []
    real_sigmask = wrapper.signal.pthread_sigmask
    real_signal = wrapper.signal.signal

    def tracking_sigmask(how, mask):
        calls.append(("sigmask", how))
        return real_sigmask(how, mask)

    def tracking_signal(sig, handler):
        calls.append(("signal", sig))
        return real_signal(sig, handler)

    with mock.patch.object(wrapper.signal, "pthread_sigmask", side_effect=tracking_sigmask), \
         mock.patch.object(wrapper.signal, "signal", side_effect=tracking_signal):
        with wrapper._cleanup_signals_deferred():
            pass
    sigmask_calls = [c for c in calls if c[0] == "sigmask"]
    # Install swap (BLOCK/SETMASK), exit flush (UNBLOCK/SETMASK), restore
    # swap (BLOCK/SETMASK) -- three bracketed pairs, six calls total.
    assert len(sigmask_calls) == 6
    assert [how for _, how in sigmask_calls] == [
        wrapper.signal.SIG_BLOCK, wrapper.signal.SIG_SETMASK,
        wrapper.signal.SIG_UNBLOCK, wrapper.signal.SIG_SETMASK,
        wrapper.signal.SIG_BLOCK, wrapper.signal.SIG_SETMASK,
    ]
    # The three `signal.signal()` calls for the INSTALL phase all land
    # strictly between the first BLOCK and its matching SETMASK.
    block_idx = calls.index(("sigmask", wrapper.signal.SIG_BLOCK))
    setmask_idx = calls.index(("sigmask", wrapper.signal.SIG_SETMASK))
    signal_calls_between = [c for c in calls[block_idx + 1:setmask_idx] if c[0] == "signal"]
    assert len(signal_calls_between) == 3
    # The three RESTORE `signal.signal()` calls land strictly between the
    # SECOND BLOCK (the restore swap's own) and its matching SETMASK.
    restore_block_idx = len(calls) - 1 - calls[::-1].index(("sigmask", wrapper.signal.SIG_BLOCK))
    restore_setmask_idx = len(calls) - 1 - calls[::-1].index(("sigmask", wrapper.signal.SIG_SETMASK))
    restore_signal_calls = [c for c in calls[restore_block_idx + 1:restore_setmask_idx] if c[0] == "signal"]
    assert len(restore_signal_calls) == 3


def test_cleanup_signals_deferred_restores_a_pre_blocked_signal_mask() -> None:
    # The exact regression this closes: `SIG_UNBLOCK` unconditionally
    # unblocks both signals regardless of what the CALLER had set --
    # confirmed live that a caller who deliberately pre-blocked SIGTERM
    # found it silently UNBLOCKED after this context manager returned.
    # `SIG_SETMASK` restoring the captured entry mask must leave a
    # pre-blocked signal still blocked afterward.
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        with wrapper._cleanup_signals_deferred():
            pass
        current_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        assert signal.SIGTERM in current_mask
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def test_cleanup_signals_deferred_flushes_a_pending_signal_to_the_recorder(monkeypatch) -> None:
    # The exact regression this closes: with SIGTERM pre-blocked by the
    # CALLER (as in the test above), a signal sent during `yield` stays
    # PENDING for the whole context-manager lifetime -- confirmed live
    # that unblocking only AFTER the old handler was already restored
    # delivered it to THAT handler (bypassing `_record`/the replay
    # decision entirely). The flush step must deliver it to `_record`
    # instead, so it still shows up in the normal `_TerminationRequested`
    # replay path.
    old_handler_calls: list[int] = []
    monkeypatch.setattr(wrapper.signal, "SIGTERM", signal.SIGTERM)
    previous_handler = signal.signal(signal.SIGTERM, lambda signum, frame: old_handler_calls.append(signum))
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        try:
            with wrapper._cleanup_signals_deferred():
                os.kill(os.getpid(), signal.SIGTERM)
        except wrapper._TerminationRequested:
            pass
        else:
            raise AssertionError("expected the pending SIGTERM to be replayed as _TerminationRequested")
        # The OLD (pre-existing, caller-installed) handler must never
        # have fired -- the pending signal was flushed to `_record`
        # instead, which is what drove the replay above.
        assert old_handler_calls == []
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def test_cleanup_signals_deferred_replays_a_signal_received_during_cleanup() -> None:
    # The exact gap a bare `SIG_IGN` would leave open: a signal arriving
    # while NORMAL (non-exceptional) cleanup is running must not be
    # silently discarded, or a cancelled invocation could report success.
    # It must still be raised (as `_TerminationRequested`) once cleanup
    # itself has finished running to completion uninterrupted.
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        try:
            with wrapper._cleanup_signals_deferred():
                # Simulate a signal arriving mid-cleanup by invoking the
                # now-installed handler directly, exactly as the OS would.
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                # Cleanup "completes" normally despite the signal -- it
                # must not have been interrupted by it.
        except wrapper._TerminationRequested as exc:
            assert "signal" in str(exc).lower()
        else:
            raise AssertionError("expected _TerminationRequested to be replayed")
        # The previous handlers must still be restored despite the raise.
        assert signal.getsignal(signal.SIGTERM) == previous_sigterm
        assert signal.getsignal(signal.SIGINT) == previous_sigint
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def test_cleanup_signals_deferred_does_not_mask_a_primary_exception_already_in_flight(capsys) -> None:
    # Replaying a deferred signal UNCONDITIONALLY would let it REPLACE a
    # genuine primary failure already propagating when `_cleanup_signals_deferred`
    # is entered -- exactly the masking this wrapper's teardown logic
    # elsewhere exists to prevent. The signal must be reported (not
    # silently dropped), but the ORIGINAL exception must win.
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        try:
            raise ValueError("original primary failure")
        except ValueError:
            with wrapper._cleanup_signals_deferred():
                # Simulate a signal arriving mid-cleanup while an
                # exception is already being handled.
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            raise  # re-raise the original ValueError, as main() itself does
    except ValueError as exc:
        assert str(exc) == "original primary failure"
    except wrapper._TerminationRequested:
        raise AssertionError(
            "the deferred signal must not replace the original exception"
        )
    else:
        raise AssertionError("expected the original ValueError to propagate")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
    assert "signal" in capsys.readouterr().err.lower()


def test_cleanup_signals_deferred_does_not_mask_a_failure_raised_by_the_cleanup_body(capsys) -> None:
    # The sibling case: the cleanup BODY itself (e.g. a real `_tear_down`
    # failure) raises, AFTER a signal was already recorded during that
    # same cleanup call -- the cleanup failure must still win, not be
    # replaced by the replayed signal.
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        try:
            with wrapper._cleanup_signals_deferred():
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                raise RuntimeError("cleanup body failure")
        except RuntimeError as exc:
            assert str(exc) == "cleanup body failure"
        except wrapper._TerminationRequested:
            raise AssertionError(
                "the deferred signal must not replace the cleanup body's own failure"
            )
        else:
            raise AssertionError("expected the cleanup body's RuntimeError to propagate")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
    assert "signal" in capsys.readouterr().err.lower()


def test_main_rejects_allow_host_state() -> None:
    # --allow-host-state's documented contract (preserve the caller's
    # real HOME/config/credentials) cannot be honored through this
    # wrapper -- the container always gets a fresh, credential-free
    # tmpfs $HOME by design. Silently accepting the flag would let a
    # credential-dependent test proceed without the credentials it
    # asked for, invisibly.
    try:
        wrapper.main(["agent-worktrees", "--allow-host-state"])
    except SystemExit as exc:
        assert "--allow-host-state" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_main_rejects_abbreviated_allow_host_state() -> None:
    try:
        wrapper.main(["agent-worktrees", "--allow-host"])
    except SystemExit as exc:
        assert "--allow-host-state" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_main_strips_double_dash_separator_anywhere_in_passthrough(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(wrapper, "_per_instance_config",
                         lambda label: (Path("/tmp/fake-devcontainer-dir/devcontainer.json"), "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, config_path: "container-1")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper, "_run_tests",
                         lambda container_id, config_path, passthrough: calls.append(passthrough) or 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)
    monkeypatch.setattr(wrapper.shutil, "rmtree", lambda path, ignore_errors=False: None)
    # This test is about `--` stripping specifically -- base-rewriting has
    # its own dedicated tests, so keep it a no-op here.
    monkeypatch.setattr(wrapper, "_rewrite_base_to_resolved_sha", lambda passthrough: passthrough)

    rc = wrapper.main(["--", "--changed"])
    assert rc == 0
    assert calls == [["--changed"]]
    calls.clear()

    # The documented `--all -- -k some_filter` case: the separator lands in
    # the MIDDLE of the extras list, not just the front.
    rc = wrapper.main(["--all", "--", "-k", "some_filter"])
    assert rc == 0
    assert calls == [["--all", "-k", "some_filter"]]


def test_main_prepares_dependencies_and_disconnects_networks_before_running_tests(monkeypatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(wrapper, "_per_instance_config",
                         lambda label: (Path("/tmp/fake-devcontainer-dir/devcontainer.json"), "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, config_path: "container-1")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: order.append("prepare"))
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks",
                         lambda container_id: order.append("disconnect"))
    monkeypatch.setattr(wrapper, "_run_tests",
                         lambda container_id, config_path, passthrough: order.append("run") or 0)
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)
    monkeypatch.setattr(wrapper.shutil, "rmtree", lambda path, ignore_errors=False: None)

    rc = wrapper.main(["agent-worktrees"])
    assert rc == 0
    assert order == ["prepare", "disconnect", "run"]


def test_main_strips_reinstall_before_the_real_pass_after_preparing(monkeypatch) -> None:
    # The prep pass already rebuilt the venv -- the real pass must reuse
    # it, not delete and rebuild it again with no network left.
    real_pass_args: list[list[str]] = []

    monkeypatch.setattr(wrapper, "_per_instance_config",
                         lambda label: (Path("/tmp/fake-devcontainer-dir/devcontainer.json"), "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, config_path: "container-1")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(
        wrapper, "_run_tests",
        lambda container_id, config_path, passthrough: real_pass_args.append(passthrough) or 0,
    )
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)
    monkeypatch.setattr(wrapper.shutil, "rmtree", lambda path, ignore_errors=False: None)

    rc = wrapper.main(["agent-worktrees", "--reinstall"])
    assert rc == 0
    assert real_pass_args == [["agent-worktrees"]]


def test_main_skips_dependency_preparation_and_disconnect_for_list_only(monkeypatch) -> None:
    order: list[str] = []

    monkeypatch.setattr(wrapper, "_per_instance_config",
                         lambda label: (Path("/tmp/fake-devcontainer-dir/devcontainer.json"), "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, config_path: "container-1")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: order.append("prepare"))
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks",
                         lambda container_id: order.append("disconnect"))
    monkeypatch.setattr(wrapper, "_run_tests",
                         lambda container_id, config_path, passthrough: order.append("run") or 0)
    monkeypatch.setattr(wrapper, "_tear_down", lambda container_id, volume_name: None)
    monkeypatch.setattr(wrapper.shutil, "rmtree", lambda path, ignore_errors=False: None)

    rc = wrapper.main(["--list"])
    assert rc == 0
    assert order == ["run"]



def test_main_tears_down_container_and_volume_unless_keep_is_passed(monkeypatch, tmp_path: Path) -> None:
    torn_down: list[tuple[str, str]] = []

    def make_config_path() -> Path:
        d = tmp_path / f"cfg-{uuid.uuid4().hex[:8]}"
        d.mkdir()
        p = d / "devcontainer.json"
        p.write_text("{}")
        return p

    config_path = make_config_path()
    monkeypatch.setattr(wrapper, "_per_instance_config",
                         lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-2")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(
        wrapper, "_tear_down",
        lambda container_id, volume_name: torn_down.append((container_id, volume_name)),
    )

    wrapper.main(["agent-worktrees"])
    assert torn_down == [("container-2", "fake-volume")]
    assert not config_path.parent.exists()

    torn_down.clear()
    config_path = make_config_path()
    monkeypatch.setattr(wrapper, "_per_instance_config",
                         lambda label: (config_path, "fake-volume"))
    wrapper.main(["--keep", "agent-worktrees"])
    assert torn_down == []


def test_main_defers_sigterm_during_the_teardown_call(monkeypatch, tmp_path: Path) -> None:
    # A real second SIGTERM (or Ctrl-C) arriving mid-`_tear_down` must not
    # interrupt it between removing the container and removing its
    # volume -- the OS-level handler must genuinely have been swapped to
    # a recording (not immediately-raising) handler for the call's
    # duration, not merely wrapped in a try/except that happens to catch
    # a resulting exception.
    config_path = tmp_path / "cfgdir-sigterm" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")
    observed_handlers_during_teardown = []

    def fake_tear_down(container_id: str, volume_name: str) -> None:
        observed_handlers_during_teardown.append(
            (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
        )

    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-sigterm")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(wrapper, "_tear_down", fake_tear_down)

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        wrapper.main(["agent-worktrees"])
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)
    assert len(observed_handlers_during_teardown) == 1
    sigterm_handler, sigint_handler = observed_handlers_during_teardown[0]
    assert sigterm_handler not in (signal.SIG_IGN, signal.SIG_DFL, wrapper._raise_on_sigterm)
    assert sigint_handler not in (signal.SIG_IGN, signal.SIG_DFL)
    assert callable(sigterm_handler) and callable(sigint_handler)


def test_main_propagates_a_signal_received_during_successful_teardown(monkeypatch, tmp_path: Path) -> None:
    # The exact scenario the bare-`SIG_IGN` design would have gotten
    # wrong: a cancellation signal arriving during NORMAL (non-
    # exceptional) post-success teardown must not be silently discarded
    # -- `main` must propagate it rather than returning 0, or a cancelled
    # invocation would misreport success.
    config_path = tmp_path / "cfgdir-sigterm-success" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")

    def fake_tear_down(container_id: str, volume_name: str) -> None:
        # Simulate a signal arriving mid-teardown by invoking the
        # now-installed (recording) handler directly, then let teardown
        # finish normally -- it must not be interrupted by this.
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-sigterm-success")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(wrapper, "_tear_down", fake_tear_down)

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    previous_sigint = signal.getsignal(signal.SIGINT)
    try:
        try:
            wrapper.main(["agent-worktrees"])
        except wrapper._TerminationRequested:
            pass
        else:
            raise AssertionError(
                "expected main() to propagate the signal instead of returning 0"
            )
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


def test_main_raises_teardown_failure_when_primary_path_succeeded(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "cfgdir3" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")

    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-3")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace",
                         lambda container_id, passthrough, *, include_untracked: None)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)

    def failing_tear_down(container_id: str, volume_name: str) -> None:
        raise SystemExit("teardown failed: boom")

    monkeypatch.setattr(wrapper, "_tear_down", failing_tear_down)

    # The primary test path succeeded (exit 0) -- teardown's own failure
    # must surface directly (nothing to preserve over it).
    try:
        wrapper.main(["agent-worktrees"])
    except SystemExit as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected SystemExit from the failed teardown")


def test_main_preserves_primary_exception_when_teardown_also_fails(monkeypatch, tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "cfgdir4" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")

    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-4")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)

    def failing_populate(container_id: str, passthrough: list[str], *, include_untracked: bool) -> None:
        raise SystemExit("primary failure: real test problem")

    def failing_tear_down(container_id: str, volume_name: str) -> None:
        raise SystemExit("secondary teardown failure")

    monkeypatch.setattr(wrapper, "_populate_workspace", failing_populate)
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 0)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)
    monkeypatch.setattr(wrapper, "_tear_down", failing_tear_down)

    # The PRIMARY failure must win -- a `_tear_down` failure in the
    # `finally` must not silently replace it.
    try:
        wrapper.main(["agent-worktrees"])
    except SystemExit as exc:
        assert "primary failure" in str(exc)
    else:
        raise AssertionError("expected the primary SystemExit to propagate")
    # The secondary teardown failure is still reported, just not raised.
    assert "secondary teardown failure" in capsys.readouterr().err


def test_main_preserves_nonzero_test_result_when_teardown_also_fails(monkeypatch, tmp_path: Path, capsys) -> None:
    # A nonzero `_run_tests` exit code is a RETURNED value, not a raised
    # exception -- it must be treated the same as an exception for
    # teardown-masking purposes: a secondary `_tear_down` failure must not
    # replace it with a confusing, unrelated SystemExit.
    config_path = tmp_path / "cfgdir5" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")

    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-5")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace",
                         lambda container_id, passthrough, *, include_untracked: None)
    # A real test FAILURE (nonzero exit), not an exception.
    monkeypatch.setattr(wrapper, "_run_tests", lambda container_id, cfg, passthrough: 7)
    monkeypatch.setattr(wrapper._net_scope, "prepare_dependencies",
                         lambda exe, repo, container_id, config_path, passthrough, canonicalize: None)
    monkeypatch.setattr(wrapper._net_scope, "disconnect_container_networks", lambda container_id: None)

    def failing_tear_down(container_id: str, volume_name: str) -> None:
        raise SystemExit("secondary teardown failure")

    monkeypatch.setattr(wrapper, "_tear_down", failing_tear_down)

    # The nonzero test result must still be returned, not masked by the
    # teardown's own SystemExit.
    assert wrapper.main(["agent-worktrees"]) == 7
    assert "secondary teardown failure" in capsys.readouterr().err


def test_main_cleans_up_orphan_and_reraises_when_bring_up_fails(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "cfgdir" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")
    cleanup_calls: list[tuple[str, str]] = []

    def failing_bring_up(label: str, cfg: Path) -> str:
        raise SystemExit("devcontainer up failed: boom")

    monkeypatch.setattr(wrapper, "_per_instance_config",
                         lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", failing_bring_up)
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(
        wrapper, "_cleanup_orphan",
        lambda instance_label, volume_name: cleanup_calls.append((instance_label, volume_name)),
    )

    try:
        wrapper.main(["agent-worktrees"])
    except SystemExit as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected the original SystemExit to propagate")
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0][1] == "fake-volume"


def test_main_tears_down_not_orphan_cleans_up_when_bring_up_succeeded_but_something_then_fails(
    monkeypatch, tmp_path: Path,
) -> None:
    # The exact gap this closes: previously, a failure arriving in the
    # narrow window right after `_bring_up` returns (but before the
    # `_populate_workspace`/`_run_tests` try block) had NO cleanup guard
    # active at all -- neither `_cleanup_orphan` (gated on the old
    # bring-up-only except block) nor `_tear_down` (gated on a later
    # try/finally) would fire, leaking the live container and volume.
    # `container_id` being non-`None` must now route to `_tear_down`
    # (which knows the real container to remove), never `_cleanup_orphan`
    # (which only searches by label, for when no container id exists yet).
    config_path = tmp_path / "cfgdir-gap" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")
    tear_down_calls: list[tuple[str, str]] = []
    orphan_calls: list[tuple[str, str]] = []

    def failing_populate(container_id: str, passthrough: list[str], *, include_untracked: bool) -> None:
        raise RuntimeError("failure right after bring-up succeeded")

    monkeypatch.setattr(wrapper, "_per_instance_config", lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", lambda volume_name: None)
    monkeypatch.setattr(wrapper, "_bring_up", lambda label, cfg: "container-gap")
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(wrapper, "_populate_workspace", failing_populate)
    monkeypatch.setattr(
        wrapper, "_tear_down",
        lambda container_id, volume_name: tear_down_calls.append((container_id, volume_name)),
    )
    monkeypatch.setattr(
        wrapper, "_cleanup_orphan",
        lambda instance_label, volume_name: orphan_calls.append((instance_label, volume_name)),
    )

    try:
        wrapper.main(["agent-worktrees"])
    except RuntimeError as exc:
        assert "failure right after bring-up succeeded" in str(exc)
    else:
        raise AssertionError("expected the original RuntimeError to propagate")
    assert tear_down_calls == [("container-gap", "fake-volume")]
    assert orphan_calls == []
    # The per-instance config dir must still be cleaned up even on this
    # failure path.
    assert not config_path.parent.exists()


def test_main_cleans_up_orphan_when_create_bounded_volume_itself_fails(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "cfgdir2" / "devcontainer.json"
    config_path.parent.mkdir()
    config_path.write_text("{}")
    cleanup_calls: list[tuple[str, str]] = []

    def failing_create_volume(volume_name: str) -> None:
        raise SystemExit("failed to create bounded workspace volume: boom")

    monkeypatch.setattr(wrapper, "_per_instance_config",
                         lambda label: (config_path, "fake-volume"))
    monkeypatch.setattr(wrapper, "_create_bounded_volume", failing_create_volume)
    monkeypatch.setattr(wrapper._admission, "needs_admission", lambda passthrough, canonicalize: False)
    monkeypatch.setattr(
        wrapper, "_cleanup_orphan",
        lambda instance_label, volume_name: cleanup_calls.append((instance_label, volume_name)),
    )

    try:
        wrapper.main(["agent-worktrees"])
    except SystemExit:
        pass
    else:
        raise AssertionError("expected the original SystemExit to propagate")
    assert len(cleanup_calls) == 1


def _load_devcontainer_config() -> dict:
    # `.devcontainer/test-isolation/devcontainer.json` is JSONC (it carries extensive
    # `//` explanatory comments) -- strip full-line and trailing `//`
    # comments before parsing, mirroring the same crude-but-sufficient
    # approach used to hand-validate this file during development.
    text = wrapper.DEVCONTAINER_CONFIG.read_text()
    cleaned = re.sub(r"(?m)^\s*//.*$", "", text)
    cleaned = re.sub(r'(?<!:)//[^"\n]*$', "", cleaned, flags=re.MULTILINE)
    return json.loads(cleaned)


def test_devcontainer_config_never_mounts_a_docker_socket() -> None:
    # A mounted Docker socket is a full host-escape vector -- this spec's
    # entire point is a HARDENED isolation boundary, so this invariant
    # must never silently regress even though nothing here exercises
    # Docker itself.
    config = _load_devcontainer_config()
    run_args = config["runArgs"]
    assert not any("docker.sock" in str(arg) for arg in run_args)
    assert not any(str(arg).startswith("--privileged") for arg in run_args)
    assert "mounts" not in config or not any(
        "docker.sock" in str(m) for m in config["mounts"]
    )


def test_devcontainer_config_workspace_is_a_volume_not_a_host_bind() -> None:
    # The whole point of the workspace-storage-model fix (Phase 1, item 1)
    # is that the host checkout is never bind-mounted -- a regression back
    # to a host bind would silently reopen the original host-mutation gap
    # this effort exists to close.
    config = _load_devcontainer_config()
    assert "type=volume" in config["workspaceMount"]
    assert "type=bind" not in config["workspaceMount"]


def test_devcontainer_config_declares_the_runtime_hardening_invariants() -> None:
    # Fast structural coverage for the runtime-posture invariants
    # documented at length in the config's own comments: dropping these
    # flags (or the resource ceilings) would leave the suite green under
    # mocked Docker/devcontainer CI while silently reopening the exact
    # host-escape and resource-exhaustion risks Phase 1 closed.
    config = _load_devcontainer_config()
    run_args = [str(arg) for arg in config["runArgs"]]
    for required in (
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--read-only",
        "--memory=14g",
        "--memory-swap=14g",
        "--cpus=4",
        "--pids-limit=512",
    ):
        assert required in run_args, f"missing required runArg: {required!r}"
    tmpfs_mounts = [
        run_args[i + 1] for i, arg in enumerate(run_args) if arg == "--tmpfs"
    ]
    assert any(m.startswith("/home/vscode:") for m in tmpfs_mounts)
    assert any(m.startswith("/tmp:") for m in tmpfs_mounts)
    assert any(m.startswith("/run:") for m in tmpfs_mounts)


def test_devcontainer_config_bootstraps_uv_via_a_pinned_verified_download() -> None:
    # Closes the supply-chain gap a bare `curl ... | sh` pipeline would
    # reopen: the bootstrap must pin an exact `uv` version and verify the
    # downloaded archive's SHA-256 before ever executing anything from it.
    config = _load_devcontainer_config()
    on_create = config["onCreateCommand"]
    assert "UV_VERSION=" in on_create
    assert "sha256sum" in on_create
    assert "| sh" not in on_create
    assert "astral.sh/uv/install.sh" not in on_create


def test_devcontainer_config_exempts_the_workspace_from_dubious_ownership_checks() -> None:
    # The workspace volume's own top-level mountpoint is always root-owned
    # (Docker creates it that way, and `--cap-drop=ALL` means nothing can
    # ever `chown` it) even though `_populate_workspace` extracts its
    # CONTENTS as the non-root `vscode` user. Modern Git's own ownership
    # check inspects the working-tree ROOT, not just `.git`, so without
    # this exemption every git invocation inside the container -- including
    # `run-plugin-tests.py`'s own changed-file diffing -- would fail.
    config = _load_devcontainer_config()
    env = config["containerEnv"]
    assert env.get("GIT_CONFIG_KEY_0") == "safe.directory"
    assert env.get("GIT_CONFIG_VALUE_0") == wrapper.CONTAINER_WORKSPACE
    assert env.get("GIT_CONFIG_COUNT") == "1"


def test_devcontainer_config_pins_the_base_image_to_an_immutable_digest() -> None:
    # The base image IS the trust root for everything else in this spec --
    # a mutable tag could be silently retagged/replaced to something else
    # entirely, with no reviewed source change, bypassing the integrity
    # posture the pinned/verified uv install otherwise provides.
    config = _load_devcontainer_config()
    assert "@sha256:" in config["image"]
    assert config["image"].startswith("mcr.microsoft.com/devcontainers/python@sha256:")
