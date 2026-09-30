"""A detached session's launch flags survive a resume that omits them (``launch_memory``)."""

from __future__ import annotations

import json
import threading

from agent_codespaces import launch_memory as lm

TENANT = "cli:anchor-example-web@cs-1"


def test_a_bare_resume_gets_the_recorded_flags_and_driver_back():
    lm.remember("cs-1", TENANT, ["--no-ask-user", "--reasoning-effort=max", "--resume=old"], "orchestrator")
    args, driver, recalled = lm.apply("cs-1", TENANT, ["--resume=sid-42"], lm.DEFAULT_DRIVER)
    assert args == ["--no-ask-user", "--reasoning-effort=max", "--resume=sid-42"]
    assert driver == "orchestrator"
    assert recalled == ["copilot_args", "driver"]


def test_a_new_session_or_explicit_flags_never_inherit_the_record():
    lm.remember("cs-1", TENANT, ["--reasoning-effort=max"], "orchestrator")
    # A new session (no selector) starts from the current defaults.
    assert lm.apply("cs-1", TENANT, [], lm.DEFAULT_DRIVER) == ([], lm.DEFAULT_DRIVER, [])
    assert lm.apply("cs-1", TENANT, ["--no-ask-user"], lm.DEFAULT_DRIVER) == (["--no-ask-user"], lm.DEFAULT_DRIVER, [])
    # A resume that names its own driver keeps it, and the recorded flags don't come back.
    assert lm.apply("cs-1", TENANT, ["--resume=s"], "odsp") == (["--resume=s"], "odsp", [])
    # A resume with its own flags keeps exactly those (and its driver).
    args, driver, recalled = lm.apply("cs-1", TENANT, ["--model=m2", "--resume=s"], lm.DEFAULT_DRIVER)
    assert (args, driver, recalled) == (["--model=m2", "--resume=s"], lm.DEFAULT_DRIVER, [])
    lm.remember("cs-1", TENANT, args, "odsp")
    assert lm.apply("cs-1", TENANT, ["--resume=s"], lm.DEFAULT_DRIVER)[:2] == (["--model=m2", "--resume=s"], "odsp")


def test_records_are_per_codespace_and_tenant_and_names_are_checked():
    lm.remember("cs-1", TENANT, ["--no-ask-user"], "orchestrator")
    assert lm.apply("cs-2", TENANT, ["--resume=s"], lm.DEFAULT_DRIVER) == (["--resume=s"], lm.DEFAULT_DRIVER, [])
    assert lm.apply("cs-1", "cli:other", ["--resume=s"], lm.DEFAULT_DRIVER) == (["--resume=s"], lm.DEFAULT_DRIVER, [])
    lm.remember("../evil", TENANT, ["--x"], "d")
    assert not list(lm.LAUNCHES_DIR.parent.glob("evil*"))
    assert lm.apply("../evil", TENANT, ["--resume=s"], lm.DEFAULT_DRIVER) == (["--resume=s"], lm.DEFAULT_DRIVER, [])


def test_two_tenants_recording_at_once_keep_both_records():
    tenants = [f"cli:anchor-example-web-{i}@cs-1" for i in range(8)]
    threads = [threading.Thread(target=lm.remember, args=("cs-1", t, [f"--model=m{i}"], "o"))
               for i, t in enumerate(tenants)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    for i, t in enumerate(tenants):
        assert lm.apply("cs-1", t, ["--resume=s"], lm.DEFAULT_DRIVER)[0] == [f"--model=m{i}", "--resume=s"]


def test_a_corrupt_or_foreign_record_is_ignored():
    path = lm._path("cs-1", TENANT)
    path.parent.mkdir(parents=True)
    path.write_text("{nope", encoding="utf-8")
    assert lm.apply("cs-1", TENANT, ["--resume=s"], lm.DEFAULT_DRIVER) == (["--resume=s"], lm.DEFAULT_DRIVER, [])
    path.write_text(json.dumps({"tenant": "cli:someone-else", "copilot_args": ["--x"]}), encoding="utf-8")
    assert lm.apply("cs-1", TENANT, ["--resume=s"], lm.DEFAULT_DRIVER)[2] == []
    lm.remember("cs-1", TENANT, ["--no-ask-user"], "o")
    assert json.loads(path.read_text())["copilot_args"] == ["--no-ask-user"]
