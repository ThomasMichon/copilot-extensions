from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "sync-installer-engine.py"

_SPEC = importlib.util.spec_from_file_location("sync_installer_engine", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
sync_installer_engine = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sync_installer_engine)


def _human_admission():
    path = REPO / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "installer_capabilities.py"
    spec = importlib.util.spec_from_file_location("native_human_capability", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.powershell_human_utf8_supported


def test_human_output_declarations_bind_real_packaged_entrypoints():
    contract = sync_installer_engine.ier
    source = REPO / "libs" / "installer-engine" / contract.HUMAN_HELPER
    supports = _human_admission()
    for name in contract.human_output_adopters(REPO, contract.ADOPTERS):
        script = REPO / "plugins" / name / "scripts" / "install.ps1"
        assert (script.parent / contract.HUMAN_HELPER).read_bytes() == source.read_bytes()
        assert supports(script), name
        declaration = json.loads(script.with_suffix(".human-output.json").read_text("utf-8"))
        assert declaration == contract.human_output_declaration(script)


def test_native_watchdog_is_packaged_before_every_staging_path():
    contract = sync_installer_engine.ier
    names = contract.watchdog_adopters(REPO)
    assert len(names) == 13
    daemon_plugins = {
        "agent-bridge", "agent-codespaces", "agent-dispatch", "agent-index",
        "agent-mcp", "agent-vault", "agent-worktrees",
    }
    for name in names:
        scripts = REPO / "plugins" / name / "scripts"
        for asset in contract.WATCHDOG_FILES:
            assert (scripts / asset).read_bytes() == (
                REPO / "libs" / "installer-engine" / asset
            ).read_bytes()
        for extension in ("ps1", "sh"):
            entries = [
                path for stem in ("install", "init")
                if (path := scripts / f"{stem}.{extension}").is_file()
                and "# === install-contract:v5 self-stage" in path.read_text(encoding="utf-8")
            ]
            assert len(entries) == 1
            text = entries[0].read_text(encoding="utf-8")
            staging = text.index("# === install-contract:v5 self-stage")
            assert text.index(f"installer-watchdog.{extension}") < staging
            if name == "agent-worktrees":
                assert text.index(f"installer-watchdog.{extension}") < text.index(
                    "# The standard self-stage block"
                )
            if name in daemon_plugins:
                assert ("Enter-InstallPhase -Name cutover" if extension == "ps1" else
                        "enter_install_phase cutover") in text


def test_materialized_human_declaration_binds_rewritten_entrypoint(tmp_path):
    import materialize_installer_engine

    contract = sync_installer_engine.ier
    source = REPO / "plugins" / "agent-bridge"
    destination = tmp_path / "payload"
    scripts = destination / "scripts"
    scripts.mkdir(parents=True)
    for name in ("install.ps1", "install.sh", contract.HUMAN_HELPER, "install.human-output.json"):
        shutil.copyfile(source / "scripts" / name, scripts / name)
    before = (scripts / "install.ps1").read_bytes()
    log = materialize_installer_engine.materialize_ref_into(
        source_consumer_dir=source, dest_consumer_dir=destination,
        canonical_root=REPO, dest_root=tmp_path,
    )
    assert any(line.startswith("OK") for line in log)
    assert (scripts / "install.ps1").read_bytes() != before
    assert (scripts / "installer-engine.ps1").is_file()
    assert _human_admission()(scripts / "install.ps1")
    with (scripts / contract.HUMAN_HELPER).open("a", encoding="ascii") as stream:
        stream.write("# drift\n")
    assert not _human_admission()(scripts / "install.ps1")
    with pytest.raises(ValueError, match="conflicting native human-output assets"):
        materialize_installer_engine.materialize_ref_into(
            source_consumer_dir=source, dest_consumer_dir=destination,
            canonical_root=REPO, dest_root=tmp_path,
        )


@pytest.mark.parametrize("in_place", [False, True])
def test_whole_payload_human_contract(tmp_path, monkeypatch, in_place):
    import materialize_main

    contract = sync_installer_engine.ier
    names = contract.human_output_adopters(REPO, contract.ADOPTERS)
    monkeypatch.chdir(tmp_path)
    source_root = Path("source")
    destination = Path("payload")
    for name in names:
        source = REPO / "plugins" / name
        consumer = source_root / "plugins" / name
        scripts = consumer / "scripts"
        scripts.mkdir(parents=True)
        # The complete native producer artifact, not unrelated Python sources
        # or protocol-test corpora. All materializer owners still run below.
        for asset in ("install.ps1", "install.sh", contract.HUMAN_HELPER,
                      "install.human-output.json"):
            shutil.copyfile(source / "scripts" / asset, scripts / asset)
        (consumer / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.0.0"\n', encoding="utf-8",
        )
        for manifest in ("launch-wrapper-assets.json", "packaged-assets.json"):
            if (source / manifest).is_file():
                shutil.copyfile(source / manifest, consumer / manifest)
                data = json.loads((source / manifest).read_text(encoding="utf-8"))
                for asset in data["files"]:
                    origin = REPO / data["canonicalDir"] / asset
                    assert origin.resolve().is_relative_to(REPO.resolve())
                    target = source_root / data["canonicalDir"] / asset
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(origin, target)
    canonical = source_root / "libs" / "installer-engine"
    canonical.mkdir(parents=True, exist_ok=True)
    for asset in (*contract.FILES, *contract.support_files(REPO / "libs" / "installer-engine")):
        shutil.copyfile(REPO / "libs" / "installer-engine" / asset, canonical / asset)
    original = materialize_main.materialize_launch_wrapper_assets

    def later_owner(dest, *, canonical_root):
        result = original(dest, canonical_root=canonical_root)
        # Exercise ordering after the real final asset materializer, including
        # the engine's own installer (which has no installer-engine reference).
        script = dest / "plugins" / "agent-worktrees" / "scripts" / "install.ps1"
        with script.open("a", encoding="ascii") as stream:
            stream.write("\n# later owning materializer\n")
        return result

    monkeypatch.setattr(materialize_main, "materialize_launch_wrapper_assets", later_owner)
    if in_place:
        shutil.copytree(source_root, destination)
        report = materialize_main.materialize(destination, canonical_root=destination)
    else:
        report = materialize_main.build(destination, source_root=source_root)
    assert not [line for line in report if line.startswith("SKIP")], report
    supports = _human_admission()
    assert len(names) == 9
    for name in names:
        script = destination / "plugins" / name / "scripts" / "install.ps1"
        assert supports(script), name
        assert (script.parent / contract.HUMAN_HELPER).read_bytes() == (
            REPO / "libs" / "installer-engine" / contract.HUMAN_HELPER
        ).read_bytes()


@pytest.mark.parametrize("asset", [
    "install.ps1", "install.human-output.json", "installer-human-output.ps1",
])
def test_same_root_invalid_human_source_fails_before_any_rewrite(tmp_path, monkeypatch, asset):
    import materialize_main

    root = tmp_path / "source"
    source = REPO / "plugins" / "agent-bridge" / "scripts"
    scripts = root / "plugins" / "agent-bridge" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("install.ps1", "install.human-output.json", "installer-human-output.ps1"):
        shutil.copyfile(source / name, scripts / name)
    canonical = root / "libs" / "installer-engine"
    canonical.mkdir(parents=True)
    shutil.copyfile(source / "installer-human-output.ps1", canonical / "installer-human-output.ps1")
    with (scripts / asset).open("a", encoding="ascii") as stream:
        stream.write("\n# invalid original source\n")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    monkeypatch.setattr(
        materialize_main, "materialize_file_pointers",
        lambda *args, **kwargs: pytest.fail("rewrites must not precede source validation"),
    )
    with pytest.raises(ValueError, match="conflicting native human-output assets"):
        materialize_main.materialize(root, canonical_root=root)
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    canonical_dir = tmp_path / "libs" / "installer-engine"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (canonical_dir / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    monkeypatch.setattr(sync_installer_engine, "REPO", tmp_path)
    monkeypatch.setattr(sync_installer_engine, "CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(sync_installer_engine, "ADOPTERS", ("agent-registered",))
    return tmp_path


def _mk_plugin_scripts(repo: Path, name: str) -> Path:
    scripts = repo / "plugins" / name / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    return scripts


def _write_canonical_refs(scripts: Path) -> None:
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )


def _write_local_refs(scripts: Path) -> None:
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/installer-engine.sh"\n',
        encoding="utf-8",
    )


@pytest.mark.parametrize("asset", sync_installer_engine.ier.WATCHDOG_FILES)
def test_sync_native_watchdog_for_non_engine_adopters(fake_repo, asset):
    registered = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(registered)
    scripts = _mk_plugin_scripts(fake_repo, "agent-standalone")
    (scripts / "init.sh").write_text(
        "# === install-contract:v5 self-stage -- keep byte-identical across plugins ===\n",
        encoding="utf-8",
    )
    canonical = fake_repo / "libs" / "installer-engine"
    for name in sync_installer_engine.ier.WATCHDOG_FILES:
        (canonical / name).write_text(f"canonical {name}\n", encoding="utf-8")
    assert any("native watchdog differs" in p for p in sync_installer_engine.verify())
    sync_installer_engine.sync()
    assert sync_installer_engine.verify() == []
    (scripts / asset).write_text("drift\n", encoding="utf-8")
    assert any("native watchdog differs" in p for p in sync_installer_engine.verify())
    sync_installer_engine.sync()
    assert (scripts / asset).read_bytes() == (canonical / asset).read_bytes()


def test_sync_refuses_indirected_watchdog_destination(fake_repo, monkeypatch):
    registered = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(registered)
    with (registered / "install.sh").open("a", encoding="utf-8") as stream:
        stream.write("# === install-contract:v5 self-stage -- keep byte-identical across plugins ===\n")
    canonical = fake_repo / "libs" / "installer-engine"
    for name in sync_installer_engine.ier.WATCHDOG_FILES:
        (canonical / name).write_text("canonical\n", encoding="utf-8")
    destination = registered / sync_installer_engine.ier.WATCHDOG_FILES[0]
    original = sync_installer_engine.uer._find_symlinked_ancestor
    monkeypatch.setattr(
        sync_installer_engine.uer, "_find_symlinked_ancestor",
        lambda path, root: destination if path == destination else original(path, root),
    )
    assert any("linked watchdog asset refused" in p for p in sync_installer_engine.verify())
    with pytest.raises(RuntimeError, match="linked watchdog asset refused"):
        sync_installer_engine.sync()
    assert not destination.exists()


def test_verify_passes_when_adopter_is_byte_identical(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    assert sync_installer_engine.verify() == []


def test_verify_accepts_a_canonical_reference_with_no_local_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)

    assert sync_installer_engine.verify() == []


def test_verify_flags_drifted_registered_adopter(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("drifted content\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("differs from" in p and "installer-engine.ps1" in p for p in problems)


def test_verify_flags_missing_registered_adopter(fake_repo):
    # agent-registered is declared in ADOPTERS but never vendored the files.
    assert any("is missing" in p for p in sync_installer_engine.verify())


def test_verify_flags_unregistered_adopter_with_a_stray_copy(fake_repo):
    # The registered adopter is in sync...
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    # ...but a second plugin has its own copy without being added to ADOPTERS.
    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    (stray_scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_flags_unregistered_adopter_with_local_ref_and_no_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    _write_local_refs(stray_scripts)

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_flags_unregistered_adopter_with_trailing_comment_ref_and_no_copy(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    stray_scripts = _mk_plugin_scripts(fake_repo, "agent-unregistered")
    (stray_scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/installer-engine.sh" # load helpers\n',
        encoding="utf-8",
    )
    (stray_scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1') # load helpers\n",
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any(
        "agent-unregistered" in p and "not listed in ADOPTERS" in p for p in problems
    )


def test_verify_ignores_plugin_with_no_installer_engine_file(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    _mk_plugin_scripts(fake_repo, "agent-unrelated")

    assert sync_installer_engine.verify() == []


def test_verify_flags_an_escaping_noncanonical_reference(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../outside/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    outside = fake_repo / "outside"
    outside.mkdir()
    (outside / "installer-engine.sh").write_text("nope\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("which is not libs/installer-engine/installer-engine.sh" in p for p in problems)


def test_verify_rejects_malformed_local_reference_even_when_copies_exist(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'missing/installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/missing/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("neither" in p and "installer-engine.sh" in p for p in problems)
    assert any("neither" in p and "installer-engine.ps1" in p for p in problems)


def test_verify_rejects_normalized_local_reference_with_missing_intermediate(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/missing/../installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'missing\\..\\installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("neither" in p and "missing/../installer-engine.sh" in p for p in problems)
    assert any("neither" in p and "missing\\..\\installer-engine.ps1" in p for p in problems)


def test_verify_rejects_mismatched_powershell_quotes(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1\")\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any("does not source installer-engine" in p for p in problems)


def test_verify_rejects_mixed_local_and_canonical_forms(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')\n",
        encoding="utf-8",
    )
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")

    problems = sync_installer_engine.verify()

    assert any("mixes installer-engine forms" in p for p in problems)


def test_verify_rejects_symlinked_canonical_engine(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        (fake_repo / "libs" / "installer-engine" / name).symlink_to(real / name)

    problems = sync_installer_engine.verify()

    assert any("is a symlink -- refusing" in p for p in problems)


def test_verify_rejects_symlinked_canonical_engine_in_local_mode(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        (fake_repo / "libs" / "installer-engine" / name).symlink_to(real / name)

    problems = sync_installer_engine.verify()

    assert any("is a symlink -- refusing" in p for p in problems)


def test_verify_flags_duplicate_engine_source_lines(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    (scripts / "install.sh").write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )

    problems = sync_installer_engine.verify()

    assert any("contains 2 installer-engine source lines" in p for p in problems)


def test_sync_repairs_missing_local_copy_when_wrappers_are_local(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)

    written = sync_installer_engine.sync()

    assert "plugins/agent-registered/scripts/installer-engine.ps1" in written
    assert "plugins/agent-registered/scripts/installer-engine.sh" in written
    assert (scripts / "installer-engine.ps1").read_text(encoding="utf-8") == "canonical ps1\n"
    assert (scripts / "installer-engine.sh").read_text(encoding="utf-8") == "canonical sh\n"


def test_sync_removes_stale_local_copy_when_wrappers_are_canonical(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_canonical_refs(scripts)
    (scripts / "installer-engine.ps1").write_text("stale\n", encoding="utf-8")
    (scripts / "installer-engine.sh").write_text("stale\n", encoding="utf-8")

    written = sync_installer_engine.sync()

    assert "removed plugins/agent-registered/scripts/installer-engine.ps1" in written
    assert "removed plugins/agent-registered/scripts/installer-engine.sh" in written
    assert not (scripts / "installer-engine.ps1").exists()
    assert not (scripts / "installer-engine.sh").exists()


@pytest.mark.parametrize("mode", ["canonical", "local"])
@pytest.mark.parametrize("initial", ["missing", "stale"])
def test_sync_repairs_python_support_in_both_wrapper_modes(fake_repo, mode, initial):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    writer = _write_canonical_refs if mode == "canonical" else _write_local_refs
    writer(scripts)
    canonical = fake_repo / "libs" / "installer-engine"
    with (canonical / "installer-engine.sh").open("a", encoding="utf-8") as stream:
        stream.write("# installer_dependency_policy.py\n")
    for name in sync_installer_engine.ier.SUPPORT_FILES:
        source = canonical / name
        source.write_text(f"# canonical {name}\n", encoding="utf-8")
        source.chmod(0o700)
        if initial == "stale":
            (scripts / name).write_text("# stale support\n", encoding="utf-8")
    if mode == "canonical" and initial == "missing":
        assert sync_installer_engine.verify() == []
    else:
        assert sync_installer_engine.verify()
    written = sync_installer_engine.sync()
    for name in sync_installer_engine.ier.SUPPORT_FILES:
        source = canonical / name
        destination = scripts / name
        assert f"plugins/agent-registered/scripts/{name}" in written
        assert destination.read_bytes() == source.read_bytes()
        if os.name != "nt":
            assert stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
    for name in sync_installer_engine.FILES:
        assert (scripts / name).exists() == (mode == "local")
    assert sync_installer_engine.verify() == []
    assert sync_installer_engine.sync() == []


def test_sync_refuses_symlinked_canonical_engine_in_local_mode(fake_repo):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    _write_local_refs(scripts)
    real = fake_repo / "outside"
    real.mkdir()
    (real / "installer-engine.ps1").write_text("canonical ps1\n", encoding="utf-8")
    (real / "installer-engine.sh").write_text("canonical sh\n", encoding="utf-8")
    for name in ("installer-engine.ps1", "installer-engine.sh"):
        (fake_repo / "libs" / "installer-engine" / name).unlink()
        (fake_repo / "libs" / "installer-engine" / name).symlink_to(real / name)

    with pytest.raises(RuntimeError, match="symlink -- refusing"):
        sync_installer_engine.sync()


@pytest.mark.parametrize("mode", ["canonical", "local"])
@pytest.mark.parametrize("name", sync_installer_engine.ier.SUPPORT_FILES)
@pytest.mark.parametrize("target_state", ["different", "matching", "dangling"])
def test_sync_refuses_linked_support_without_touching_target(fake_repo, mode, name, target_state):
    scripts = _mk_plugin_scripts(fake_repo, "agent-registered")
    writer = _write_canonical_refs if mode == "canonical" else _write_local_refs
    writer(scripts)
    canonical = fake_repo / "libs" / "installer-engine"
    with (canonical / "installer-engine.sh").open("a", encoding="utf-8") as stream:
        stream.write("# installer_dependency_policy.py\n")
    for support in sync_installer_engine.ier.SUPPORT_FILES:
        source = canonical / support
        source.write_text(f"# canonical {support}\n", encoding="utf-8")
        source.chmod(0o700)
    outside = fake_repo / "unrelated"
    outside.mkdir()
    target = outside / name
    if target_state != "dangling":
        target.write_bytes(
            (canonical / name).read_bytes() if target_state == "matching" else b"unrelated bytes\n"
        )
        target.chmod(0o600)
    destination = scripts / name
    destination.symlink_to(target)
    original_link = os.readlink(destination)
    before = (
        target.read_bytes(), stat.S_IMODE(target.stat().st_mode), target.stat().st_mtime_ns,
    ) if target.exists() else None

    assert sync_installer_engine.verify()
    with pytest.raises(RuntimeError, match="symlink -- refusing"):
        sync_installer_engine.sync()

    assert destination.is_symlink()
    assert os.readlink(destination) == original_link
    if before is None:
        assert not target.exists()
    else:
        assert (
            target.read_bytes(), stat.S_IMODE(target.stat().st_mode), target.stat().st_mtime_ns,
        ) == before
