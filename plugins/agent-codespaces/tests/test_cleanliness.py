"""Tests for the CodeSpace at-rest cleanliness predicate + probe (Phase 3b)."""

from __future__ import annotations

import pytest

from agent_codespaces import cleanliness as cl

# ── is_git_clean ─────────────────────────────────────────────────────────────

def test_clean_when_known_and_all_zero():
    gc = cl.GitCleanliness(known=True, dirty=False, ahead=0, unpushed_branches=0)
    assert cl.is_git_clean(gc)


def test_unknown_is_never_clean():
    gc = cl.GitCleanliness(known=False, dirty=False, ahead=0, unpushed_branches=0)
    assert not cl.is_git_clean(gc)


@pytest.mark.parametrize("kw", [
    {"dirty": True},
    {"ahead": 1},
    {"unpushed_branches": 2},
])
def test_any_dirty_signal_is_not_clean(kw):
    base = {"known": True, "dirty": False, "ahead": 0, "unpushed_branches": 0}
    gc = cl.GitCleanliness(**{**base, **kw})
    assert not cl.is_git_clean(gc)


def test_default_is_conservative_not_clean():
    # The dataclass defaults (known=False, dirty=True) must read as not clean.
    assert not cl.is_git_clean(cl.GitCleanliness())


# ── at_rest (combines git + in_flight) ───────────────────────────────────────

def test_at_rest_requires_clean_and_not_in_flight():
    clean = cl.GitCleanliness(known=True, dirty=False, ahead=0, unpushed_branches=0)
    assert cl.at_rest(clean, in_flight=False)
    assert not cl.at_rest(clean, in_flight=True)


def test_at_rest_false_when_dirty_even_if_not_in_flight():
    dirty = cl.GitCleanliness(known=True, dirty=True)
    assert not cl.at_rest(dirty, in_flight=False)


# ── probe_command ────────────────────────────────────────────────────────────

def test_probe_command_is_readonly_and_emits_markers():
    cmd = cl.probe_command()
    assert "status --porcelain" in cmd
    assert "OBLIGATION_PROBE" in cmd
    # Read-only: no mutating git verbs.
    for bad in ("git push", "git commit", "git reset", "git checkout", "rm "):
        assert bad not in cmd


def test_probe_command_honors_workspace_glob():
    assert "/custom/ws" in cl.probe_command("/custom/ws")


def test_probe_command_glob_is_unquoted_so_it_expands():
    # Regression: the glob must NOT be single-quoted inside the script body, or
    # bash treats `*` literally and the probe finds no repo on every real
    # CodeSpace (silently known=False). Assert the bare glob appears and is not
    # wrapped in single quotes right around the `*`.
    cmd = cl.probe_command("/workspaces/*")
    assert "/workspaces/*/.git" in cmd
    assert "'/workspaces/*'" not in cmd


def test_probe_command_scans_all_repos_and_uses_not_remotes():
    # The hardened probe aggregates across repos (a for-loop, not first-match)
    # and uses `--not --remotes` (well-defined without an upstream) rather than
    # the `@{u}` framing that reads 0 on a no-upstream branch.
    cmd = cl.probe_command()
    assert "--not --remotes" in cmd
    assert "@{u}" not in cmd
    assert "found=1" in cmd  # loop marks a repo was seen


def test_probe_command_uses_nullglob():
    # An unmatched glob must vanish (nullglob), not pass a literal path.
    assert "nullglob" in cl.probe_command()


# ── parse_probe ──────────────────────────────────────────────────────────────

def test_parse_clean_output():
    out = "OBLIGATION_PROBE=1\nDIRTY=0\nAHEAD=0\nUNPUSHED_BRANCHES=0\n"
    gc = cl.parse_probe(out)
    assert gc.known and not gc.dirty and gc.ahead == 0 and gc.unpushed_branches == 0
    assert cl.is_git_clean(gc)


def test_parse_dirty_output():
    out = "OBLIGATION_PROBE=1\nDIRTY=1\nAHEAD=3\nUNPUSHED_BRANCHES=2\n"
    gc = cl.parse_probe(out)
    assert gc.dirty and gc.ahead == 3 and gc.unpushed_branches == 2
    assert not cl.is_git_clean(gc)


@pytest.mark.parametrize("out", ["", None, "garbage\nno markers", "DIRTY=0\nAHEAD=0"])
def test_parse_without_marker_is_unknown(out):
    # Missing the OBLIGATION_PROBE marker -> conservative unknown (not clean).
    gc = cl.parse_probe(out)
    assert not gc.known and not cl.is_git_clean(gc)


def test_parse_tolerates_bad_ints():
    out = "OBLIGATION_PROBE=1\nDIRTY=0\nAHEAD=x\nUNPUSHED_BRANCHES=\n"
    gc = cl.parse_probe(out)
    assert gc.known and gc.ahead == 0 and gc.unpushed_branches == 0


def test_parse_ignores_surrounding_noise():
    out = "some login banner\nOBLIGATION_PROBE=1\nDIRTY=0\nAHEAD=0\nUNPUSHED_BRANCHES=0\nbye\n"
    gc = cl.parse_probe(out)
    assert cl.is_git_clean(gc)


def test_roundtrip_probe_shape_is_parseable():
    # A representative clean emission parses to at-rest (not in flight).
    out = "\n".join(["OBLIGATION_PROBE=1", "DIRTY=0", "AHEAD=0", "UNPUSHED_BRANCHES=0"])
    assert cl.at_rest(cl.parse_probe(out), in_flight=False)


# ── probe_cleanliness (async runner over a fake manager) ─────────────────────

class _FakeManager:
    def __init__(self, stdout="", exit_code=0, raises=False):
        self._stdout = stdout
        self._exit = exit_code
        self._raises = raises
        self.calls: list[str] = []

    async def exec_command(self, name, command, timeout=None):
        self.calls.append(command)
        if self._raises:
            raise RuntimeError("ssh dropped")
        from types import SimpleNamespace
        return SimpleNamespace(stdout=self._stdout, stderr="", exit_code=self._exit)


@pytest.mark.asyncio
async def test_probe_cleanliness_clean(monkeypatch):
    mgr = _FakeManager(stdout="OBLIGATION_PROBE=1\nDIRTY=0\nAHEAD=0\nUNPUSHED_BRANCHES=0\n")
    gc = await cl.probe_cleanliness(mgr, "cs")
    assert cl.is_git_clean(gc)
    assert any("status --porcelain" in c for c in mgr.calls)


@pytest.mark.asyncio
async def test_probe_cleanliness_dirty():
    mgr = _FakeManager(stdout="OBLIGATION_PROBE=1\nDIRTY=1\nAHEAD=0\nUNPUSHED_BRANCHES=0\n")
    gc = await cl.probe_cleanliness(mgr, "cs")
    assert not cl.is_git_clean(gc)


@pytest.mark.asyncio
async def test_probe_cleanliness_nonzero_exit_is_unknown():
    mgr = _FakeManager(stdout="OBLIGATION_PROBE=1\nDIRTY=0\n", exit_code=2)
    gc = await cl.probe_cleanliness(mgr, "cs")
    assert not gc.known


@pytest.mark.asyncio
async def test_probe_cleanliness_exec_failure_degrades():
    mgr = _FakeManager(raises=True)
    gc = await cl.probe_cleanliness(mgr, "cs")
    assert not gc.known  # degrade-safe, no raise
