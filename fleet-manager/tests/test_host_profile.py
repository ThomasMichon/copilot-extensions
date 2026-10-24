"""Offline root/permission and parser/kernel/process models; never host activation."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from fleet_manager import host_profile as host  # noqa: E402


class HostProfileTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(__file__).resolve().parents[1] / (".host-profile-test-" + uuid.uuid4().hex)
        self.work.mkdir()
        self.profiles, self.state = self.work / "profiles", self.work / "state"
        self.profiles.mkdir()
        self.kernel, self.used, self.uncertain = None, False, False
        self.calls, self.modes, self.owners, self.fd_paths = [], {}, {}, {}
        self.executable = self.work / "apparmor_parser"
        self.executable.write_bytes(b"offline parser fixture")
        self.modes[self.executable] = 0o755
        self.raw_lstat, self.raw_fstat, self.raw_open = Path.lstat, os.fstat, os.open
        self.real_parser, self.real_root = host._parser, host._root
        self.real_kernel, self.real_unused = host._kernel, host._unused
        from contextlib import ExitStack
        self.stack = ExitStack()
        self.stack.enter_context(mock.patch.multiple(
            host, STATE_ROOT=self.state, PROFILE_ROOT=self.profiles, _root=lambda: None,
            _kernel=lambda: self.kernel, _unused=self.unused, _parser=self.parser,
            _flush_directory=lambda _: None, PARSER=str(self.executable),
        ))
        self.stack.enter_context(mock.patch.dict(sys.modules, {
            "fcntl": SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *_: None),
        }))
        self.stack.enter_context(mock.patch.object(Path, "lstat", lambda path: self.lstat(path)))
        self.stack.enter_context(mock.patch.object(os, "fstat", self.fstat))
        self.stack.enter_context(mock.patch.object(os, "open", self.open))
        self.stack.enter_context(mock.patch.object(
            os, "fchmod", lambda fd, mode: self.modes.__setitem__(self.fd_paths[fd], mode), create=True,
        ))

    def tearDown(self):
        self.stack.close()
        shutil.rmtree(self.work)

    def metadata(self, info, path):
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            mode = 0o700 if path == self.state or self.state in path.parents else 0o755
        elif path.name in ("receipt.json", "receipt.next", "lock"):
            mode = 0o600
        mode = self.modes.get(path, mode)
        uid, gid = self.owners.get(path, (0, 0))
        return SimpleNamespace(
            st_dev=info.st_dev, st_ino=info.st_ino, st_uid=uid, st_gid=gid,
            st_mode=stat.S_IFMT(info.st_mode) | mode, st_nlink=info.st_nlink,
            st_size=info.st_size, st_mtime_ns=info.st_mtime_ns,
        )

    def lstat(self, path):
        return self.metadata(self.raw_lstat(path), path)

    def fstat(self, fd):
        return self.metadata(self.raw_fstat(fd), self.fd_paths[fd])

    def open(self, path, flags, mode=0o777):
        path = Path(path)
        fd = self.raw_open(path, flags, 0o600)
        self.fd_paths[fd] = path
        if flags & os.O_CREAT:
            self.modes[path] = mode
        return fd

    def receipt(self):
        return self.state / host.PROFILE_NAME / "receipt.json"

    def record(self):
        return json.loads(self.receipt().read_text())

    def parser(self, action, path):
        self.calls.append(action)
        expected = {"validate": "prepared", "add": "load-pending", "remove": "unload-pending"}
        self.assertEqual(self.record()["phase"], expected[action])
        host._owned(self.record())
        if action == "add":
            self.kernel = "enforce"
        elif action == "remove":
            self.assertTrue(self.record()["activation_quiesced"])
            self.kernel = None

    def unused(self):
        if self.uncertain:
            raise OSError("fixture-only inspection failure")
        if self.used:
            raise host.ProfileError("profile-in-use")

    def install(self):
        self.assertEqual(host.operate("install")["state"], "ready")
        return host._profile(self.record()["digest"])

    def test_ready_repeat_and_status_are_observational(self):
        self.assertEqual(host.operate("status")["state"], "absent")
        self.assertFalse(self.state.exists())
        self.install()
        saved = self.receipt().read_bytes()
        for action in ("install", "status"):
            self.assertEqual(host.operate(action)["state"], "ready")
        self.assertEqual(self.calls, ["validate", "add"])
        self.assertEqual(self.receipt().read_bytes(), saved)

    def test_uninstall_requires_asserted_fence_and_retains_terminal_state(self):
        path = self.install()
        with self.assertRaisesRegex(host.ProfileError, "quiescence"):
            host.operate("uninstall")
        self.assertEqual(host.operate("uninstall", activation_quiesced=True)["state"], "retired")
        self.assertFalse(path.exists())
        self.assertTrue(self.receipt().exists())
        self.assertTrue(self.receipt().with_name("lock").exists())
        saved = self.receipt().read_bytes()
        self.assertEqual(host.operate("uninstall", activation_quiesced=True)["state"], "retired")
        self.assertEqual(self.receipt().read_bytes(), saved)
        self.install()
        self.assertEqual(self.calls, ["validate", "add", "remove", "validate", "add"])

    def test_template_drift_is_readonly_and_old_owned_bytes_can_retire(self):
        path = self.install()
        saved, original = self.receipt().read_bytes(), path.read_bytes()
        with mock.patch.object(host, "profile_bytes", return_value=original + b"\n"):
            self.assertEqual(host.operate("install")["state"], "template-drift")
            self.assertEqual(host.operate("status")["state"], "template-drift")
            self.assertEqual(self.receipt().read_bytes(), saved)
            host.operate("uninstall", activation_quiesced=True)
        self.assertFalse(path.exists())

    def test_foreign_same_bytes_kernel_or_file_are_not_adopted(self):
        self.kernel = "enforce"
        with self.assertRaises(host.ProfileError):
            host.operate("install")
        self.assertEqual(self.calls, [])
        self.kernel = None
        self.assertFalse(self.state.exists())
        path = self.profiles / (host.PROFILE_NAME + "." + "a" * 64)
        path.write_bytes(host.profile_bytes())
        with self.assertRaises(host.ProfileError):
            host.operate("install")
        self.assertEqual(path.read_bytes(), host.profile_bytes())

    def test_ambiguous_lock_only_state_cannot_resume(self):
        state = self.state / host.PROFILE_NAME
        state.mkdir(parents=True)
        (state / "lock").write_bytes(b"")
        self.modes[state / "lock"] = 0o600
        with self.assertRaisesRegex(host.ProfileError, "without-receipt"):
            host.operate("install")
        self.assertEqual(self.calls, [])

    def test_active_or_uncertain_processes_prevent_unload(self):
        self.install()
        saved = self.receipt().read_bytes()
        for used, uncertain in ((True, False), (False, True)):
            self.used, self.uncertain = used, uncertain
            with self.assertRaises((host.ProfileError, OSError)):
                host.operate("uninstall", activation_quiesced=True)
            self.assertEqual(self.receipt().read_bytes(), saved)
        self.assertNotIn("remove", self.calls)

    def test_timeout_or_caller_loss_retains_pending_and_never_replays(self):
        def fail(action, path):
            self.parser(action, path) if action == "validate" else None
            if action == "add":
                self.assertEqual(self.record()["phase"], "load-pending")
                raise host.ProfileError("parser-outcome-unknown")
        with mock.patch.object(host, "_parser", side_effect=fail):
            with self.assertRaises(host.ProfileError):
                host.operate("install")
        saved = self.receipt().read_bytes()
        for action in ("install", "status", "uninstall"):
            with self.assertRaisesRegex(host.ProfileError, "pending-outcome"):
                host.operate(action, activation_quiesced=True)
        self.assertEqual(self.receipt().read_bytes(), saved)

    def test_validation_failure_has_no_load_and_can_be_explicitly_retired(self):
        with mock.patch.object(host, "_parser", side_effect=host.ProfileError("parser-syntax-error")):
            with self.assertRaises(host.ProfileError):
                host.operate("install")
        self.assertEqual(self.record()["phase"], "validation-failed")
        self.assertEqual(self.calls, [])
        host.operate("uninstall", activation_quiesced=True)
        self.assertEqual(self.record()["phase"], "retired")

    def test_foreign_owner_mode_content_and_replacement_are_refused(self):
        path = self.install()
        for owner, mode in (((1234, 0), 0o444), ((0, 0), 0o644)):
            self.owners[path], self.modes[path] = owner, mode
            with self.assertRaises(host.ProfileError):
                host.operate("uninstall", activation_quiesced=True)
        self.owners[path], self.modes[path] = (0, 0), 0o444
        old = path.with_name("preserved")
        path.rename(old)
        path.write_bytes(old.read_bytes())
        with self.assertRaises(host.ProfileError):
            host.operate("status")
        path.unlink()
        old.rename(path)
        path.write_bytes(b"changed")
        with self.assertRaises(host.ProfileError):
            host.operate("status")

    def test_linked_or_hardlinked_owned_profile_is_refused(self):
        path = self.install()
        saved = path.with_name("saved")
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(host.ProfileError):
            host.operate("status")
        path.unlink()
        saved.rename(path)
        os.link(path, saved)
        with self.assertRaises(host.ProfileError):
            host.operate("uninstall", activation_quiesced=True)

    def test_replaced_receipt_lock_and_private_directory_drift_refuse(self):
        self.install()
        lock = self.receipt().with_name("lock")
        self.modes[lock] = 0o644
        with self.assertRaises(host.ProfileError):
            host.operate("status")
        self.modes[lock] = 0o600
        state = self.receipt().parent
        self.modes[state] = 0o755
        with self.assertRaises(host.ProfileError):
            host.operate("status")
        self.modes[state] = 0o700
        content = self.receipt().read_bytes()
        saved = self.receipt().with_name("saved")
        self.receipt().rename(saved)
        self.receipt().write_bytes(content)
        self.modes[self.receipt()] = 0o600
        with self.assertRaisesRegex(host.ProfileError, "unattributed"):
            host.operate("status")

    def test_cleanup_guard_never_deletes_replaced_file(self):
        path = self.install()
        original_save = host._save
        def replace(state, record):
            original_save(state, record)
            if record["phase"] == "unloaded-cleanup-pending":
                path.rename(path.with_name("old"))
                path.write_bytes(b"foreign")
        with mock.patch.object(host, "_save", side_effect=replace):
            with self.assertRaises(host.ProfileError):
                host.operate("uninstall", activation_quiesced=True)
        self.assertEqual(path.read_bytes(), b"foreign")
        self.assertEqual(self.record()["phase"], "unloaded-cleanup-pending")

    def test_parser_uses_fixed_bounded_flags_and_sanitized_diagnostics(self):
        streams = [SimpleNamespace(fileno=lambda n=n: n, close=lambda: None) for n in (101, 102)]
        process = mock.Mock(pid=42, stdout=streams[0], stderr=streams[1])
        process.poll.return_value = 0
        process.wait.return_value = 1
        selector = mock.Mock()
        selector.get_map.side_effect = [True, False]
        selector.select.return_value = [(SimpleNamespace(fileobj=streams[0]), 1)]
        with (mock.patch.object(host.subprocess, "Popen", return_value=process) as spawn,
              mock.patch("selectors.DefaultSelector", return_value=selector),
              mock.patch.object(os, "set_blocking"), mock.patch.object(os, "read", return_value=b"syntax error /private/example"),
              mock.patch.object(signal, "SIGKILL", 9, create=True)):
            with self.assertRaisesRegex(host.ProfileError, "^parser-syntax-error$"):
                self.real_parser("validate", Path("/etc/apparmor.d/example"))
        args, kwargs = spawn.call_args
        self.assertEqual(args[0][:4], [host.PARSER, "--jobs=1", "--skip-cache", "--skip-kernel-load"])
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(set(kwargs["env"]), {"PATH", "LC_ALL"})

    def test_root_guard_imports_on_windows_and_blocks_before_filesystem(self):
        with (mock.patch.object(sys, "platform", "win32"),
              mock.patch.object(host, "_root", self.real_root),
              mock.patch.object(host, "_locked", side_effect=AssertionError("filesystem reached"))):
            with self.assertRaisesRegex(host.ProfileError, "linux-root"):
                host.operate("install")

    def test_actual_kernel_and_process_decoders_use_only_fixtures(self):
        listing = self.work / "kernel"
        proc = self.work / "proc"
        current = proc / "123/attr/current"
        current.parent.mkdir(parents=True)
        current.write_text(host.PROFILE_NAME + "//child (enforce)\n")
        listing.write_text(host.PROFILE_NAME + " (enforce)\n")
        with mock.patch.multiple(host, KERNEL_PROFILES=listing, PROC=proc):
            self.assertEqual(self.real_kernel(), "enforce")
            with self.assertRaisesRegex(host.ProfileError, "profile-in-use"):
                self.real_unused()
            current.unlink()
            with self.assertRaises(OSError):
                self.real_unused()
            listing.write_text((host.PROFILE_NAME + " (enforce)\n") * 2)
            with self.assertRaisesRegex(host.ProfileError, "ambiguous"):
                self.real_kernel()

    def test_unload_unknown_and_preownership_crash_are_not_replayed(self):
        self.install()
        with mock.patch.object(host, "_parser", side_effect=host.ProfileError("parser-outcome-unknown")):
            with self.assertRaises(host.ProfileError):
                host.operate("uninstall", activation_quiesced=True)
        self.assertEqual(self.record()["phase"], "unload-pending")
        with self.assertRaisesRegex(host.ProfileError, "pending-outcome"):
            host.operate("uninstall", activation_quiesced=True)
        shutil.rmtree(self.state)
        for path in self.profiles.iterdir():
            path.unlink()
        self.kernel = None
        write = host._write_all
        def interrupt(fd, data):
            write(fd, data)
            if data == host.profile_bytes():
                raise KeyboardInterrupt
        with mock.patch.object(host, "_write_all", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                host.operate("install")
        self.assertEqual(self.record()["phase"], "preparing")
        with self.assertRaisesRegex(host.ProfileError, "pending-outcome"):
            host.operate("install")

    def test_cli_reports_bounded_json_without_host_details(self):
        from contextlib import redirect_stdout
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(host.main(["uninstall"]), 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["reason"], "activation-quiescence-assertion-required")
        self.assertNotIn(str(self.work), output.getvalue())

    def test_parser_deadline_and_output_bound_clean_up_only_owned_process(self):
        streams = [SimpleNamespace(fileno=lambda n=n: n, close=lambda: None) for n in (101, 102)]
        for overflow in (False, True):
            process = mock.Mock(pid=42, stdout=streams[0], stderr=streams[1])
            process.poll.return_value = None
            selector = mock.Mock()
            selector.get_map.return_value = True
            selector.select.return_value = [(SimpleNamespace(fileobj=streams[0]), 1)]
            clock = [0, 1] if overflow else [0, 31]
            with (mock.patch.object(host.subprocess, "Popen", return_value=process),
                  mock.patch("selectors.DefaultSelector", return_value=selector),
                  mock.patch.object(host.time, "monotonic", side_effect=clock),
                  mock.patch.object(os, "set_blocking"), mock.patch.object(os, "read", return_value=b"x" * (host.LIMIT + 1)),
                  mock.patch.object(os, "killpg", create=True) as terminate,
                  mock.patch.object(signal, "SIGKILL", 9, create=True)):
                with self.assertRaisesRegex(host.ProfileError, "outcome-unknown"):
                    self.real_parser("add", Path("/etc/apparmor.d/example"))
                self.assertEqual(
                    terminate.call_args_list,
                    [mock.call(42, signal.SIGTERM), mock.call(42, signal.SIGKILL)],
                )

    def test_incomplete_receipt_publication_and_linked_ancestor_are_refused(self):
        self.install()
        next_receipt = self.receipt().with_name("receipt.next")
        next_receipt.write_bytes(b"incomplete")
        with self.assertRaisesRegex(host.ProfileError, "interrupted"):
            host.operate("status")
        next_receipt.unlink()
        saved = self.state.with_name("saved-state")
        self.state.rename(saved)
        self.state.symlink_to(saved, target_is_directory=True)
        with self.assertRaises(host.ProfileError):
            host.operate("status")
        self.state.unlink()
        saved.rename(self.state)

    def test_unrelated_directory_entries_do_not_invalidate_physical_ownership(self):
        self.install()
        (self.state / "another-profile-version").mkdir()
        (self.profiles / "unrelated-directory").mkdir()
        self.assertEqual(host.operate("status")["state"], "ready")
        self.assertEqual(host.operate("uninstall", activation_quiesced=True)["state"], "retired")

    def test_absent_status_refuses_foreign_state_ancestors_without_creating_state(self):
        self.state.mkdir()
        self.owners[self.state] = (1234, 0)
        with self.assertRaisesRegex(host.ProfileError, "unsafe-directory"):
            host.operate("status")
        self.assertFalse((self.state / host.PROFILE_NAME).exists())

    def test_parser_executable_is_checked_before_launch(self):
        self.owners[self.executable] = (1234, 0)
        with mock.patch.object(host.subprocess, "Popen", side_effect=AssertionError("launched")):
            with self.assertRaisesRegex(host.ProfileError, "unsafe-parser"):
                self.real_parser("validate", Path("/etc/apparmor.d/example"))

    def test_nested_kernel_profile_and_empty_process_label_are_uncertain(self):
        listing = self.work / "kernel"
        listing.write_text(host.PROFILE_NAME + "//foreign-child (enforce)\n")
        current = self.work / "proc/123/attr/current"
        current.parent.mkdir(parents=True)
        current.write_bytes(b"")
        with mock.patch.multiple(host, KERNEL_PROFILES=listing, PROC=self.work / "proc"):
            with self.assertRaisesRegex(host.ProfileError, "foreign-nested"):
                self.real_kernel()
            with self.assertRaisesRegex(host.ProfileError, "process-inspection-uncertain"):
                self.real_unused()

    def test_confirmed_exited_process_does_not_block_retirement(self):
        proc = self.work / "proc"
        current = proc / "123/attr/current"
        current.parent.mkdir(parents=True)
        current.write_text("unconfined\n")
        original = Path.open

        def vanished(path, *args, **kwargs):
            if path == current:
                shutil.rmtree(proc / "123")
                raise FileNotFoundError
            return original(path, *args, **kwargs)

        with (mock.patch.object(host, "PROC", proc), mock.patch.object(Path, "open", vanished)):
            self.real_unused()

    def test_root_group_is_required_before_creating_root_owned_state(self):
        with (mock.patch.object(sys, "platform", "linux"),
              mock.patch.object(os, "geteuid", return_value=0, create=True),
              mock.patch.object(os, "getegid", return_value=1234, create=True)):
            with self.assertRaisesRegex(host.ProfileError, "linux-root-required"):
                self.real_root()

    def test_exited_parser_leader_does_not_leave_pipe_holding_descendants(self):
        streams = [SimpleNamespace(fileno=lambda n=n: n, close=lambda: None) for n in (101, 102)]
        process = mock.Mock(pid=42, stdout=streams[0], stderr=streams[1])
        process.poll.return_value = 0
        selector = mock.Mock()
        selector.get_map.return_value = True
        with (mock.patch.object(host.subprocess, "Popen", return_value=process),
              mock.patch("selectors.DefaultSelector", return_value=selector),
              mock.patch.object(host.time, "monotonic", side_effect=[0, 31]),
              mock.patch.object(host.time, "sleep"), mock.patch.object(os, "set_blocking"),
              mock.patch.object(os, "killpg", create=True) as terminate,
              mock.patch.object(signal, "SIGKILL", 9, create=True)):
            with self.assertRaisesRegex(host.ProfileError, "outcome-unknown"):
                self.real_parser("add", Path("/etc/apparmor.d/example"))
            self.assertEqual(
                terminate.call_args_list,
                [mock.call(42, signal.SIGTERM), mock.call(42, signal.SIGKILL)],
            )


if __name__ == "__main__":
    unittest.main()
