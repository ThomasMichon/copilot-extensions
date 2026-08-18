"""Tests for the superseded-coordinator reaper (:mod:`agent_dispatch.reap`)."""

from __future__ import annotations

from agent_dispatch.reap import (
    CoordProc,
    ReapResult,
    is_coordinator_cmdline,
    parse_ps_output,
    parse_win_output,
    reap_superseded_coordinators,
    select_superseded_pids,
)

# ---- is_coordinator_cmdline: precise coordinator matching -------------------


def test_matches_module_serve_forms():
    assert is_coordinator_cmdline("/x/.venv/bin/python -m agent_dispatch serve")
    assert is_coordinator_cmdline(
        "/x/.venv/bin/python -m agent_dispatch serve --host 127.0.0.1 "
        "--port 54975 --passive"
    )


def test_matches_binstub_serve_form():
    assert is_coordinator_cmdline("/home/u/.local/bin/agent-dispatch serve")


def test_rejects_supervisor_and_scheduler_serve():
    # 'serve' appears, but not as the subcommand right after agent_dispatch.
    assert not is_coordinator_cmdline(
        "/x/python -m agent_dispatch supervise serve --legacy-env"
    )
    assert not is_coordinator_cmdline(
        "/x/python -m agent_dispatch schedule serve /path/spec.json"
    )
    assert not is_coordinator_cmdline(
        "/x/python -m agent_dispatch supervise --all-repos --label general"
    )


def test_rejects_other_subcommands_and_noise():
    assert not is_coordinator_cmdline("/x/python -m agent_dispatch _cutover")
    assert not is_coordinator_cmdline("/x/python -m agent_dispatch mcp")
    assert not is_coordinator_cmdline("/x/python -m something_else serve")
    assert not is_coordinator_cmdline("")
    # 'agent_dispatch serve' buried inside a single quoted arg is not a match.
    assert not is_coordinator_cmdline("bash -lc 'echo agent_dispatch serve'")


def test_matcher_tolerates_unparseable_quotes():
    # A stray unbalanced quote must not raise -- falls back to whitespace split.
    assert is_coordinator_cmdline("python -m agent_dispatch serve --note it's")


# ---- output parsers ---------------------------------------------------------


def test_parse_ps_output_selects_only_coordinators():
    text = (
        "  817428 /x/versions/dev159/bin/python -m agent_dispatch serve --port 44199 --passive\n"
        " 1639638 /x/.venv/bin/python -m agent_dispatch serve\n"
        " 1219949 /x/.venv/bin/python -m agent_dispatch supervise serve --legacy-env\n"
        "     999 /usr/bin/python3 -m unrelated serve\n"
        "\n"
    )
    procs = parse_ps_output(text)
    assert {p.pid for p in procs} == {817428, 1639638}


def test_parse_win_output_tab_separated():
    text = (
        "43108\tC:\\py\\python.exe -m agent_dispatch serve --port 57461 --passive\n"
        "58612\tC:\\py\\python.exe -m agent_dispatch serve\n"
        "700\tC:\\py\\python.exe -m agent_dispatch supervise serve\n"
        "garbage line without tab\n"
    )
    procs = parse_win_output(text)
    assert {p.pid for p in procs} == {43108, 58612}


# ---- select_superseded_pids: pure filtering ---------------------------------


def test_select_excludes_keep_set():
    procs = [CoordProc(1, "a"), CoordProc(2, "b"), CoordProc(3, "c")]
    assert select_superseded_pids(procs, {2}) == [1, 3]
    assert select_superseded_pids(procs, {1, 2, 3}) == []
    assert select_superseded_pids([], {2}) == []


# ---- reap_superseded_coordinators: orchestration ----------------------------


def _fixed_list(pids):
    return lambda: [CoordProc(p, f"python -m agent_dispatch serve #{p}") for p in pids]


def test_reap_terminates_all_but_kept():
    killed: list[int] = []
    res = reap_superseded_coordinators(
        keep_pids={100, 999},  # 100 = active, 999 = self
        list_procs=_fixed_list([100, 200, 300, 999]),
        terminate=lambda pid: killed.append(pid) or True,
    )
    assert killed == [200, 300]
    assert res.reaped == [200, 300]
    assert res.ok


def test_reap_noop_when_keep_empty():
    killed: list[int] = []
    res = reap_superseded_coordinators(
        keep_pids=set(),
        list_procs=_fixed_list([1, 2, 3]),
        terminate=lambda pid: killed.append(pid) or True,
    )
    assert killed == []
    assert res.reaped == []
    assert not res.ok  # records the skip as an error, terminates nothing


def test_reap_drops_falsey_keep_but_uses_real_ones():
    killed: list[int] = []
    reap_superseded_coordinators(
        keep_pids={0, None, 100},  # 0/None are not valid pids
        list_procs=_fixed_list([100, 200]),
        terminate=lambda pid: killed.append(pid) or True,
    )
    assert killed == [200]


def test_reap_records_terminate_failures():
    res = reap_superseded_coordinators(
        keep_pids={100},
        list_procs=_fixed_list([100, 200, 300]),
        terminate=lambda pid: pid != 300,  # 300 fails
    )
    assert res.reaped == [200]
    assert not res.ok
    assert any("300" in e for e in res.errors)


def test_reap_fail_soft_on_enumeration_error():
    def _boom():
        raise RuntimeError("ps exploded")

    res = reap_superseded_coordinators(keep_pids={100}, list_procs=_boom)
    assert isinstance(res, ReapResult)
    assert res.reaped == []
    assert not res.ok
    assert any("enumeration failed" in e for e in res.errors)
