"""Create a local demo: a copy of examples/elogd.sample.cfg plus a fake password file.

Every user name found in the config gets the password "demo" (except the
deliberately misspelled ones, so the linter has something to report).

    python demo/make_demo.py
    python -m elogmgr --settings demo/settings.json
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from elogmgr.elogcfg import ElogConfig  # noqa: E402
from elogmgr.elogpwd import sha256_crypt  # noqa: E402

HERE = os.path.join(ROOT, "demo")
TYPOS = {"alcie", "Bob"}

cfg_path = os.path.join(HERE, "elogd.cfg")
shutil.copy(os.path.join(ROOT, "examples", "elogd.sample.cfg"), cfg_path)
cfg = ElogConfig(open(cfg_path, encoding="utf-8").read())
names = set()
for s in cfg.sections:
    for key in ("Login user", "Admin user"):
        names.update(s.get_list(key))
names = sorted((names - TYPOS) | {"alice", "bob"}, key=str.lower)

pw = sha256_crypt("demo")
users = "".join(
    f"  <user>\n    <name>{n}</name>\n    <password encoding=\"SHA256\">{pw}</password>\n"
    f"    <full_name>{n.capitalize()} (demo)</full_name>\n    <last_logout>0</last_logout>\n"
    f"    <last_activity>1761300000</last_activity>\n    <email>{n.lower()}@example.org</email>\n"
    f"    <inactive>0</inactive>\n    <email_notify>\n    </email_notify>\n  </user>\n" for n in names)
with open(os.path.join(HERE, "passwords.pwd"), "w", encoding="utf-8") as f:
    f.write(f'<?xml version="1.0" encoding="ISO-8859-1"?>\n<list>\n{users}</list>\n')

with open(os.path.join(HERE, "settings.json"), "w", encoding="utf-8") as f:
    json.dump({
        "elogd_cfg": "elogd.cfg",
        "password_file": "passwords.pwd",
        "backup_dir": "backups",
        "secret_key": "demo-only-not-secret",
        "secure_cookies": False,
        "reload_mode": "none",
        "elog_url": "https://elog.example.org/",
        "host": "127.0.0.1",
        "port": 8090,
    }, f, indent=2)
print(f"Demo ready: {len(names)} users with password 'demo'. Log in as alice (manager admin) "
      "or bob (logbook admin).")
