"""Durable orphanage: --abandon re-homes obligations rather than dropping them.

resource-obligation-settlement (dotfiles#1161): `tracking.rehome_abandoned_
obligations` + `load_orphaned_obligations` + the `claims orphans` view, and the
finalize wiring that re-homes an abandoned worktree's unsettled obligations.
"""
from __future__ import annotations

import argparse
import json
import types

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import cleanup, finalize, providers, tracking


def _seed_project(tmp_path, monkeypatch, machine="m", project="p"):
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name or project}")
    (tmp_path / f".{project}").mkdir(parents=True, exist_ok=True)


def _claim(kind, ref, state="active", note=""):
    return tracking.ResourceClaim(kind=kind, ref=ref, state=state, note=note)


def _config(machine="m", project="p"):
    return types.SimpleNamespace(
        machine=machine,
        repo_name=project,
        default_repo=types.SimpleNamespace(
            pr=types.SimpleNamespace(provider="github", api_base=""),
        ),
    )


# ── rehome_abandoned_obligations / load_orphaned_obligations ─────────────────

def test_rehome_writes_registry_with_provenance(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    claims = [_claim("codespace", "verbose-space", note="borrowed"),
              _claim("worktree", "m/p/child")]
    added = tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config(),
        handoff_to="operator-flow")
    assert len(added) == 2
    loaded = tracking.load_orphaned_obligations()
    assert {e["ref"] for e in loaded} == {"verbose-space", "m/p/child"}
    cs = next(e for e in loaded if e["ref"] == "verbose-space")
    assert cs["kind"] == "codespace" and cs["source_worktree"] == "wt-owner"
    assert cs["machine"] == "m" and cs["project"] == "p"
    assert cs["disposition"] == "abandoned" and cs["abandoned_at"]
    assert cs["handoff_to"] == "operator-flow"
    assert cs["note"] == "borrowed"


def test_rehome_is_idempotent(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    claims = [_claim("codespace", "verbose-space")]
    assert len(tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config())) == 1
    # Same source+ref again -> no duplicate.
    assert tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config()) == []
    assert len(tracking.load_orphaned_obligations()) == 1
    # A different owner re-homing the same ref IS recorded (distinct provenance).
    added = tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-other", config=_config())
    assert len(added) == 1
    assert len(tracking.load_orphaned_obligations()) == 2


def test_rehome_upgrades_legacy_empty_handoff_but_not_different_target(
        tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    claims = [_claim("codespace", "verbose-space")]
    tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config())
    tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config(),
        handoff_to="operator-flow")
    assert tracking.load_orphaned_obligations()[0]["handoff_to"] == (
        "operator-flow")
    tracking.rehome_abandoned_obligations(
        claims, source_worktree="wt-owner", config=_config(),
        handoff_to="different-flow")
    assert tracking.load_orphaned_obligations()[0]["handoff_to"] == (
        "operator-flow")


def test_load_orphans_empty_when_absent(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    assert tracking.load_orphaned_obligations() == []


def test_strict_orphanage_read_preserves_corrupt_registry(
        tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    path = tracking.orphanage_path()
    path.write_text("orphaned: [", encoding="utf-8")
    assert tracking.load_orphaned_obligations() == []
    with pytest.raises(Exception):
        tracking.load_orphaned_obligations_strict()
    assert tracking.rehome_abandoned_obligations(
        [_claim("codespace", "x")], source_worktree="w", config=_config(),
        handoff_to="operator-flow") == []
    assert path.read_text(encoding="utf-8") == "orphaned: ["


def test_rehome_is_best_effort_on_io_error(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    monkeypatch.setattr(tracking, "orphanage_path",
                        lambda project=None: (_ for _ in ()).throw(OSError("nope")))
    # Never raises; returns [].
    assert tracking.rehome_abandoned_obligations(
        [_claim("codespace", "x")], source_worktree="w", config=_config()) == []


# ── claims orphans view ──────────────────────────────────────────────────────

def test_claims_orphans_json_lists_registry(tmp_path, monkeypatch, capfd):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "verbose-space")], source_worktree="wt-o", config=_config())
    rc = m.cmd_claims(argparse.Namespace(target=["orphans"], json=True))
    assert rc == 0
    out = json.loads(capfd.readouterr().out)
    assert out["count"] == 1 and out["orphaned"][0]["ref"] == "verbose-space"


def test_claims_orphans_text_names_handoff_target(
        tmp_path, monkeypatch, capfd):
    _seed_project(tmp_path, monkeypatch)
    tracking.rehome_abandoned_obligations(
        [_claim("codespace", "verbose-space")],
        source_worktree="wt-o", config=_config(),
        handoff_to="operator-flow")
    rc = m.cmd_claims(argparse.Namespace(target=["orphans"], json=False))
    assert rc == 0
    assert "handoff: operator-flow" in capfd.readouterr().out


def test_claims_orphans_empty_message(tmp_path, monkeypatch, capfd):
    _seed_project(tmp_path, monkeypatch)
    rc = m.cmd_claims(argparse.Namespace(target=["orphans"], json=False))
    assert rc == 0
    assert "no re-homed obligations" in capfd.readouterr().out.lower()


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (
            "https://github.com/example/project/pull/42",
            ("github", "example/project", 42, "github.com"),
        ),
        ("example/project#42", ("github", "example/project", 42, "")),
    ],
)
def test_pr_claim_target_parses_github_references(ref, expected):
    prcfg = types.SimpleNamespace(provider="github", api_base="")
    assert cleanup._pr_claim_target(ref, prcfg) == expected


def test_pr_claim_target_preserves_configured_github_enterprise_api():
    prcfg = types.SimpleNamespace(
        provider="github", api_base="https://github.example.com/api/v3",
    )
    assert cleanup._pr_claim_target(
        "https://github.example.com/owner/project/pull/42", prcfg,
    ) == ("github", "owner/project", 42, "https://github.example.com/api/v3")


def test_pr_claim_target_rejects_unconfigured_github_enterprise_host():
    prcfg = types.SimpleNamespace(provider="github", api_base="")
    assert cleanup._pr_claim_target(
        "https://untrusted.example/owner/project/pull/42", prcfg,
    ) is None
    assert cleanup._pr_claim_target(
        "https://[broken/owner/project/pull/42", prcfg,
    ) is None


def test_pr_claim_target_honors_gh_host_ambient_authority(monkeypatch):
    # api_base unconfigured but GH_HOST points at a GitHub Enterprise host --
    # GitHubProvider.authority_endpoint() treats that as the effective
    # authority, so a hardcoded "github.com" check would wrongly reject every
    # valid full-URL claim on that host.
    monkeypatch.setenv("GH_HOST", "github.example.com")
    prcfg = types.SimpleNamespace(provider="github", api_base="")
    assert cleanup._pr_claim_target(
        "https://github.example.com/owner/project/pull/42", prcfg,
    ) == ("github", "owner/project", 42, "github.example.com")
    # The public host is no longer the ambient authority -- reject it.
    assert cleanup._pr_claim_target(
        "https://github.com/owner/project/pull/42", prcfg,
    ) is None


def test_pr_claim_target_preserves_gh_host_port(monkeypatch):
    # An ambient GH_HOST authority carrying a port must survive into the
    # resolved api_base -- a bare `parsed.hostname` drops the port, which
    # would silently make get_pull() query the default port instead of the
    # configured one.
    monkeypatch.setenv("GH_HOST", "github.example.com:8443")
    prcfg = types.SimpleNamespace(provider="github", api_base="")
    assert cleanup._pr_claim_target(
        "https://github.example.com:8443/owner/project/pull/42", prcfg,
    ) == ("github", "owner/project", 42, "github.example.com:8443")
    # A request to the same host but the wrong (default) port must still be
    # rejected as a different authority.
    assert cleanup._pr_claim_target(
        "https://github.example.com/owner/project/pull/42", prcfg,
    ) is None


@pytest.mark.parametrize(
    "ref",
    [
        "https://github.com:8443/owner/project/pull/42",
        "https://github.com/prefix/owner/project/pull/42",
        "https://github.com/owner/project/pull/42/extra",
        "http://github.com/owner/project/pull/42",
        "https://github.com/owner/project/pull/42?redirect=elsewhere",
        "https://github.com/owner/project/pull/42#comment",
    ],
)
def test_pr_claim_target_rejects_noncanonical_public_github_urls(ref):
    prcfg = types.SimpleNamespace(provider="github", api_base="")
    assert cleanup._pr_claim_target(ref, prcfg) is None


def test_pr_claim_target_rejects_unqualified_and_credentialed_urls():
    prcfg = types.SimpleNamespace(provider="github", api_base="")
    assert cleanup._pr_claim_target("#42", prcfg) is None
    assert cleanup._pr_claim_target(
        "https://user:password@example.com/owner/repo/pull/42", prcfg,
    ) is None


def test_pr_claim_target_resolves_configured_gitea_and_azure_devops():
    gitea = types.SimpleNamespace(
        provider="gitea", api_base="https://forge.example/gitea",
    )
    assert cleanup._pr_claim_target(
        "https://forge.example/gitea/owner/project/pulls/12", gitea,
    ) == ("gitea", "owner/project", 12, "https://forge.example/gitea")

    ado = types.SimpleNamespace(
        provider="azure-devops", api_base="https://dev.azure.com/acme",
    )
    assert cleanup._pr_claim_target(
        "https://dev.azure.com/acme/Project/_git/repo/pullrequest/34", ado,
    ) == (
        "azure-devops", "Project/repo", 34, "https://dev.azure.com/acme",
    )
    assert cleanup._pr_claim_target(
        "https://dev.azure.com/other/Project/_git/repo/pullrequest/34", ado,
    ) is None


def test_pr_claim_target_rejects_gitea_claim_under_different_root_path():
    # A path-hosted Gitea instance's api_base is the instance ROOT, not the
    # whole host -- same host/port/scheme alone doesn't prove the claim URL
    # names the same application. A claim under a different root on that
    # same host (a different app entirely) must be rejected, not queried
    # against the configured root with its own repo/PR number.
    gitea = types.SimpleNamespace(
        provider="gitea", api_base="https://forge.example/gitea",
    )
    assert cleanup._pr_claim_target(
        "https://forge.example/owner/project/pulls/12", gitea,
    ) is None
    assert cleanup._pr_claim_target(
        "https://forge.example/other-app/owner/project/pulls/12", gitea,
    ) is None
    # Root-hosted Gitea (empty api_base path) is unaffected.
    root_gitea = types.SimpleNamespace(
        provider="gitea", api_base="https://forge.example",
    )
    assert cleanup._pr_claim_target(
        "https://forge.example/owner/project/pulls/12", root_gitea,
    ) == ("gitea", "owner/project", 12, "https://forge.example")


def test_pr_claim_target_rejects_shorthand_for_non_github_providers():
    # owner/repo#N is a GitHub-only grammar (sweep.py's _GH_PR_SHORT); Gitea
    # and Azure DevOps claims always carry a full authority-bearing URL.
    # Recognizing the shorthand there would let a legacy slug/number be
    # re-queried against a different service if the provider ever changes.
    gitea = types.SimpleNamespace(
        provider="gitea", api_base="https://forge.example/gitea",
    )
    assert cleanup._pr_claim_target("owner/project#12", gitea) is None

    ado = types.SimpleNamespace(
        provider="azure-devops", api_base="https://dev.azure.com/acme",
    )
    assert cleanup._pr_claim_target("owner/project#34", ado) is None


def test_pr_claim_target_rejects_shorthand_when_gh_host_is_non_default(
    monkeypatch,
):
    # The owner/repo#N shorthand carries no authority of its own. If it was
    # resolvable while GH_HOST pointed at an Enterprise host (or any
    # non-default authority), it must be rejected once that authority no
    # longer matches the implicit default public github.com -- the shape
    # this guards against is the reverse of the test below: a shorthand
    # claim re-queried after the ambient authority changed could confirm an
    # unrelated PR under the new authority and silently drop the original
    # obligation.
    monkeypatch.setenv("GH_HOST", "github.example.com")
    prcfg = types.SimpleNamespace(provider="github", api_base="")
    assert cleanup._pr_claim_target("owner/project#12", prcfg) is None


def test_pr_claim_target_rejects_shorthand_with_configured_enterprise_api():
    # Same risk as the GH_HOST case above, but via a configured Enterprise
    # api_base instead of the ambient env var.
    prcfg = types.SimpleNamespace(
        provider="github", api_base="https://github.example.com/api/v3",
    )
    assert cleanup._pr_claim_target("owner/project#12", prcfg) is None


@pytest.mark.parametrize(
    ("provider", "api_base", "ref"),
    [
        (
            "github", "",
            "https://dev.azure.com/acme/Project/_git/repo/pullrequest/34",
        ),
        (
            "azure-devops", "https://dev.azure.com/acme",
            "https://dev.azure.com/other/Project/_git/repo/pullrequest/34",
        ),
    ],
)
def test_reclaim_pr_rejects_provider_or_authority_mismatch_before_credentials(
        monkeypatch, provider, api_base, ref):
    calls = []
    config = _config()
    config.default_repo.pr = types.SimpleNamespace(
        provider=provider, api_base=api_base,
    )
    monkeypatch.setattr(
        providers, "account_token_for_slug",
        lambda *_a, **_k: calls.append("token"),
    )
    monkeypatch.setattr(
        providers, "get_provider",
        lambda _name: calls.append("provider"),
    )
    result = cleanup.reclaim_pr(ref, config, apply=True)
    assert result.status == "failed"
    assert calls == []


def test_claims_cleanup_releases_only_provider_confirmed_merged_prs(
        tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    ref = "https://github.com/example/project/pull/42"
    tracking.rehome_abandoned_obligations(
        [_claim("pr", ref)], source_worktree="wt-owner", config=_config())

    class Provider:
        def get_pull(self, repo, number, **kwargs):
            assert repo == "example/project"
            assert number == 42
            return types.SimpleNamespace(state="merged", merged=True)

    monkeypatch.setattr(providers, "get_provider", lambda _name: Provider())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_a, **_k: None)
    config = _config()

    preview = cleanup.cleanup_orphanage(config, apply=False)
    assert preview[0]["status"] == "reclaimed"
    assert "would release" in preview[0]["detail"]
    assert len(tracking.load_orphaned_obligations()) == 1

    applied = cleanup.cleanup_orphanage(config, apply=True)
    assert applied[0]["status"] == "reclaimed"
    assert tracking.load_orphaned_obligations() == []


def test_claims_cleanup_keeps_unmerged_or_unqueryable_pr_claims(
        tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    ref = "https://github.com/example/project/pull/42"
    tracking.rehome_abandoned_obligations(
        [_claim("pr", ref)], source_worktree="wt-owner", config=_config())

    class Provider:
        def get_pull(self, repo, number, **kwargs):
            return types.SimpleNamespace(state="closed", merged=False)

    monkeypatch.setattr(providers, "get_provider", lambda _name: Provider())
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_a, **_k: None)

    result = cleanup.cleanup_orphanage(_config(), apply=True)
    assert result[0]["status"] == "skipped"
    assert tracking.load_orphaned_obligations()[0]["ref"] == ref


# ── finalize wiring: --abandon re-homes only unsettled, before releasing ─────

def _record_with_active_claim():
    return types.SimpleNamespace(
        resources=[_claim("codespace", "verbose-space", state="active"),
                   _claim("worktree", "m/p/settled", state="at-rest")])


def test_finalize_rehome_helper_selects_only_unsettled(tmp_path, monkeypatch):
    seen = {}

    def _fake_rehome(
            claims, *, source_worktree, config, handoff_to=None, project=None):
        seen["refs"] = [c.ref for c in claims]
        seen["src"] = source_worktree
        seen["handoff_to"] = handoff_to
        seen["entries"] = [{
            "ref": c.ref,
            "source_worktree": source_worktree,
            "handoff_to": handoff_to,
        } for c in claims]
        return seen["entries"]

    monkeypatch.setattr(tracking, "rehome_abandoned_obligations", _fake_rehome)
    monkeypatch.setattr(
        tracking, "load_orphaned_obligations_strict",
        lambda project=None: seen.get("entries", []))
    rec = _record_with_active_claim()
    out = finalize._rehome_abandoned_obligations(
        rec, "wt-owner", _config(), handoff_to="operator-flow")
    # Only the active (unsettled) claim is re-homed; the at-rest one is not.
    assert seen["refs"] == ["verbose-space"]
    assert seen["src"] == "wt-owner"
    assert seen["handoff_to"] == "operator-flow"
    assert out is True


def test_finalize_rehome_helper_noop_when_nothing_unsettled(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(tracking, "rehome_abandoned_obligations",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    rec = types.SimpleNamespace(resources=[_claim("worktree", "m/p/x", state="at-rest")])
    assert finalize._rehome_abandoned_obligations(
        rec, "wt", _config(), handoff_to="operator-flow") is True
    assert called["n"] == 0  # nothing unsettled -> the registry is never touched


def test_finalize_abandon_rehomes_end_to_end(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch)
    rec = _record_with_active_claim()
    finalize._rehome_abandoned_obligations(
        rec, "wt-owner", _config(), handoff_to="operator-flow")
    loaded = tracking.load_orphaned_obligations()
    assert [e["ref"] for e in loaded] == ["verbose-space"]  # real registry write
    assert loaded[0]["handoff_to"] == "operator-flow"


def test_finalize_rehome_refuses_when_handoff_write_is_not_durable(
        monkeypatch, capfd):
    rec = _record_with_active_claim()
    monkeypatch.setattr(
        tracking, "rehome_abandoned_obligations", lambda *a, **k: [])
    monkeypatch.setattr(
        tracking, "load_orphaned_obligations_strict", lambda project=None: [])
    assert finalize._rehome_abandoned_obligations(
        rec, "wt-owner", _config(),
        handoff_to="operator-flow") is False
    captured = capfd.readouterr()
    assert "ownership is preserved" in captured.out + captured.err
