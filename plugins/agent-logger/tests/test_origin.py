"""Tests for session origin derivation + sidecar marking."""

from __future__ import annotations

import json
from pathlib import Path

from agent_logger.config import Config
from agent_logger.sync import origin

HARNESSES = ["aperture-labs", "copilot-extensions", "dotfiles"]


def _mk(root: Path, name: str, workspace: str | None) -> Path:
    d = root / "session-state" / name
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    if workspace is not None:
        (d / "workspace.yaml").write_text(workspace, encoding="utf-8")
    return d


def test_derive_from_git_root(tmp_path: Path) -> None:
    d = _mk(tmp_path, "s1", "git_root: /home/u/src/dotfiles\ncwd: /home/u/src/dotfiles\n")
    o = origin.derive_origin(d, "book2", HARNESSES)
    assert o["source_repo"] == "dotfiles"
    assert o["basis"] == "git_root"
    assert o["machine"] == "book2"


def test_derive_worktree_path_resolves_repo(tmp_path: Path) -> None:
    # A worktree path must still resolve to the base harness repo name.
    d = _mk(tmp_path, "s2",
            "git_root: D:/Src/aperture-labs.worktrees/feature-x-win-abcd\n")
    o = origin.derive_origin(d, "book2", HARNESSES)
    assert o["source_repo"] == "aperture-labs"


def test_derive_machine_default_when_no_workspace(tmp_path: Path) -> None:
    d = _mk(tmp_path, "s3", None)
    o = origin.derive_origin(d, "book2", HARNESSES)
    assert o["source_repo"] is None
    assert o["basis"] == "machine-default"


def test_derive_machine_default_when_unrecognized_repo(tmp_path: Path) -> None:
    d = _mk(tmp_path, "s4", "git_root: /home/u/work/acme-webapp\n")
    o = origin.derive_origin(d, "book2", HARNESSES)
    assert o["source_repo"] is None
    assert o["basis"] == "machine-default"


def test_derive_precedence_git_root_over_cwd(tmp_path: Path) -> None:
    # git_root wins over cwd when both resolve to different harnesses.
    d = _mk(tmp_path, "s5",
            "cwd: /home/u/src/dotfiles\ngit_root: /home/u/src/aperture-labs\n")
    o = origin.derive_origin(d, "book2", HARNESSES)
    assert o["source_repo"] == "aperture-labs"
    assert o["basis"] == "git_root"


def test_write_sidecar_idempotent(tmp_path: Path) -> None:
    d = _mk(tmp_path, "s6", "git_root: /home/u/src/aperture-labs\n")
    o = origin.derive_origin(d, "book2", HARNESSES)
    assert origin.write_origin_sidecar(d, o) is True   # first write
    assert origin.write_origin_sidecar(d, o) is False  # unchanged
    written = json.loads((d / origin.ORIGIN_SIDECAR).read_text(encoding="utf-8"))
    assert written["source_repo"] == "aperture-labs"
    assert written["schema_version"] == origin.SCHEMA_VERSION


def test_read_sidecar_roundtrip_and_missing(tmp_path: Path) -> None:
    d = _mk(tmp_path, "s6r", "git_root: /home/u/src/aperture-labs\n")
    # No sidecar yet -> None (no resolvable recorded origin).
    assert origin.read_origin_sidecar(d) is None
    o = origin.derive_origin(d, "book2", HARNESSES)
    origin.write_origin_sidecar(d, o)
    back = origin.read_origin_sidecar(d)
    assert back is not None
    assert back["source_repo"] == "aperture-labs"
    # A malformed sidecar reads as None (never raises).
    (d / origin.ORIGIN_SIDECAR).write_text("not json", encoding="utf-8")
    assert origin.read_origin_sidecar(d) is None


def test_mark_all_writes_and_summarizes(tmp_path: Path) -> None:
    src = tmp_path / "copilot"
    _mk(src, "facility", "git_root: /home/u/src/aperture-labs\n")
    _mk(src, "work", "git_root: /home/u/work/acme-webapp\n")
    _mk(src, "ce", "git_root: /home/u/src/copilot-extensions\n")

    summary = origin.mark_all(src, "book2", HARNESSES)
    assert summary["total"] == 3
    assert summary["marked"] == 3
    assert summary["by_repo"] == {
        "aperture-labs": 1, "copilot-extensions": 1, "(machine-only)": 1}
    # Sidecars exist and are correct.
    assert (src / "session-state" / "facility" / "origin.json").is_file()
    work = json.loads(
        (src / "session-state" / "work" / "origin.json").read_text(encoding="utf-8"))
    assert work["source_repo"] is None

    # Second pass writes nothing new (idempotent).
    again = origin.mark_all(src, "book2", HARNESSES)
    assert again["marked"] == 0
    assert again["total"] == 3


def test_mark_all_dry_run_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "copilot"
    _mk(src, "facility", "git_root: /home/u/src/aperture-labs\n")
    summary = origin.mark_all(src, "book2", HARNESSES, dry_run=True)
    assert summary["total"] == 1
    assert summary["marked"] == 0
    assert not (src / "session-state" / "facility" / "origin.json").exists()


def test_backfill_corpus_tags_every_machine(tmp_path: Path) -> None:
    """Phase-4 backfill over the multi-machine <root>/<machine>/session-state
    corpus shape (the NAS): each session's machine is its dir name."""
    corpus = tmp_path / "nas"
    _mk(corpus / "book2", "fac", "git_root: /home/u/src/aperture-labs\n")
    _mk(corpus / "book2", "work", "git_root: /home/u/work/acme-webapp\n")
    _mk(corpus / "lambda-core", "lc", "git_root: /home/u/src/aperture-labs\n")

    summary = origin.backfill_corpus(corpus, HARNESSES)
    assert summary["total"] == 3
    assert summary["marked"] == 3
    assert set(summary["by_machine"]) == {"book2", "lambda-core"}
    # The book2 sessions are stamped with the right machine + derived source.
    book2_fac = json.loads(
        (corpus / "book2" / "session-state" / "fac" / "origin.json")
        .read_text(encoding="utf-8")
    )
    assert book2_fac["machine"] == "book2"
    assert book2_fac["source_repo"] == "aperture-labs"
    book2_work = json.loads(
        (corpus / "book2" / "session-state" / "work" / "origin.json")
        .read_text(encoding="utf-8")
    )
    assert book2_work["source_repo"] is None  # work repo -> machine-only

    # Idempotent re-run writes nothing new.
    again = origin.backfill_corpus(corpus, HARNESSES)
    assert again["marked"] == 0
    assert again["total"] == 3


def test_backfill_corpus_missing_root_is_empty(tmp_path: Path) -> None:
    summary = origin.backfill_corpus(tmp_path / "nope", HARNESSES)
    assert summary == {"total": 0, "marked": 0, "by_machine": {}}


def test_config_harness_repos_parsing(tmp_path: Path) -> None:
    cfg = Config({"sync": {"harness_repos": "aperture-labs, dotfiles"}}, tmp_path)
    assert cfg.sync_harness_repos == ["aperture-labs", "dotfiles"]
    assert Config({"sync": {}}, tmp_path).sync_harness_repos == []


def test_effective_harness_union_allowlist_first(tmp_path: Path) -> None:
    eff = origin.effective_harness(
        ["aperture-labs", "copilot-extensions"],
        ["dotfiles", "aperture-labs"])  # aperture-labs is a dup
    assert eff == ["aperture-labs", "copilot-extensions", "dotfiles"]


def test_classify_allowlisted_repo_syncs(tmp_path: Path) -> None:
    d = _mk(tmp_path, "s", "git_root: /home/u/src/aperture-labs\n")
    eff = origin.effective_harness(["aperture-labs"], ["dotfiles"])
    inc, o = origin.classify_for_sync(d, "book2", ["aperture-labs"], eff)
    assert inc is True
    assert o["source_repo"] == "aperture-labs"


def test_classify_known_work_repo_excluded_but_marked(tmp_path: Path) -> None:
    # dotfiles is a harness repo (so it's marked) but NOT in the allowlist -> no sync.
    d = _mk(tmp_path, "s", "git_root: /home/u/src/dotfiles\n")
    eff = origin.effective_harness(["aperture-labs"], ["dotfiles"])
    inc, o = origin.classify_for_sync(d, "book2", ["aperture-labs"], eff)
    assert inc is False
    assert o["source_repo"] == "dotfiles"  # still classified for local visibility


def test_classify_path_present_unmatched_excluded(tmp_path: Path) -> None:
    # A path that resolves to no known repo is a strict exclude, even fail-open.
    d = _mk(tmp_path, "s", "git_root: /home/u/work/mystery\n")
    eff = origin.effective_harness(["aperture-labs"], ["dotfiles"])
    inc, _ = origin.classify_for_sync(d, "book2", ["aperture-labs"], eff,
                                      fail_closed=False)
    assert inc is False


def test_classify_no_metadata_follows_fail_closed(tmp_path: Path) -> None:
    d = _mk(tmp_path, "s", None)  # no workspace.yaml
    eff = origin.effective_harness(["aperture-labs"], ["dotfiles"])
    assert origin.classify_for_sync(
        d, "book2", ["aperture-labs"], eff, fail_closed=False)[0] is True
    assert origin.classify_for_sync(
        d, "book2", ["aperture-labs"], eff, fail_closed=True)[0] is False


# -- denylist / catch-all "everything else" sink (book2 work OneDrive) --------

def test_effective_harness_folds_in_denylist(tmp_path: Path) -> None:
    # A denied repo must be derivable for the denylist to match, so it is folded
    # into the derivation set.
    eff = origin.effective_harness([], ["dotfiles"],
                                   ["aperture-labs", "copilot-extensions"])
    assert eff == ["aperture-labs", "copilot-extensions", "dotfiles"]


def test_classify_denylist_excludes_facility_from_work_sink(tmp_path: Path) -> None:
    # book2 work sink: allowlist empty (catch-all), deny facility repos.
    deny = ["aperture-labs", "copilot-extensions"]
    eff = origin.effective_harness([], ["dotfiles", "acme-webapp"], deny)
    fac = _mk(tmp_path, "fac", "git_root: /home/u/src/aperture-labs\n")
    inc, o = origin.classify_for_sync(fac, "book2", [], eff, denylist=deny)
    assert inc is False              # facility session never goes to the work sink
    assert o["source_repo"] == "aperture-labs"  # ...but is still truthfully tagged


def test_classify_catchall_syncs_everything_not_denied(tmp_path: Path) -> None:
    deny = ["aperture-labs", "copilot-extensions"]
    eff = origin.effective_harness([], ["dotfiles", "acme-webapp"], deny)
    # A recognized work repo -> synced to the catch-all (work) sink, tagged.
    work = _mk(tmp_path, "work", "git_root: /home/u/work/dotfiles\n")
    inc, o = origin.classify_for_sync(work, "book2", [], eff, denylist=deny)
    assert inc is True
    assert o["source_repo"] == "dotfiles"
    # An UNRECOGNIZED employer repo -> still caught (not denied), tagged machine-only.
    unknown = _mk(tmp_path, "unknown", "git_root: /home/u/work/secret-thing\n")
    inc2, o2 = origin.classify_for_sync(unknown, "book2", [], eff, denylist=deny)
    assert inc2 is True
    assert o2["source_repo"] is None
    # A metadata-less session -> catch-all keeps it (fail-open) for the work sink.
    bare = _mk(tmp_path, "bare", None)
    assert origin.classify_for_sync(bare, "book2", [], eff, denylist=deny)[0] is True


def test_classify_denylist_beats_allowlist(tmp_path: Path) -> None:
    # When both are set, an explicit deny wins over a matching allow.
    d = _mk(tmp_path, "s", "git_root: /home/u/src/aperture-labs\n")
    eff = origin.effective_harness(["aperture-labs"], [], ["aperture-labs"])
    inc, _ = origin.classify_for_sync(
        d, "book2", ["aperture-labs"], eff, denylist=["aperture-labs"])
    assert inc is False


def test_config_denylist_parsing(tmp_path: Path) -> None:
    cfg = Config({"sync": {"repo_denylist": "aperture-labs, copilot-extensions"}},
                 tmp_path)
    assert cfg.sync_repo_denylist == ["aperture-labs", "copilot-extensions"]
    assert Config({"sync": {}}, tmp_path).sync_repo_denylist == []
