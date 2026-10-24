"""Manager settings: a handful of environment variables.

Everything ELOG already knows is read from elogd.cfg instead of being
configured twice: the password file, the manager admins ([global]
Admin user), the ELOG URL and the session length (Login expiration).

    ELOG_DIR               directory with elogd.cfg (default /usr/local/elog)
    ELOGD_CFG              full path of elogd.cfg (default $ELOG_DIR/elogd.cfg)
    ELOGMGR_STATE_DIR      backups, audit log, session key (default <cfg dir>/elog-manager)
    ELOGMGR_RELOAD         signal | command | none (default signal)
    ELOGMGR_RELOAD_COMMAND command for ELOGMGR_RELOAD=command
    ELOGMGR_BEHIND_PROXY   1 (default): behind an HTTPS reverse proxy; 0 for plain http
    ELOGMGR_ELOG_URL       ELOG's public URL if elogd.cfg has no "URL"
    ELOGMGR_AUTH_HEADER    trust the user name in this header (proxy auth; off by default)
    ELOGMGR_HOST, ELOGMGR_PORT   listen address (default 127.0.0.1:8090)

The same file serves Docker Compose (`env_file: .env`), systemd
(`EnvironmentFile=`) and `python -m elogmgr --env-file .env`.
"""
from __future__ import annotations

import os
import secrets

PATH_VARS = ("ELOG_DIR", "ELOGD_CFG", "ELOGMGR_STATE_DIR")


def load_env_file(path: str) -> None:
    """Minimal .env reader: KEY=VALUE lines; existing environment wins.

    Relative paths are resolved against the file's directory."""
    base = os.path.dirname(os.path.abspath(path))
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key in PATH_VARS and value and not os.path.isabs(value):
                value = os.path.join(base, value)
            os.environ.setdefault(key, value)


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None or v == "" else v.strip().lower() in ("1", "true", "yes", "on")


def from_env() -> dict:
    env = os.environ
    cfg = env.get("ELOGD_CFG") or os.path.join(env.get("ELOG_DIR") or "/usr/local/elog", "elogd.cfg")
    behind_proxy = _flag("ELOGMGR_BEHIND_PROXY", True)
    return {
        "elogd_cfg": os.path.abspath(cfg),
        "state_dir": env.get("ELOGMGR_STATE_DIR") or None,
        "reload_mode": env.get("ELOGMGR_RELOAD") or "signal",
        "reload_command": env.get("ELOGMGR_RELOAD_COMMAND") or "",
        "behind_proxy": behind_proxy,
        "secure_cookies": behind_proxy,
        "elog_url": env.get("ELOGMGR_ELOG_URL") or "",
        "auth_header": env.get("ELOGMGR_AUTH_HEADER") or "",
        "host": env.get("ELOGMGR_HOST") or "127.0.0.1",
        "port": int(env.get("ELOGMGR_PORT") or 8090),
    }


def secret_key(state_dir: str) -> str:
    """Session signing key, created once and kept in the state directory."""
    path = os.path.join(state_dir, "secret_key")
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    os.makedirs(state_dir, exist_ok=True)
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    return key
