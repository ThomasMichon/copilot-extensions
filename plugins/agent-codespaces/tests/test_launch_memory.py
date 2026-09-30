"""A detached session's launch flags survive a resume that omits them (``launch_memory``)."""

from __future__ import annotations

import json
import threading

from agent_codespaces import launch_memory as lm

TENANT = "cli:anchor-example-web@cs-1"
D = lm.DEFAULT_DRIVER


def test_a_bare_resume_of_the_recorded_session_gets_its_flags_and_driver_back():
    lm.remember("cs-1", TENANT, ["--no-ask-user", "--reasoning-effort=max", "--session-id=s1"], "orchestrator", "s1")
    for sel in (["--resume=s1"], ["--resume", "s1"], ["-r", "s1"], ["--session-id=s1"]):
        args, driver, recalled = lm.apply("cs-1", TENANT, sel, D)
        assert args == ["--no-ask-user", "--reasoning-effort=max", *sel], sel
        assert (driver, recalled) == ("orchestrator", ["copilot_args", "driver"])


def test_another_session_or_continue_never_borrows_the_record():
    lm.remember("cs-1", TENANT, ["--allow-all-tools"], "orchestrator", "s-b")
    # Resuming a different (earlier) session: it must not gain session B's permissions.
    assert lm.apply("cs-1", TENANT, ["--resume=s-a"], D) == (["--resume=s-a"], D, [])
    # --continue names no session: nothing to match, nothing recalled.
    assert lm.apply("cs-1", TENANT, ["--continue"], D) == (["--continue"], D, [])


def test_a_new_session_or_explicit_settings_never_inherit_the_record():
    lm.remember("cs-1", TENANT, ["--reasoning-effort=max"], "orchestrator", "s1")
    assert lm.apply("cs-1", TENANT, [], D) == ([], D, [])
    assert lm.apply("cs-1", TENANT, ["--no-ask-user"], D) == (["--no-ask-user"], D, [])
    assert lm.apply("cs-1", TENANT, ["--model=m2", "--resume=s1"], D) == (["--model=m2", "--resume=s1"], D, [])
    assert lm.apply("cs-1", TENANT, ["--resume=s1"], "odsp") == (["--resume=s1"], "odsp", [])


def test_a_split_selector_is_one_selector_not_a_flag():
    assert lm.split_selectors(["-r", "s1", "--no-ask-user"]) == (["--no-ask-user"], ["-r", "s1"], "s1")
    assert lm.split_selectors(["--continue", "--x"]) == (["--x"], ["--continue"], None)
    lm.remember("cs-1", TENANT, ["-r", "old-id", "--no-ask-user"], D, "s1")
    assert json.loads(lm._path("cs-1", TENANT).read_text())["copilot_args"] == ["--no-ask-user"]


def test_records_are_per_codespace_and_tenant_and_names_are_checked():
    lm.remember("cs-1", TENANT, ["--no-ask-user"], "orchestrator", "s1")
    assert lm.apply("cs-2", TENANT, ["--resume=s1"], D) == (["--resume=s1"], D, [])
    assert lm.apply("cs-1", "cli:other", ["--resume=s1"], D) == (["--resume=s1"], D, [])
    lm.remember("../evil", TENANT, ["--x"], "d", "s1")
    assert not list(lm.LAUNCHES_DIR.parent.glob("evil*"))
    lm.remember("cs-1", TENANT, ["--x"], "d", "")  # no session id: nothing to bind it to
    assert lm.apply("cs-1", TENANT, ["--resume=s1"], D)[0] == ["--no-ask-user", "--resume=s1"]


def test_two_tenants_recording_at_once_keep_both_records():
    tenants = [f"cli:anchor-example-web-{i}@cs-1" for i in range(8)]
    threads = [threading.Thread(target=lm.remember, args=("cs-1", t, [f"--model=m{i}"], "o", f"s{i}"))
               for i, t in enumerate(tenants)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    for i, t in enumerate(tenants):
        assert lm.apply("cs-1", t, [f"--resume=s{i}"], D)[0] == [f"--model=m{i}", f"--resume=s{i}"]


def test_a_corrupt_or_foreign_record_is_ignored():
    path = lm._path("cs-1", TENANT)
    path.parent.mkdir(parents=True)
    path.write_text("{nope", encoding="utf-8")
    assert lm.apply("cs-1", TENANT, ["--resume=s1"], D) == (["--resume=s1"], D, [])
    path.write_text(json.dumps({"tenant": "cli:else", "session_id": "s1", "copilot_args": ["--x"]}), encoding="utf-8")
    assert lm.apply("cs-1", TENANT, ["--resume=s1"], D)[2] == []


def test_more_than_one_selector_is_ambiguous_and_recalls_nothing():
    lm.remember("cs-1", TENANT, ["--allow-all-tools"], "orchestrator", "s1")
    for sel in (["--continue", "--resume=s1"], ["--resume=s0", "--resume=s1"], ["-r", "s1", "--session-id=s1"]):
        assert lm.apply("cs-1", TENANT, sel, D) == (sel, D, []), sel


def test_a_schema_corrupt_record_is_ignored_whole():
    path = lm._path("cs-1", TENANT)
    base = {"tenant": TENANT, "session_id": "s1", "copilot_args": ["--no-ask-user"], "driver": "o"}
    for bad in ({**base, "copilot_args": ["--x", 7]}, {**base, "copilot_args": "--x"},
                {**base, "driver": None}, {**base, "session_id": 1}):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bad), encoding="utf-8")
        assert lm.apply("cs-1", TENANT, ["--resume=s1"], D) == (["--resume=s1"], D, []), bad


def test_the_record_is_owner_only_and_leaves_no_temp_file():
    import os
    import stat

    lm.remember("cs-1", TENANT, ["--no-ask-user"], "o", "s1")
    path = lm._path("cs-1", TENANT)
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0


def test_records_under_an_unsafe_directory_are_never_trusted(tmp_path):
    import os

    import pytest

    lm.remember("cs-1", TENANT, ["--no-ask-user"], "o", "s1")
    assert lm.apply("cs-1", TENANT, ["--resume=s1"], D)[2] == ["copilot_args", "driver"]
    if os.name == "nt":
        pytest.skip("POSIX permission bits and symlinks")
    runtime = lm.LAUNCHES_DIR.parent
    runtime.chmod(0o777)  # a runtime dir others could write: its write bits are dropped
    assert lm.apply("cs-1", TENANT, ["--resume=s1"], D)[2] == ["copilot_args", "driver"]
    assert (runtime.stat().st_mode & 0o022) == 0
    lm.LAUNCHES_DIR.chmod(0o777)  # made group/world-writable after the fact
    assert lm.apply("cs-1", TENANT, ["--resume=s1"], D)[2] == ["copilot_args", "driver"]
    assert (lm.LAUNCHES_DIR.stat().st_mode & 0o077) == 0  # tightened again, not trusted as is
    rec = lm._path("cs-1", TENANT)
    rec.chmod(0o666)  # a record others could have written is ignored
    assert lm.apply("cs-1", TENANT, ["--resume=s1"], D) == (["--resume=s1"], D, [])
    rec.chmod(0o600)
    # A codespace directory that's a symlink to somewhere else is refused.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (elsewhere / lm._path("cs-2", TENANT).name).write_text(json.dumps(
        {"tenant": TENANT, "session_id": "s1", "copilot_args": ["--allow-all-tools"], "driver": "o"}))
    (lm.LAUNCHES_DIR / "cs-2").symlink_to(elsewhere, target_is_directory=True)
    assert lm.apply("cs-2", TENANT, ["--resume=s1"], D) == (["--resume=s1"], D, [])
    lm.remember("cs-2", TENANT, ["--x"], "o", "s1")
    assert json.loads((elsewhere / lm._path("cs-2", TENANT).name).read_text())["copilot_args"] == ["--allow-all-tools"]

