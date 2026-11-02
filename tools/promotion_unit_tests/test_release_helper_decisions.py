"""Release-helper decisions using fixture bytes and actor-boundary substitutes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import artifact_builder_cli as cli
import changefile
import governed_feed_trust as trust
import installer_engine_ref as engine
import launch_wrapper_assets_ref as assets
import materialize_installer_engine as installers
import materialize_launch_wrapper_assets as wrappers
import materialize_main as materializer
import nested_uv_editable_ref as nested
import promote_release as promotion
import promotion_source_alias as aliases
import public_toolchain_source as public
import reuse_promotion_artifacts as reuse
import runtime_profile_release as profiles
import seasoned_public_feed as seasoned
import standalone_consumers as standalone
import tag_promotion as tags
import uv_editable_ref as editable
from runtime_profile import ProfileError, SCHEMA as PROFILE_SCHEMA

DEV, MAIN, TREE = "d" * 40, "a" * 40, "c" * 40


@pytest.fixture(autouse=True)
def _no_process_actors(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("release decision unit reached an unmocked process actor")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("case", ["same", "changed", "wrong-source", "bad-json", "git-error"])
def test_source_tag_reuse_requires_matching_provenance_and_content(tmp_path, monkeypatch, case):
    calls = []

    def git(argv, **kwargs):
        calls.append(argv)
        assert kwargs["cwd"] == tmp_path
        if argv[0] == "show":
            state = {"last_promotion": {
                "release_id": DEV, "dev_head": MAIN if case == "wrong-source" else DEV,
            }}
            return subprocess.CompletedProcess(
                argv, 0, "{" if case == "bad-json" else json.dumps(state), "",
            )
        assert argv == [
            "diff", "--quiet", "prior", "landed", "--", ".",
            f":(exclude){tags.PIPELINE_STATE_PATH}",
            f":(exclude){tags.COVERAGE_BASELINES_DIR}",
        ]
        return subprocess.CompletedProcess(
            argv, {"changed": 1, "git-error": 128}.get(case, 0), "", "comparison failed",
        )

    monkeypatch.setattr(tags, "_git", git)
    if case in {"bad-json", "git-error"}:
        with pytest.raises(tags.TagError, match="unreadable|cannot compare"):
            tags._same_source_release("prior", "landed", DEV, repo=tmp_path)
    else:
        assert tags._same_source_release("prior", "landed", DEV, repo=tmp_path) == (case == "same")
    assert len(calls) == (1 if case in {"wrong-source", "bad-json"} else 2)


@pytest.mark.parametrize("case", ["valid", "wrong-tree", "wrong-release-id", "unrelated-history"])
def test_prior_alias_requires_frozen_source_and_main_history(tmp_path, monkeypatch, case):
    previous = {
        "dev_head": DEV, "release_id": MAIN if case == "wrong-release-id" else DEV,
        "tag": f"promote-dev-{DEV}", "main_before": MAIN,
    }
    monkeypatch.setattr(aliases, "_git", lambda *a: (MAIN + "\n").encode())
    monkeypatch.setattr(aliases, "_tree", lambda *a: {aliases._STATE: "entry"})
    monkeypatch.setattr(aliases, "_blob", lambda *a, **kw: json.dumps({
        "schema": "copilot-extensions.release-pipeline-state", "version": 1,
        "last_promotion": previous,
    }).encode())
    monkeypatch.setattr(promotion, "_is_ancestor", lambda *a, **kw: case != "unrelated-history")
    monkeypatch.setattr(promotion, "_git", lambda *a, **kw: TREE)
    projections = []

    def project_tree(argv, **kwargs):
        assert argv in [
            ["git", "read-tree", TREE],
            ["git", "rm", "--cached", "-f", "-q", "--ignore-unmatch", promotion.PIPELINE_STATE_PATH],
            ["git", "rm", "--cached", "-f", "-q", "-r", "--ignore-unmatch",
             promotion.COVERAGE_BASELINES_DIR],
            ["git", "write-tree"],
        ]
        assert kwargs["cwd"] == str(tmp_path)
        assert Path(kwargs["env"]["GIT_INDEX_FILE"]).name == "index"
        projections.append(argv)
        return subprocess.CompletedProcess(argv, 0, TREE + "\n", "")

    monkeypatch.setattr(subprocess, "run", project_tree)
    cache = {("example/plugins", DEV): {
        "dev_head": DEV, "content_tree": MAIN if case == "wrong-tree" else TREE,
    }}
    kwargs = dict(
        repository="example/plugins", tag_commit="prior", current_commit="current",
        current_index={"dev_head": DEV}, repo=tmp_path, cache=cache,
    )
    if case == "valid":
        aliases.validate_prior_source_alias(**kwargs)
        assert cache[("example/plugins", DEV)]["content_tree"] == TREE
    else:
        with pytest.raises(reuse.PromotionArtifactError, match="history|composition"):
            aliases.validate_prior_source_alias(**kwargs)
    assert len(projections) == (4 if case in {"valid", "wrong-tree"} else 0)


@pytest.mark.parametrize("case", ["valid", "digest", "oversize"])
def test_frozen_artifact_download_checks_bytes_before_writing(tmp_path, monkeypatch, case):
    content = b"frozen source artifact"
    calls = []

    def gh(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, content, b"")

    monkeypatch.setattr(reuse.publisher, "_gh", gh)
    asset = {
        "id": 7, "size": len(content),
        "digest": "sha256:" + hashlib.sha256(content if case != "digest" else b"other").hexdigest(),
    }
    kwargs = dict(
        repository="example/plugins", tag="python-artifacts-fixture",
        name="frozen.zip", asset=asset, limit=1 if case == "oversize" else 100,
    )
    if case == "valid":
        reuse._download(tmp_path, **kwargs)
        assert (tmp_path / "frozen.zip").read_bytes() == content
        assert calls == [([
            "api", "repos/example/plugins/releases/assets/7",
            "-H", "Accept: application/octet-stream",
        ], {"verification": True, "binary": True})]
    else:
        with pytest.raises(reuse.PromotionArtifactError, match="digest mismatch|size limit"):
            reuse._download(tmp_path, **kwargs)
        assert not (tmp_path / "frozen.zip").exists()
        assert len(calls) == (0 if case == "oversize" else 1)


@pytest.mark.parametrize("case", ["absent", "bad-repository", "bad-dev"])
def test_only_genuine_source_release_absence_allows_build(tmp_path, monkeypatch, case):
    reads = []
    monkeypatch.setattr(
        reuse.readiness, "_read_release",
        lambda repository, tag: reads.append((repository, tag)),
    )
    kwargs = dict(
        repository="../plugins" if case == "bad-repository" else "example/plugins",
        dev_head="short" if case == "bad-dev" else DEV,
        main_before=MAIN, content_tree=TREE, candidate_root=tmp_path,
        repo=tmp_path, source_policy="governed", min_age_days=None,
    )
    if case == "absent":
        assert reuse.reuse_artifacts(tmp_path, **kwargs) is None
        assert reads == [("example/plugins", f"python-artifacts-{DEV}")]
    else:
        with pytest.raises(reuse.PromotionArtifactError, match="reuse identity"):
            reuse.reuse_artifacts(tmp_path, **kwargs)
        assert reads == []


@pytest.mark.parametrize("case", ["valid", "wrong-tree", "foreign-main", "extra-bundle", "misbound-tag"])
def test_frozen_source_index_requires_exact_composition_and_original_main_admission(
    tmp_path, monkeypatch, case,
):
    filename = "example-1.0-" + "b" * 64 + ".zip"
    index = {
        "schema": reuse.SCHEMA, "schema_version": 2, "dev_head": DEV,
        "content_tree": MAIN if case == "wrong-tree" else TREE,
        "admission": {"signer_digest": MAIN, "source_digest": MAIN, "source_ref": "refs/heads/main"},
        "artifacts": [{"filename": filename, "sha256": "b" * 64}],
    }
    content = json.dumps(index).encode()
    inventory = [
        {"id": 1, "name": reuse.INDEX_NAME, "state": "uploaded", "size": len(content),
         "digest": "sha256:" + hashlib.sha256(content).hexdigest()},
        {"id": 2, "name": filename, "state": "uploaded", "size": 100, "digest": "sha256:" + "b" * 64},
    ]
    if case == "extra-bundle":
        inventory.append({
            "id": 3, "name": "other-1.0-" + "e" * 64 + ".zip",
            "state": "uploaded", "size": 100, "digest": "sha256:" + "e" * 64,
        })
    monkeypatch.setattr(reuse.readiness, "_read_release", lambda *a: {"id": 10})
    monkeypatch.setattr(reuse.publisher, "_tag_commit", lambda *a: MAIN if case == "misbound-tag" else DEV)
    monkeypatch.setattr(promotion, "_is_ancestor", lambda *a, **kw: case != "foreign-main")
    verified = []

    def gh(argv, **kwargs):
        if argv[:4] == ["api", "--paginate", "--slurp", "repos/example/plugins/releases/10/assets?per_page=100"]:
            assert kwargs == {"verification": True}
            return subprocess.CompletedProcess(argv, 0, json.dumps([inventory]), "")
        if argv[:2] == ["api", "repos/example/plugins/releases/assets/1"]:
            assert kwargs == {"verification": True, "binary": True}
            return subprocess.CompletedProcess(argv, 0, content, b"")
        assert argv == [
            "attestation", "verify", str(tmp_path / reuse.INDEX_NAME), "--repo", "example/plugins",
            "--signer-workflow", "example/plugins/.github/workflows/validate-and-promote.yml",
            "--signer-digest", MAIN, "--source-digest", MAIN,
            "--source-ref", "refs/heads/main", "--deny-self-hosted-runners",
        ]
        assert kwargs == {"verification": True}
        assert Path(argv[2]).read_bytes() == content
        verified.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(reuse.publisher, "_gh", gh)
    kwargs = dict(
        repository="example/plugins", dev_head=DEV, main_before=MAIN,
        content_tree=TREE, repo=tmp_path,
    )
    if case == "valid":
        admitted, admitted_assets = reuse.read_admitted_source_index(tmp_path, **kwargs)
        assert admitted == index
        assert set(admitted_assets) == {reuse.INDEX_NAME, filename}
        assert len(verified) == 1
        assert not (tmp_path / filename).exists()
    else:
        with pytest.raises(
            reuse.PromotionArtifactError,
            match="composition|trusted Main history|unexpected files|tag is misbound",
        ):
            reuse.read_admitted_source_index(tmp_path, **kwargs)
        assert len(verified) == (1 if case == "extra-bundle" else 0)


@pytest.mark.parametrize("case", ["plugin", "standalone", "unavailable", "age-with-governed"])
def test_builder_cli_selects_builder_and_refuses_invalid_policy(tmp_path, monkeypatch, capsys, case):
    argv = ["builder", "fixture", "--out-dir", str(tmp_path)]
    if case in {"standalone", "unavailable"}:
        argv.append("--standalone")
    if case == "age-with-governed":
        argv += ["--toolchain-min-age-days", "7"]
    monkeypatch.setattr(sys, "argv", argv)
    calls = []

    def builder(name, **kwargs):
        calls.append((name, kwargs))
        return {"kind": "plugin"}

    def standalone_builder(name, **kwargs):
        calls.append((name, kwargs))
        return {"kind": "standalone"}

    if case in {"unavailable", "age-with-governed"}:
        with pytest.raises(SystemExit) as error:
            cli.main(builder, "fixture")
        assert error.value.code == 2
        assert calls == []
        assert ("unavailable" if case == "unavailable" else "requires") in capsys.readouterr().err
    else:
        assert cli.main(builder, "fixture", standalone_builder=standalone_builder) == 0
        assert json.loads(capsys.readouterr().out) == {"kind": case}
        assert calls == [("fixture", {"out_dir": tmp_path, "python": None, "toolchain": None})]


@pytest.mark.parametrize("case", ["valid", "missing-editable", "wrong-canonical"])
def test_uv_materialization_copies_nested_closure_or_refuses_before_copy(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source, target = canonical / "plugins/example", destination / "plugins/example"
    raw = "../../plugins/core" if case == "wrong-canonical" else "../../libs/core"
    manifest = (
        '[project]\nname="example"\nversion="1.0"\n[tool.uv.sources]\n'
        f'core = {{ path = "{raw}"'
        + ("" if case == "missing-editable" else ", editable = true") + " }\n"
    )
    _write(source / "pyproject.toml", manifest)
    _write(target / "pyproject.toml", manifest)
    _write(canonical / "libs/core/pyproject.toml",
           '[project]\nname="core"\nversion="1.0"\n[tool.uv.sources]\n'
           'leaf = { editable = true, path = "../leaf" }\n')
    _write(canonical / "libs/core/src/core/__init__.py", "VALUE = 1\n")
    _write(canonical / "libs/leaf/pyproject.toml", '[project]\nname="leaf"\nversion="1.0"\n')
    _write(canonical / "libs/leaf/src/leaf/__init__.py", "VALUE = 2\n")
    log = materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=source, dest_consumer_dir=target,
        canonical_root=canonical, dest_root=destination,
    )
    if case == "valid":
        assert len(log) == 2 and all(line.startswith("OK") for line in log)
        assert editable.find_uv_editable_refs(target) == []
        assert 'core = { path = "libs/core" }' in (target / "pyproject.toml").read_text()
        assert 'leaf = { path = "../leaf" }' in (target / "libs/core/pyproject.toml").read_text()
        assert (target / "libs/leaf/src/leaf/__init__.py").read_text() == "VALUE = 2\n"
    else:
        assert len(log) == 1 and log[0].startswith("SKIP")
        assert ("missing editable" if case == "missing-editable" else "not canonical_root") in log[0]
        assert not (target / "libs").exists()
        assert (target / "pyproject.toml").read_text() == manifest


@pytest.mark.parametrize("case", ["matching", "conflicting"])
def test_nested_existing_library_requires_complete_canonical_match(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    parent = destination / "plugins/example/libs/core"
    manifest = '[tool.uv.sources]\nleaf = { path = "../leaf", editable = true }\n'
    _write(parent / "pyproject.toml", manifest)
    for root in (canonical / "libs/leaf", parent.parent / "leaf"):
        _write(root / "pyproject.toml", '[project]\nname="leaf"\nversion="1.0"\n')
        _write(root / "src/leaf.py", "VALUE = 1\n")
    if case == "conflicting":
        _write(parent.parent / "leaf/src/leaf.py", "VALUE = 9\n")
    log, copied = nested.materialize_nested_uv_editable_refs(
        parent, canonical_root=canonical, dest_root=destination,
    )
    assert len(log) == 1
    if case == "matching":
        assert copied == {"leaf"} and log[0].startswith("OK")
        assert 'leaf = { path = "../leaf" }' in (parent / "pyproject.toml").read_text()
    else:
        assert copied == set() and "does not match canonical" in log[0]
        assert (parent / "pyproject.toml").read_text() == manifest
        assert (parent.parent / "leaf/src/leaf.py").read_text() == "VALUE = 9\n"


@pytest.mark.parametrize("case", ["fresh", "identical", "conflicting", "unsafe-name"])
def test_packaged_assets_materialize_only_safe_nonconflicting_bytes(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source, target = canonical / "plugins/example", destination / "plugins/example"
    target.mkdir(parents=True)
    declaration = {
        "schema": "copilot-extensions.launch-wrapper-assets", "version": 1,
        "canonicalDir": "wrapper/bin", "files": ["../escape" if case == "unsafe-name" else "launch.sh"],
    }
    _write(source / assets.MANIFEST_NAME, json.dumps(declaration))
    _write(canonical / "wrapper/bin/launch.sh", "#!/bin/sh\nexit 0\n")
    output = target / "bin/launch.sh"
    if case in {"identical", "conflicting"}:
        _write(output, "foreign\n" if case == "conflicting" else "#!/bin/sh\nexit 0\n")
    log = wrappers.materialize_into(
        source_consumer_dir=source, dest_consumer_dir=target,
        canonical_root=canonical, dest_root=destination,
        manifest_name=assets.MANIFEST_NAME, reuse_existing=True,
    )
    if case == "fresh":
        assert len(log) == 1 and log[0].startswith("OK")
        assert output.read_bytes() == (canonical / "wrapper/bin/launch.sh").read_bytes()
    elif case == "identical":
        assert log == [] and output.read_text() == "#!/bin/sh\nexit 0\n"
    else:
        assert len(log) == 1 and log[0].startswith("SKIP")
        assert ("basename-only" if case == "unsafe-name" else "already exists") in log[0]
        if case == "conflicting":
            assert output.read_text() == "foreign\n"
        else:
            assert not output.exists()


@pytest.mark.parametrize("ext", ["ps1", "sh"])
@pytest.mark.parametrize("case", ["valid", "duplicate"])
def test_installer_reference_materialization_preserves_comments_and_refuses_ambiguity(tmp_path, ext, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source, target = canonical / "plugins/example", destination / "plugins/example"
    text = "  " + engine.canonical_line(ext) + " # retain\n"
    if case == "duplicate":
        text += engine.canonical_line(ext) + "\n"
    _write(source / f"scripts/install.{ext}", text)
    _write(target / f"scripts/install.{ext}", text)
    _write(canonical / f"libs/installer-engine/installer-engine.{ext}", "# fixture engine\n")
    log = installers.materialize_ref_into(
        source_consumer_dir=source, dest_consumer_dir=target,
        canonical_root=canonical, dest_root=destination,
    )
    output = target / f"scripts/installer-engine.{ext}"
    assert len(log) == 1
    if case == "valid":
        assert log[0].startswith("OK") and output.read_text() == "# fixture engine\n"
        script = target / f"scripts/install.{ext}"
        assert script.read_text() == "  " + engine.local_line(ext) + " # retain\n"
        assert engine.is_local_ref(engine.find_engine_ref(script, ext), target)
    else:
        assert "expected exactly one" in log[0] and not output.exists()
        assert (target / f"scripts/install.{ext}").read_text() == text


@pytest.mark.parametrize("case", ["valid", "wrong-name", "conditional", "escape"])
def test_standalone_dependency_source_follows_exact_declared_identity(tmp_path, case):
    project = tmp_path / "services/example"
    path = "../../../outside" if case == "escape" else "../../libs/selected"
    entry = f'{{ path = "{path}", editable = true'
    entry += ', marker = "sys_platform == \'linux\'"' if case == "conditional" else ""
    _write(project / "pyproject.toml", '[tool.uv.sources]\nExample_Lib = ' + entry + " }\n")
    selected = tmp_path / "libs/selected"
    _write(selected / "pyproject.toml",
           '[project]\nname="' + ("different" if case == "wrong-name" else "example-lib") + '"\n')
    if case == "valid":
        assert standalone.local_dependency_source(
            project, "example.lib", source_root=tmp_path,
        ) == selected.resolve()
    else:
        with pytest.raises(ValueError, match="invalid local source mapping"):
            standalone.local_dependency_source(project, "example.lib", source_root=tmp_path)


@pytest.mark.parametrize("case", ["valid", "fresh-file", "yanked-file", "missing-hash"])
def test_public_release_selection_ages_every_file_and_binds_hashes(case):
    def file(digest, timestamp="2026-01-01T00:00:00Z"):
        return {"yanked": False, "upload_time_iso_8601": timestamp, "digests": {"sha256": digest}}

    releases = {
        "1.0": [file("b" * 64)],
        "1.0.post1": [file("A" * 64), file("a" * 64)],
        "2.0rc1": [file("c" * 64)],
    }
    latest = releases["1.0.post1"][1]
    if case == "fresh-file":
        latest["upload_time_iso_8601"] = "2026-01-31T00:00:00Z"
    elif case == "yanked-file":
        latest["yanked"] = True
    elif case == "missing-hash":
        latest["digests"] = {}
    urls = []

    def fetch(url):
        urls.append(url)
        return json.dumps({"releases": releases}).encode()

    kwargs = dict(min_age_days=7, now=datetime(2026, 2, 1, tzinfo=timezone.utc), fetch=fetch)
    if case == "missing-hash":
        with pytest.raises(seasoned.SeasonedVersionError, match="file SHA-256"):
            seasoned.resolve_seasoned_public_release("Example_Pkg", **kwargs)
    else:
        result = seasoned.resolve_seasoned_public_release("Example_Pkg", **kwargs)
        assert (result.version, result.hashes) == (
            ("1.0.post1", ("a" * 64,)) if case == "valid" else ("1.0", ("b" * 64,))
        )
    assert urls == ["https://pypi.org/pypi/example-pkg/json"]


@pytest.mark.parametrize("case", ["valid", "duplicate-hash", "wrong-pin", "boolean-age"])
def test_public_toolchain_requires_canonical_hash_and_pin_provenance(tmp_path, case):
    source = public.PublicToolchainSource(7, {"wheel": "1.0"}, {"wheel": ("a" * 64,)})
    provenance = source.provenance | {"python": "3.11.9"}
    if case == "duplicate-hash":
        provenance["file_hashes"]["wheel"] *= 2
    elif case == "wrong-pin":
        provenance["packages"]["wheel"] = "2.0"
    elif case == "boolean-age":
        provenance["min_age_days"] = True
    if case == "valid":
        public.validate_lock_provenance(provenance, {"wheel": "1.0"})
        source.verify({"wheel": "1.0"})
        args = source.install_args(tmp_path)
        assert args == [
            "--require-hashes", "--requirements", str(tmp_path / "public-toolchain-requirements.txt"),
        ]
        assert Path(args[-1]).read_text() == "wheel==1.0 --hash=sha256:" + "a" * 64 + "\n"
        with pytest.raises(trust.ArtifactBuildError, match="installed versions"):
            source.verify({"wheel": "2.0"})
    else:
        with pytest.raises(trust.ArtifactBuildError, match="provenance"):
            public.validate_lock_provenance(provenance, {"wheel": "1.0"})


@pytest.mark.parametrize("case", ["trusted", "plaintext", "public", "untrusted", "no-allowlist"])
def test_governed_feed_requires_explicit_https_nonpublic_allowlist(case):
    url = {
        "plaintext": "http://feed.example/simple", "public": "https://pypi.org/simple",
        "untrusted": "https://other.example/simple",
    }.get(case, "https://feed.example/simple")
    env = {"UV_DEFAULT_INDEX": url, "UV_INDEX_URL": "https://lower.example/simple"}
    if case != "no-allowlist":
        env["BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS"] = "FEED.EXAMPLE, pypi.org"
    assert trust._validated_trusted_index_url(env) == ((url, None) if case == "trusted" else None)


@pytest.mark.parametrize("case", ["valid", "unpaired", "invalid-type", "empty"])
def test_changefile_add_writes_paired_intent_only(tmp_path, monkeypatch, capsys, case):
    monkeypatch.setattr(changefile, "CHANGEFILES_DIR", tmp_path / ".changefiles")
    args = argparse.Namespace(
        plugin=[] if case == "empty" else ["example", "other"],
        type=[] if case == "empty" else ["patch", "dev"],
        comment="Preserve paired release intent",
    )
    if case == "unpaired":
        args.type.pop()
    elif case == "invalid-type":
        args.type[0] = "unknown"
    assert changefile.cmd_add(args) == (0 if case == "valid" else 1)
    output = capsys.readouterr()
    if case == "valid":
        pending = changefile.read_changefiles()
        assert len(pending) == 1
        assert pending[0][1] == {
            "comment": args.comment,
            "changes": [{"plugin": "example", "type": "patch"}, {"plugin": "other", "type": "dev"}],
        }
        assert "Wrote" in output.out
    else:
        assert output.err and not (tmp_path / ".changefiles").exists()


def _release_profile():
    return {
        "schema": PROFILE_SCHEMA, "version": 1, "component": "engine", "backend": "cpu",
        "release": {"identity": "engine:1.0", "sha256": "sha256:" + "2" * 64},
        "mode": "artifact", "approval_source": profiles.APPROVAL_SOURCE,
        "artifact": {"artifact_id": "sha256:" + "3" * 64, "manifest_sha256": "sha256:" + "4" * 64},
        "acquisition_policy": "governed-source-policy",
        "target": {
            "device": "cpu", "tags": ["py3-none-any"],
            "environment": {
                "implementation_name": "cpython", "implementation_version": "3.11.9",
                "os_name": "nt", "platform_machine": "AMD64", "platform_release": "11",
                "platform_system": "Windows", "platform_version": "10.0.1",
                "python_full_version": "3.11.9", "platform_python_implementation": "CPython",
                "python_version": "3.11", "sys_platform": "win32",
            },
        },
        "roots": ["engine==1.0"],
        "wheels": [{
            "name": "engine", "version": "1.0", "filename": "engine-1.0-py3-none-any.whl",
            "sha256": "sha256:" + "1" * 64, "requires_python": ">=3.11",
            "provides_extras": [], "selected_extras": [], "requires_dist": [],
        }],
    }


@pytest.mark.parametrize("case", ["valid", "foreign-artifact", "self-approval", "duplicate"])
def test_release_profiles_bind_whole_records_to_promotion_channel(case):
    record = _release_profile()
    identity = record["artifact"]["artifact_id"]
    index = {
        "artifacts": [{"artifact_id": identity}],
        "runtime_profiles": {"schema": profiles.SCHEMA, "version": 1, "profiles": [record]},
    }
    if case == "foreign-artifact":
        index["artifacts"][0]["artifact_id"] = "sha256:" + "9" * 64
    elif case == "self-approval":
        record["approval_source"] = "self"
    elif case == "duplicate":
        index["runtime_profiles"]["profiles"].append(record)
    if case == "valid":
        selected, = profiles.release_profiles(index)
        assert selected.record() == record
        record["backend"] = "cuda12"
        assert selected.record()["backend"] == "cpu"
    else:
        with pytest.raises(ProfileError, match="promotion artifact channel|duplicate release"):
            profiles.release_profiles(index)
