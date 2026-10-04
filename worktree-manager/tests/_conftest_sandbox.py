"""Logic for ``conftest.py``'s suite-wide safety-net fixture.

Extracted into its own, uniquely-named module rather than left in
``conftest.py`` itself: ``tests/production_picker/conftest.py`` also exists,
and pytest's rootless (no ``__init__.py``) test layout gives every
``conftest.py`` the SAME bare module name ``conftest`` in ``sys.modules`` --
a regression test that did ``from conftest import ...`` (to unit-test this
logic directly, see ``test_conftest_safety_net.py``) non-deterministically
resolved to whichever ``conftest.py`` pytest's collection happened to import
first, which is a real, observed CI failure (the production_picker one has
no such names, so the import raised ``ImportError``) rather than a merely
theoretical ambiguity. A uniquely-named module is importable unambiguously
from anywhere in the test suite.
"""

from __future__ import annotations

_SELF_INSTALL_TEST_MODULES = {
    "test_self_install",
    "test_update",
    "test_e2e_delivery",
}


def _apply_sandbox(module_name: str, tmp_path, monkeypatch) -> None:
    """``self_install()``/``self_update(dry_run=False)`` resolve several real
    paths straight from ``USERPROFILE``/``HOME`` (``default_root()``,
    ``local_bin()``, ``control_plane_providers_dir()`` -- see
    ``worktree_manager/self_install.py``). Individual tests patch the ones
    they touch (``_patch_provider_registry`` in ``test_self_install.py``,
    ``_isolate_provider_registry`` in ``test_e2e_delivery.py``), but that is
    opt-in and per-function -- exactly the shape that let
    ``control-plane-providers.d/worktree-manager.json`` get clobbered with a
    tmp_path's contents on a real developer machine (#5122): every call site
    has to remember to patch, and a single forgotten one reaches through to
    production state.

    This is a second, independent layer that holds even when a future test in
    one of ``_SELF_INSTALL_TEST_MODULES`` forgets a per-function patch: it
    forces ``USERPROFILE``/``HOME`` to a tmp sandbox, so the three functions
    above can never resolve outside pytest's own tmp_path, regardless of
    whether that test also does its own, more specific patching (an explicit
    ``monkeypatch.setattr`` in the test body still wins -- this only changes
    what an *unpatched* call falls back to). A no-op for any other module.

    Extracted as a plain function (rather than inlined in the fixture) so it
    can be unit-tested directly, bypassing pytest's own autouse-fixture
    scheduling -- which has no clean way to simulate "an override was already
    set before this fixture ran" (see ``test_conftest_safety_net.py``).

    Adding a new test file that drives the real self_install()/self_update()
    delivery path? Add its module basename to ``_SELF_INSTALL_TEST_MODULES``
    above so it gets this safety net automatically.
    """
    if module_name not in _SELF_INSTALL_TEST_MODULES:
        return

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    # Clear any override a prior test/session may have left pointed elsewhere.
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", raising=False)
