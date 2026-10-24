"""Make elogd re-read elogd.cfg after a save.

elogd only reloads its configuration on SIGHUP (`killall -HUP elogd`) or
when its own web Config page saves.  The manager sends the signal itself:
elogd drops to `Usr = elog` after start-up, so a manager running as that
same user may signal it without sudo.  Otherwise a command can be
configured (e.g. `sudo -n /usr/bin/killall -HUP elogd`).
"""
from __future__ import annotations

import os
import signal
import subprocess


def find_pids(name: str = "elogd") -> list[int]:
    pids = []
    if not os.path.isdir("/proc"):
        return pids
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm", encoding="utf-8") as f:
                if f.read().strip() == name:
                    pids.append(int(entry))
        except OSError:
            continue
    return pids


def reload_elogd(settings: dict) -> dict:
    """Returns {"ok": bool, "message": str}."""
    mode = settings.get("reload_mode", "signal")
    if mode == "none":
        return {"ok": True, "message": "Reload disabled (reload_mode = none)"}
    if mode == "command":
        cmd = settings.get("reload_command") or settings.get("after_save_command")
        if not cmd:
            return {"ok": False, "message": "reload_mode is 'command' but reload_command is empty"}
        try:
            r = subprocess.run(cmd, shell=True, timeout=30, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": f"'{cmd}' timed out"}
        if r.returncode:
            return {"ok": False, "message": f"'{cmd}' failed: {(r.stderr or r.stdout).strip()[:300]}"}
        return {"ok": True, "message": "elogd reloaded"}

    name = settings.get("elogd_process", "elogd")
    pids = find_pids(name)
    if not pids:
        return {"ok": False, "message": f"No running '{name}' process found; restart elogd to apply the change"}
    denied = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGHUP)
        except PermissionError:
            denied.append(pid)
        except ProcessLookupError:
            pass
    if denied:
        return {"ok": False, "message": f"Not allowed to signal elogd (pid {', '.join(map(str, denied))}). "
                                        "Run the manager as the elogd user, or set reload_mode to 'command' "
                                        "with 'sudo -n /usr/bin/killall -HUP elogd'."}
    return {"ok": True, "message": "elogd reloaded"}
