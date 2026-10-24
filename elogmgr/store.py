"""File access for elogd.cfg: optimistic locking, backups, audit log."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime

from .elogcfg import ElogConfig
from .elogpwd import ElogUser, load_users


class ConflictError(Exception):
    pass


class Store:
    def __init__(self, settings: dict):
        self.cfg_path = settings["elogd_cfg"]
        self.pwd_override = settings.get("password_file") or None  # tests; normally from elogd.cfg
        self.state_dir = settings.get("state_dir") or os.path.join(os.path.dirname(self.cfg_path), "elog-manager")
        self.backup_dir = os.path.join(self.state_dir, "backups")
        self.audit_path = os.path.join(self.state_dir, "audit.jsonl")
        self.keep_backups = 200
        self._lock = threading.Lock()
        self._users_cache: tuple[str, float, dict] | None = None

    # ---- reading -----------------------------------------------------
    def _read_raw(self) -> bytes:
        with open(self.cfg_path, "rb") as f:
            return f.read()

    @staticmethod
    def _decode(raw: bytes) -> tuple[str, str]:
        try:
            return raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            return raw.decode("latin-1"), "latin-1"

    def load(self) -> tuple[ElogConfig, str]:
        raw = self._read_raw()
        text, _ = self._decode(raw)
        return ElogConfig(text), hashlib.sha256(raw).hexdigest()[:16]

    def raw_text(self) -> tuple[str, str]:
        raw = self._read_raw()
        return self._decode(raw)[0], hashlib.sha256(raw).hexdigest()[:16]

    def password_file(self, cfg: ElogConfig | None = None) -> str | None:
        """'Password file' from [global]; relative paths are relative to 'Logbook dir', as in elogd."""
        if self.pwd_override:
            return self.pwd_override
        cfg = cfg or self.load()[0]
        pf = cfg.global_.get("Password file")
        if not pf:
            return None
        if os.path.isabs(pf):
            return pf
        return os.path.join(cfg.global_.get("Logbook dir") or os.path.dirname(self.cfg_path), pf)

    def users(self, cfg: ElogConfig | None = None) -> dict[str, ElogUser]:
        path = self.password_file(cfg)
        if not path or not os.path.exists(path):
            return {}
        mtime = os.path.getmtime(path)
        if self._users_cache and self._users_cache[:2] == (path, mtime):
            return self._users_cache[2]
        users = load_users(path)
        self._users_cache = (path, mtime, users)
        return users

    # ---- writing -----------------------------------------------------
    def write(self, new_text: str, etag: str, user: str, action: str, detail: dict | None = None) -> str:
        """Atomically replace elogd.cfg if it still matches `etag`."""
        with self._lock:
            raw = self._read_raw()
            if hashlib.sha256(raw).hexdigest()[:16] != etag:
                raise ConflictError("elogd.cfg was changed by someone else (or via ELOG's Config page). "
                                    "Reload and re-apply your change.")
            _, enc = self._decode(raw)
            new_raw = new_text.encode(enc)
            if new_raw == raw:
                return etag
            backup = self._backup(raw, user, action)
            if not self._replace_atomically(new_raw):
                # Directory not writable, or elogd.cfg is a single bind-mounted file
                # (Docker): rewrite it in place instead.  The backup above is the safety net.
                with open(self.cfg_path, "r+b") as f:
                    f.write(new_raw)
                    f.truncate()
            self._audit(user, action, detail, backup)
            return hashlib.sha256(new_raw).hexdigest()[:16]

    def _replace_atomically(self, data: bytes) -> bool:
        d = os.path.dirname(os.path.abspath(self.cfg_path))
        try:
            fd, tmp = tempfile.mkstemp(prefix=".elogd.cfg.", dir=d)
        except OSError:
            return False
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            try:
                shutil.copymode(self.cfg_path, tmp)
                st = os.stat(self.cfg_path)
                if hasattr(os, "chown"):
                    os.chown(tmp, st.st_uid, st.st_gid)
            except OSError:
                pass
            os.replace(tmp, self.cfg_path)
            return True
        except OSError:
            return False
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _backup(self, raw: bytes, user: str, action: str) -> str:
        os.makedirs(self.backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        name = f"elogd.cfg.{stamp}"
        with open(os.path.join(self.backup_dir, name), "wb") as f:
            f.write(raw)
        with open(os.path.join(self.backup_dir, name + ".json"), "w", encoding="utf-8") as f:
            json.dump({"user": user, "action": action, "time": time.time()}, f)
        self._prune()
        return name

    def _prune(self) -> None:
        files = sorted(f for f in os.listdir(self.backup_dir)
                       if f.startswith("elogd.cfg.") and not f.endswith(".json"))
        for f in files[: max(0, len(files) - self.keep_backups)]:
            for p in (f, f + ".json"):
                try:
                    os.unlink(os.path.join(self.backup_dir, p))
                except FileNotFoundError:
                    pass

    def _audit(self, user, action, detail, backup) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.audit_path)), exist_ok=True)
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "user": user,
                                "action": action, "detail": detail or {}, "backup": backup}) + "\n")

    # ---- history -----------------------------------------------------
    def backups(self) -> list[dict]:
        if not os.path.isdir(self.backup_dir):
            return []
        out = []
        for f in sorted(os.listdir(self.backup_dir), reverse=True):
            if not f.startswith("elogd.cfg.") or f.endswith(".json"):
                continue
            meta = {}
            try:
                with open(os.path.join(self.backup_dir, f + ".json"), encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                pass
            out.append({"id": f, **meta})
        return out

    def backup_text(self, backup_id: str) -> str:
        if "/" in backup_id or "\\" in backup_id or not backup_id.startswith("elogd.cfg."):
            raise FileNotFoundError(backup_id)
        with open(os.path.join(self.backup_dir, backup_id), "rb") as f:
            return self._decode(f.read())[0]

    def audit(self, limit: int = 300) -> list[dict]:
        if not os.path.exists(self.audit_path):
            return []
        with open(self.audit_path, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        return [json.loads(l) for l in reversed(lines) if l.strip()]


def unified_diff(old: str, new: str, a="current", b="new") -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), a, b, n=2))
