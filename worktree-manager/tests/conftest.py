"""Pytest entry point for the suite-wide safety net -- see
``_conftest_sandbox.py`` for the actual logic and full rationale (kept out of
this file so it can be imported unambiguously by its own regression test;
every ``conftest.py`` in a rootless pytest layout shares the bare module name
``conftest``, so a test importing this module directly would be at the mercy
of whichever ``conftest.py`` pytest's collection happened to import first --
a real, observed CI failure, not a theoretical one).
"""

from __future__ import annotations

import pytest
from _conftest_sandbox import _SELF_INSTALL_TEST_MODULES, _apply_sandbox

# Enables the `pytester` fixture (declaring it here, the suite's single
# root-level conftest.py, is required -- pytest only honors `pytest_plugins`
# at the rootdir conftest). Used by test_self_install.py's own
# end-to-end regression test for this fixture's autouse wiring.
pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _sandbox_real_user_paths(request):
    """Checks the module name BEFORE touching ``tmp_path``/``monkeypatch`` at
    all -- materializing either one unconditionally (even just to pass them
    into a function that then no-ops) forces pytest to create a real
    temp-dir and activate a real monkeypatch context for every single test in
    the ENTIRE suite, not only the selected modules. That proved to be a real,
    observed regression (not theoretical): it was enough, by itself, to make
    unrelated, pre-existing ``test_plugin_contracts.py`` tests fail in a
    full-suite run -- confirmed by bisection against a clean checkout, with
    this fixture's body reduced to a guaranteed no-op (empty
    ``_SELF_INSTALL_TEST_MODULES``) and the failures still reproducing purely
    from the unconditional ``getfixturevalue`` calls below. Checking the name
    first means every test outside the selected modules pays zero fixture
    overhead, exactly as intended."""
    module_name = request.module.__name__.rsplit(".", 1)[-1]
    if module_name not in _SELF_INSTALL_TEST_MODULES:
        return
    _apply_sandbox(
        module_name,
        request.getfixturevalue("tmp_path"),
        request.getfixturevalue("monkeypatch"),
    )
