"""Integration tests for the Phase 2 CLI codename touch points (effort:
``pr-attribution-codenames``, issue #2838): ``create`` assigns and persists a
codename, ``list``/``resolve`` accept ``--codename`` as an alternate selector.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import tracking
from agent_worktrees.worktree_identity import resolve_worktree_id_by_codename


def _create_config(tmp_path: Path) -> cfg.Config:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    return cfg.Config(
        srcroot=str(tmp_path),
        machine="test",
        platform="windows",
        repo_name="demo-repo",
        repos={
            "demo-repo": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
    )


def _stub_create_worktree_core_internals(
    monkeypatch, tmp_path: Path, config: cfg.Config | None = None,
) -> None:
    """Neutralize every side-effecting internal except the tracking-store
    write path, so the real ``create_new_record``/codename assignment runs.
    """
    monkeypatch.setattr(m.git_ops, "resolve_start_point", lambda *_a, **_k: "HEAD")
    monkeypatch.setattr(
        m, "_prepare_worktree_source", lambda *_a, **_k: SimpleNamespace(start_point="HEAD"),
    )
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a, **_k: None)
    monkeypatch.setattr(m.permissions, "clone_permissions", lambda *_a: False)
    monkeypatch.setattr(m.permissions, "add_trusted_folder", lambda *_a: False)
    monkeypatch.setattr(m.activity, "log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        m.state_root_mod,
        "resolve_state_root",
        lambda config, cwd=None: m.state_root.StateRoot(
            path=None, source="knowledge_repo", repo="", stateless=False,
            requires_external=False, bound=False, error=None,
        ),
    )
    monkeypatch.setattr(
        m, "_launch_profile_selection",
        lambda *_a, **_k: SimpleNamespace(profile=None, assignment=None),
    )
    monkeypatch.setattr(m, "_reflect_assignment", lambda *_a, **_k: None)
    monkeypatch.setattr(m, "_build_launch_cmd", lambda *_a, **_k: ["copilot"])
    monkeypatch.setattr(m, "_repo_session_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_build_env", lambda *_a, **_k: {})
    monkeypatch.setattr(m, "_apply_assignment_env", lambda env, _selection: env)
    # codename-attribution-by-default (PR #3037 review finding): the
    # allocation-policy second revalidation reloads config fresh (never
    # reusing the pre-lock snapshot) -- this test's config is a bare,
    # in-memory `cfg.Config`, never registered as a real project on disk,
    # so `cfg.load_config(project=...)` would otherwise fail here (a
    # legitimate difference from real usage, not the race the fresh-reload
    # fix targets) and trip the new fail-closed fallback. Resolve it back
    # to the SAME config object the caller already constructed (never
    # reconstruct one -- `_create_config` creates the anchor directory,
    # which would raise on a second call).
    if config is not None:
        monkeypatch.setattr(m.cfg, "load_config", lambda *a, **k: config)


class TestCreateAssignsCodename:
    def test_create_worktree_core_assigns_and_persists_codename(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        config = _create_config(tmp_path)
        tracking_path = tmp_path / "tracking"
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
        _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)

        result = m._create_worktree_core(config)

        worktree_id = result["worktree"]["id"]
        rec = tracking.load_record_by_id(worktree_id, tracking_path=tracking_path)
        assert rec is not None
        assert rec.codename
        # Also surfaced through the JSON envelope `create --json` emits.
        assert result["worktree"]["codename"] == rec.codename

    def test_two_creates_get_distinct_codenames(self, tmp_path: Path, monkeypatch) -> None:
        config = _create_config(tmp_path)
        tracking_path = tmp_path / "tracking"
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
        _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)

        first = m._create_worktree_core(config)
        second = m._create_worktree_core(config)

        assert first["worktree"]["codename"] != second["worktree"]["codename"]


class TestCreatePolicyPreflightIntegration:
    """PR #3037 review finding: dedicated integration coverage for the
    normal `create` path's allocation-time policy preflight -- a PR-active
    repo with a custom wordlist configured but `pr.source_attribution`
    omitted must refuse to allocate a codename, and must do so before ANY
    `create` side effect (no git worktree/branch, no tracking record, no
    owner claim). Earlier coverage only exercised
    `codename_tracking.check_allocation_policy` directly; this exercises
    the real `_create_worktree_core` call site end to end.
    """

    def test_create_worktree_core_refuses_before_any_side_effect(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        anchor = tmp_path / "anchor"
        anchor.mkdir()
        config = cfg.Config(
            srcroot=str(tmp_path),
            machine="test",
            platform="windows",
            repo_name="demo-repo",
            repos={
                "demo-repo": cfg.RepoConfig(
                    anchor=str(anchor),
                    worktree_root=str(tmp_path / "worktrees"),
                    pr=cfg.PRConfig(enabled=True, source_attribution_configured=False),
                    codename=cfg.CodenameConfig(
                        wordlist_path=str(tmp_path / "custom-wordlist.json"),
                        wordlist_path_configured=True,
                    ),
                )
            },
        )
        tracking_path = tmp_path / "tracking"
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
        _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
        create_worktree_calls: list[object] = []
        monkeypatch.setattr(
            m.git_ops,
            "create_worktree",
            lambda *a, **k: create_worktree_calls.append((a, k)),
        )

        with pytest.raises(m.codename_tracking.CodenameAttributionPolicyError):
            m._create_worktree_core(config)

        assert not create_worktree_calls
        assert not tracking_path.exists() or not list(tracking_path.glob("*.yaml"))

    def test_create_worktree_core_fails_closed_on_reload_failure(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """PR #3037 review finding: if the fresh config reload inside the
        allocation lock fails, the second revalidation must degrade to a
        conservative, ALWAYS-non-authorizing state -- never silently reuse
        the pre-lock snapshot (which, for this benign pre-lock config,
        would have passed). A transient reload failure must fail the
        allocation closed, not open.
        """
        config = _create_config(tmp_path)  # benign: no PR, no custom wordlist
        tracking_path = tmp_path / "tracking"
        monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_path)
        _stub_create_worktree_core_internals(monkeypatch, tmp_path, config=None)

        def _raise_on_reload(*_a, **_k):
            raise RuntimeError("simulated transient config-reload failure")

        monkeypatch.setattr(m.cfg, "load_config", _raise_on_reload)

        with pytest.raises(m.codename_tracking.CodenameAttributionPolicyError):
            m._create_worktree_core(config)


class TestCodenameSelectorWiring:
    def test_resolve_worktree_id_by_codename_matches_real_record(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        tracking.create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        assert resolve_worktree_id_by_codename("rusty-gizmo") == "wt-a"
        assert resolve_worktree_id_by_codename("no-such-codename") is None

    def test_cmd_list_filters_by_codename(self, tmp_path: Path, monkeypatch, capfd) -> None:
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        tracking.create_new_record(
            "wt-a", "worktree/wt-a", str(tmp_path / "wt-a"), "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        tracking.create_new_record(
            "wt-b", "worktree/wt-b", str(tmp_path / "wt-b"), "repo", "machine", "wsl",
            tmp_path, codename="humming-widget",
        )
        records = tracking.list_records(tmp_path)
        monkeypatch.setattr(m, "_list_records_for_args", lambda args: records)
        monkeypatch.setattr(m.profile_assignment, "maintain", lambda: None)

        args = argparse.Namespace(
            worktree_id=None, codename="humming-widget", refresh=False,
            glance=False, stream=False, json=True, cache_only=False,
        )
        assert m.cmd_list(args) == 0
        # _json_output writes to sys.__stdout__, so a plain fd-level capture
        # (capfd) is required -- capsys patches sys.stdout only.
        payload = capfd.readouterr().out
        assert '"wt-b"' in payload
        assert '"wt-a"' not in payload

    def test_cmd_list_unmatched_codename_is_empty_not_a_suffix_match(
        self, tmp_path: Path, monkeypatch, capfd,
    ) -> None:
        # An unresolved codename must never fall back to ID-suffix matching:
        # a configured codename could otherwise coincidentally match the
        # tail of an unrelated worktree id and return the WRONG record.
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        tracking.create_new_record(
            "wt-humming-widget", "worktree/wt-humming-widget",
            str(tmp_path / "wt-humming-widget"), "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        records = tracking.list_records(tmp_path)
        monkeypatch.setattr(m, "_list_records_for_args", lambda args: records)
        monkeypatch.setattr(m.profile_assignment, "maintain", lambda: None)

        args = argparse.Namespace(
            worktree_id=None, codename="humming-widget", refresh=False,
            glance=False, stream=False, json=True, cache_only=False,
        )
        assert m.cmd_list(args) == 0
        payload = capfd.readouterr().out
        assert '"wt-humming-widget"' not in payload
        assert '"worktrees": []' in payload

    def test_cmd_resolve_unmatched_codename_is_a_hard_error(
        self, tmp_path: Path, monkeypatch, capfd,
    ) -> None:
        # Real invocation of the handler: an unmatched --codename must fail
        # fast with a clear JSON error, never silently fall through to the
        # misleading "wrong selector count" message or an interactive picker.
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        args = argparse.Namespace(
            json=True, base=False, new_worktree=False, auto=False, machine=None,
            codename="no-such-codename", worktree_id=None, restore=False, no_mux=False,
        )
        assert m.cmd_resolve(args) == 1
        payload = capfd.readouterr().out
        assert "no-such-codename" in payload
        assert '"error"' in payload

    def test_cmd_resolve_matched_codename_sets_worktree_id_before_launch_logic(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        # Real invocation of the handler: a matched --codename must set
        # args.worktree_id and proceed into the normal --json worktree-id
        # flow (rather than erroring or falling through unmapped). A marker
        # exception raised from the first call past the codename-resolution
        # block (cfg.load_config, inside a broad `except Exception` a few
        # lines later) proves the handler actually reached that flow -- a
        # SystemExit is not an Exception subclass, so it is not swallowed.
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        tracking.create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )

        def _boom():
            raise SystemExit(97)

        monkeypatch.setattr(m.cfg, "load_config", _boom)
        args = argparse.Namespace(
            json=True, base=False, new_worktree=False, auto=False, machine=None,
            codename="rusty-gizmo", worktree_id=None, restore=False, no_mux=False,
        )
        try:
            m.cmd_resolve(args)
        except SystemExit as exc:
            assert exc.code == 97
        else:
            raise AssertionError("expected cmd_resolve to reach cfg.load_config")
        assert args.worktree_id == "wt-a"

    def test_cmd_embody_matched_codename_sets_raw_id_before_launch_logic(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        # pr-attribution-codenames Phase 3: embody --codename must resolve
        # to the SAME local worktree_id via the identical local-match path
        # resolve uses (no cross-machine SSH call for a local hit) and
        # proceed into embody's normal flow.
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        tracking.create_new_record(
            "wt-a", "worktree/wt-a", "/tmp/wt-a", "repo", "machine", "wsl",
            tmp_path, codename="rusty-gizmo",
        )
        called = []
        import agent_worktrees.codename_reverse_lookup as crl
        monkeypatch.setattr(
            crl, "resolve_codename_cross_machine_unique",
            lambda *a, **k: called.append(1),
        )

        def _boom():
            raise SystemExit(98)

        monkeypatch.setattr(m.cfg, "load_config", _boom)
        args = argparse.Namespace(
            codename="rusty-gizmo", worktree_id=None, new=False,
        )
        try:
            m.cmd_embody(args)
        except SystemExit as exc:
            assert exc.code == 98
        else:
            raise AssertionError("expected cmd_embody to reach cfg.load_config")
        # A local match never invokes the cross-machine scan.
        assert called == []

class TestLegacyRecordBackfillEndToEnd:
    """A pre-Phase-2 record (no codename at all) gets one lazily assigned and
    persisted the first time a production path touches it explicitly --
    the backfill path the Phase 2 plan describes, exercised through the real
    `status` write handler rather than `codename_tracking` directly.
    """

    def test_status_write_backfills_a_legacy_record(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(cfg, "tracking_dir", lambda: tmp_path)
        monkeypatch.setattr(
            m.cfg, "load_config",
            lambda: SimpleNamespace(
                default_repo=SimpleNamespace(codename=SimpleNamespace(wordlist_path=""))
            ),
        )
        wt_dir = tmp_path / "wt-legacy"
        wt_dir.mkdir()
        tracking.create_new_record(
            "wt-legacy", "worktree/wt-legacy", str(wt_dir), "repo", "machine", "wsl",
            tmp_path,
        )
        rec_before = tracking.load_record_by_id("wt-legacy", tracking_path=tmp_path)
        assert rec_before.codename is None  # genuinely a pre-Phase-2 record

        args = argparse.Namespace(worktree_id="wt-legacy", summary="touched")
        assert m._cmd_status_write(args, summary="touched") == 0

        rec_after = tracking.load_record_by_id("wt-legacy", tracking_path=tmp_path)
        assert rec_after is not None
        assert rec_after.codename
        assert rec_after.summary == "touched"
