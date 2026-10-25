# ELOG Manager

A web GUI for administering a [PSI ELOG](https://elog.psi.ch/elog/) server by
editing `elogd.cfg`. You can create, clone, rename and delete logbooks, organise
them into groups by drag and drop, edit attributes in a table, and decide which
users can access which logbook. Users sign in with their existing ELOG
accounts.

## What it does

| Page | Purpose |
|------|---------|
| **Logbooks** | Tree of groups and subgroups, in the same order as on ELOG's start page. Reorder logbooks inside a group and groups among their siblings with ↑ ↓ or by dragging. Drag logbooks between groups (Ctrl+drag copies, since a logbook can be in several groups). Drag a group by its title into another group to nest it, or onto *Not in any group* to make it top-level again. *+ Subgroup* creates a nested group. *New logbook* copies settings from a template, suggests the next name in a series (`np011`) and adds the logbook to its group. Tick logbooks to **bulk edit** a setting across all of them. |
| **Logbook editor** | *Overview* (comment, titles, paging, list columns, quick filters, groups) · *Access* (login and admin users, with autocomplete from the password file) · *Attributes* (table: input type, options, required, locked, extendable, preset, comment; renaming an attribute updates every setting that refers to it) · *Templates* (the "New from template" menu: per template the preset fields and an optional text, inline HTML or a file name; add, rename, reorder, delete; converts the old Quick entries / Text templates settings) · *All settings* (key/value grid) · *Raw text*. |
| **Users & access** | Pick a user to see every logbook they can open or administer, and toggle access with checkboxes. *Copy access from…* onboards a new person like an existing one. *Replace with…* fixes typos across all logbooks. Names in the config that do not match an account are listed separately. Managers also create, edit, deactivate and delete ELOG accounts and set passwords (see *Accounts*). |
| **My account** | Click your name in the top bar to change your own full name, e-mail and password. |
| **Issues** | Linter: invalid option names (e.g. `extendable attributes`), groups that point to missing logbooks, users that do not exist, attributes referenced but not defined, malformed styles, duplicates. Many issues have a one-click fix. |
| **History** | Who changed what. Every save first writes a timestamped backup that you can compare with the current file or restore. |
| **Global settings / Raw config** | `[global]` key/value editor and a full-file editor (manager admins only). |

Every change opens a diff preview showing the exact lines of `elogd.cfg`
that will change, together with the result of a **configuration check**. A
change may not introduce errors: unknown or misspelled options, values of the
wrong type (`Reverse sort = yes`, `Entries per page = fifty`), groups that
point to missing logbooks, unknown users, or a logbook whose Author is not
locked and filled in automatically. Managers can override the check
deliberately; everyone else must fix the problem first. Nothing is written
until you confirm.

**Option catalog.** `elogmgr/options.json` lists the 210 options of ELOG's
[option reference](https://elog.psi.ch/elog/config.html): value syntax, type,
default, where the option works, and a link to the documentation entry. It
also lists the options added by the companion ELOG fork (start page,
editor, live updates, templates, custom pages, views, e-mail digest, change
notes, config watching ...), marked `"source": "fork"`; old option names the
fork still reads are marked `"deprecated"` with their new name.

Each option has a scope: `global` (only in `[global]`, e.g. `Password file`,
`Watch config`), `logbook` (describes one logbook, e.g. `Subdir`, `Comment`,
`Hidden`) or `any` (in a logbook, or in `[global]` as the default for all
logbooks). The logbook editors suggest only options that work in a logbook,
the global editor only those that work in `[global]`; a setting in the wrong
place is marked in the table and reported by the config check.

The editors use the catalog to suggest option names, show a matching input
(yes/no, choice list, number, text), and show what applies when a field is
empty, as placeholder text: the `[global]` value (`global: 40`), otherwise
ELOG's default (`default: 20`). `tools/build_options.py` rebuilds the catalog
from the reference and keeps the fork's entries; the descriptions are our own
short summaries. New options of the fork are added to the `"source": "fork"`
entries of `options.json` (and to `FORK_OPTIONS` in the tests).

**Custom options.** Site-specific or undocumented keys (for example
`Show banner`) are reported as "not in ELOG's option reference". A manager can
register them, either from that notice (*Make known…*), from the "?" badge in
*All settings*, or under *Global settings → Custom options*, with a value type,
where they may be set, a default and a description. Registered options count as
known: no notice, a matching input field, and their values are checked before
saving. They are stored in `custom_options.json` in the state directory, not in
`elogd.cfg`.

**Author policy.** Every logbook has an `Author` attribute that is filled in
automatically (`Preset Author`, e.g. `$long_name`) and locked. Settings in
`[global]` count. New logbooks get this automatically, the Attributes tab
protects the Author row, and the Issues page offers a one-click fix for
logbooks that break the rule.

**Names.** Logbook names must be unique, ignoring case, among all logbooks and
groups, including ones the user cannot see. In *New logbook* you choose the
group first; if the group continues a numbered series (`np001` … `np010`), the
name is prefilled with the next free one (`np011`). You can still change it.
The dialog checks availability as you type.

The file is edited line by line. Comments, blank lines, key spelling and order
are kept, and unchanged files round-trip byte for byte. If someone saves
`elogd.cfg` through ELOG's own *Config* page in the meantime, the save is
refused rather than overwriting their change.

## Who can sign in

Every account in ELOG's password file can sign in with its ELOG password
(SHA-256 crypt, as elogd 3.x stores it). What you can do depends on
`elogd.cfg` alone:

| Role | Who | Can |
|------|-----|-----|
| **Manager** | `Admin user` in `[global]` | Everything: all logbooks, groups, global settings, raw file, history and restore. |
| **Logbook admin** | `Admin user` of at least one logbook | See and edit the logbooks they administer, including access, attributes and which groups they are in. Create new logbooks. |
| **User** | Any other account | Create logbooks. |

Whoever creates a logbook becomes its admin automatically. If the logbook has a
`Login user` list, the creator is added to it as well. New logbooks start from
a built-in template (*Experiment (runs)*, *Instrument*, *Simple*) or from a
copy of a logbook you administer. Non-managers do not see other people's
logbooks, the private `[global]` settings (paths, mail, accounts) or other
users' e-mail addresses. Renaming and deleting logbooks, and restructuring
groups, are left to managers.

### Accounts

Managers can create accounts, edit their full name and e-mail, deactivate them
(they can no longer log in to ELOG or the manager, and running sessions end),
set new passwords and delete them. Deleting can also remove the user from all
logbook access lists. Everyone can change their own name, e-mail and password;
changing your own password needs the current one.

The manager writes `passwords.pwd` in the same layout and byte encoding as
elogd. elogd keeps that file in memory, writes its copy back whenever a user is
active, and only re-reads it on SIGHUP. So each account change is written,
followed immediately by a reload, and then re-checked; if elogd wrote its old
copy back in between, the change is applied again. Account changes therefore
need a working reload (see *Applying changes*); without it the save message
warns that elogd may undo the change. Before each change a copy of the file
goes to the state directory (`backups/passwords.pwd.*`, readable by the owner
only, the last 50 are kept).

Passwords are stored the way elogd checks them: SHA-256 crypt without a salt.
That is ELOG's format and cannot be strengthened from outside; keep
`passwords.pwd` readable only by the elog user.

If ELOG authenticates via the web server (`Authentication = Webserver`), set
`ELOGMGR_AUTH_HEADER=X-Remote-User` so the manager trusts the user name your
reverse proxy puts in that header. Only do this if the proxy always sets the
header and strips it from client requests.

## Settings

Everything ELOG already knows comes from `elogd.cfg`: the password file, the
managers, the ELOG URL (`URL`) and the session length (`Login expiration`). The
remaining settings are environment variables in one `.env` file, which Docker
Compose, systemd and `python -m elogmgr` all read. See `.env.example`:

| Variable | Default | |
|----------|---------|-|
| `ELOG_DIR` | `/usr/local/elog` | Directory containing `elogd.cfg` |
| `ELOGD_CFG` | `$ELOG_DIR/elogd.cfg` | If the file has another name |
| `ELOG_UID`, `ELOG_GID` | 1000 | Docker only: the account elogd runs as |
| `ELOGMGR_PORT` | 8090 | Port on 127.0.0.1 |
| `ELOGMGR_STATE_DIR` | Docker: the `state` volume; systemd: `/var/lib/elog-manager`; otherwise `<dir of elogd.cfg>/elog-manager` | Backups, change log and the session key. Must be writable by the account the manager runs as |
| `ELOGMGR_RELOAD` | `signal` | `signal`, `command` or `none` (see *Applying changes*) |
| `ELOGMGR_RELOAD_COMMAND` | | Used with `ELOGMGR_RELOAD=command` |
| `ELOGMGR_BEHIND_PROXY` | `1` | Behind an HTTPS proxy: secure cookies, client IPs from `X-Forwarded-For`. Use `0` only for plain-http testing |
| `ELOGMGR_ELOG_URL` | `URL` from elogd.cfg | For the "Open in ELOG" links |
| `ELOGMGR_AUTH_HEADER` | | See above |

## Try it locally

```bash
pip install -r requirements.txt
python demo/make_demo.py
python -m elogmgr --env-file demo/.env
```

Open http://127.0.0.1:8090 and sign in as `alice` (manager), `bob` (logbook
admin) or `grace` (a user without logbooks), password `demo`. The demo works on
a copy of `examples/elogd.sample.cfg`, a fictional configuration with a few
deliberate mistakes for the Issues page.

Run the tests with:

```bash
python -m pytest -q tests
```

## Run with Docker Compose

Try the demo without installing Python:

```bash
docker compose -f docker-compose.demo.yml up --build
```

To run it on the ELOG server next to an elogd that runs on the host:

```bash
cp .env.example .env              # ELOG_DIR, ELOG_UID/ELOG_GID (id -u elog / id -g elog)
sudo docker compose up -d --build
```

Starting containers needs sudo or the `docker` group. The `docker` group is
effectively root, so don't add `elog` to it. The container itself runs as
`ELOG_UID:ELOG_GID`, whoever starts it, and `restart: unless-stopped` keeps it
running across reboots.

* The manager reads the paths in `elogd.cfg` (`Password file`, `Logbook dir`),
  so it sees the configuration folder at `ELOG_DIR` and the data folder at
  `ELOG_DATA_DIR`, exactly the paths used in `elogd.cfg`. For an elogd on the
  host these are mounted from the same paths. If elogd runs in Docker with its
  folders somewhere else on the host (e.g. in a home directory), set only
  `ELOG_DIR_HOST` and `ELOG_DATA_HOST` to those host folders, the same values as
  in the ELOG compose project; `ELOG_DIR` and `ELOG_DATA_DIR` keep their defaults
  `/usr/local/elog` and `/mnt/elog_data`, the fixed paths of the ELOG container. Other files that `elogd.cfg` refers to must be in
  one of the two folders.
* Backups, the change log and the session key go into the `state` volume
  (`docker compose exec elog-manager ls /state/backups`). The container needs
  write access to `elogd.cfg` itself. If the directory holding it is not
  writable, the file is rewritten in place instead of being replaced
  atomically; the backup is still taken first.
* At startup the manager checks these paths and stops with a message saying
  what to fix, for example a missing `elogd.cfg` or a state directory it
  cannot write.
* `pid: host` lets the manager see elogd and send it `SIGHUP`. Because the
  container runs as a non-root user, it can only signal processes of that same
  user. Docker's default AppArmor profile also blocks signals from a container
  to processes outside Docker, so the compose file sets
  `security_opt: [apparmor=unconfined]` for this container; on SELinux hosts use
  `label=disable` instead. If elogd runs in the same compose project, use
  `pid: "service:elogd"` and drop both.
* If reloading still fails, the save message says why: a different user, an
  elogd that switched only its effective user (its real user is still root), or
  a security module.
* The port is published on 127.0.0.1 only. Serve it through your HTTPS reverse
  proxy (see *Reverse proxy* below). Over plain `http://`, sign-in does not
  work unless `ELOGMGR_BEHIND_PROXY=0`.

## Install without Docker

```bash
sudo mkdir -p /opt/elog-manager && sudo chown elog: /opt/elog-manager
# copy this repository to /opt/elog-manager, then:
cd /opt/elog-manager
sudo -u elog python3 -m venv venv && sudo -u elog venv/bin/pip install -r requirements.txt
sudo -u elog cp .env.example .env      # set ELOG_DIR (the Docker-only lines are ignored)
sudo cp deploy/elog-manager.service /etc/systemd/system/
sudo systemctl enable --now elog-manager
```

The service runs as the same user as elogd (`Usr = elog`), so it can write
`elogd.cfg` and read `passwords.pwd`; only installing it needs sudo.

## Reverse proxy

`deploy/nginx.conf` is a complete nginx site: HTTPS with Let's Encrypt
certificates, ELOG at `https://elog.example.org/` and the manager at
`https://elog.example.org/manage/`.

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/elog   # then edit the host name
sudo ln -s /etc/nginx/sites-available/elog /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

* The manager uses only relative URLs, so any subfolder works. Keep the
  trailing slashes in `location /manage/` and `proxy_pass http://127.0.0.1:8090/;`
  so the prefix is stripped. nginx then redirects `/manage` to `/manage/`.
* Avoid a logbook called `manage`; its ELOG URL would be routed to the manager.
* In `elogd.cfg`, set `URL = https://elog.example.org/` so ELOG builds correct
  links, and `Interface = 127.0.0.1` so elogd is only reachable through nginx.
* For the manager, the config adds a strict Content-Security-Policy and clears
  any `X-Remote-User` header sent by clients.

**Applying changes.** elogd does not watch `elogd.cfg`. It re-reads the file
on `SIGHUP` (`killall -HUP elogd`). After every save, the manager sends that
signal itself (`ELOGMGR_RELOAD=signal`). The result appears in the save
message, and the **Reload ELOG** button in the top bar sends the signal again
by hand.

* elogd switches to `Usr = elog` after start-up. A manager running as the same
  `elog` user may therefore signal it **without sudo**; this is the recommended
  setup and what `deploy/elog-manager.service` does.
* If elogd keeps running as root, set `ELOGMGR_RELOAD=command` and
  `ELOGMGR_RELOAD_COMMAND=sudo -n /usr/bin/killall -HUP elogd`. Then add this
  one rule with `visudo -f /etc/sudoers.d/elog-manager`:
  `elog ALL=(root) NOPASSWD: /usr/bin/killall -HUP elogd`, and remove
  `NoNewPrivileges=true` from the service file, because it blocks sudo.
* `ELOGMGR_RELOAD=none` disables reloading.

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
elogmgr/lint.py      config checks (also run before every save)
elogmgr/options.py   option catalog (options.json) and value checks
elogmgr/store.py     atomic writes, conflict detection, backups, audit log
elogmgr/app.py       Flask API and permissions
elogmgr/settings.py  environment settings
elogmgr/templates.py built-in logbook templates
elogmgr/static/      single-page UI (no build step)
examples/            fictional sample elogd.cfg used by the demo and tests
tools/build_options.py  rebuilds options.json from ELOG's option reference
Dockerfile, docker-compose.yml, docker-compose.demo.yml
```
