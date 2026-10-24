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
        self.pwd_path = settings.get("password_file") or None
        self.backup_dir = settings.get("backup_dir") or os.path.join(os.path.dirname(self.cfg_path), "cfg-backups")
        self.audit_path = settings.get("audit_log") or os.path.join(self.backup_dir, "audit.jsonl")
        self.keep_backups = int(settings.get("keep_backups", 200))
        self._lock = threading.Lock()
        self._users_cache: tuple[float, dict] | None = None

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

    def users(self) -> dict[str, ElogUser]:
        if not self.pwd_path or not os.path.exists(self.pwd_path):
            return {}
        mtime = os.path.getmtime(self.pwd_path)
        if self._users_cache and self._users_cache[0] == mtime:
            return self._users_cache[1]
        users = load_users(self.pwd_path)
        self._users_cache = (mtime, users)
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
            d = os.path.dirname(os.path.abspath(self.cfg_path))
            fd, tmp = tempfile.mkstemp(prefix=".elogd.cfg.", dir=d)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(new_raw)
                try:
                    shutil.copymode(self.cfg_path, tmp)
                    st = os.stat(self.cfg_path)
                    if hasattr(os, "chown"):
                        os.chown(tmp, st.st_uid, st.st_gid)
                except (OSError, PermissionError):
                    pass
                os.replace(tmp, self.cfg_path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            self._audit(user, action, detail, backup)
            return hashlib.sha256(new_raw).hexdigest()[:16]

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
