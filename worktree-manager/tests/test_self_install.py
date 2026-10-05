"""Tests for the versioned self-install (Phase 2/3 — same convention as the core).

Asserts the shared versioning artifacts: a plain-text ``current-version`` marker,
an immutable ``versions/<ver>/`` slot, and a ``~/.local/bin`` binstub — plus
idempotent, version-gated behavior. No real venv is built (uv is not invoked);
the fast materialize+marker+binstub path is exercised against a synthetic payload
and a synthetic HOME/root.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _conftest_sandbox import _apply_sandbox

import worktree_manager.self_install as si
from worktree_manager.__main__ import main
from worktree_manager.self_install import (
    current_version,
    needs_install,
    payload_version,
    self_install,
    status,
    version_slot,
)


def _fake_payload(tmp: Path, version: str) -> Path:
    pd = tmp / "payload"
    pkg = pd / "src" / "worktree_manager"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (pkg / "__main__.py").write_text("")
    (pd / "pyproject.toml").write_text("[project]\nname='x'\n")
    return pd


def _patch_local_bin(monkeypatch, tmp: Path) -> Path:
    lb = tmp / ".local" / "bin"
    monkeypatch.setattr(si, "local_bin", lambda: lb)
    return lb


def _patch_provider_registry(monkeypatch, tmp: Path) -> Path:
    registry = tmp / ".agent-worktrees" / "control-plane-providers.d"
    monkeypatch.setattr(si, "control_plane_providers_dir", lambda: registry)
    return registry


def test_payload_version_reads_init(tmp_path):
    pd = _fake_payload(tmp_path, "9.9.9-dev1")
    assert payload_version(pd) == "9.9.9-dev1"


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    res = self_install(pd, root=root, dry_run=True)
    assert res.action == "planned"
    assert res.version == "1.2.3"
    assert current_version(root) is None
    assert not (root / "current-version").exists()


def test_apply_installs_marker_slot_and_binstub(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    registry = _patch_provider_registry(monkeypatch, tmp_path)
    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    # marker file (plain text, names the active version)
    assert (root / "current-version").read_text().strip() == "1.2.3"
    assert current_version(root) == "1.2.3"
    # immutable version slot with the payload copied in
    slot = version_slot("1.2.3", root)
    assert slot.is_dir()
    assert (slot / "src" / "worktree_manager" / "__init__.py").exists()
    # binstub deployed to ~/.local/bin
    stub = lb / "worktree-manager"
    assert stub.exists()
    body = stub.read_text()
    assert "current-version" in body and "worktree-manager" in body
    manifest = registry / "worktree-manager.json"
    payload = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert payload["provider"] == "worktree-manager"
    assert payload["command"] == [str((lb / si._primary_binstub_name()).resolve())]
    assert payload["provider_root"] == str(root.resolve())


def test_windows_binstubs_force_utf8_mode():
    """``worktree-manager.cmd``/``.ps1`` must set ``PYTHONUTF8=1`` so the
    launched interpreter's stdout/stderr are UTF-8 regardless of the active
    console codepage (#5218) -- a Windows console defaults to the system ANSI
    codepage (e.g. ``cp1252``), which crashes on a status glyph like
    ``\u2713``/``\u2192`` unless UTF-8 mode is forced before the interpreter
    starts.
    """
    assert 'set "PYTHONUTF8=1"' in si._cmd_binstub()
    assert "$env:PYTHONUTF8 = '1'" in si._ps1_binstub()


def test_apply_reaps_stranded_cutover_passive_before_copying_payload(tmp_path, monkeypatch):
    """A slot left occupied by an abandoned cutover passive (spawned by
    ``mux_daemon_cutover.spawn_passive`` with its ``cwd`` pinned inside the
    slot, then stranded when the orchestrator driving that cutover died
    before promoting or terminating it) must be reaped via the existing
    breadcrumb-driven recovery BEFORE ``_copy_payload`` tries to delete the
    slot -- otherwise a bare ``self-install --apply`` can hit a Windows
    ``PermissionError`` that only ``self_update()``'s own cutover path would
    have cleared (the gap this test guards against regressing)."""
    from zdd import breadcrumb

    import worktree_manager.mux_daemon_cutover as mdc

    pd = _fake_payload(tmp_path, "9.9.9")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    # Pre-create the slot, as a crashed prior self-update attempt would have
    # left it (payload already copied in, but never finalized/marked).
    slot = version_slot("9.9.9", root)
    slot.mkdir(parents=True)
    (slot / "stale-marker.txt").write_text("from an aborted attempt")

    # A non-terminal breadcrumb naming the stranded passive's pid -- exactly
    # what spawn_passive + CutoverOrchestrator leave behind when the
    # orchestrator dies before the passive is ever promoted or terminated.
    stranded_pid = 999999
    routing_dir = mdc.routing_dir(root)
    breadcrumb.write_breadcrumb(
        routing_dir, state="started", old=None, new_port=54321, new_pid=stranded_pid,
    )

    reaped: list[int] = []
    monkeypatch.setattr(mdc, "_iter_mux_daemon_pids", lambda: {stranded_pid})
    monkeypatch.setattr(
        mdc, "_terminate_mux_daemon_pid",
        lambda pid, *, root: (reaped.append(pid), True)[1],
    )

    res = self_install(pd, root=root, dry_run=False)

    assert res.action == "installed"
    assert reaped == [stranded_pid], "the stranded passive must be reaped before rmtree"
    assert not (slot / "stale-marker.txt").exists(), "the slot must be freshly recopied"
    assert (slot / "src" / "worktree_manager" / "__init__.py").exists()
    assert current_version(root) == "9.9.9"
    # The breadcrumb must be left ALONE (not cleared, not rewritten): it may
    # still name an "old" endpoint that a later, full activate_after_update()
    # -> recover_stale_cutover() needs to undrain. A later real cutover
    # re-reading this same file simply finds the reaped pid no longer alive
    # -- a clean no-op on its side -- so leaving it is always safe.
    record = breadcrumb.read_breadcrumb(routing_dir)
    assert record is not None
    assert record["new_pid"] == stranded_pid
    assert breadcrumb.is_stale(record)


def test_apply_rechecks_install_need_after_acquiring_the_lease(tmp_path, monkeypatch):
    """A concurrent self_install()/self_update() could finish installing (and
    even activating) this EXACT version while this call was waiting to
    acquire the cutover lease. Without a recheck under the lease, this call
    would blindly rmtree + recopy a slot that is now the live, already-active
    install -- racing whatever is currently running out of it. The
    lock-free pre-check only proves the version was needed at that point in
    time, not that it still is by the time the lease is actually held."""
    pd = _fake_payload(tmp_path, "7.8.9")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    real_needs_install = si.needs_install
    calls: list[bool] = []

    def _needs_install_then_satisfied(version_arg, root_arg):
        result = real_needs_install(version_arg, root_arg)
        calls.append(result)
        if len(calls) == 1:
            return True  # the lock-free pre-check: install still needed
        # Simulate a concurrent self_install() having finished installing
        # (and activating) this exact version while we waited for the lease.
        slot = version_slot(version_arg, root_arg)
        slot.mkdir(parents=True, exist_ok=True)
        (slot / "installed-by-concurrent-caller.txt").write_text("already live")
        si._write_marker(root_arg, version_arg)
        return False

    monkeypatch.setattr(si, "needs_install", _needs_install_then_satisfied)

    res = self_install(pd, root=root, dry_run=False)

    assert res.action == "already-current"
    assert len(calls) == 2, "must recheck needs_install() a second time under the lease"
    # The concurrently-installed slot must be left completely untouched --
    # not rmtree'd and not recopied over.
    slot = version_slot("7.8.9", root)
    assert (slot / "installed-by-concurrent-caller.txt").exists()
    assert not (slot / "src").exists()


def test_apply_defers_when_cutover_lock_is_busy(tmp_path, monkeypatch):
    """If another process genuinely holds the cutover lock (a real,
    concurrent self_update()/activate_after_update() in flight),
    self_install must never mutate the slot unprotected -- per
    docs/patterns/graceful-daemon-cutover.md's "serialize cutover attempts
    under one lease" rule, it must defer (report action="error") rather
    than proceed with an unguarded rmtree/copy that a concurrent cutover's
    own spawn_passive could race against."""
    import worktree_manager.mux_daemon_cutover as mdc

    pd = _fake_payload(tmp_path, "4.5.6")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    def _always_busy(root_arg, *, timeout_s=5.0, poll_s=0.2):
        raise TimeoutError("mux-daemon cutover lock busy")

    monkeypatch.setattr(mdc, "_acquire_cutover_lock", _always_busy)

    res = self_install(pd, root=root, dry_run=False)

    assert res.action == "error"
    assert "cutover" in res.reason
    # Nothing must have been mutated: no marker, no slot.
    assert current_version(root) is None
    assert not version_slot("4.5.6", root).exists()


def test_apply_is_idempotent_and_version_gated(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    assert needs_install("1.2.3", root) is False
    again = self_install(pd, root=root, dry_run=False)
    assert again.action == "already-current"


def test_stale_legacy_binstub_content_forces_redeploy(tmp_path, monkeypatch):
    """Regression: a version-current marker + slot must not mask a stale or
    legacy binstub. copilot-extensions#2788-adjacent report -- a prior
    cutover left an old/incompatible ``worktree-manager`` file occupying the
    binstub name, which then failed the consuming agent-worktrees seam's
    ``--version`` health probe and silently fell back to the bundled picker.
    Presence-only idempotency checking hid the problem; content must match.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    registry = _patch_provider_registry(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)

    # Simulate a legacy/incompatible binstub clobbering the deployed one --
    # e.g. left over from an ancient pre-versioned install attempt.
    stub = lb / "worktree-manager"
    stub.write_text("#!/usr/bin/env bash\necho legacy stub; exit 1\n")

    # The marker + slot still say "1.2.3 is installed" -- but the binstub on
    # disk no longer matches what this version would deploy.
    assert needs_install("1.2.3", root) is True

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    assert "legacy stub" not in stub.read_text()
    assert (registry / "worktree-manager.json").is_file()
    assert needs_install("1.2.3", root) is False


def test_needs_install_rejects_an_empty_broken_slot(tmp_path, monkeypatch):
    """Regression: a slot that exists but is EMPTY must not be mistaken for
    a valid, already-current install just because ``current-version``
    already names it and the directory exists. ``needs_install()`` must
    verify the slot's actual runnable content, not merely that it exists.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    assert needs_install("1.2.3", root) is False

    # Simulate the on-disk corruption directly: marker + binstub still
    # correct, but the slot's content is gone (e.g. an interrupted
    # rmtree/copytree left an existing, empty directory behind).
    slot = version_slot("1.2.3", root)
    import shutil as _shutil
    _shutil.rmtree(slot)
    slot.mkdir(parents=True)
    assert slot.is_dir() and not any(slot.iterdir())

    assert needs_install("1.2.3", root) is True, (
        "an empty slot must never be treated as a valid install"
    )
    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    assert (slot / "src" / "worktree_manager" / "__init__.py").exists()
    assert needs_install("1.2.3", root) is False


def test_needs_install_rejects_a_slot_missing_the_module_launch_target(tmp_path, monkeypatch):
    """A slot can retain ``src/worktree_manager/__init__.py`` while still
    missing another file the shipped binstubs need to launch (``uv run
    --project <slot> python -m worktree_manager``, which also requires
    ``pyproject.toml`` and ``__main__.py``) -- a partial recopy failure
    need not empty the whole tree to leave it unlaunchable. Only checking
    for one file would still mistake this for a valid install.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    assert needs_install("1.2.3", root) is False

    slot = version_slot("1.2.3", root)
    (slot / "src" / "worktree_manager" / "__main__.py").unlink()
    assert (slot / "src" / "worktree_manager" / "__init__.py").is_file()

    assert needs_install("1.2.3", root) is True, (
        "a slot missing its module launch target must never be treated as a valid install"
    )
    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    assert needs_install("1.2.3", root) is False


def test_self_install_refuses_to_mark_complete_a_payload_missing_a_key_file(tmp_path, monkeypatch):
    """A payload missing ``__main__.py`` (not caught by ``payload_version()``,
    which only reads ``__init__.py``, nor by the ``pyproject.toml``-only
    check ``self_update()`` performs on the fetched payload) must not be
    marked complete, published as ``current-version``, or reported
    ``action='installed'`` -- it would leave a slot ``_slot_is_complete()``
    immediately rejects, with the binstub unable to launch it.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    (pd / "src" / "worktree_manager" / "__main__.py").unlink()
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)

    assert res.action == "error"
    assert "__main__.py" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("1.2.3", root).exists()


def test_copy_payload_oserror_is_normalized_to_a_clean_error_result(tmp_path, monkeypatch):
    """A bare ``OSError`` raised by ``shutil.rmtree``/``shutil.copytree``
    inside ``_copy_payload`` (most notably a Windows ``PermissionError``/
    WinError 32 -- copilot-extensions#4999) must be normalized to the one
    exception type ``self_install()`` catches at its boundary, so it
    reports a clean ``action='error'`` result -- exactly like any other
    recognized install failure -- instead of propagating uncaught and
    skipping cleanup.
    """
    import shutil as _shutil

    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    real_copytree = _shutil.copytree

    def _boom(*args, **kwargs):
        raise PermissionError(
            "[WinError 32] The process cannot access the file because it "
            "is being used by another process"
        )

    monkeypatch.setattr(si.shutil, "copytree", _boom)

    res = self_install(pd, root=root, dry_run=False)

    assert res.action == "error"
    assert "WinError 32" in (res.reason or "") or "being used" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("1.2.3", root).exists(), (
        "a failed copy must not leave a broken, empty slot behind"
    )

    # And a retry with the real implementation restored succeeds cleanly.
    monkeypatch.setattr(si.shutil, "copytree", real_copytree)
    res2 = self_install(pd, root=root, dry_run=False)
    assert res2.action == "installed"
    assert current_version(root) == "1.2.3"


def test_marker_invalidation_permission_error_aborts_instead_of_proceeding(tmp_path, monkeypatch):
    """A ``PermissionError`` invalidating a prior slot's completion marker
    (e.g. the marker file itself is momentarily held open) must abort the
    install rather than being swallowed and letting mutation proceed with
    a stale marker still on disk. Only a genuinely absent marker
    (``FileNotFoundError``) is the ordinary, ignorable case.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    assert current_version(root) == "1.2.3"

    real_unlink = Path.unlink

    def _flaky_unlink(self, *a, **k):
        if self.name == si._SLOT_COMPLETE_MARKER:
            raise PermissionError(13, "Access is denied")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _flaky_unlink)
    # Force a reinstall of the SAME version so _copy_payload_unsafe's
    # invalidation step actually runs against a marker that already exists.
    monkeypatch.setattr(si, "_binstubs_are_stale", lambda: True)

    res = self_install(pd, root=root, dry_run=False)

    assert res.action == "error"
    assert "Access is denied" in (res.reason or "")


def test_stale_provider_manifest_forces_repair(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    registry = _patch_provider_registry(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)

    manifest = registry / "worktree-manager.json"
    manifest.write_text(
        '{"schema_version":1,"provider":"worktree-manager","command":["C:/stale.cmd"],'
        '"minimum_version":"0.1.0-dev21","provider_root":"C:/stale"}\n',
        encoding="utf-8",
    )

    assert needs_install("1.2.3", root) is True
    repaired = self_install(pd, root=root, dry_run=False)
    assert repaired.action == "installed"
    payload = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert payload["command"] == [str((lb / si._primary_binstub_name()).resolve())]
    assert payload["provider_root"] == str(root.resolve())


def test_stale_provider_manifest_repair_never_touches_the_payload_slot(tmp_path, monkeypatch):
    """Regression: a manifest-only repair (marker/slot/binstubs already
    correct -- only the control-plane provider manifest is stale) must not
    go through ``_copy_payload``. That path rmtrees the existing version
    slot before recopying it, which fails with a PermissionError if any
    OTHER running ``worktree-manager`` instance has that slot's venv open --
    exactly the shape a routine repair should be safe to run under, not one
    more way for it to fail.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    registry = _patch_provider_registry(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)

    manifest = registry / "worktree-manager.json"
    manifest.write_text(
        '{"schema_version":1,"provider":"worktree-manager","command":["C:/stale.cmd"],'
        '"minimum_version":"0.1.0-dev21","provider_root":"C:/stale"}\n',
        encoding="utf-8",
    )

    def _boom(*a, **k):
        raise AssertionError("_copy_payload must not run for a manifest-only repair")

    monkeypatch.setattr(si, "_copy_payload", _boom)

    assert needs_install("1.2.3", root) is True
    repaired = self_install(pd, root=root, dry_run=False)
    assert repaired.action == "installed"
    payload = __import__("json").loads(manifest.read_text(encoding="utf-8"))
    assert payload["command"] != ["C:/stale.cmd"]
    assert needs_install("1.2.3", root) is False


def test_concurrent_manifest_repairs_do_not_race(tmp_path, monkeypatch):
    """Regression: two writers racing _write_control_plane_provider_manifest
    must not share a temp filename -- the loser used to raise
    FileNotFoundError when os.replace() found the winner had already moved
    the shared ``.tmp`` file out from under it.
    """
    import threading

    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    errors: list[BaseException] = []
    start = threading.Barrier(2)

    def _writer():
        start.wait()
        try:
            si._write_control_plane_provider_manifest(pd, root=root)
        except BaseException as exc:  # noqa: BLE001 -- capture to assert none occurred
            errors.append(exc)

    threads = [threading.Thread(target=_writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
    manifest = si._control_plane_provider_manifest_path(pd, root=root)
    assert manifest.is_file()
    assert not si._control_plane_provider_manifest_is_stale(pd, root=root)


def test_replace_with_retry_recovers_from_a_transient_permission_error(tmp_path, monkeypatch):
    """Deterministic coverage for ``_replace_with_retry``'s own retry loop:
    a transient Windows ``PermissionError`` from ``os.replace()`` (the
    exact failure mode ``test_concurrent_manifest_repairs_do_not_race``
    exercises only probabilistically, via genuine thread contention) must
    not fail the call -- it must retry until a later attempt succeeds.
    """
    src = tmp_path / "src.tmp"
    dst = tmp_path / "dst"
    src.write_text("content", encoding="utf-8")

    real_replace = os.replace
    calls: list[int] = []

    def _flaky_replace(s, d):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(13, "Access is denied")
        return real_replace(s, d)

    monkeypatch.setattr(si.os, "replace", _flaky_replace)

    si._replace_with_retry(src, dst)

    assert len(calls) == 2, "must have retried exactly once after the transient failure"
    assert dst.read_text(encoding="utf-8") == "content"
    assert not src.exists()


def test_replace_with_retry_reraises_after_exhausting_attempts(tmp_path, monkeypatch):
    """A PermissionError that never clears must still surface to the caller
    -- not be swallowed indefinitely -- once the bounded retry budget is
    spent.
    """
    src = tmp_path / "src.tmp"
    dst = tmp_path / "dst"
    src.write_text("content", encoding="utf-8")

    def _always_fails(s, d):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(si.os, "replace", _always_fails)

    with pytest.raises(PermissionError):
        si._replace_with_retry(src, dst, attempts=3, delay_s=0.0)


def test_known_legacy_prerename_binstub_is_recognized_and_cleaned(tmp_path, monkeypatch):
    """The pre-rename ``worktree-manager`` plugin prototype (before it became
    agent-worktrees, commit ab0716e28..6512114be) shipped this exact
    ``~/.local/bin/worktree-manager`` (+ ``.cmd``) content and a non-versioned
    ``~/.worktree-manager/.venv`` + ``.../lib`` runtime -- both colliding with
    this Manager's own binstub name and root. A machine that installed that
    prototype before the rename can still carry these exact artifacts; a real
    self-install must positively recognize and remove them, not just
    overwrite the binstub incidentally.
    """
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    lb.mkdir(parents=True)
    (lb / "worktree-manager").write_text(si._LEGACY_PRERENAME_SH, encoding="utf-8", newline="")
    (lb / "worktree-manager.cmd").write_text(
        si._LEGACY_PRERENAME_CMD, encoding="utf-8", newline=""
    )
    legacy_venv = root / ".venv" / "bin"
    legacy_venv.mkdir(parents=True)
    (legacy_venv / "python").write_text("#!/usr/bin/env python\n")
    (root / "lib").mkdir(parents=True)

    # Dry-run reports, but never touches, the recognized legacy artifacts.
    planned = si.plan_legacy_cleanup(root)
    assert any("legacy binstub" in p and "worktree-manager" in p for p in planned)
    assert any("legacy binstub" in p and "worktree-manager.cmd" in p for p in planned)
    assert any("legacy root artifact" in p and ".venv" in p for p in planned)
    assert any("legacy root artifact" in p and str(root / "lib") in p for p in planned)
    assert (root / ".venv").exists() and (root / "lib").exists()

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    assert len(res.cleaned) == 4
    assert not (root / ".venv").exists()
    assert not (root / "lib").exists()
    # The binstub is now this version's own content, not the legacy one.
    assert (lb / "worktree-manager").read_text() != si._LEGACY_PRERENAME_SH


def test_unrecognized_binstub_content_is_never_attributed_as_legacy(tmp_path, monkeypatch):
    """Only the byte-exact known prototype signature is auto-attributed as
    legacy and named in ``cleaned`` -- an operator's own unrelated file at
    the same path is still replaced (the general staleness fix), but never
    mislabeled or specially called out as a *recognized* legacy artifact."""
    pd = _fake_payload(tmp_path, "1.2.3")
    root = tmp_path / "root"
    lb = _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)
    lb.mkdir(parents=True)
    (lb / "worktree-manager").write_text("#!/usr/bin/env bash\necho mine\n")

    assert si.plan_legacy_cleanup(root) == []
    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    assert res.cleaned == ()


def test_new_version_publishes_new_slot(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)
    self_install(_fake_payload(tmp_path, "1.0.0"), root=root, dry_run=False)
    # bump the payload version and re-install
    pd2 = _fake_payload(tmp_path / "b", "2.0.0")
    res = self_install(pd2, root=root, dry_run=False)
    assert res.action == "installed"
    assert current_version(root) == "2.0.0"
    assert version_slot("1.0.0", root).is_dir()  # old slot immutable, retained
    assert version_slot("2.0.0", root).is_dir()


def test_status_reports_marker_and_binstub(tmp_path, monkeypatch):
    pd = _fake_payload(tmp_path, "3.3.3")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)
    assert status(root).installed is False
    self_install(pd, root=root, dry_run=False)
    st = status(root)
    assert st.installed_version == "3.3.3"
    assert st.binstub is not None


def _fake_monorepo_with_pointer(tmp: Path, version: str) -> Path:
    """A synthetic monorepo shape: <tmp>/monorepo/{worktree-manager,libs}/
    -- the payload's ``libs/<lib>`` carries an UN-materialized vendor pointer,
    and ``libs/`` (top-level) carries the real canonical content.
    ``_materialize_payload_pointers`` never needs a fetched ``tools/`` at
    all -- it uses the LOCAL, already-trusted
    ``_trusted_pointer_materializer`` module shipped with worktree_manager
    itself, never dynamically executing anything from the fetched source.
    Returns the payload dir (``<tmp>/monorepo/worktree-manager``)."""
    mono = tmp / "monorepo"
    pd = _fake_payload(mono, version)
    pd.rename(mono / "worktree-manager")
    pd = mono / "worktree-manager"

    canon = mono / "libs" / "shared-lib"
    (canon / "src" / "shared_lib").mkdir(parents=True)
    (canon / "src" / "shared_lib" / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (canon / "pyproject.toml").write_text(
        '[project]\nname = "shared-lib"\nversion = "0.1.0-dev1"\n', encoding="utf-8"
    )

    copy_dir = pd / "libs" / "shared-lib"
    (copy_dir / "src" / "shared_lib").mkdir(parents=True)
    (copy_dir / "src" / "shared_lib" / "__init__.py").write_text("# stub\n", encoding="utf-8")
    (copy_dir / "VENDOR_POINTER.json").write_text(
        '{"schema": "copilot-extensions.vendor-pointer", "version": 1, '
        '"source": "libs/shared-lib", "kind": "src-passthrough"}\n',
        encoding="utf-8",
    )
    return pd


def test_self_install_materializes_a_vendor_pointer_from_a_live_monorepo(tmp_path, monkeypatch):
    """A self-installed slot has no libs/+plugins/ monorepo ancestor of its
    own, so a src-passthrough pointer's runtime import would be permanently
    broken there -- self_install must expand any pointer copy into real,
    self-contained content BEFORE publishing the slot, using the still-live
    monorepo ancestor available at copy time (see
    ``_materialize_payload_pointers``)."""
    pd = _fake_monorepo_with_pointer(tmp_path, "5.5.5")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    slot = version_slot("5.5.5", root)
    copy_dir = slot / "libs" / "shared-lib"
    assert not (copy_dir / "VENDOR_POINTER.json").exists()
    assert (copy_dir / "src" / "shared_lib" / "__init__.py").read_text() == "value = 1\n"


def test_self_install_never_requires_a_monorepo_ancestor_when_there_are_no_pointers(
    tmp_path, monkeypatch,
):
    """Round-18 review finding: when slot/libs exists but contains no
    VENDOR_POINTER.json at all, a normal payload with real libs must not
    even inspect (let alone require) a monorepo ancestor -- there is
    nothing to materialize, so a fetch/checkout that never provides a
    libs/ sibling (or one with an unrelated malformed file inside it)
    must not fail installation."""
    pd = _fake_payload(tmp_path, "6.6.7")
    real_copy = pd / "libs" / "real-lib"
    (real_copy / "src" / "real_lib").mkdir(parents=True)
    (real_copy / "src" / "real_lib" / "__init__.py").write_text(
        "value = 1\n", encoding="utf-8"
    )
    # No libs/ sibling next to pd at all -- if this were even inspected,
    # a monorepo-ancestor-required error would fire; it must not be.
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    slot = version_slot("6.6.7", root)
    assert (slot / "libs" / "real-lib" / "src" / "real_lib" / "__init__.py").read_text() == (
        "value = 1\n"
    )


def test_self_install_raises_when_pointer_present_without_monorepo_ancestor(tmp_path, monkeypatch):
    """The dependency-free, no-ancestor case: a pointer copy with no
    reachable canonical source would install successfully but be permanently
    unimportable at runtime -- self_install must report an error (rather
    than crash or silently ship a broken payload), and self_update's own
    "error"-action handling then forwards this cleanly (see
    test_self_update_reports_error_and_cleans_up_when_pointer_unresolvable
    in test_e2e_delivery.py / test_update.py)."""
    pd = _fake_payload(tmp_path, "6.6.6")
    copy_dir = pd / "libs" / "shared-lib"
    (copy_dir / "src" / "shared_lib").mkdir(parents=True)
    (copy_dir / "src" / "shared_lib" / "__init__.py").write_text("# stub\n", encoding="utf-8")
    (copy_dir / "VENDOR_POINTER.json").write_text(
        '{"schema": "copilot-extensions.vendor-pointer", "version": 1, '
        '"source": "libs/shared-lib", "kind": "src-passthrough"}\n',
        encoding="utf-8",
    )
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "unmaterialized vendor pointers" in (res.reason or "")
    # Nothing was published -- a rejected install must not leave a broken
    # slot on disk (it would otherwise be mistaken for a valid install by
    # a later needs_install() existence check) or a marker naming it as
    # current.
    assert current_version(root) is None
    assert not version_slot("6.6.6", root).exists()


def _fake_monorepo_with_uv_editable_ref(tmp: Path, version: str) -> Path:
    """A synthetic monorepo shape mirroring ``_fake_monorepo_with_pointer``
    above, but for the `uv`-editable canonical-reference form
    (vendor-pointer-generalization effort's second course correction,
    which superseded the ``VENDOR_POINTER.json`` directory-pointer form):
    the payload's ``pyproject.toml`` carries an escaping
    ``[tool.uv.sources]`` entry (``editable = true``) instead of vendoring
    a local ``libs/<lib>`` copy at all -- there is no local copy to find a
    pointer marker in; the reference lives in the manifest itself. Returns
    the payload dir (``<tmp>/monorepo/worktree-manager``)."""
    mono = tmp / "monorepo"
    pd = _fake_payload(mono, version)
    pd.rename(mono / "worktree-manager")
    pd = mono / "worktree-manager"

    canon = mono / "libs" / "shared-lib"
    (canon / "src" / "shared_lib").mkdir(parents=True)
    (canon / "src" / "shared_lib" / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    (canon / "pyproject.toml").write_text(
        '[project]\nname = "shared-lib"\nversion = "0.1.0-dev1"\n', encoding="utf-8"
    )

    (pd / "pyproject.toml").write_text(
        "[project]\nname='x'\n"
        "\n[tool.uv.sources]\n"
        'agent-shared-lib = { path = "../libs/shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    return pd


def test_self_install_materializes_a_uv_editable_ref_from_a_live_monorepo(tmp_path, monkeypatch):
    """The `uv`-editable counterpart of
    ``test_self_install_materializes_a_vendor_pointer_from_a_live_monorepo``
    above (review finding on PR #4331: `_materialize_payload_pointers`
    originally only expanded the older directory-pointer form). A
    self-installed slot's ``pyproject.toml`` retains an escaping
    ``[tool.uv.sources]`` `path` that can never resolve once published
    (no monorepo ancestor of its own) -- self_install must copy canonical's
    complete lib tree into the slot's own ``libs/<lib>/`` and rewrite the
    manifest entry to the local, non-editable form BEFORE publishing."""
    pd = _fake_monorepo_with_uv_editable_ref(tmp_path, "5.5.6")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "installed"
    slot = version_slot("5.5.6", root)
    lib_dir = slot / "libs" / "shared-lib"
    assert (lib_dir / "src" / "shared_lib" / "__init__.py").read_text() == "value = 1\n"
    manifest_text = (slot / "pyproject.toml").read_text(encoding="utf-8")
    assert 'agent-shared-lib = { path = "libs/shared-lib" }' in manifest_text
    assert "editable = true" not in manifest_text


def test_self_install_raises_when_uv_editable_ref_present_without_monorepo_ancestor(
    tmp_path, monkeypatch,
):
    """The `uv`-editable counterpart of
    ``test_self_install_raises_when_pointer_present_without_monorepo_ancestor``
    above: an escaping ``[tool.uv.sources]`` entry with no reachable
    canonical ``libs/`` sibling would install successfully but ship an
    unresolvable dependency reference -- self_install must report an error
    and publish nothing, not silently ship the broken payload."""
    pd = _fake_payload(tmp_path, "6.6.8")
    (pd / "pyproject.toml").write_text(
        "[project]\nname='x'\n"
        "\n[tool.uv.sources]\n"
        'agent-shared-lib = { path = "../libs/shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "unmaterialized vendor pointers or uv-editable" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("6.6.8", root).exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs elevation on Windows")
def test_self_install_refuses_a_symlink_anywhere_in_the_payload(tmp_path, monkeypatch):
    """Round-9 review finding: symlinks=True on the copytree PRESERVES a
    symlink instead of dereferencing it, but preservation alone doesn't
    make it safe to publish -- a symlink anywhere in the payload (not
    just within a vendor-pointer's canonical src/tests) would survive into
    the slot and could resolve outside it at runtime. self_install must
    reject the whole payload rather than let it through, and must not
    leave the partially-copied slot behind."""
    pd = _fake_payload(tmp_path, "7.7.7")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("should never be reachable from the slot\n")
    (pd / "sneaky-link").symlink_to(outside)
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "symlink" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("7.7.7", root).exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs elevation on Windows")
def test_self_install_refuses_a_symlinked_payload_root(tmp_path, monkeypatch):
    """Round-16 review finding: symlinks=True on the copytree only
    protects symlinks encountered DURING the walk of payload_dir's own
    tree -- it cannot protect payload_dir being a symlink ITSELF
    (shutil.copytree always creates dst as a real directory, so there's
    nowhere for a preserved-root-symlink object to go). In the git-backed
    self_update path, a checked-out worktree-manager/ dir could itself be
    a symlink; the earlier (payload/"pyproject.toml").is_file() check
    would still pass (it follows the link), copytree would dereference
    it, and _find_any_symlink(slot) afterward would see no link at all
    (the slot's own root is never included in its own scan)."""
    real_payload = _fake_payload(tmp_path, "10.10.10")
    linked_payload = tmp_path / "linked-payload"
    linked_payload.symlink_to(real_payload, target_is_directory=True)
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(linked_payload, root=root, dry_run=False)
    assert res.action == "error"
    assert "symlink" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("10.10.10", root).exists()


def test_bin_directory_is_deployed_into_the_slot(tmp_path, monkeypatch):
    """Phase 3b Slice 2 (Mux relocation): the versioned self-install copies the
    WHOLE payload directory (``_copy_payload`` -> ``shutil.copytree``), so a
    sibling ``bin/`` directory of launcher scripts -- like
    ``worktree-manager/bin/launch-session.{sh,ps1,cmd}`` -- deploys to
    ``<slot>/bin/`` with no self-install code change. This proves that
    mechanism generically with a synthetic script, independent of the real
    launcher scripts' content."""
    pd = _fake_payload(tmp_path, "4.4.4")
    (pd / "bin").mkdir()
    (pd / "bin" / "launch-session.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)
    self_install(pd, root=root, dry_run=False)
    slot = version_slot("4.4.4", root)
    deployed = slot / "bin" / "launch-session.sh"
    assert deployed.exists()
    assert deployed.read_text() == (pd / "bin" / "launch-session.sh").read_text()


def test_relocated_launchers_resolve_pane_wrappers_from_their_own_bin():
    root = Path(__file__).resolve().parents[1] / "bin"
    sh = (root / "launch-session.sh").read_text(encoding="utf-8")
    ps1 = (root / "launch-session.ps1").read_text(encoding="utf-8")
    assert 'PANE_WRAPPER="$SCRIPT_DIR/pane-wrapper.sh"' in sh
    assert 'dirname -- "${BASH_SOURCE[0]}"' in sh
    assert "$paneWrapper = Join-Path $PSScriptRoot 'pane-wrapper.ps1'" in ps1
    assert 'RUNTIME_DIR="${AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT:-}"' in sh
    assert 'AGENT_WORKTREES_LAUNCH_RECOVERY_ANCHOR' in sh
    assert '$RuntimeDir = $env:AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT' in ps1


def test_relocated_launchers_resume_existing_ahp_legs_without_ensuring_new_ones():
    root = Path(__file__).resolve().parents[1] / "bin"
    sh = (root / "launch-session.sh").read_text(encoding="utf-8")
    ps1 = (root / "launch-session.ps1").read_text(encoding="utf-8")
    assert "execution-leg get" in sh
    assert "'execution-leg', 'get'" in ps1
    assert "session-backend" not in sh
    assert "session-backend" not in ps1
    assert 'AGENT_WORKTREES_AHP_AUTH_TOKEN="$GH_TOKEN"' not in sh
    assert "$env:AGENT_WORKTREES_AHP_AUTH_TOKEN = $token.Trim()" not in ps1
    assert "launching via the Worktree Manager Picker" in sh
    assert "launching via the Worktree Manager Picker" in ps1


def test_relocated_launcher_ships_the_mux_status_bar_scripts_it_needs():
    """``launch-session.ps1`` dot-sources ``session-options.ps1`` and
    ``psmux-path.ps1`` (and ``session-options.ps1`` in turn resolves
    ``psmux-passthrough.conf``) via ``$PSScriptRoot``-relative paths, so all
    must be deployed as siblings of the relocated launcher. Omitting them
    left Worktree Manager-launched sessions with a silently unconfigured
    psmux status bar (the failure is swallowed, not a launch error) once
    ``launch-session.ps1`` moved out of ``plugins/agent-worktrees/bin/``."""
    root = Path(__file__).resolve().parents[1] / "bin"
    ps1 = (root / "launch-session.ps1").read_text(encoding="utf-8")
    assert "$script:AwSessionOptions = Join-Path $PSScriptRoot 'session-options.ps1'" in ps1
    assert "$pathHelper = Join-Path $PSScriptRoot 'psmux-path.ps1'" in ps1
    for name in (
        "session-options.ps1",
        "session-options.sh",
        "apply-mux-keybinds.ps1",
        "apply-mux-keybinds.sh",
        "psmux-passthrough.conf",
        "psmux-path.ps1",
    ):
        assert (root / name).is_file(), f"missing {name} beside the relocated launcher"
    session_options_ps1 = (root / "session-options.ps1").read_text(encoding="utf-8")
    assert "Join-Path $PSScriptRoot 'psmux-passthrough.conf'" in session_options_ps1
    assert '$env:AGENT_WORKTREES_LAUNCH_RECOVERY_ANCHOR' in ps1


def test_copied_psmux_path_helper_matches_its_canonical_source():
    """Phase 3b Sub-slice 2a Step 2 completed the mux-launch cutover:
    ``session-options.*``/``apply-mux-keybinds.*``/``psmux-passthrough.conf``
    were deleted from agent-worktrees along with ``launch-session.*``/
    ``pane-wrapper.*`` -- Worktree Manager's ``bin/`` is now their sole copy,
    with independent regression coverage (see ``test_terminal_decoupling.py``,
    ``test_launch_session_unwrap.py``). ``psmux-path.ps1`` is the one
    exception: agent-worktrees keeps its own copy for install-time psmux
    provisioning (``Ensure-Psmux``/``Ensure-PsmuxSshSafe``) independent of the
    launch scripts, so the two copies of that one helper must stay
    byte-identical rather than silently diverging."""
    repo_root = Path(__file__).resolve().parents[2]
    wm_bin = repo_root / "worktree-manager" / "bin"
    aw_scripts = repo_root / "plugins" / "agent-worktrees" / "scripts"
    copy = wm_bin / "psmux-path.ps1"
    canonical = aw_scripts / "psmux-path.ps1"
    assert copy.read_bytes() == canonical.read_bytes(), (
        f"{copy} has drifted from its canonical source {canonical}; "
        "re-sync both copies verbatim"
    )


def test_self_install_command_dry_run(capsys):
    rc = main(["self-install"])
    out = capsys.readouterr().out
    assert "self-install" in out.lower()
    assert "current-version" in out
    assert rc in (0, 1)


def test_doctor_shows_self_section(monkeypatch, capsys):
    from worktree_manager import doctor_cli

    monkeypatch.setattr(doctor_cli.daemon_health, "doctor_report", lambda apply=False: {"findings": []})
    main(["doctor"])
    assert "worktree-manager (self)" in capsys.readouterr().out


def test_self_install_normalizes_a_malformed_pointer_failure_to_runtimeerror(tmp_path, monkeypatch):
    """Round-9 review finding: a malformed pointer file (bad JSON) can make
    materialize_libs_dir() raise JSONDecodeError instead of returning a
    SKIP line -- self_install() only translates RuntimeError, so this must
    be normalized at the _materialize_payload_pointers boundary rather
    than letting an unexpected exception type escape self_update's own
    best-effort/non-fatal contract."""
    pd = _fake_monorepo_with_pointer(tmp_path, "8.8.8")
    # Corrupt the pointer file with invalid JSON.
    (pd / "libs" / "shared-lib" / "VENDOR_POINTER.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    root = tmp_path / "root"
    _patch_local_bin(monkeypatch, tmp_path)
    _patch_provider_registry(monkeypatch, tmp_path)

    res = self_install(pd, root=root, dry_run=False)
    assert res.action == "error"
    assert "pointer materialization" in (res.reason or "")
    assert current_version(root) is None
    assert not version_slot("8.8.8", root).exists()


# ---------------------------------------------------------------------------
# Regression coverage for conftest.py's own suite-wide safety-net fixture.
#
# Round-review finding on #5157: the tests above pass explicit root=/patch the
# bin and provider-registry paths directly, so their assertions never actually
# exercise this fallback safety net itself. These tests call
# ``_conftest_sandbox._apply_sandbox`` directly -- the fixture's own logic,
# extracted as a plain function precisely so it can be driven without relying
# on pytest's autouse-fixture scheduling (which has no clean way to simulate
# "an override was already set before this fixture ran").
#
# Deliberately placed here (NOT a new standalone test_*.py file): an earlier
# version of this coverage lived in its own ``test_conftest_safety_net.py``,
# which collects alphabetically before ``test_plugin_contracts.py`` and was
# observed to make that unrelated file's tests fail in full-suite CI runs
# (pre-existing global-state fragility there, triggered only by collection
# order). Appending to this already-later-sorting file sidesteps the trigger
# entirely without touching the unrelated module.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    # Enumerated literally, independent of the implementation's own
    # `_SELF_INSTALL_TEST_MODULES` allowlist -- reading that set here would
    # make this parametrization track whatever the implementation claims to
    # cover, silently losing coverage for any module quietly removed from it.
    ["test_e2e_delivery", "test_self_install", "test_update"],
)
def test_apply_sandbox_activates_for_every_selected_module(module_name, tmp_path, monkeypatch):
    """The fixture activates (sets USERPROFILE/HOME under the tmp sandbox)
    for all three selected modules -- not just whichever one a prior test
    happened to exercise.

    Uses a distinct inner sub-path (``tmp_path / "inner"``), never
    ``tmp_path`` itself: this test runs inside ``test_self_install`` -- a
    selected module -- so the outer, real autouse fixture has already set
    USERPROFILE/HOME to ``tmp_path / "fake-home"`` before this body even
    runs. Asserting against that same path would pass even if
    ``_apply_sandbox`` were deleted or stopped selecting ``module_name``
    entirely, since the outer activation alone already satisfies it."""
    inner = tmp_path / "inner"
    _apply_sandbox(module_name, inner, monkeypatch)
    fake_home = inner / "fake-home"
    assert os.environ["USERPROFILE"] == str(fake_home)
    assert os.environ["HOME"] == str(fake_home)
    assert fake_home.is_dir()


def test_apply_sandbox_is_a_noop_for_an_unrelated_module(tmp_path, monkeypatch):
    """A module not in the selected set must be left completely untouched --
    confirms the overhead/behavior really is scoped, not accidentally global.

    Uses a distinct, unused sub-path (``tmp_path / "unrelated"``) rather than
    asserting ``tmp_path / "fake-home"`` is absent: this test itself runs
    inside ``test_self_install`` -- a selected module -- so conftest.py's own
    real autouse fixture has already created that exact path for THIS test
    before its body even runs; asserting its absence would conflate that
    outer, legitimate activation with the inner, unrelated-module call this
    test is actually exercising."""
    before_userprofile = os.environ.get("USERPROFILE")
    before_home = os.environ.get("HOME")
    unrelated = tmp_path / "unrelated"
    _apply_sandbox("test_something_unrelated", unrelated, monkeypatch)
    assert os.environ.get("USERPROFILE") == before_userprofile
    assert os.environ.get("HOME") == before_home
    assert not (unrelated / "fake-home").exists()


def test_apply_sandbox_removes_a_preexisting_root_override(tmp_path, monkeypatch):
    """A WORKTREE_MANAGER_ROOT/AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR
    override a prior test/session left set (the exact production incident
    this whole fixture exists to prevent, #5122) must be cleared, not merely
    shadowed -- otherwise an unpatched self_install()/self_update() call
    would still resolve through the stale override instead of the sandbox."""
    stale_root = tmp_path / "stale-root-from-a-prior-test"
    stale_registry = tmp_path / "stale-registry-from-a-prior-test"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(stale_root))
    monkeypatch.setenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", str(stale_registry))

    _apply_sandbox("test_self_install", tmp_path / "inner", monkeypatch)

    assert "WORKTREE_MANAGER_ROOT" not in os.environ
    assert "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR" not in os.environ


def test_autouse_fixture_resolves_unpatched_default_paths_under_the_sandbox():
    """With NO explicit ``_apply_sandbox`` call at all -- relying purely on
    automatic fixture setup -- the real, unpatched ``default_root()``/
    ``local_bin()``/``control_plane_providers_dir()`` must resolve under the
    sandbox -- never under the real ``~``. Calling ``_apply_sandbox`` here
    directly (as an earlier version of this test did) would mask a failure
    of the autouse fixture itself to select this module, since the explicit
    call alone is enough to satisfy the assertions regardless of whether
    the real fixture ran at all."""
    fake_home = Path(os.environ["USERPROFILE"])
    assert fake_home.name == "fake-home"

    assert si.default_root() == fake_home / ".worktree-manager"
    assert si.local_bin() == fake_home / ".local" / "bin"
    assert si.control_plane_providers_dir() == (
        fake_home / ".agent-worktrees" / "control-plane-providers.d"
    )


def test_autouse_fixture_activates_automatically_for_this_module():
    """Unlike every test above (which calls ``_apply_sandbox`` directly), this
    one makes NO such call -- it asserts purely on ambient state, proving the
    REAL ``conftest.py`` autouse fixture (not the plain function it delegates
    to) actually wires up automatically for a selected module. This is the
    exact seam a prior round of this fixture's own regression -- the
    fixture unconditionally materializing ``tmp_path``/``monkeypatch`` via
    ``request.getfixturevalue(...)`` before checking the module name, which
    broke unrelated ``test_plugin_contracts.py`` tests in full-suite CI runs
    -- slipped through: every test here called ``_apply_sandbox`` directly, so
    none of them exercised the autouse fixture's own wiring at all."""
    assert "fake-home" in os.environ["USERPROFILE"]
    assert "fake-home" in os.environ["HOME"]
    assert Path(os.environ["USERPROFILE"]).is_dir()


def test_autouse_fixture_leaves_an_unrelated_module_untouched(pytester):
    """End-to-end integration check, via a real nested pytest run: a test in
    a module NOT in ``_SELF_INSTALL_TEST_MODULES`` must see the real
    USERPROFILE/HOME untouched, AND must not have a ``tmp_path`` directory
    gratuitously created for it -- both regressed together in the exact
    incident ``test_autouse_fixture_activates_automatically_for_this_module``
    documents above, since the fixture called ``request.getfixturevalue``
    unconditionally before checking the module name at all.

    Compares against the OUTER test's own (already-sandboxed) USERPROFILE/
    HOME values, passed down via env vars rather than re-asserting the raw
    strings: this outer test itself runs inside ``test_self_install`` -- a
    selected module -- so its own USERPROFILE/HOME are already pointed at an
    outer ``fake-home``, which `pytester`'s nested run inherits. Asserting
    those are simply absent/unsandboxed would therefore always fail,
    regardless of whether the unrelated module was genuinely left alone."""
    outer_userprofile = os.environ["USERPROFILE"]
    outer_home = os.environ["HOME"]
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text(encoding="utf-8"))
    sandbox_src = (Path(__file__).parent / "_conftest_sandbox.py").read_text(encoding="utf-8")
    pytester.makepyfile(_conftest_sandbox=sandbox_src)
    pytester.makepyfile(
        test_totally_unrelated_module=f"""
        import os

        def test_sees_the_inherited_environment_unchanged(request):
            assert os.environ.get("USERPROFILE") == {outer_userprofile!r}
            assert os.environ.get("HOME") == {outer_home!r}
            assert "tmp_path" not in request.fixturenames
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(passed=1)
