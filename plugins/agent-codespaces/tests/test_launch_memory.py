"""A detached session's launch flags survive a resume that omits them (``launch_memory``)."""

from __future__ import annotations

import json

from agent_codespaces import launch_memory as lm

TENANT = "cli:anchor-example-web@cs-1"


def test_a_bare_resume_gets_the_recorded_flags_and_driver_back():
    lm.remember("cs-1", TENANT, ["--no-ask-user", "--reasoning-effort=max", "--resume=old"], "orchestrator")
    args, driver, recalled = lm.apply("cs-1", TENANT, ["--resume=sid-42"], lm.DEFAULT_DRIVER)
    assert args == ["--no-ask-user", "--reasoning-effort=max", "--resume=sid-42"]
    assert driver == "orchestrator"
    assert recalled == ["copilot_args", "driver"]


def test_explicit_flags_win_and_replace_the_record():
    lm.remember("cs-1", TENANT, ["--reasoning-effort=max"], "orchestrator")
    args, driver, recalled = lm.apply("cs-1", TENANT, ["--model=m2", "--resume=s"], "odsp-web-harness")
    assert (args, driver, recalled) == (["--model=m2", "--resume=s"], "odsp-web-harness", [])
    lm.remember("cs-1", TENANT, args, driver)
    assert lm.apply("cs-1", TENANT, [], lm.DEFAULT_DRIVER)[:2] == (["--model=m2"], "odsp-web-harness")


def test_records_are_per_codespace_and_tenant_and_names_are_checked():
    lm.remember("cs-1", TENANT, ["--no-ask-user"], "orchestrator")
    assert lm.apply("cs-2", TENANT, [], lm.DEFAULT_DRIVER) == ([], lm.DEFAULT_DRIVER, [])
    assert lm.apply("cs-1", "cli:other", [], lm.DEFAULT_DRIVER) == ([], lm.DEFAULT_DRIVER, [])
    lm.remember("../evil", TENANT, ["--x"], "d")
    assert not list(lm.LAUNCHES_DIR.parent.glob("evil*"))
    assert lm.apply("../evil", TENANT, [], lm.DEFAULT_DRIVER) == ([], lm.DEFAULT_DRIVER, [])


def test_a_corrupt_record_is_ignored():
    lm.LAUNCHES_DIR.mkdir(parents=True)
    (lm.LAUNCHES_DIR / "cs-1.json").write_text("{nope", encoding="utf-8")
    assert lm.apply("cs-1", TENANT, ["--resume=s"], lm.DEFAULT_DRIVER) == (["--resume=s"], lm.DEFAULT_DRIVER, [])
    lm.remember("cs-1", TENANT, ["--no-ask-user"], "o")
    assert json.loads((lm.LAUNCHES_DIR / "cs-1.json").read_text())[TENANT]["copilot_args"] == ["--no-ask-user"]
