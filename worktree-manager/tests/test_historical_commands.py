"""Finite recognition and real selected disposal inside disposable test roots."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import historical_commands as commands
from worktree_manager import self_install


# Retained prototype producers, independent of today's incremental writer.
ARCHIVED_FORMS = {
    'worktree-manager': (
        b'#!/usr/bin/env bash\nset -euo pipefail\n\n'
        b'if [[ -z "\x24{WORKTREE_PROJECT:-}" ]]; then\n'
        b'    echo "ERROR: WORKTREE_PROJECT is not set. Use the project-specific binstub or export WORKTREE_PROJECT." >&2\n'
        b'    exit 1\nfi\n\n'
        b'# Dual-layout: prefer new runtime, fall back to legacy\n'
        b'if [[ -x "\x24HOME/.worktree-manager/.venv/bin/python" ]]; then\n'
        b'    PYTHON="\x24HOME/.worktree-manager/.venv/bin/python"\n'
        b'    export PYTHONPATH="\x24HOME/.worktree-manager/lib"\n'
        b'else\n    echo "ERROR: Venv not found. Run the installer first." >&2\n'
        b'    exit 1\nfi\n\nunset PYTHONHOME\n'
        b'exec "\x24PYTHON" -m worktree_manager "\x24@"\n'
    ),
    'worktree-manager.cmd': (
        b'@echo off\nsetlocal\n\nif not defined WORKTREE_PROJECT (\n'
        b'    echo ERROR: WORKTREE_PROJECT is not set. Use the project-specific binstub or set WORKTREE_PROJECT. >&2\n'
        b'    exit /b 1\n)\n\nrem Resolve runtime\n'
        b'set "NEW_RUNTIME=%USERPROFILE%\\.worktree-manager"\n\n'
        b'if exist "%NEW_RUNTIME%\\.venv\\Scripts\\python.exe" (\n'
        b'    set "PYTHON=%NEW_RUNTIME%\\.venv\\Scripts\\python.exe"\n'
        b'    set "PYTHONPATH=%NEW_RUNTIME%\\lib"\n) else (\n'
        b'    echo ERROR: Venv not found. Run the installer first. >&2\n'
        b'    exit /b 1\n)\n\n"%PYTHON%" -m worktree_manager %*\nexit /b %ERRORLEVEL%\n'
    ),
}



@pytest.fixture
def scope(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.delenv('WORKTREE_MANAGER_ROOT', raising=False)
    root = home / '.worktree-manager'
    root.mkdir()
    bins = home / '.local' / 'bin'
    bins.mkdir(parents=True)
    return root, bins


def archived(bins, name='worktree-manager'):
    path = bins / name
    path.write_bytes(ARCHIVED_FORMS[name])
    return path


@pytest.mark.parametrize('name', ['worktree-manager', 'worktree-manager.cmd'])
def test_exact_archive_is_a_candidate_without_installing_or_cleaning(scope, name):
    root, bins = scope
    path = archived(bins, name)
    before = path.read_bytes()
    finding, = commands.inspect(root, bins)
    assert {key: value for key, value in finding.items() if key != 'evidence'} == {
        'detector': 'manager-prototype-commands-v1', 'revision': 1,
        'resource': str(path), 'name': name,
        'sha256': hashlib.sha256(before).hexdigest(),
        'status': 'candidate', 'reason': 'exact-archived-prototype-command',
    }
    assert path.read_bytes() == before
    assert finding['evidence']['physical']['inode'] == path.stat().st_ino
    result = commands.apply(root, bins, finding)
    if os.name == 'nt':
        assert result == {
            'status': 'applied', 'resource': str(path),
            'reason': 'archived-command-handle-removed',
        }
        assert not path.exists()
        assert commands.apply(root, bins, finding)['status'] == 'already-absent'
    else:
        assert result['status'] == 'preserved'
        assert result['reason'] == 'posix-native-command-removal-unqualified'
        assert path.read_bytes() == before



@pytest.mark.parametrize('kind', ['edited', 'foreign', 'current'])
def test_nonhistorical_commands_are_preserved(scope, kind):
    root, bins = scope
    path = bins / 'worktree-manager'
    original = ARCHIVED_FORMS[path.name].decode('utf-8')
    content = {'edited': original + '# edited\n', 'foreign': '# unrelated command\n',
               'current': self_install._expected_binstub_contents()[path.name]}[kind]
    path.write_bytes(content.encode('utf-8'))
    finding, = commands.inspect(root, bins)
    assert finding['status'] == 'preserved'
    assert finding['reason'] == 'not-an-archived-prototype-command'
    assert finding['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert commands.apply(root, bins, finding)['status'] == 'preserved'
    assert path.read_bytes() == content.encode('utf-8')


def test_inspection_is_finite_and_retains_noncommand_root_artifacts(scope):
    root, bins = scope
    for name in ('worktree-manager.ps1', 'other-project', 'agent-logger'):
        (bins / name).write_text('unselected', encoding='utf-8')
    (root / '.venv').mkdir()
    (root / 'lib').mkdir()
    assert commands.inspect(root, bins) == []
    assert sorted(p.name for p in root.iterdir()) == ['.venv', 'lib']


def test_changed_after_report_is_refused_and_siblings_are_preserved(scope):
    root, bins = scope
    selected = archived(bins)
    sibling = archived(bins, 'worktree-manager.cmd')
    unselected = bins / 'custom'
    unselected.write_bytes(b'untouched')
    finding = next(f for f in commands.inspect(root, bins) if f['resource'] == str(selected))
    selected.write_bytes(b'changed after reporting')
    before = {p.name: p.read_bytes() for p in bins.iterdir()}
    result = commands.apply(root, bins, finding)
    assert result['status'] == 'preserved'
    assert result['reason'] == 'reported-content-changed'
    assert {p.name: p.read_bytes() for p in bins.iterdir()} == before
    assert sibling.exists() and unselected.exists()


@pytest.mark.parametrize('change', ['detector', 'revision', 'bool-revision', 'name', 'path', 'digest', 'status'])
def test_forged_or_unselected_evidence_never_grants_authority(scope, change):
    root, bins = scope
    selected = archived(bins)
    finding, = commands.inspect(root, bins)
    if change == 'detector':
        finding['detector'] = 'another-owner'
    elif change == 'revision':
        finding['revision'] = 2
    elif change == 'bool-revision':
        finding['revision'] = True
    elif change == 'name':
        finding['name'] = 'worktree-manager.ps1'
    elif change == 'path':
        finding['resource'] = str(root / selected.name)
    elif change == 'digest':
        finding['sha256'] = '0' * 64
    else:
        finding['status'] = 'preserved'
    assert commands.apply(root, bins, finding)['status'] == 'preserved'
    assert selected.exists()


def test_absence_is_only_confirmed_for_valid_selected_archive_evidence(scope):
    root, bins = scope
    selected = archived(bins)
    finding, = commands.inspect(root, bins)
    selected.unlink()
    assert commands.apply(root, bins, finding)['status'] == 'already-absent'
    finding['sha256'] = '0' * 64
    assert commands.apply(root, bins, finding)['status'] == 'preserved'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory'])
def test_nonordinary_command_is_not_admitted(scope, kind):
    root, bins = scope
    target = root / 'target'
    target.write_bytes(ARCHIVED_FORMS['worktree-manager'])
    path = bins / 'worktree-manager'
    try:
        if kind == 'symlink':
            path.symlink_to(target)
        elif kind == 'hardlink':
            os.link(target, path)
        else:
            path.mkdir()
    except OSError as exc:
        pytest.skip(f'{kind} creation unavailable: {exc}')
    finding, = commands.inspect(root, bins)
    assert finding['status'] == 'preserved'
    assert finding['sha256'] is None
    assert target.read_bytes() == ARCHIVED_FORMS[path.name]


def test_reparse_attribute_refuses_even_ordinary_looking_file(scope, monkeypatch):
    root, bins = scope
    path = archived(bins)
    real_lstat = Path.lstat
    def lstat(candidate):
        result = real_lstat(candidate)
        if candidate == path:
            return SimpleNamespace(st_mode=result.st_mode, st_nlink=1, st_file_attributes=0x400)
        return result
    monkeypatch.setattr(Path, 'lstat', lstat)
    finding, = commands.inspect(root, bins)
    assert finding['status'] == 'preserved'
    assert finding['reason'] == 'linked-or-reparse-path'


def test_no_follow_when_scope_ancestor_is_linked(scope):
    root, bins = scope
    archived(bins)
    link = bins.parent / 'linked-bin'
    try:
        link.symlink_to(bins, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'directory symlink creation unavailable: {exc}')
    findings = commands.inspect(root, link)
    assert findings and all(f['status'] == 'preserved' for f in findings)
    assert all(f['sha256'] is None for f in findings)


def test_unreadable_command_reports_uncertainty(scope, monkeypatch):
    root, bins = scope
    path = archived(bins)
    real_open = os.open
    def denied(candidate, flags, *args, **kwargs):
        if Path(candidate) == path:
            raise PermissionError('denied')
        return real_open(candidate, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', denied)
    finding, = commands.inspect(root, bins)
    assert finding['status'] == 'preserved'
    assert finding['sha256'] is None
    assert finding['reason'] == 'command-unreadable'


def test_relative_or_traversing_scope_is_not_read(scope):
    root, bins = scope
    archived(bins)
    for unsafe in (Path('relative-bin'), bins / '..' / bins.name):
        findings = commands.inspect(root, unsafe)
        assert findings and all(f['status'] == 'preserved' for f in findings)
        assert all(f['reason'] == 'invalid-owner-scope' for f in findings)


def test_selected_only_cleanup_preserves_sibling_and_unselected_artifact(scope):
    root, bins = scope
    selected = archived(bins)
    sibling = archived(bins, 'worktree-manager.cmd')
    (bins / 'custom').write_bytes(b'unselected')
    artifact = root / 'unselected-artifact'
    artifact.write_bytes(b'untouched')
    finding = next(f for f in commands.inspect(root, bins) if f['resource'] == str(selected))
    result = commands.apply(root, bins, finding)
    assert result['status'] == ('applied' if os.name == 'nt' else 'preserved')
    assert selected.exists() is (os.name != 'nt')
    assert sibling.read_bytes() == ARCHIVED_FORMS[sibling.name]
    assert (bins / 'custom').read_bytes() == b'unselected'
    assert artifact.read_bytes() == b'untouched'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory'])
def test_link_or_nonfile_substitution_after_report_is_refused(scope, kind):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    target = root / 'target'
    path.rename(target)
    try:
        if kind == 'symlink':
            path.symlink_to(target)
        elif kind == 'hardlink':
            os.link(target, path)
        else:
            path.mkdir()
    except OSError as exc:
        pytest.skip(f'{kind} creation unavailable: {exc}')
    result = commands.apply(root, bins, finding)
    assert result['status'] == 'preserved'
    assert result['reason'] in ('linked-or-reparse-path', 'not-an-unshared-regular-file')
    assert target.exists() and path.exists()


def test_same_bytes_different_file_during_observation_are_not_admitted(scope, monkeypatch):
    root, bins = scope
    path = archived(bins)
    previous = root / 'previous'
    real_open = os.open
    def replaced(candidate, flags, *args, **kwargs):
        if Path(candidate) == path and not previous.exists():
            path.rename(previous)
            archived(bins)
        return real_open(candidate, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', replaced)
    finding, = commands.inspect(root, bins)
    assert finding['status'] == 'preserved'
    assert finding['reason'] == 'physical-file-changed'
    assert finding['sha256'] is None
    assert previous.read_bytes() == path.read_bytes()


def test_same_archive_bytes_in_replacement_file_do_not_reuse_report_authority(scope):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    original = root / 'original'
    path.rename(original)
    archived(bins)
    result = commands.apply(root, bins, finding)
    assert result['status'] == 'preserved'
    assert result['reason'] == 'reported-physical-identity-changed'
    assert original.read_bytes() == path.read_bytes()


@pytest.mark.parametrize('name', ['current-version', 'last-known-good', 'current', 'dev-claim.json', '.venv', 'lib'])
def test_current_recovery_or_unresolved_prototype_route_is_never_obsolete(scope, name):
    root, bins = scope
    path = archived(bins)
    protected = root / name
    if name in ('.venv', 'lib'):
        protected.mkdir()
    else:
        protected.write_bytes(b'current or unresolved route')
    findings = commands.inspect(root, bins)
    assert findings and all(f['status'] == 'preserved' for f in findings)
    assert all(f['reason'] == 'manager-selected-or-recovery-binding' for f in findings)
    assert path.exists() and protected.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native command disposal qualification')
def test_selector_appearing_after_report_is_rechecked_under_owner_custody(scope):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    (root / 'current-version').write_bytes(b'1.2.3')
    result = commands.apply(root, bins, finding)
    assert result['status'] == 'preserved'
    assert result['reason'] == 'manager-selected-or-recovery-binding'
    assert path.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native command disposal qualification')
def test_replacement_at_native_open_boundary_is_refused(scope, monkeypatch):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    real_open = commands._WindowsCommand._open
    previous = root / 'previous'
    def replaced(held, candidate, *, directory):
        if not directory:
            path.rename(previous)
            archived(bins)
        return real_open(held, candidate, directory=directory)
    monkeypatch.setattr(commands._WindowsCommand, '_open', replaced)
    result = commands.apply(root, bins, finding)
    assert result['status'] == 'preserved'
    assert result['reason'] == 'reported-physical-identity-changed'
    assert previous.read_bytes() == path.read_bytes()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native command disposal qualification')
def test_mutation_pinned_handle_blocks_foreign_replacement_then_deletes_real_object(scope, monkeypatch):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    foreign = root / 'foreign'
    foreign.write_bytes(b'foreign replacement')
    original_dispose = commands._WindowsCommand.dispose
    attempted = []
    def contested(held):
        assert isinstance(held.pinned, commands.PinnedPath)
        assert held.pinned.path == path
        with pytest.raises(OSError):
            os.replace(foreign, path)
        attempted.append(True)
        original_dispose(held)
    monkeypatch.setattr(commands._WindowsCommand, 'dispose', contested)
    result = commands.apply(root, bins, finding)
    assert attempted == [True]
    assert result['status'] == 'applied'
    assert not path.exists()
    assert foreign.read_bytes() == b'foreign replacement'


@pytest.mark.skipif(os.name != 'nt', reason='Windows native command disposal qualification')
def test_actual_selector_publication_lease_refuses_cleanup_without_effect(scope):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    runtime = self_install._runtime_installer().runtime
    with runtime._publication_lease(root, timeout=0):
        result = commands.apply(root, bins, finding)
    assert result['status'] in ('preserved', 'failed')
    assert path.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native command disposal qualification')
def test_native_disposition_failure_never_reports_success_or_path_unlinks(scope, monkeypatch):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    def refuse(held):
        raise PermissionError('native disposition denied')
    monkeypatch.setattr(commands._WindowsCommand, 'dispose', refuse)
    result = commands.apply(root, bins, finding)
    assert result['status'] == 'failed'
    assert path.exists()


def test_missing_or_foreign_physical_evidence_is_not_authority(scope):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    finding.pop('evidence')
    assert commands.apply(root, bins, finding)['status'] == 'preserved'
    assert path.exists()


def test_protected_selection_reports_only_existing_archived_names(scope):
    root, bins = scope
    path = archived(bins)
    (root / '.venv').mkdir()
    finding, = commands.inspect(root, bins)
    assert finding['resource'] == str(path)
    assert finding['status'] == 'preserved'
    assert finding['reason'] == 'manager-selected-or-recovery-binding'
    assert finding['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not (bins / 'worktree-manager.cmd').exists()


@pytest.mark.parametrize('command_name', ['worktree-manager', 'worktree-manager.cmd'])
@pytest.mark.parametrize('protected_name', ['.venv', 'lib', 'current-version'])
def test_empty_substituted_root_never_bypasses_actual_archive_target(scope, protected_name, command_name):
    root, bins = scope
    path = archived(bins, command_name)
    finding, = commands.inspect(root, bins)
    protected = root / protected_name
    if protected_name == 'current-version':
        protected.write_bytes(b'current')
    else:
        protected.mkdir()
    foreign = root.parent / 'empty-foreign-root'
    foreign.mkdir()
    observed, = commands.inspect(foreign, bins)
    assert observed['status'] == 'preserved'
    assert observed['reason'] == 'archive-target-root-mismatch'
    finding['evidence']['root'] = str(foreign)
    result = commands.apply(foreign, bins, finding)
    assert result['status'] == 'preserved'
    assert result['reason'] == 'archive-target-root-mismatch'
    assert path.read_bytes() == ARCHIVED_FORMS[path.name]
    assert protected.exists() and list(foreign.iterdir()) == []


@pytest.mark.parametrize('variable,value', [
    ('HOME', None), ('USERPROFILE', None), ('HOME', 'relative-home'),
    ('USERPROFILE', 'relative-profile'), ('HOME', 'other'), ('USERPROFILE', 'other'),
])
def test_unknown_or_contradictory_execution_homes_preserve(scope, monkeypatch, variable, value):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    if value is None:
        monkeypatch.delenv(variable)
    else:
        monkeypatch.setenv(variable, str(root.parent / 'other-home') if value == 'other' else value)
    observed, = commands.inspect(root, bins)
    assert observed['status'] == 'preserved'
    assert observed['reason'] in ('archive-execution-home-unresolved', 'archive-execution-homes-contradictory')
    assert commands.apply(root, bins, finding)['status'] == 'preserved'
    assert path.read_bytes() == ARCHIVED_FORMS[path.name]


def test_actual_writer_root_override_never_borrows_archive_target_custody(scope, monkeypatch):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    monkeypatch.setenv('WORKTREE_MANAGER_ROOT', str(root.parent / 'writer-root'))
    result = commands.apply(root, bins, finding)
    assert result['status'] == 'preserved'
    assert result['reason'] == 'manager-writer-root-differs-from-archive-target'
    assert path.exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows native command disposal qualification')
@pytest.mark.parametrize('change', ['selector', 'home', 'writer-root'])
def test_actual_binding_is_rechecked_at_final_disposition_boundary(scope, monkeypatch, change):
    root, bins = scope
    path = archived(bins)
    finding, = commands.inspect(root, bins)
    original_dispose = commands._WindowsCommand.dispose
    def changed(held):
        if change == 'selector':
            (root / 'current-version').write_bytes(b'now-selected')
        elif change == 'home':
            monkeypatch.setenv('HOME', str(root.parent / 'other-home'))
        else:
            monkeypatch.setenv('WORKTREE_MANAGER_ROOT', str(root.parent / 'other-writer'))
        original_dispose(held)
    monkeypatch.setattr(commands._WindowsCommand, 'dispose', changed)
    result = commands.apply(root, bins, finding)
    assert result['status'] == 'preserved'
    assert path.read_bytes() == ARCHIVED_FORMS[path.name]
