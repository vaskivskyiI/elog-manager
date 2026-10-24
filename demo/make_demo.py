"""Create a local demo: a copy of examples/elogd.sample.cfg plus a fake password file.

Every user name found in the config gets the password "demo" (except the
deliberately misspelled ones, so the linter has something to report).
"grace" has no logbooks yet, to show what a new user sees.

    python demo/make_demo.py
    python -m elogmgr --env-file demo/.env
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from elogmgr.elogcfg import ElogConfig  # noqa: E402
from elogmgr.elogpwd import sha256_crypt  # noqa: E402

HERE = os.path.join(ROOT, "demo")
TYPOS = {"alcie", "Bob"}

cfg = ElogConfig(open(os.path.join(ROOT, "examples", "elogd.sample.cfg"), encoding="utf-8").read())
# point the demo at its own password file (relative paths are relative to Logbook dir, as in elogd)
cfg.global_.set("Logbook dir", HERE)
cfg.global_.set("password file", "passwords.pwd")
with open(os.path.join(HERE, "elogd.cfg"), "w", encoding="utf-8", newline="") as f:
    f.write(cfg.text())

names = set()
for s in cfg.sections:
    for key in ("Login user", "Admin user"):
        names.update(s.get_list(key))
names = sorted((names - TYPOS) | {"alice", "bob", "grace"}, key=str.lower)

pw = sha256_crypt("demo")
users = "".join(
    f"  <user>\n    <name>{n}</name>\n    <password encoding=\"SHA256\">{pw}</password>\n"
    f"    <full_name>{n.capitalize()} (demo)</full_name>\n    <last_logout>0</last_logout>\n"
    f"    <last_activity>1761300000</last_activity>\n    <email>{n.lower()}@example.org</email>\n"
    f"    <inactive>0</inactive>\n    <email_notify>\n    </email_notify>\n  </user>\n" for n in names)
with open(os.path.join(HERE, "passwords.pwd"), "w", encoding="utf-8") as f:
    f.write(f'<?xml version="1.0" encoding="ISO-8859-1"?>\n<list>\n{users}</list>\n')

with open(os.path.join(HERE, ".env"), "w", encoding="utf-8") as f:
    f.write("ELOGD_CFG=elogd.cfg\n"
            "ELOGMGR_STATE_DIR=state\n"
            "ELOGMGR_BEHIND_PROXY=0\n"
            "ELOGMGR_RELOAD=none\n"
            "ELOGMGR_ELOG_URL=https://elog.example.org/\n")
print(f"Demo ready: {len(names)} users with password 'demo'. Log in as alice (manager), "
      "bob (logbook admin) or grace (new user without logbooks).")
