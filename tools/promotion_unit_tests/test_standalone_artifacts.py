"""Pure identity, candidate inventory and build-hook refusal controls."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import promotion_python_artifacts as artifacts
from artifact_source_build import validate_static_build
from standalone_python_artifacts import artifact_label, project_key


@pytest.mark.parametrize("record,label,key", [
    ({"plugin": "agent-pull-requests"}, "agent-pull-requests", "agent-pull-requests"),
    ({"component": "pr-service"}, "standalone-pr-service", "standalone:pr-service"),
    ({"component": "fleet-manager"}, "standalone-fleet-manager", "standalone:fleet-manager"),
], ids=["plugin", "pr-service", "fleet-manager"])
def test_explicit_standalone_identity_preserves_plugin_names(record, label, key):
    assert artifact_label(record) == label
    assert project_key(record) == key


@pytest.mark.parametrize("record", [
    {"component": "../services/pr"},
    {"component": "services/pr"},
    {"component": "worktree-manager"},
    {"component": []},
    {"component": "pr-service", "plugin": "pr-service"},
], ids=["traversal", "path", "unregistered", "invalid-type", "plugin-alias"])
def test_unknown_or_masquerading_components_refuse(record):
    with pytest.raises(artifacts.PromotionArtifactError, match="identity"):
        artifacts._artifact_label(record)


def test_candidate_inventory_keeps_same_name_plugin_and_standalone_distinct(tmp_path):
    for relative in ("plugins/pr-service", "services/pr", "fleet-manager"):
        source = tmp_path / relative
        source.mkdir(parents=True)
        (source / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\nversion = "1.0"\n', encoding="utf-8",
        )
    assert artifacts.candidate_projects(tmp_path) == {
        "pr-service": "1.0", "standalone:pr-service": "1.0", "standalone:fleet-manager": "1.0",
    }


@pytest.mark.parametrize("failure", ["missing", "malformed"])
def test_registered_candidate_metadata_cannot_be_silently_omitted(tmp_path, failure):
    source = tmp_path / "services" / "pr"
    source.mkdir(parents=True)
    if failure == "malformed":
        (source / "pyproject.toml").write_text("not TOML", encoding="utf-8")
    with pytest.raises(artifacts.PromotionArtifactError, match="invalid standalone"):
        artifacts.candidate_projects(tmp_path)


@pytest.mark.parametrize("kind", ["untrusted", "alternate", "missing-trusted", "trusted"])
def test_static_admission_allows_only_exact_selected_hook(tmp_path, kind):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        '[project]\nname = "fixture"\nversion = "1.0"\n', encoding="utf-8",
    )
    approved = b"raise RuntimeError('not executed by admission')\n"
    if kind != "missing-trusted":
        (tmp_path / ("setup.cfg" if kind == "alternate" else "setup.py")).write_bytes(
            b"untrusted" if kind == "untrusted" else approved,
        )
    if kind == "trusted":
        assert validate_static_build(tmp_path, trusted_setup=approved)["project"]["name"] == "fixture"
    else:
        with pytest.raises(artifacts.PromotionArtifactError):
            validate_static_build(tmp_path, trusted_setup=approved)


@pytest.mark.parametrize("outcome", ["built", "reused", "reuse-miss", "failed", "invalid-admission"])
def test_export_isolates_canonical_source_from_nested_scratch(tmp_path, monkeypatch, outcome):
    scratch = tmp_path / ".promote-scratch" / "promote-fixture"
    scratch.mkdir(parents=True)
    candidate = None
    events = []
    tree, commit = "a" * 40, "b" * 40
    monkeypatch.setitem(sys.modules, "promote_release", SimpleNamespace(
        add_scratch_worktree=lambda *a, **kw: scratch,
        remove_scratch_worktree=lambda path, **kw: events.append(("remove", path)),
        _is_ancestor=lambda *a, **kw: True,
    ))
    monkeypatch.setenv("GITHUB_WORKFLOW_SHA", commit)
    monkeypatch.setenv("GITHUB_SHA", "c" * 40 if outcome == "invalid-admission" else commit)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")

    def run(argv, *, cwd):
        nonlocal candidate
        if argv[0] == "git":
            assert argv[:4] == ["git", f"--attr-source={tree}", "checkout-index", "--all"]
            assert cwd == scratch and argv[4].endswith(os.sep)
            candidate = Path(argv[4].removeprefix("--prefix="))
            assert candidate.name == "source" and candidate.parent.name == "inputs"
            assert not candidate.is_relative_to(tmp_path)
            assert not any((parent / "libs").exists() for parent in list(candidate.parents)[:2])
            (candidate / "canonical.txt").write_bytes(b"attribute-normalized\r\n")
            events.append(("checkout", candidate))
            return ""
        assert Path(argv[argv.index("--repo") + 1]) == candidate
        assert cwd == Path(artifacts.__file__).resolve().parent
        events.append(("build", candidate))
        if outcome == "failed":
            raise artifacts.PromotionArtifactError("builder failed")
        output = Path(argv[argv.index("--out-dir") + 1])
        (output / artifacts._BUNDLES_NAME).write_text("[]", encoding="utf-8")
        return ""

    def inventory(root):
        assert root == candidate
        assert (root / "canonical.txt").read_bytes() == b"attribute-normalized\r\n"
        return {}

    def reuse_source(staging, **kwargs):
        assert kwargs["candidate_root"] == candidate
        assert kwargs["content_tree"] == tree and kwargs["repo"] == tmp_path
        events.append(("reuse", candidate))
        return {"reused": True} if outcome == "reused" else None

    def validate(directory, **kwargs):
        assert kwargs["candidate_root"] == candidate and kwargs["projects"] == {}
        assert kwargs["content_tree"] == tree and kwargs["dev_head"] == commit
        assert (candidate / "canonical.txt").read_bytes() == b"attribute-normalized\r\n"
        index = json.loads((directory / artifacts.INDEX_NAME).read_text())
        assert index["content_tree"] == tree
        if outcome == "reuse-miss":
            assert index["admission"] == {
                "signer_digest": commit, "source_digest": commit, "source_ref": "refs/heads/main",
            }
        events.append(("validate", candidate))

    monkeypatch.setattr(artifacts, "_run", run)
    monkeypatch.setattr(artifacts, "candidate_projects", inventory)
    monkeypatch.setattr(artifacts, "validate_artifacts", validate)
    monkeypatch.setitem(sys.modules, "reuse_promotion_artifacts", SimpleNamespace(
        reuse_artifacts=reuse_source,
    ))
    kwargs = dict(
        repo=tmp_path, commit=commit, content_tree=tree, dev_head=commit, main_before=commit,
        out_dir=tmp_path / "export",
        reuse_repository=(
            "example/plugins" if outcome in {"reused", "reuse-miss", "invalid-admission"} else None
        ),
    )
    if outcome in {"failed", "invalid-admission"}:
        with pytest.raises(
            artifacts.PromotionArtifactError,
            match="builder failed" if outcome == "failed" else "source artifact admission",
        ):
            artifacts.export_artifacts(**kwargs)
    else:
        artifacts.export_artifacts(**kwargs)
    assert [event[0] for event in events] == {
        "built": ["checkout", "build", "validate", "remove"],
        "reused": ["checkout", "reuse", "remove"],
        "reuse-miss": ["checkout", "reuse", "build", "validate", "remove"],
        "failed": ["checkout", "build", "remove"],
        "invalid-admission": ["checkout", "reuse", "build", "remove"],
    }[outcome]
    assert candidate is not None and not candidate.parent.parent.exists()
    assert (tmp_path / "export").exists() == (outcome not in {"failed", "invalid-admission"})
    assert not list(tmp_path.glob("python-artifacts-*"))
