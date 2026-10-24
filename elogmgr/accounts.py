"""Apply account changes to ELOG's password file without elogd undoing them.

elogd keeps the password file in memory and writes its copy back whenever
a logged-in user is active (at most once a minute).  It only re-reads the
file at start-up and on SIGHUP.  So a change is: write the file, signal
elogd at once, wait a moment and check the change is still on disk; if an
old copy slipped in between, apply it again.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from datetime import datetime

from .elogpwd import AccountError, Change, parse, serialize
from .reload import reload_elogd

_lock = threading.Lock()


class AccountStore:
    def __init__(self, store, settings: dict, settle: float = 1.5, attempts: int = 3):
        self.store = store  # for the password file path, backups directory and audit log
        self.settings = settings
        self.settle = float(settings.get("account_settle", settle))
        self.attempts = attempts

    def _path(self) -> str:
        path = self.store.password_file()
        if not path:
            raise AccountError("elogd.cfg has no 'Password file'; ELOG accounts are not enabled")
        if not os.path.exists(path):
            raise AccountError(f"Password file {path} does not exist")
        return path

    def _write(self, path: str, data: bytes) -> None:
        d = os.path.dirname(os.path.abspath(path))
        try:
            fd, tmp = tempfile.mkstemp(prefix=".passwords.", dir=d)
        except OSError:
            tmp = None
        if tmp:
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                try:
                    shutil.copymode(path, tmp)
                    st = os.stat(path)
                    if hasattr(os, "chown"):
                        os.chown(tmp, st.st_uid, st.st_gid)
                except OSError:
                    pass
                os.replace(tmp, path)
                return
            except OSError:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        with open(path, "r+b") as f:  # directory not writable: rewrite in place
            f.write(data)
            f.truncate()

    def _backup(self, raw: bytes) -> str:
        os.makedirs(self.store.backup_dir, exist_ok=True)
        name = f"passwords.pwd.{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        with open(os.path.join(self.store.backup_dir, name), "wb") as f:
            f.write(raw)
        os.chmod(os.path.join(self.store.backup_dir, name), 0o600)  # contains password hashes
        files = sorted(f for f in os.listdir(self.store.backup_dir) if f.startswith("passwords.pwd."))
        for old in files[:-50]:
            os.unlink(os.path.join(self.store.backup_dir, old))
        return name

    def apply(self, change: Change, user: str) -> dict:
        path = self._path()
        # store names the way elogd would for its page encoding
        change.charset = self.store.load()[0].global_.get("charset") or "ISO-8859-1"
        with _lock:
            reload = {"ok": True, "message": ""}
            with open(path, "rb") as f:
                raw = f.read()
            backup = self._backup(raw)
            for attempt in range(self.attempts):
                if attempt:
                    with open(path, "rb") as f:
                        raw = f.read()
                root = parse(raw)
                if attempt and change.done(root):
                    break
                change.apply(root)
                self._write(path, serialize(root))
                reload = reload_elogd(self.settings)
                if not reload["ok"]:
                    break
                if self.settle and self.settings.get("reload_mode") != "none":
                    time.sleep(self.settle)  # let elogd re-read before checking
                with open(path, "rb") as f:
                    if change.done(parse(f.read())):
                        break
            else:
                raise AccountError("elogd kept writing back its old copy of the password file; "
                                   "try again in a minute")
            self.store._audit(user, change.description, {"account": getattr(change, "name", "")},
                              backup)
        result = {"ok": True, "reload": reload}
        if not reload["ok"]:
            result["warning"] = ("The password file was changed, but elogd was not told to re-read it: "
                                 + reload["message"] + " Until elogd is reloaded or restarted, it may "
                                 "write its old copy back and undo this change.")
        return result
