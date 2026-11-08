"""Short creation custody is distinct from seed and backend custody."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from agent_worktrees import embody_completion, mux_creation, sessions
from single_instance_lease import SingleInstance


def test_gate_rechecks_under_lease_and_releases_before_join(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    answers = iter([False, True])
    with mux_creation.session_gate("wt-a", exists=lambda: next(answers), enabled=True) as create:
        assert create is False
        path, = (tmp_path / ".psmux" / "launch-locks").iterdir()
        with SingleInstance(path.parent, lock_name=path.name):
            pass


def test_gate_releases_on_creation_failure_and_keeps_lock_inode(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(RuntimeError, match="creation failed"):
        with mux_creation.session_gate("wt-a", exists=lambda: False, enabled=True) as create:
            assert callable(create)
            raise RuntimeError("creation failed")
    path, = (tmp_path / ".psmux" / "launch-locks").iterdir()
    before = path.stat().st_ino
    with mux_creation.session_gate("wt-a", exists=lambda: False, enabled=True):
        assert path.stat().st_ino == before


@pytest.mark.parametrize("failure", [2, 17, "timeout"])
def test_discovery_failure_never_builds_or_launches_a_backend(monkeypatch, failure):
    from agent_remote_driver import mux_observation

    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr(
        sessions, "build_mux_new_session_argv",
        lambda *args, **kwargs: pytest.fail("uncertain discovery is not absence"),
    )

    def observe(*args, **kwargs):
        assert kwargs["timeout"] == 2
        return mux_observation.Observation("unknown", str(failure))

    monkeypatch.setattr(mux_observation, "observe_psmux", observe)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("passive PSMux CLI"))
    result = sessions.mux_new_session("a", "checkout", ["copilot"])
    assert result["ok"] is False
    assert result["new_pane"] is None


def test_detached_join_does_not_build_a_second_pane_or_emit_creation(monkeypatch):
    @contextmanager
    def joined(*args, **kwargs):
        yield False

    monkeypatch.setattr(mux_creation, "session_gate", joined)
    monkeypatch.setattr(
        sessions, "build_mux_new_session_argv",
        lambda *args, **kwargs: pytest.fail("joined target must not prepare a pane"),
    )
    monkeypatch.setattr(
        sessions.activity, "log_event",
        lambda *args, **kwargs: pytest.fail("joined target is not a creation"),
    )
    result = sessions.mux_new_session("a", "checkout", ["copilot"])
    assert result == {
        "ok": True, "session": "wt-a", "new_pane": None, "error": None, "created": False,
    }

@pytest.mark.parametrize("failure", [
    "query", "nonzero-before-discovery", "nonzero-after-discovery",
])
def test_post_dispatch_failure_preserves_pending_pane_arguments(monkeypatch, failure):
    released = []

    @contextmanager
    def acquired(*args, **kwargs):
        yield lambda: released.append(True)

    class Client:
        killed = False
        returncode = None
        waits = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, timeout=None):
            if self.killed:
                return 1
            self.waits += 1
            if failure == "nonzero-before-discovery" or (
                failure == "nonzero-after-discovery" and self.waits > 1
            ):
                self.returncode = 17
                return 17
            raise subprocess.TimeoutExpired(["psmux"], timeout)

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = 1

    child = Client()
    def failed_query(*args, **kwargs):
        if failure == "query":
            raise RuntimeError("query unavailable")
        return True

    monkeypatch.setattr(mux_creation, "session_gate", acquired)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(mux_creation, "has_session", failed_query)
    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(sessions, "_mux_bin", lambda mux=None: "psmux")
    monkeypatch.setattr(sessions, "build_mux_new_session_argv", lambda *a, **k: ["psmux"])
    monkeypatch.setattr(sessions, "cleanup_mux_pane_args",
                        lambda argv: pytest.fail("post-dispatch is not definite non-creation"))
    result = sessions.mux_new_session("a", "checkout", ["copilot"])
    assert result["ok"] is False
    assert "uncertain" in result["error"]
    assert ("query unavailable" if failure == "query" else "exited 17") in result["error"]
    assert child.killed is (failure == "query")
    assert bool(released) is (failure == "nonzero-after-discovery")


@pytest.mark.parametrize("acquisition", [False, True])
def test_join_completion_never_claims_or_types_a_seed(monkeypatch, acquisition):
    from agent_worktrees import agent_policy

    monkeypatch.setattr(agent_policy, "require", lambda path: None)
    replies = []
    output = SimpleNamespace(
        _json_output=replies.append,
        _json_error=lambda message, *, exit_code: replies.append(message) or exit_code,
    )
    result = embody_completion.complete_launch(
        None, wt_id="a", work_dir="checkout", record=object(), acquisition=acquisition,
        deferred={}, explicit_seed=True, seed="do not type this", prepared_context=None,
        resume_target=None, driver=None, selection=None,
        result={"created": False, "session": "wt-a"}, cfg=None, sessions=None,
        output=output, launch_seed_state=None, tracking_write=None,
        profile_assignment=None, clock=None,
    )
    assert result == (3 if acquisition else 0)
    assert len(replies) == 1
    if not acquisition:
        assert replies[0]["created"] is False and replies[0]["resumed"] is True


@pytest.mark.parametrize("observation", ["present", "absent", "unavailable", "timeout"])
def test_requested_verification_controls_success_without_replaying_native_seed(
    tmp_path, monkeypatch, observation,
):
    from agent_worktrees import agent_policy

    monkeypatch.setattr(agent_policy, "require", lambda path: None)
    replies = []
    now = [0.0]
    queries = []

    def probe(worktree_id, *, strict, timeout):
        assert worktree_id == "a" and strict
        assert 0 < timeout <= 1 - now[0]
        queries.append(now[0])
        if observation == "unavailable":
            raise RuntimeError("observation unavailable")
        if observation == "timeout":
            raise subprocess.TimeoutExpired(["mux", "has-session"], timeout)
        return observation == "present"

    def no_replay(*args, **kwargs):
        pytest.fail("Verification must not claim, restage or resend the native prompt")

    code = embody_completion.complete_launch(
        SimpleNamespace(verify_timeout=1), wt_id="a", work_dir="checkout",
        record=object(), acquisition=False, deferred={}, explicit_seed=True,
        seed="original prompt", prepared_context=None, resume_target=None, driver=None,
        selection=SimpleNamespace(assignment=None),
        result={"created": True, "session": "wt-a", "new_pane": "%1"},
        cfg=SimpleNamespace(tracking_dir=lambda: tmp_path),
        sessions=SimpleNamespace(
            has_mux_session=probe, mux_session_name=lambda wt: "wt-" + wt,
            mux_seed_pane=no_replay,
        ),
        output=SimpleNamespace(_json_output=replies.append),
        launch_seed_state=SimpleNamespace(
            settle_creation=lambda *args: {}, claim_creation=no_replay,
        ),
        tracking_write=None, profile_assignment=None,
        clock=SimpleNamespace(
            monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        ),
        native_seed_id="original-seed",
    )
    response, = replies
    assert response["created"] is True
    assert response["seed_id"] == "original-seed"
    assert response["seed_reason"] == "native-startup-pending"
    assert response["seeded"] is False and response["seed_submitted"] is False
    assert response["ok"] is (observation == "present")
    assert code == (0 if observation == "present" else 3)
    assert response["mux_verified"] is (
        True if observation == "present" else False if observation == "absent" else None
    )
    if observation != "present":
        assert response["error"] and "retry" in response["error"]
    if observation == "absent":
        assert len(queries) > 1 and now[0] == 1
    else:
        assert len(queries) == 1
