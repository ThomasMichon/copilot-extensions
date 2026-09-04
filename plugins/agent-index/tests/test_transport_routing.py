"""Tests for the project-aware, role-routed read transport (transport.py).

The routing decision is host vs client vs no-project, plus the SSH argv/encoded
command a client builds to run the same read subcommand on the indexer host.
"""

from __future__ import annotations

import base64
import json

import pytest

from agent_index import transport


def _patch(monkeypatch, *, root, indexer, machine, role="client"):
    monkeypatch.setattr(transport.config, "repo_root", lambda explicit=None: root)
    monkeypatch.setattr(transport.config, "read_indexer", lambda r: indexer)
    monkeypatch.setattr(transport.config, "read_indexers", lambda r: [indexer] if indexer else [])
    monkeypatch.setattr(transport.config, "machine_id", lambda: machine)
    monkeypatch.setattr(transport.config, "resolve_role", lambda: role)


def _patch_multi(monkeypatch, *, root, indexers, machine, role="client"):
    """Patch config for an ordered multi-indexer designation."""
    monkeypatch.setattr(transport.config, "repo_root", lambda explicit=None: root)
    monkeypatch.setattr(transport.config, "read_indexer", lambda r: indexers[0] if indexers else None)
    monkeypatch.setattr(transport.config, "read_indexers", lambda r: list(indexers))
    monkeypatch.setattr(transport.config, "machine_id", lambda: machine)
    monkeypatch.setattr(transport.config, "resolve_role", lambda: role)


# -- plan_route ----------------------------------------------------------------

def test_plan_route_host_when_machine_matches_designation(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "indexer-host", "ssh": "indexer-host"},
           machine="indexer-host")
    role, indexer = transport.plan_route()
    assert role == "host"
    assert indexer["ssh"] == "indexer-host"


def test_plan_route_client_when_machine_differs(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "indexer-host", "ssh": "indexer-host"},
           machine="client-host")
    role, _ = transport.plan_route()
    assert role == "client"


def test_plan_route_designation_compare_is_case_insensitive(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "indexer-host", "ssh": "x"},
           machine="indexer-host")
    role, _ = transport.plan_route()
    assert role == "host"


def test_plan_route_secondary_indexer_is_host(monkeypatch):
    _patch_multi(
        monkeypatch,
        root="/repo",
        indexers=[
            {"machine": "primary", "ssh": "primary"},
            {"machine": "secondary", "ssh": "secondary"},
        ],
        machine="secondary",
    )
    role, indexer = transport.plan_route()
    assert role == "host"
    assert indexer["machine"] == "primary"


def test_plan_route_no_project_falls_back_to_machine_role_host(monkeypatch):
    # No project/indexer (e.g. the delegated command running in the host's home
    # dir over SSH) -> machine-global role decides; host runs local => recursion
    # terminates at the host.
    _patch(monkeypatch, root=None, indexer=None, machine="indexer-host", role="host")
    role, indexer = transport.plan_route()
    assert role == "host"
    assert indexer is None


def test_plan_route_no_project_falls_back_to_machine_role_client(monkeypatch):
    _patch(monkeypatch, root=None, indexer=None, machine="client-host", role="client")
    role, indexer = transport.plan_route()
    assert role == "client"
    assert indexer is None


def test_plan_route_project_without_indexer_is_unconfigured(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer=None, machine="client-host", role="host")
    role, indexer = transport.plan_route()
    assert role == "unconfigured"
    assert indexer is None


# -- maybe_delegate ------------------------------------------------------------

def test_maybe_delegate_non_delegable_returns_none(monkeypatch):
    _patch(monkeypatch, root=None, indexer=None, machine="x", role="client")
    assert transport.maybe_delegate("index", ["index"]) is None


def test_maybe_delegate_host_runs_local(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "indexer-host", "ssh": "indexer-host"}, machine="indexer-host")
    assert transport.maybe_delegate("search", ["search", "q"]) is None


def test_maybe_delegate_client_no_project_errors(monkeypatch, capsys):
    _patch(monkeypatch, root=None, indexer=None, machine="client-host", role="client")
    rc = transport.maybe_delegate("search", ["search", "q"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "no indexer transport" in out


def test_maybe_delegate_in_project_without_indexer_is_unconfigured(monkeypatch, capsys):
    _patch(monkeypatch, root="/repo", indexer=None, machine="anybox", role="client")
    assert transport.maybe_delegate("search", ["search", "q"]) == 1
    assert "no usable indexer transport" in capsys.readouterr().out


def test_maybe_delegate_endpoint_only_client_runs_local_handler(monkeypatch):
    _patch(
        monkeypatch,
        root="/repo",
        indexer={"machine": "indexer-host", "endpoint": "http://indexer:8420"},
        machine="client-host",
    )
    assert transport.maybe_delegate("status", ["status"]) is None


def test_machine_local_endpoint_precedes_repo_ssh(monkeypatch):
    _patch(
        monkeypatch,
        root="/repo",
        indexer={
            "machine": "indexer-host",
            "ssh": "indexer-host",
            "endpoint": "http://shared-endpoint:8420",
        },
        machine="client-host",
    )
    monkeypatch.setattr(
        transport.config,
        "configured_endpoints",
        lambda: ["http://local-forward:18420"],
    )
    monkeypatch.setattr(
        transport,
        "_delegate_over_ssh",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("SSH must not run when a client-local endpoint exists")
        ),
    )

    assert transport.maybe_delegate("search", ["search", "q"]) is None


def test_other_repo_endpoint_does_not_override_ssh_only_repo(monkeypatch):
    _patch(
        monkeypatch,
        root="/repo",
        indexer={"machine": "indexer-host", "ssh": "indexer-host"},
        machine="client-host",
    )
    monkeypatch.setattr(
        transport.config,
        "configured_endpoints",
        lambda: ["http://other-repo-forward:18420"],
    )
    monkeypatch.setattr(transport, "_delegate_over_ssh", lambda *_args: 23)

    assert transport.maybe_delegate("search", ["search", "q"]) == 23


def test_machine_local_endpoint_satisfies_client_readiness(monkeypatch):
    _patch(
        monkeypatch,
        root="/repo",
        indexer={"machine": "indexer-host"},
        machine="client-host",
    )
    monkeypatch.setattr(
        transport.config,
        "configured_endpoints",
        lambda: ["http://local-forward:18420"],
    )

    assert transport.has_usable_client_transport()


def test_maybe_delegate_bare_unconfigured_errors(monkeypatch, capsys):
    _patch(monkeypatch, root=None, indexer=None, machine="anybox", role="unconfigured")
    assert transport.maybe_delegate("status", ["status"]) == 1
    assert "not configured on this machine" in capsys.readouterr().out


def test_maybe_delegate_bare_host_runs_local(monkeypatch):
    # Truly bare on a host-role box (also the delegated-over-SSH home-dir case)
    # -> run local, terminating the recursion at the host.
    _patch(monkeypatch, root=None, indexer=None, machine="indexer-host", role="host")
    assert transport.maybe_delegate("status", ["status"]) is None


def test_maybe_delegate_client_delegates_over_ssh(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "indexer-host", "ssh": "indexer-host"},
           machine="client-host")
    captured = {}

    class _Proc:
        returncode = 0

    def _run(argv, check=False):
        captured["argv"] = argv
        return _Proc()

    monkeypatch.setattr(transport.subprocess, "run", _run)
    rc = transport.maybe_delegate("search", ["search", "hello world"])
    assert rc == 0
    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert "indexer-host" in argv
    # Non-interactive, bounded-connect options are present.
    assert "BatchMode=yes" in argv
    assert any(a.startswith("ConnectTimeout=") for a in argv)
    # The remote command is the last argv element.
    cmd = argv[-1]
    assert cmd.startswith("pwsh -NoProfile -WindowStyle Hidden -EncodedCommand ")
    enc = cmd.rsplit(" ", 1)[1]
    inner = base64.b64decode(enc).decode("utf-16-le")
    assert "agent-index.ps1" in inner
    assert "'hello world'" in inner
    assert "'--json'" in inner


def test_external_effective_config_enables_ordered_ssh_routing(
    tmp_path, monkeypatch
):
    repository = tmp_path / "harness"
    repository.mkdir()
    config_path = tmp_path / "knowledge" / ".agent-index" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "indexers:\n"
        "  - machine: primary\n"
        "    ssh: primary\n"
        "  - machine: secondary\n"
        "    ssh: secondary\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_INDEX_REPO", str(repository))
    monkeypatch.setenv("AGENT_INDEX_EFFECTIVE_CONFIG", str(config_path))
    monkeypatch.setenv("AGENT_INDEX_MACHINE", "client")
    run, calls = _runner([transport.SSH_TRANSPORT_RC, 0])
    monkeypatch.setattr(transport.subprocess, "run", run)

    assert transport.maybe_delegate("status", ["status"]) == 0
    assert calls == ["primary", "secondary"]


def test_maybe_delegate_client_ssh_failure_reports_error(monkeypatch, capsys):
    _patch(monkeypatch, root="/repo", indexer={"machine": "indexer-host", "ssh": "indexer-host"}, machine="client-host")

    def _boom(argv, check=False):
        raise OSError("ssh: command not found")

    monkeypatch.setattr(transport.subprocess, "run", _boom)
    rc = transport.maybe_delegate("status", ["status"])
    assert rc == 1
    assert "SSH transport" in capsys.readouterr().out


# -- maybe_delegate: ordered SSH failover --------------------------------------

def _runner(results):
    """Build a fake ``subprocess.run`` that yields queued results per call.

    Each entry is either an int (returncode) or an ``OSError`` to raise. Records
    the ssh alias reached on each call in ``calls``."""
    calls: list[str] = []
    seq = list(results)

    def _run(argv, check=False):
        # alias is the element just before the remote command (the last arg).
        calls.append(argv[-2])
        outcome = seq.pop(0)
        if isinstance(outcome, OSError):
            raise outcome

        class _P:
            returncode = outcome

        return _P()

    return _run, calls


def test_failover_primary_down_falls_back_to_secondary(monkeypatch):
    _patch_multi(
        monkeypatch, root="/repo",
        indexers=[{"machine": "primary", "ssh": "primary"},
                  {"machine": "secondary", "ssh": "secondary"}],
        machine="client-host",
    )
    run, calls = _runner([transport.SSH_TRANSPORT_RC, 0])
    monkeypatch.setattr(transport.subprocess, "run", run)
    rc = transport.maybe_delegate("search", ["search", "q"])
    assert rc == 0
    assert calls == ["primary", "secondary"]


def test_failover_primary_oserror_falls_back(monkeypatch):
    _patch_multi(
        monkeypatch, root="/repo",
        indexers=[{"machine": "primary", "ssh": "primary"},
                  {"machine": "secondary", "ssh": "secondary"}],
        machine="client-host",
    )
    run, calls = _runner([OSError("ssh: connect"), 0])
    monkeypatch.setattr(transport.subprocess, "run", run)
    rc = transport.maybe_delegate("status", ["status"])
    assert rc == 0
    assert calls == ["primary", "secondary"]


def test_failover_all_down_reports_aggregated_error(monkeypatch, capsys):
    _patch_multi(
        monkeypatch, root="/repo",
        indexers=[{"machine": "primary", "ssh": "primary"},
                  {"machine": "secondary", "ssh": "secondary"}],
        machine="client-host",
    )
    run, calls = _runner([transport.SSH_TRANSPORT_RC, transport.SSH_TRANSPORT_RC])
    monkeypatch.setattr(transport.subprocess, "run", run)
    rc = transport.maybe_delegate("status", ["status"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "every designated indexer" in out
    assert "primary" in out and "secondary" in out
    assert calls == ["primary", "secondary"]


def test_failover_genuine_remote_error_is_authoritative(monkeypatch):
    # A non-255 remote exit is the real result: do NOT retry on a secondary.
    _patch_multi(
        monkeypatch, root="/repo",
        indexers=[{"machine": "primary", "ssh": "primary"},
                  {"machine": "secondary", "ssh": "secondary"}],
        machine="client-host",
    )
    run, calls = _runner([2, 0])
    monkeypatch.setattr(transport.subprocess, "run", run)
    rc = transport.maybe_delegate("search", ["search", "q"])
    assert rc == 2
    assert calls == ["primary"]


def test_single_indexer_255_is_returned_verbatim(monkeypatch):
    # With no fallback, a lone indexer's 255 is its authoritative result
    # (single-indexer back-compat), not an aggregated transport error.
    _patch(monkeypatch, root="/repo", indexer={"machine": "only", "ssh": "only"}, machine="client-host")
    run, calls = _runner([transport.SSH_TRANSPORT_RC])
    monkeypatch.setattr(transport.subprocess, "run", run)
    rc = transport.maybe_delegate("status", ["status"])
    assert rc == transport.SSH_TRANSPORT_RC
    assert calls == ["only"]


def test_secondary_indexer_machine_runs_local(monkeypatch):
    # This machine is the *secondary* designated indexer -> serve from its own
    # store (run local), never delegate to the primary.
    _patch_multi(
        monkeypatch, root="/repo",
        indexers=[{"machine": "primary", "ssh": "primary"},
                  {"machine": "secondary", "ssh": "secondary"}],
        machine="secondary",
    )
    called = {"n": 0}
    monkeypatch.setattr(transport.subprocess, "run",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert transport.maybe_delegate("search", ["search", "q"]) is None
    assert called["n"] == 0


def test_failover_skips_indexers_without_ssh(monkeypatch):
    # A designated indexer lacking an ssh alias is not SSH-reachable; it is
    # skipped and the reachable secondary is used.
    _patch_multi(
        monkeypatch, root="/repo",
        indexers=[{"machine": "primary"},
                  {"machine": "secondary", "ssh": "secondary"}],
        machine="client-host",
    )
    run, calls = _runner([0])
    monkeypatch.setattr(transport.subprocess, "run", run)
    rc = transport.maybe_delegate("status", ["status"])
    assert rc == 0
    assert calls == ["secondary"]


def test_failover_skips_whitespace_only_ssh_alias(monkeypatch):
    # A blank/whitespace ssh alias strips to '' in build_ssh_argv -> not
    # reachable; it must be filtered out, not treated as a candidate.
    _patch_multi(
        monkeypatch, root="/repo",
        indexers=[{"machine": "primary", "ssh": "   "},
                  {"machine": "secondary", "ssh": "secondary"}],
        machine="client-host",
    )
    run, calls = _runner([0])
    monkeypatch.setattr(transport.subprocess, "run", run)
    rc = transport.maybe_delegate("status", ["status"])
    assert rc == 0
    assert calls == ["secondary"]


# -- inner command / argv builders --------------------------------------------

def test_build_inner_pwsh_quotes_special_chars():
    inner = transport._build_inner("pwsh", ["search", "it's a test", "--limit", "5"])
    assert inner.startswith("$env:AGENT_INDEX_CONFIG_DATA_B64=")
    assert '$env:AGENT_INDEX_MACHINE=\'indexer\';' in inner
    assert '& "$env:USERPROFILE\\.local\\bin\\agent-index.ps1" ' in inner
    # single quote doubled for pwsh literal quoting
    assert "'it''s a test'" in inner
    assert "'--limit' '5'" in inner


def test_build_inner_bash_uses_home_binstub_and_shlex():
    inner = transport._build_inner("bash", ["search", "a b", "--json"])
    assert inner.startswith("AGENT_INDEX_CONFIG_DATA_B64=")
    assert "AGENT_INDEX_MACHINE=indexer" in inner
    assert '"$HOME/.local/bin/agent-index" ' in inner
    assert "'a b'" in inner


def test_build_inner_forwards_only_the_selected_indexer():
    inner = transport._build_inner(
        "bash",
        ["status"],
        {"machine": "secondary", "ssh": "secondary", "endpoint": "ignored"},
    )
    encoded = inner.split("AGENT_INDEX_CONFIG_DATA_B64=", 1)[1].split(" ", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    assert payload == {"indexers": [{"machine": "secondary"}]}


def test_build_ssh_argv_bash_wraps_in_login_shell():
    argv = transport.build_ssh_argv(
        {"machine": "linbox", "ssh": "linbox", "shell": "bash"},
        ["status"],
    )
    assert argv[0] == "ssh"
    assert "linbox" in argv
    assert argv[-1].startswith("bash -lc '")
    assert argv[-1].endswith("'")


def test_build_ssh_argv_defaults_to_pwsh():
    argv = transport.build_ssh_argv(
        {"machine": "winbox", "ssh": "winbox"}, ["status"]
    )
    assert "EncodedCommand" in argv[-1]


def test_build_ssh_argv_carries_connect_options(monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_SSH_CONNECT_TIMEOUT_S", raising=False)
    argv = transport.build_ssh_argv(
        {"machine": "winbox", "ssh": "winbox"}, ["status"]
    )
    assert "BatchMode=yes" in argv
    assert f"ConnectTimeout={transport.DEFAULT_SSH_CONNECT_TIMEOUT_S}" in argv


def test_build_ssh_argv_connect_timeout_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_SSH_CONNECT_TIMEOUT_S", "3")
    argv = transport.build_ssh_argv(
        {"machine": "winbox", "ssh": "winbox"}, ["status"]
    )
    assert "ConnectTimeout=3" in argv


@pytest.mark.parametrize("bad", ["", "0", "-5", "nope"])
def test_ssh_connect_timeout_falls_back_on_bad_value(monkeypatch, bad):
    monkeypatch.setenv("AGENT_INDEX_SSH_CONNECT_TIMEOUT_S", bad)
    assert transport._ssh_connect_timeout() == transport.DEFAULT_SSH_CONNECT_TIMEOUT_S


@pytest.mark.parametrize("sub,expect_json", [("search", True), ("status", False),
                                             ("similar", False), ("clusters", False)])
def test_forward_argv_forces_json_only_for_search(sub, expect_json):
    argv = transport._forward_argv(sub, [sub, "x"])
    assert ("--json" in argv) is expect_json


def test_forward_argv_search_no_double_json():
    argv = transport._forward_argv("search", ["search", "x", "--json"])
    assert argv.count("--json") == 1
