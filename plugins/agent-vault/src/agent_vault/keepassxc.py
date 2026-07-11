"""KeePassXC command-line backend for agent-vault."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess

from .config import resolve_kpdb


class KeePassXCBackend:
    """keepassxc-cli backend - full access with master password."""

    def __init__(self):
        self._master_pass: str | None = None
        self._cli_path: str | None = self._find_cli()

    @staticmethod
    def _find_cli() -> str | None:
        win_path = r"C:\Program Files\KeePassXC\keepassxc-cli.exe"
        if os.path.isfile(win_path):
            return win_path
        return shutil.which("keepassxc-cli")

    @property
    def available(self) -> bool:
        return self._cli_path is not None

    @property
    def has_password(self) -> bool:
        return self._master_pass is not None

    @property
    def status(self) -> str:
        if not self._cli_path:
            return "not_found"
        return "unlocked" if self._master_pass else "locked"

    def set_password(self, password: str) -> None:
        self._master_pass = password

    def get_password(self) -> str | None:
        return self._master_pass

    def clear_password(self) -> None:
        self._master_pass = None

    def verify_password(self, password: str) -> bool:
        kpdb = resolve_kpdb(required=False)
        if not self._cli_path or not kpdb:
            return False
        try:
            r = subprocess.run(
                [self._cli_path, "ls", "-q", kpdb, "/"],
                input=password + "\n",
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    # -- credential access ---------------------------------------------------

    def _run(self, args: list[str], timeout: int = 10) -> subprocess.CompletedProcess | None:
        if not self._cli_path or not self._master_pass:
            return None
        try:
            return subprocess.run(
                [self._cli_path, *args],
                input=self._master_pass + "\n",
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception:
            return None

    def get_entry(self, entry_path: str, field: str = "password") -> str | None:
        attr_map = {
            "password": "Password",
            "username": "UserName",
            "url": "URL",
            "title": "Title",
            "notes": "Notes",
        }
        attr = attr_map.get(field, field)
        r = self._run(["show", "-q", "-s", resolve_kpdb(), entry_path, "-a", attr])
        if r and r.returncode == 0:
            return r.stdout.strip()
        return None

    def has_entry(self, entry_path: str) -> bool:
        r = self._run(["show", "-q", "-s", resolve_kpdb(), entry_path])
        return bool(r and r.returncode == 0)

    def search(self, query: str) -> list[str]:
        r = self._run(["search", "-q", resolve_kpdb(), query])
        if r and r.returncode == 0:
            return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        return []

    # -- mutations -----------------------------------------------------------

    def add_entry(
        self,
        entry_path: str,
        *,
        username: str | None = None,
        url: str | None = None,
        password: str | None = None,
        generate: bool = False,
    ) -> tuple[bool, str]:
        """Create a new KeePass entry. Returns (success, message)."""
        if not self._cli_path or not self._master_pass:
            return False, "CLI not available or vault locked"
        args = ["add", "-q", resolve_kpdb(), entry_path]
        if username:
            args.extend(["-u", username])
        if url:
            args.extend(["--url", url])
        if generate:
            args.append("-g")
        stdin = self._master_pass + "\n"
        if password and not generate:
            args.append("-p")
            stdin += password + "\n"
        try:
            r = subprocess.run(
                [self._cli_path, *args],
                input=stdin, capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return True, "Entry created"
            return False, r.stderr.strip() or "keepassxc-cli add failed"
        except Exception as e:
            return False, str(e)

    def edit_password(
        self,
        entry_path: str,
        password: str,
    ) -> tuple[bool, str]:
        """Update the password of an existing entry. Returns (success, message)."""
        if not self._cli_path or not self._master_pass:
            return False, "CLI not available or vault locked"
        stdin = self._master_pass + "\n" + password + "\n"
        try:
            r = subprocess.run(
                [self._cli_path, "edit", "-q", resolve_kpdb(), entry_path, "-p"],
                input=stdin, capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return True, "Password updated"
            return False, r.stderr.strip() or "keepassxc-cli edit failed"
        except Exception as e:
            return False, str(e)

    def import_attachment(
        self,
        entry_path: str,
        attachment_name: str,
        data: bytes,
    ) -> tuple[bool, str]:
        """Import an attachment into an entry. Returns (success, message)."""
        import tempfile

        if not self._cli_path or not self._master_pass:
            return False, "CLI not available or vault locked"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{attachment_name}") as f:
                tmp_path = f.name
                f.write(data)
            r = subprocess.run(
                [
                    self._cli_path, "attachment-import", "-q", "-f",
                    resolve_kpdb(), entry_path, attachment_name, tmp_path,
                ],
                input=self._master_pass + "\n",
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return True, f"Imported {attachment_name}"
            return False, r.stderr.strip() or "attachment-import failed"
        except Exception as e:
            return False, str(e)
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

    def export_attachment(
        self,
        entry_path: str,
        attachment_name: str,
    ) -> tuple[bytes | None, str]:
        """Export an attachment from an entry. Returns (data, message)."""
        import tempfile

        if not self._cli_path or not self._master_pass:
            return None, "CLI not available or vault locked"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{attachment_name}") as f:
                tmp_path = f.name
            r = subprocess.run(
                [
                    self._cli_path, "attachment-export", "-q",
                    resolve_kpdb(), entry_path, attachment_name, tmp_path,
                ],
                input=self._master_pass + "\n",
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                with open(tmp_path, "rb") as f:
                    data = f.read()
                return data, f"Exported {attachment_name}"
            return None, r.stderr.strip() or "attachment-export failed"
        except Exception as e:
            return None, str(e)
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
