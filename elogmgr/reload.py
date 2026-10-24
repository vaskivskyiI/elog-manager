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
        return {"ok": True, "message": "Reload disabled (ELOGMGR_RELOAD=none)"}
    if mode == "command":
        cmd = settings.get("reload_command")
        if not cmd:
            return {"ok": False, "message": "ELOGMGR_RELOAD=command but ELOGMGR_RELOAD_COMMAND is empty"}
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
        return {"ok": False, "message": denied_message(denied[0], _proc_uids(denied[0]), os.getuid(),
                                                       _apparmor_profile())}
    return {"ok": True, "message": "elogd reloaded"}


def _proc_uids(pid: int) -> tuple[int, int, int] | None:
    """(real, effective, saved) uid of a process, from /proc/<pid>/status."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("Uid:"):
                    real, eff, saved = (int(x) for x in line.split()[1:4])
                    return real, eff, saved
    except (OSError, ValueError):
        pass
    return None


def _apparmor_profile() -> str:
    try:
        with open("/proc/self/attr/current", encoding="utf-8") as f:
            return f.read().strip().strip("\x00")
    except OSError:
        return ""


def denied_message(pid: int, target: tuple[int, int, int] | None, me: int, apparmor: str) -> str:
    """Explain why the kernel refused SIGHUP and what to change."""
    head = f"Not allowed to signal elogd (pid {pid}). "
    if target and me in (target[0], target[2]):
        # Same user, so the uid check passed: a security module refused it.
        if apparmor and apparmor != "unconfined":
            return head + (f"The manager runs as the same user (uid {me}), but its AppArmor profile "
                           f"'{apparmor.split()[0]}' blocks signals to processes outside the container. "
                           "Add 'security_opt: [apparmor=unconfined]' to docker-compose.yml.")
        return head + (f"The manager runs as the same user (uid {me}), so a security module (SELinux or "
                       "AppArmor) refused it. In Docker add 'security_opt: [label=disable]' or "
                       "'[apparmor=unconfined]'.")
    if target:
        real, eff, saved = target
        if real != eff:
            return head + (f"elogd runs with real uid {real} but effective uid {eff}; the kernel checks the "
                           f"real uid, so only uid {real} may signal it (the manager is uid {me}). Start elogd "
                           "as the elog user (e.g. User= in its systemd unit), or set ELOGMGR_RELOAD=command "
                           "with 'sudo -n /usr/bin/killall -HUP elogd'.")
        return head + (f"elogd runs as uid {real}, the manager as uid {me}. Run the manager as uid {real} "
                       "(ELOG_UID in .env), or set ELOGMGR_RELOAD=command with "
                       "'sudo -n /usr/bin/killall -HUP elogd'.")
    return head + ("Run the manager as the elogd user, or set ELOGMGR_RELOAD=command with "
                   "'sudo -n /usr/bin/killall -HUP elogd'.")
