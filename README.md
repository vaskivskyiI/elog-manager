# ELOG Manager

A web GUI for administering a [PSI ELOG](https://elog.psi.ch/elog/) server by
editing `elogd.cfg`. You can create, clone, rename and delete logbooks, organise
them into groups by drag and drop, edit attributes in a table, and decide which
users can access which logbook. Users sign in with their existing ELOG
accounts.

## What it does

| Page | Purpose |
|------|---------|
| **Logbooks** | Board of all groups. Drag logbooks between groups (Ctrl+drag copies, since a logbook can be in several groups), reorder them, and create, rename or delete groups. *New logbook* copies settings from a template, suggests the next name in a series (`np011`) and adds the logbook to its group. Tick logbooks to **bulk edit** a setting across all of them. |
| **Logbook editor** | *Overview* (comment, titles, paging, list columns, quick filters, groups) · *Access* (login and admin users, with autocomplete from the password file) · *Attributes* (table: input type, options, required, locked, extendable, preset, comment; renaming an attribute updates every setting that refers to it) · *All settings* (key/value grid) · *Raw text*. |
| **Users & access** | Pick a user to see every logbook they can open or administer, and toggle access with checkboxes. *Copy access from…* onboards a new person like an existing one. *Replace with…* fixes typos across all logbooks. Names in the config that do not match an account are listed separately. |
| **Issues** | Linter: invalid option names (e.g. `extendable attributes`), groups that point to missing logbooks, users that do not exist, attributes referenced but not defined, malformed styles, duplicates. Many issues have a one-click fix. |
| **History** | Who changed what. Every save first writes a timestamped backup that you can compare with the current file or restore. |
| **Global settings / Raw config** | `[global]` key/value editor and a full-file editor (manager admins only). |

Every change opens a diff preview showing the exact lines of `elogd.cfg`
that will change, plus any new issues the change introduces. Nothing is written
until you confirm.

The file is edited line by line. Comments, blank lines, key spelling and order
are kept, and unchanged files round-trip byte for byte. If someone saves
`elogd.cfg` through ELOG's own *Config* page in the meantime, the save is
refused rather than overwriting their change.

## Who can sign in

Users authenticate against ELOG's `passwords.pwd` (SHA-256 crypt, as elogd 3.x
stores it). Accounts and passwords stay managed in ELOG.

* **Manager admins:** `manager_admins` in `settings.json`, or the `[global]`
  `Admin user` list when that setting is empty. They can do everything.
* **Logbook admins:** users listed in a logbook's `Admin user`. They can edit
  only the logbooks they administer: settings, attributes and login users. They
  cannot create, delete, rename or regroup logbooks, change `Subdir` or touch
  `[global]`. Set `allow_logbook_admins: false` to restrict the manager to
  manager admins.
* `allowed_users` (optional): when non-empty, only these accounts may sign in
  at all.

If ELOG authenticates via the web server (`Authentication = Webserver`), set
`"auth_mode": "header"` and let the reverse proxy pass the user in
`X-Remote-User`. In that mode, bind the manager to 127.0.0.1 only.

## Try it locally

```bash
pip install -r requirements.txt
python demo/make_demo.py
python -m elogmgr --settings demo/settings.json
```

Open http://127.0.0.1:8090 and sign in as `alice` (manager admin) or
`bob` (logbook admin), password `demo`. The demo works on a copy of
`examples/elogd.sample.cfg`, a fictional configuration with a few deliberate
mistakes for the Issues page.

Run the tests with:

```bash
python -m pytest -q tests
```

## Install on the ELOG server

```bash
sudo mkdir -p /opt/elog-manager && sudo chown elog: /opt/elog-manager
# copy this repository to /opt/elog-manager, then:
cd /opt/elog-manager
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp settings.example.json settings.json   # edit paths, secret_key, manager_admins
sudo cp deploy/elog-manager.service /etc/systemd/system/
sudo systemctl enable --now elog-manager
```

Add `deploy/nginx-location.conf` to the HTTPS server block that already proxies
ELOG, so the manager is served at `https://elog.example.org/manager/`. Run the
service as the same user as elogd (`Usr = elog`) so it can write `elogd.cfg`
and read `passwords.pwd`.

**Applying changes.** elogd does not watch `elogd.cfg`. It re-reads the file
on `SIGHUP` (`killall -HUP elogd`). After every save, the manager sends that
signal itself (`"reload_mode": "signal"`). The result appears in the save
message, and the **Reload ELOG** button in the top bar sends the signal again
by hand.

* elogd switches to `Usr = elog` after start-up. A manager running as the same
  `elog` user may therefore signal it **without sudo**; this is the recommended
  setup and what `deploy/elog-manager.service` does.
* If elogd keeps running as root, use `"reload_mode": "command"` with
  `"reload_command": "sudo -n /usr/bin/killall -HUP elogd"`. Then add this one
  rule with `visudo -f /etc/sudoers.d/elog-manager`:
  `elog ALL=(root) NOPASSWD: /usr/bin/killall -HUP elogd`, and remove
  `NoNewPrivileges=true` from the service file, because it blocks sudo.
* `"reload_mode": "none"` disables reloading.

`Logbook dir`, `Resource dir` and `Max content length` still need a full
restart of elogd.

Why not use ELOG's own Config page, which also reloads? That would mean storing
an ELOG admin password in the manager and replaying a web form whose format
changes between ELOG versions. It would also limit edits to what that page
allows. The signal is the reload mechanism elogd itself provides.

**Logbook data.** Deleting a logbook removes only its config section; entries
stay on disk. Renaming adds `Subdir = <old name>` so the existing entries are
still found without moving directories.

## Layout

```
elogmgr/elogcfg.py   round-trip parser / editor for elogd.cfg
elogmgr/elogpwd.py   password file reader, SHA-256 crypt
elogmgr/lint.py      config checks
elogmgr/store.py     atomic writes, conflict detection, backups, audit log
elogmgr/app.py       Flask API and permissions
elogmgr/static/      single-page UI (no build step)
examples/            fictional sample elogd.cfg used by the demo and tests
```
