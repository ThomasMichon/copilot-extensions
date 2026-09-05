"""Tests for the supervisor **registration** registry (registered-supervision).

Three surfaces are covered:

* the **model** -- kind/status vocabularies, eager spec validation, and the
  deterministic id derivation that makes a re-register upsert (idempotent by
  handle);
* the persisted **store** -- registrations registered / listed / filtered /
  inspected / status-set / removed as first-class rows on the coordinator's
  single-writer SQLite; and
* the **HTTP + CLI** surface -- the ``supervise register|status|list|remove``
  verbs write a registration and return its handle rather than becoming the
  foreground loop (the *supervise-registers-and-returns* behavior).
"""

from __future__ import annotations

import math
import threading

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.registrations import (
    RegistrationError,
    RegistrationKind,
    RegistrationStatus,
    derive_registration_id,
    validate_companion_config_result,
    validate_companion_health_result,
    validate_registration,
)
from tests._helpers import OTHER_REPO, TEST_REPO


def _lane(**over) -> dict:
    spec = {"repo": TEST_REPO, "max_concurrent": 1, "max_attempts": 3}
    spec.update(over)
    return spec


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


# -- model: validation + id derivation ---------------------------------------


def test_validate_accepts_each_kind():
    validate_registration(
        RegistrationKind.SUPERVISED_LANE,
        {
            "repo": TEST_REPO,
            "labels": ["review"],
            "embody_backend": "cli",
            "disposable_cli_labels": ["review"],
        },
    )
    validate_registration(RegistrationKind.SUPERVISED_LANE, {"all_repos": True})
    validate_registration(RegistrationKind.SCHEDULE, {"id": "nightly", "repo": TEST_REPO})
    validate_registration(RegistrationKind.EMITTER, {"url": "http://x"})
    validate_registration(
        RegistrationKind.EMITTER,
        {"id": "review-inbox", "command": ["review-emitter", "tick"],
         "interval_seconds": 3600},
    )
    validate_registration(
        RegistrationKind.EVALUATOR, {"evaluator_spec": {}, "all_repos": True}
    )
    validate_registration(
        RegistrationKind.EVALUATOR, {"evaluator": "eval.json", "repo": TEST_REPO}
    )
    validate_registration(
        RegistrationKind.PLUGIN_COMPANION,
        {
            "command": ["bin/serve", "--foreground"],
            "stop_command": ["bin/stop"],
            "health_probe": ["bin/health", "--json"],
            "config_provider": ["bin/config"],
            "health_timeout_seconds": 5,
            "managed_runtime": {
                "schema_version": 1,
                "runtimes": [
                    {
                        "name": "service",
                        "version": "1.2.3",
                        "profile": "host",
                        "python_env": "EXAMPLE_MANAGED_PYTHON",
                        "projects": [
                            {"path": "libs/helper"},
                            {"path": ".", "extras": ["service"]},
                        ],
                        "imports": ["example_service", "example_service.api"],
                    }
                ],
            },
        },
    )


@pytest.mark.parametrize(
    "kind, spec, needle",
    [
        ("bogus", {"repo": TEST_REPO}, "unknown registration kind"),
        (RegistrationKind.SUPERVISED_LANE, [], "JSON object"),
        (RegistrationKind.SUPERVISED_LANE, {}, "needs a 'repo'"),
        (RegistrationKind.SCHEDULE, {}, "non-empty"),
        (RegistrationKind.SCHEDULE, {"repo": TEST_REPO}, "needs an 'id'"),
        (RegistrationKind.SCHEDULE, {"id": "n"}, "needs a 'repo'"),
        (RegistrationKind.EMITTER, {}, "non-empty"),
        (RegistrationKind.EMITTER, {"command": ["tick"], "interval_seconds": 1}, "id"),
        (RegistrationKind.EMITTER,
         {"id": "x", "command": "tick", "interval_seconds": 1}, "list"),
        (RegistrationKind.EMITTER,
         {"id": "x", "command": ["tick"], "interval_seconds": 0}, "> 0"),
        (RegistrationKind.EVALUATOR, {}, "non-empty"),
        (RegistrationKind.EVALUATOR, {"all_repos": True}, "evaluator_spec"),
        (RegistrationKind.EVALUATOR, {"evaluator": "e.json"}, "needs a 'repo'"),
        (RegistrationKind.EVALUATOR, {"evaluator_spec": "not-a-dict", "all_repos": True},
         "must be a JSON object"),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {"stop_command": ["bin/stop"], "health_probe": ["bin/health"]},
            "command",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["/bin/serve"],
                "stop_command": ["bin/stop"],
                "health_probe": ["bin/health"],
            },
            "plugin-relative",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["../serve"],
                "stop_command": ["bin/stop"],
                "health_probe": ["bin/health"],
            },
            "contained relative",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["C:../serve.exe"],
                "stop_command": ["bin/stop"],
                "health_probe": ["bin/health"],
            },
            "plugin-relative",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "health_timeout_seconds": math.nan,
            },
            "> 0 and <= 3600",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "stop_command": ["bin/stop"],
                "health_probe": ["bin/health"],
                "surprise": True,
            },
            "unknown fields",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {"command": ["bin/serve"], "managed_runtime": []},
            "JSON object",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {"schema_version": 2, "runtimes": []},
            },
            "schema_version 1",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "../service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "."}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "portable filesystem component",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "CON.txt",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "."}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "portable filesystem component",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service.",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "."}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "portable filesystem component",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "../outside"}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "contained plugin-relative path",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "COM¹"}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "contained plugin-relative path",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "LPT².txt"}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "contained plugin-relative path",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "libs\\helper"}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "plugin-relative path",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "bad:stream"}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "contained plugin-relative path",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "project "}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "contained plugin-relative path",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "not-an-env",
                            "projects": [{"path": "."}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "environment variable name",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": ".", "extras": ["bad extra"]}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "unique portable names",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [
                                {"path": ".", "extras": ["service", "SERVICE"]}
                            ],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "unique portable names",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "."}],
                            "imports": ["not-valid!"],
                        }
                    ],
                },
            },
            "Python import names",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "."}],
                            "imports": ["example.api", "EXAMPLE.API"],
                        }
                    ],
                },
            },
            "Python import names",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": ".", "installer": "pip"}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "unknown fields",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "C:\\outside"}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "plugin-relative path",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "."}],
                            "imports": ["example"],
                        },
                        {
                            "name": "service",
                            "version": "2",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON_2",
                            "projects": [{"path": "."}],
                            "imports": ["example"],
                        },
                    ],
                },
            },
            "duplicate runtime name",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service-a",
                            "version": "1",
                            "profile": "host",
                            "python_env": "RUNTIME_PYTHON",
                            "projects": [{"path": "."}],
                            "imports": ["example"],
                        },
                        {
                            "name": "service-b",
                            "version": "1",
                            "profile": "host",
                            "python_env": "runtime_python",
                            "projects": [{"path": "."}],
                            "imports": ["example"],
                        },
                    ],
                },
            },
            "duplicate python environment",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "a" * 1025}],
                            "imports": ["example"],
                        }
                    ],
                },
            },
            "contained plugin-relative path",
        ),
        (
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "1",
                            "profile": "host",
                            "python_env": "EXAMPLE_PYTHON",
                            "projects": [{"path": "."}],
                            "imports": ["a" * 513],
                        }
                    ],
                },
            },
            "Python import names",
        ),
        (RegistrationKind.SCHEDULE, {"schedules": "nope", "id": "x", "repo": "r"},
         "non-empty list of objects"),
        (RegistrationKind.SCHEDULE, {"schedules": [], "id": "x", "repo": "r"},
         "non-empty list of objects"),
        (
            RegistrationKind.SUPERVISED_LANE,
            {
                "repo": TEST_REPO,
                "labels": ["review"],
                "embody_backend": "cli",
                "disposable_cli_labels": ["other"],
            },
            "not watched",
        ),
        (
            RegistrationKind.SUPERVISED_LANE,
            {
                "repo": TEST_REPO,
                "labels": ["review"],
                "embody_backend": "cli",
                "fleet": {"pool": ["host-a"]},
                "disposable_cli_labels": ["review"],
            },
            "only for local bodies",
        ),
    ],
)
def test_validate_rejects_malformed(kind, spec, needle):
    with pytest.raises(RegistrationError) as exc:
        validate_registration(kind, spec)
    assert needle in str(exc.value)


def test_derive_id_is_deterministic_and_scope_sensitive():
    a = derive_registration_id("supervised-lane", _lane(), "anomalous-potato", "default")
    b = derive_registration_id("supervised-lane", _lane(), "anomalous-potato", "default")
    assert a == b  # deterministic -> re-register upserts
    assert a.startswith("supervised-lane-")
    # different scope (env / machine / spec) -> different id
    assert a != derive_registration_id("supervised-lane", _lane(), "anomalous-potato", "prod")
    assert a != derive_registration_id("supervised-lane", _lane(), "mantis-counter", "default")
    assert a != derive_registration_id(
        "supervised-lane", _lane(labels=["x"]), "anomalous-potato", "default"
    )


def test_companion_provider_result_contracts_are_versioned_and_strict():
    validate_companion_config_result(
        {
            "schema_version": 1,
            "active": True,
            "arguments": ["--config", "config.json"],
            "environment": {"MODE": "service"},
        }
    )
    validate_companion_health_result(
        {"schema_version": 1, "healthy": True, "detail": "ready"}
    )
    with pytest.raises(RegistrationError, match="schema_version 1"):
        validate_companion_config_result({"schema_version": 2, "active": True})
    with pytest.raises(RegistrationError, match="schema_version 1"):
        validate_companion_config_result({"schema_version": True, "active": True})
    with pytest.raises(RegistrationError, match="schema_version 1"):
        validate_companion_health_result({"schema_version": 1.0, "healthy": True})
    with pytest.raises(RegistrationError, match="unknown fields"):
        validate_companion_health_result(
            {"schema_version": 1, "healthy": True, "extra": "nope"}
        )


# -- store: queue-level semantics --------------------------------------------


def test_register_and_get_roundtrips(q):
    rec = q.register_registration("supervised-lane", _lane(), machine="anomalous-potato")
    assert rec.kind == "supervised-lane"
    assert rec.machine == "anomalous-potato"
    assert rec.env == "default"
    assert rec.status == RegistrationStatus.ACTIVE
    assert rec.spec["repo"] == TEST_REPO
    assert q.get_registration(rec.id).spec["max_attempts"] == 3
    assert q.get_registration("absent") is None


def test_explicit_id_is_honored(q):
    rec = q.register_registration("supervised-lane", _lane(), reg_id="my-lane")
    assert rec.id == "my-lane"


def test_register_upserts_and_preserves_created_at_and_status(q):
    first = q.register_registration(
        "supervised-lane", _lane(), reg_id="lane", now=100.0
    )
    q.set_registration_status("lane", RegistrationStatus.PAUSED)
    second = q.register_registration(
        "supervised-lane", _lane(max_concurrent=4), reg_id="lane", now=200.0
    )
    assert second.spec["max_concurrent"] == 4
    assert second.created_at == 100.0  # preserved across upsert
    assert second.updated_at == 200.0
    assert q.get_registration("lane").status == RegistrationStatus.PAUSED  # survives
    assert first.id == second.id


def test_register_validates_eagerly(q):
    with pytest.raises(TaskError) as exc:
        q.register_registration("supervised-lane", {}, reg_id="x")
    assert "repo" in str(exc.value)


def test_direct_registration_rejects_plugin_companion(q):
    with pytest.raises(TaskError, match="not available through direct registration"):
        q.register_registration(
            RegistrationKind.PLUGIN_COMPANION,
            {
                "command": ["bin/serve"],
                "stop_command": ["bin/stop"],
                "health_probe": ["bin/health"],
            },
        )


def test_register_rejects_non_serializable_spec(q):
    with pytest.raises(TaskError) as exc:
        q.register_registration(
            "supervised-lane", {"repo": TEST_REPO, "bad": {1, 2, 3}}, reg_id="x"
        )
    assert "not JSON-serializable" in str(exc.value)


def test_list_filters_by_kind_machine_env_and_status(q):
    q.register_registration("supervised-lane", _lane(), reg_id="a", machine="m1")
    q.register_registration(
        "supervised-lane", _lane(repo=OTHER_REPO), reg_id="b", machine="m2"
    )
    q.register_registration(
        "schedule", {"id": "nightly", "repo": TEST_REPO}, reg_id="c",
        machine="m1", env="prod",
    )
    assert [r.id for r in q.list_registrations()] == ["a", "b", "c"]
    assert [r.id for r in q.list_registrations(kind="schedule")] == ["c"]
    assert [r.id for r in q.list_registrations(machine="m1")] == ["a", "c"]
    assert [r.id for r in q.list_registrations(env="prod")] == ["c"]

    q.set_registration_status("b", RegistrationStatus.PAUSED)
    assert [r.id for r in q.list_registrations(include_paused=False)] == ["a", "c"]


def test_set_status_validates_and_requires_existing(q):
    q.register_registration("supervised-lane", _lane(), reg_id="lane")
    with pytest.raises(TaskError):
        q.set_registration_status("lane", "bogus")
    with pytest.raises(TaskError):
        q.set_registration_status("nope", RegistrationStatus.PAUSED)


def test_remove(q):
    q.register_registration("supervised-lane", _lane(), reg_id="lane")
    assert q.remove_registration("lane") is True
    assert q.remove_registration("lane") is False
    assert q.list_registrations() == []


# -- HTTP surface ------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    import socket
    import time

    import uvicorn

    app = create_app(TaskQueue(tmp_path / "tasks.db"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    c = DispatchClient(f"http://127.0.0.1:{port}")
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            c.health()
            break
        except Exception:
            time.sleep(0.05)
    else:
        c.close()
        server.should_exit = True
        raise RuntimeError("coordinator did not start")

    yield c

    c.close()
    server.should_exit = True
    thread.join(timeout=5)


def test_http_register_list_status_remove(client):
    rec = client.register_registration(
        "supervised-lane", {"repo": TEST_REPO}, reg_id="lane", machine="anomalous-potato"
    )
    assert rec["id"] == "lane"
    assert rec["status"] == "active"
    assert [r["id"] for r in client.list_registrations()] == ["lane"]
    assert client.get_registration("lane")["spec"]["repo"] == TEST_REPO

    client.set_registration_status("lane", "paused")
    assert client.list_registrations(include_paused=False) == []

    removed = client.remove_registration("lane")
    assert removed["removed"] is True


def test_http_register_malformed_400(client):
    with pytest.raises(Exception) as exc:
        client.register_registration("supervised-lane", {})  # no repo/all_repos
    assert getattr(exc.value, "status_code", None) == 400


def test_http_get_missing_404(client):
    with pytest.raises(Exception) as exc:
        client.get_registration("absent")
    assert getattr(exc.value, "status_code", None) == 404


# -- CLI parser + spec building ----------------------------------------------


def _parse(argv):
    from agent_dispatch.__main__ import build_parser

    return build_parser().parse_args(argv)


def test_cli_register_parses_lane_flags():
    args = _parse(
        ["supervise", "register", "--repo", TEST_REPO, "--label", "x",
         "--embody-backend", "cli", "--disposable-cli-label", "x",
         "--max-concurrent", "2"]
    )
    assert args.supervise_command == "register"
    assert args.repo == TEST_REPO
    assert args.label == ["x"]
    assert args.disposable_cli_label == ["x"]
    assert args.max_concurrent == 2


def test_cli_bare_supervise_has_no_subcommand():
    args = _parse(["supervise", "--repo", TEST_REPO, "--once"])
    assert getattr(args, "supervise_command", None) is None


def test_cli_build_spec_from_lane_flags():
    from agent_dispatch.__main__ import _build_registration_spec

    args = _parse(
        ["supervise", "register", "--all-repos", "--label", "code-review",
         "--embody-backend", "cli",
         "--disposable-cli-label", "code-review",
         "--max-attempts", "5"]
    )
    spec = _build_registration_spec(args)
    assert spec["all_repos"] is True
    assert spec["labels"] == ["code-review"]
    assert spec["disposable_cli_labels"] == ["code-review"]
    assert spec["max_attempts"] == 5


def test_cli_build_spec_inline_json():
    from agent_dispatch.__main__ import _build_registration_spec

    args = _parse(
        ["supervise", "register", "--kind", "schedule", "--spec",
         '{"id": "nightly", "interval_seconds": 3600}']
    )
    spec = _build_registration_spec(args)
    assert spec == {"id": "nightly", "interval_seconds": 3600}


def test_cli_build_spec_missing_file_errors():
    from agent_dispatch.__main__ import _build_registration_spec

    args = _parse(
        ["supervise", "register", "--kind", "schedule", "--spec",
         "@/no/such/spec/file.json"]
    )
    with pytest.raises(SystemExit) as exc:
        _build_registration_spec(args)
    assert "could not read --spec file" in str(exc.value)


def test_cli_status_and_remove_take_id():
    assert _parse(["supervise", "status", "lane-1"]).id == "lane-1"
    assert _parse(["supervise", "remove", "lane-1"]).id == "lane-1"
    assert _parse(["supervise", "list", "--kind", "schedule"]).kind == "schedule"


def test_cli_serve_and_daemon_status_parse():
    a = _parse(["supervise", "serve", "--machine", "m1", "--env", "prod",
                "--interval", "3", "--once"])
    assert a.supervise_command == "serve"
    assert a.machine == "m1" and a.env == "prod" and a.once is True
    b = _parse(["supervise", "daemon-status", "--machine", "m1"])
    assert b.supervise_command == "daemon-status" and b.machine == "m1"


def test_cli_periodic_emitter_tick_and_serve_parse():
    tick = _parse(["emitter", "tick", "emitter.json", "--holder", "host-a"])
    assert tick.emitter_command == "tick"
    assert tick.spec == "emitter.json"
    assert tick.holder == "host-a"
    serve = _parse(["emitter", "serve", "emitter.json", "--holder", "host-a"])
    assert serve.emitter_command == "serve"


def test_cli_register_ensure_flag():
    a = _parse(["supervise", "register", "--repo", TEST_REPO, "--ensure"])
    assert a.ensure is True
    b = _parse(["supervise", "register", "--repo", TEST_REPO])
    assert b.ensure is False


def test_supervisor_daemon_root_hosts_recurring_children_windowlessly(
    monkeypatch,
):
    from agent_dispatch import __main__ as cli
    from agent_dispatch import procutil

    observed = {}
    monkeypatch.setattr(cli.sys, "executable", "python.exe")
    monkeypatch.setattr(
        procutil,
        "windowless_daemon_kwargs",
        lambda: {"creationflags": 0x08000000},
    )
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda argv, **kwargs: observed.update(argv=argv, kwargs=kwargs),
    )

    assert cli._spawn_supervisor_daemon_detached("host-a", "default")
    assert observed["argv"][0] == "python.exe"
    assert observed["kwargs"]["creationflags"] == 0x08000000


def test_cli_build_spec_evaluator_convenience():
    from agent_dispatch.__main__ import _build_registration_spec

    args = _parse(
        ["supervise", "register", "--kind", "evaluator", "--all-repos",
         "--evaluator", "eval.json", "--label", "code-review"]
    )
    spec = _build_registration_spec(args)
    assert spec["evaluator"] == "eval.json"
    assert spec["all_repos"] is True
    assert spec["labels"] == ["code-review"]


def test_cli_build_spec_evaluator_requires_evaluator():
    from agent_dispatch.__main__ import _build_registration_spec

    args = _parse(["supervise", "register", "--kind", "evaluator", "--all-repos"])
    with pytest.raises(SystemExit):
        _build_registration_spec(args)  # no --evaluator and no --spec


def test_cli_build_spec_rejects_evaluator_on_lane():
    from agent_dispatch.__main__ import _build_registration_spec

    args = _parse(
        ["supervise", "register", "--repo", TEST_REPO, "--evaluator", "e.json"]
    )
    with pytest.raises(SystemExit) as exc:
        _build_registration_spec(args)  # --evaluator on a supervised-lane is rejected
    assert "only valid with" in str(exc.value)
