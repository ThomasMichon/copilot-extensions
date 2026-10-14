"""tag_promotion.py names the pushed tag from the landed commit's own state."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import tag_promotion as tp
from promote_release import PIPELINE_STATE_PATH

STEM = "promote-20260102030405"
DEV_SHA = "d" * 40


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit_state(work: Path, promotion: dict | None) -> str:
    state = work / PIPELINE_STATE_PATH
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"last_promotion": promotion}), encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-q", "--allow-empty", "-m", "release"], work)
    _git(["push", "-q", "origin", "HEAD:main"], work)
    return _git(["rev-parse", "HEAD"], work)


@pytest.fixture
def repo(tmp_path: Path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    _git(["config", "user.name", "t"], work)
    _git(["config", "user.email", "t@example.com"], work)
    _git(["remote", "add", "origin", str(origin)], work)
    return work, origin


def _remote_tag(origin: Path, name: str) -> str:
    out = _git(["ls-remote", str(origin), f"refs/tags/{name}^{{}}"], origin)
    return out.split()[0] if out else ""


def _tag(work: Path, sha: str, dev_sha: str = DEV_SHA):
    return tp.tag_promotion(merge_sha=sha, dev_sha=dev_sha, message="m", repo=work)


def test_tags_merged_commit_as_state_stem_plus_sha(repo):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    assert _tag(work, sha) == (f"{STEM}-{sha[:8]}", True)
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == sha


def test_rerun_on_the_same_commit_is_a_noop(repo):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    _tag(work, sha)
    assert _tag(work, sha) == (f"{STEM}-{sha[:8]}", False)
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == sha


def test_state_naming_another_dev_head_refuses(repo):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    with pytest.raises(tp.TagError, match="expected the promoted"):
        _tag(work, sha, dev_sha="e" * 40)
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == ""


@pytest.mark.parametrize(
    "promotion", [None, "corrupt", ["x"], {"dev_head": DEV_SHA}, {"tag": "v1", "dev_head": DEV_SHA}]
)
def test_missing_or_foreign_stem_refuses(repo, promotion):
    work, _origin = repo
    sha = _commit_state(work, promotion)
    with pytest.raises(tp.TagError):
        _tag(work, sha)


def test_existing_tag_on_another_commit_refuses(repo):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    other = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    _git(["tag", "-a", f"{STEM}-{sha[:8]}", "-m", "x", other], work)
    _git(["push", "-q", "origin", f"{STEM}-{sha[:8]}"], work)
    with pytest.raises(tp.TagError, match="already exists"):
        _tag(work, sha)
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == other


def test_commit_not_on_main_refuses(repo):
    work, origin = repo
    _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    _git(["checkout", "-q", "-b", "side"], work)
    _git(["commit", "-q", "--allow-empty", "-m", "side"], work)
    side = _git(["rev-parse", "HEAD"], work)
    with pytest.raises(tp.TagError, match="is not on origin/main"):
        _tag(work, side)


def test_cli_reports_errors_with_nonzero_exit(repo, capsys):
    work, _origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    code = tp.main(["--merge-sha", sha, "--dev-sha", "e" * 40, "--repo", str(work)])
    assert code == 1
    assert "::error::tag-promotion:" in capsys.readouterr().err
    assert tp.main(["--merge-sha", sha, "--dev-sha", DEV_SHA, "--repo", str(work)]) == 0
    assert f"pushed {STEM}-{sha[:8]}" in capsys.readouterr().out


def test_cli_emits_only_a_verified_tag_receipt(repo, tmp_path):
    work, _origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    output = tmp_path / "output"
    argv = ["--merge-sha", sha, "--dev-sha", DEV_SHA, "--repo", str(work),
            "--github-output", str(output)]
    assert tp.main(argv) == 0
    assert output.read_text() == f"promotion_tag={STEM}-{sha[:8]}\n"
    assert tp.main(argv) == 0
    assert output.read_text().splitlines() == [f"promotion_tag={STEM}-{sha[:8]}"] * 2


@pytest.mark.parametrize("stem", ["promote-", "promote-x", STEM + "\nforged=value"])
def test_invalid_state_stem_cannot_create_a_workflow_receipt(repo, tmp_path, stem):
    work, _origin = repo
    sha = _commit_state(work, {"tag": stem, "dev_head": DEV_SHA})
    output = tmp_path / "output"
    assert tp.main(["--merge-sha", sha, "--dev-sha", DEV_SHA, "--repo", str(work),
                    "--github-output", str(output)]) == 1
    assert not output.exists()


def test_control_head_can_advance_beyond_the_published_release(repo):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    later = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    assert later != sha
    assert _tag(work, sha) == (f"{STEM}-{sha[:8]}", True)
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == sha


def test_native_workflow_tag_step_configures_fresh_git_identity_and_authentication(repo, tmp_path):
    import yaml

    git, bash = shutil.which("git"), shutil.which("bash")
    if os.name == "nt" and git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    if not bash or shutil.which("gh") is None:
        pytest.skip("native promotion tagging requires Bash and GitHub CLI")
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    _git(["config", "--unset", "user.name"], work)
    _git(["config", "--unset", "user.email"], work)
    root = Path(__file__).resolve().parent.parent
    workflow = yaml.safe_load((root / ".github/workflows/validate-and-promote.yml").read_text())
    step = next(step for step in workflow["jobs"]["publish-python-artifacts"]["steps"]
                if step.get("id") == "tag")
    assert step["env"]["GH_TOKEN"] == "${{ secrets.APERTURE_RELEASE_TOKEN }}"
    script = step["run"].replace(
        "python tools/tag_promotion.py", f'python "{(root / "tools/tag_promotion.py").as_posix()}"',
    )
    output = tmp_path / "output"
    config = tmp_path / "gitconfig"
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e"], input=script, cwd=work,
        env={**{key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")},
             "GH_TOKEN": "synthetic-release-authority", "GH_HOST": "github.com",
             "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1",
             "GH_CONFIG_DIR": str(tmp_path / "gh"), "GIT_TERMINAL_PROMPT": "0",
             "MERGE_SHA": sha, "DEV_SHA": DEV_SHA, "GITHUB_OUTPUT": str(output)},
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == sha
    assert _git(["config", "--local", "user.name"], work) == "github-actions[bot]"
    assert _git(["for-each-ref", "--format=%(taggername)", f"refs/tags/{STEM}-{sha[:8]}"], work) == "github-actions[bot]"
    assert output.read_text() == f"promotion_tag={STEM}-{sha[:8]}\n"
    assert "synthetic-release-authority" not in config.read_text()
    assert "gh" in config.read_text()


def test_retry_after_a_failed_push_pushes_the_existing_local_tag(repo):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    # A previous run created the tag locally, then its push failed.
    _git(["tag", "-a", f"{STEM}-{sha[:8]}", "-m", "m", sha], work)
    assert _tag(work, sha) == (f"{STEM}-{sha[:8]}", True)
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == sha


def test_local_tag_on_another_commit_refuses(repo):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    other = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    _git(["tag", "-a", f"{STEM}-{sha[:8]}", "-m", "m", other], work)
    with pytest.raises(tp.TagError, match="local tag"):
        _tag(work, sha)
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == ""


def test_unknown_commit_is_reported_as_unknown(repo):
    work, _origin = repo
    _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    with pytest.raises(tp.TagError, match="not a known commit"):
        _tag(work, "f" * 40)


def test_prefix_is_shared_with_promote_release():
    import promote_release

    assert tp.TAG_PREFIX == f"{promote_release.PROMOTION_TAG_PREFIX}-"
    assert promote_release.promote.__kwdefaults__["tag_prefix"] == promote_release.PROMOTION_TAG_PREFIX

def test_inherited_git_dir_does_not_redirect_the_tagging(repo, tmp_path, monkeypatch):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "wrong.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    assert _tag(work, sha) == (f"{STEM}-{sha[:8]}", True)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == sha


def _push_lightweight(work: Path, name: str, target: str) -> None:
    _git(["tag", name, target], work)
    _git(["push", "-q", "origin", f"refs/tags/{name}"], work)
    _git(["tag", "-d", name], work)


def test_lightweight_remote_tag_on_the_same_commit_is_a_noop(repo):
    work, _origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    _push_lightweight(work, f"{STEM}-{sha[:8]}", sha)
    assert _tag(work, sha) == (f"{STEM}-{sha[:8]}", False)


def test_lightweight_remote_tag_on_another_commit_refuses(repo):
    work, _origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    other = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    _push_lightweight(work, f"{STEM}-{sha[:8]}", other)
    with pytest.raises(tp.TagError, match="already exists"):
        _tag(work, sha)

def test_abbreviated_merge_sha_names_and_reruns_like_the_full_one(repo):
    work, origin = repo
    sha = _commit_state(work, {"tag": STEM, "dev_head": DEV_SHA})
    assert _tag(work, sha[:10]) == (f"{STEM}-{sha[:8]}", True)
    assert _tag(work, sha[:10]) == (f"{STEM}-{sha[:8]}", False)
    assert _remote_tag(origin, f"{STEM}-{sha[:8]}") == sha