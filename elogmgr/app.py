"""ELOG Manager: a web GUI for editing elogd.cfg."""
from __future__ import annotations

import os
import re
import sys
import time
from collections import defaultdict
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, jsonify, request, send_from_directory, session

from .elogcfg import ElogConfig, Line, join_list, norm
from .lint import author_problem, ensure_author, lint
from .options import COMMON, catalog
from .reload import reload_elogd
from .settings import SetupError, check_paths, from_env, load_env_file, secret_key
from .store import ConflictError, Store, unified_diff
from .templates import PREFIX as TEMPLATE_PREFIX, TEMPLATES, builtin

HERE = os.path.dirname(os.path.abspath(__file__))
NAME_RE = re.compile(r"^[^\[\],=#;/\\]+$")
COPY_SKIP = {"subdir", "comment", "login user", "admin user"}
# [global] keys not shown to non-managers (paths, mail, network, accounts)
PRIVATE_GLOBAL = re.compile(r"^(password file|logbook dir|resource dir|logfile|smtp|usr|grp|port|interface|"
                            r"url|mirror|ssl|certificate|admin user|use email|self register|group |top group )",
                            re.I)


class OpError(Exception):
    pass


def create_app(settings: dict | None = None) -> Flask:
    settings = settings or from_env()
    store = Store(settings)
    for warning in check_paths(store.cfg_path, store.state_dir):
        print("WARNING:", warning, file=sys.stderr, flush=True)
    cfg0, _ = store.load()
    try:  # ELOG's own session length, in hours
        session_hours = float(cfg0.global_.get("Login expiration") or 12)
    except ValueError:
        session_hours = 12
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=settings.get("secret_key") or secret_key(store.state_dir),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=bool(settings.get("secure_cookies", True)),
        SESSION_COOKIE_NAME="elogmgr",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=session_hours),
    )
    if settings.get("behind_proxy"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    failures: dict[str, list[float]] = defaultdict(list)
    auth_header = settings.get("auth_header") or ""

    # ---- roles ---------------------------------------------------------
    # super (manager): [global] Admin user - everything
    # logbook_admin: in some logbook's Admin user - those logbooks, plus creating new ones
    # user: any other ELOG account - can create logbooks and becomes their admin
    def managers(cfg: ElogConfig) -> set[str]:
        return set(cfg.global_.get_list("Admin user"))

    def admin_logbooks(cfg: ElogConfig, user: str) -> list[str]:
        return [s.name for s in cfg.logbook_sections() if user in s.get_list("Admin user")]

    def role_of(cfg: ElogConfig, user: str) -> str:
        if user in managers(cfg):
            return "super"
        return "logbook_admin" if admin_logbooks(cfg, user) else "user"

    def current_user() -> str | None:
        if auth_header:
            return request.headers.get(auth_header) or None
        return session.get("user")

    def require_login(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            if not user:
                return jsonify(error="Not logged in"), 401
            if request.method != "GET" and request.headers.get("X-Requested-With") != "elogmgr":
                return jsonify(error="Missing CSRF header"), 403
            cfg, etag = store.load()
            return fn(user, role_of(cfg, user), cfg, etag, *a, **kw)
        return wrapper

    # ---- static -------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(os.path.join(HERE, "static"), "index.html")

    @app.get("/static/<path:p>")
    def static_files(p):
        return send_from_directory(os.path.join(HERE, "static"), p)

    # ---- auth ---------------------------------------------------------
    @app.post("/api/login")
    def login():
        if auth_header:
            return jsonify(error="Login is handled by the web server"), 400
        ip = request.remote_addr or "?"
        now = time.time()
        failures[ip] = [t for t in failures[ip] if now - t < 600]
        if len(failures[ip]) >= 5:
            return jsonify(error="Too many failed attempts, try again in 10 minutes"), 429
        data = request.get_json(force=True, silent=True) or {}
        name, pw = str(data.get("user", "")).strip(), str(data.get("password", ""))
        users = store.users()
        u = users.get(name)
        if not u or not u.check(pw):
            failures[ip].append(now)
            return jsonify(error="Wrong user name or password"), 401
        session.clear()
        session.permanent = True
        session["user"] = name
        failures.pop(ip, None)
        return jsonify(ok=True)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/me")
    def me():
        user = current_user()
        if not user:
            return jsonify(user=None, auth_mode="header" if auth_header else "password_file")
        cfg, _ = store.load()
        return jsonify(user=user, role=role_of(cfg, user))

    # ---- state --------------------------------------------------------
    def build_state(user, role, cfg, etag):
        users = store.users(cfg)
        is_super = role == "super"
        mine = set(admin_logbooks(cfg, user))
        visible = {s.name.lower() for s in cfg.logbook_sections() if is_super or s.name in mine}
        lb_names = {n.lower() for n in cfg.logbook_names()}
        logbooks = [{"name": s.name, "items": s.items(), "groups": cfg.groups_of(s.name), "editable": True}
                    for s in cfg.logbook_sections() if s.name.lower() in visible]
        groups = cfg.groups()
        if not is_super:  # the group structure is shared; only the user's own logbooks are listed in it
            for g in groups:
                g["members"] = [m for m in g["members"] if m.lower() in visible or m.lower() not in lb_names]
        issues = [i for i in lint(cfg, users or None) if is_super or i["section"].lower() in visible]
        glob = [kv for kv in cfg.global_.items() if is_super or not PRIVATE_GLOBAL.match(kv[0])]
        user_list = [u.public() if is_super else {"name": u.name, "full_name": u.full_name}
                     for u in sorted(users.values(), key=lambda u: u.name.lower())]
        return {
            "etag": etag,
            "me": {"name": user, "role": role},
            "global": glob,
            "groups": groups,
            "logbooks": logbooks,
            "users": user_list,
            "issues": issues,
            "templates": [TEMPLATE_PREFIX + t for t in TEMPLATES],
            "elog_url": settings.get("elog_url") or cfg.global_.get("URL") or "",
        }

    @app.get("/api/state")
    @require_login
    def state(user, role, cfg, etag):
        return jsonify(build_state(user, role, cfg, etag))

    @app.get("/api/section/<path:name>")
    @require_login
    def section_text(user, role, cfg, etag, name):
        sec = cfg.section(name)
        if not sec or (role != "super" and sec.name not in admin_logbooks(cfg, user)):
            abort(404)
        return jsonify(text=sec.text(), etag=etag)

    @app.get("/api/options")
    @require_login
    def options(user, role, cfg, etag):
        return jsonify(options=catalog(), common=COMMON)

    @app.get("/api/check_name")
    @require_login
    def check_name(user, role, cfg, etag):
        """Is a logbook name free (among all logbooks, also ones the user cannot see)? Next name in a group?"""
        name = " ".join(request.args.get("name", "").split())
        group = request.args.get("group", "")
        out = {"suggestion": next_name(cfg, group) if group else None}
        if name:
            out["available"] = not name_taken(cfg, name)
            out["valid"] = bool(NAME_RE.match(name)) and not name.lower().startswith("global")
        return jsonify(out)

    @app.get("/api/raw")
    @require_login
    def raw(user, role, cfg, etag):
        if role != "super":
            abort(403)
        text, etag = store.raw_text()
        return jsonify(text=text, etag=etag)

    # ---- mutations ----------------------------------------------------
    def check_section_perm(cfg, user, role, name):
        if role == "super":
            return
        if name.lower().startswith("global") or name not in admin_logbooks(cfg, user):
            raise OpError(f"You are not an admin of logbook '{name}'")

    def need_super(role):
        if role != "super":
            raise OpError("Only ELOG managers ([global] Admin user) can do this")

    def sec_or_fail(cfg, name):
        s = cfg.section(name)
        if not s:
            raise OpError(f"No section [{name}]")
        return s

    def valid_name(name: str) -> str:
        name = " ".join(str(name).split())
        if not name or not NAME_RE.match(name) or name.lower().startswith("global"):
            raise OpError(f"Invalid name '{name}': avoid [ ] , = # ; / \\ and the prefix 'global'")
        return name

    def name_taken(cfg: ElogConfig, name: str) -> bool:
        return bool(cfg.section(name)) or any(g["name"].lower() == name.lower() for g in cfg.groups())

    def next_name(cfg: ElogConfig, group: str) -> str | None:
        """Next free name in a numbered series (np001..np010 -> np011), from all logbooks of the group."""
        g = next((g for g in cfg.groups() if g["name"].lower() == group.lower()), None)
        if not g:
            return None
        series: dict[str, list[tuple[int, int]]] = {}
        for m in g["members"]:
            mm = re.fullmatch(r"(.*?)(\d+)", m)
            if mm and cfg.section(m):
                series.setdefault(mm.group(1), []).append((int(mm.group(2)), len(mm.group(2))))
        if not series:
            return None
        prefix, nums = max(series.items(), key=lambda kv: (len(kv[1]), max(kv[1])))
        n, width = max(nums)[0] + 1, max(w for _, w in nums)
        while name_taken(cfg, f"{prefix}{n:0{width}d}"):
            n += 1
        return f"{prefix}{n:0{width}d}"

    def apply_op(cfg: ElogConfig, op: dict, user: str, role: str, orig: ElogConfig) -> ElogConfig:
        kind = op.get("op")
        if kind == "ensure_author":
            check_section_perm(orig, user, role, op["section"])
            ensure_author(cfg, sec_or_fail(cfg, op["section"]))
            return cfg
        if kind in ("set", "delete_key", "rename_key", "list_add", "list_remove", "section_text"):
            check_section_perm(orig, user, role, op["section"])
            sec = sec_or_fail(cfg, op["section"])
            key = str(op.get("key", "")).strip()
            if kind != "section_text" and (not key or "=" in key or key.startswith(("#", "["))):
                raise OpError(f"Invalid key '{key}'")
            if role != "super" and kind != "section_text" and norm(key) in ("subdir",):
                raise OpError("Only manager admins can change Subdir")
            if kind == "set":
                value = str(op.get("value", "")).replace("\n", " ").replace("\r", " ").strip()
                sec.set(key, value, after=op.get("after"))
            elif kind == "delete_key":
                sec.delete(key)
            elif kind == "rename_key":
                sec.rename_key(key, str(op["new"]).strip())
            elif kind in ("list_add", "list_remove"):
                cur = sec.get_list(key)
                items = [i.strip() for i in op.get("items", []) if i.strip()]
                if kind == "list_add":
                    cur += [i for i in items if i.lower() not in (c.lower() for c in cur)]
                    sec.set(key, join_list(cur))
                else:
                    rm = {i.lower() for i in items}
                    cur = [c for c in cur if c.lower() not in rm]
                    if cur:
                        sec.set(key, join_list(cur))
                    elif op.get("delete_if_empty", True):
                        sec.delete(key)
                    else:
                        sec.set(key, "")
            else:  # section_text
                text = str(op["text"])
                parsed = ElogConfig(text)
                if len(parsed.sections) != 1 or parsed.sections[0].name.lower() != sec.name.lower() \
                        or any(l.kind != "blank" for l in parsed.preamble.lines):
                    raise OpError(f"Section text must start with [{sec.name}] and contain no other section")
                if role != "super" and parsed.sections[0].get("Subdir") != sec.get("Subdir"):
                    raise OpError("Only manager admins can change Subdir")
                sec.lines = parsed.sections[0].lines
                if not sec.lines or sec.lines[-1].kind != "blank":
                    sec.lines.append(Line(""))
            return cfg

        if kind == "create_logbook":  # anyone; the creator becomes admin
            name = valid_name(op["name"])
            if name_taken(cfg, name):
                raise OpError(f"A logbook or group called '{name}' already exists")
            items: list[tuple[str, str]] = []
            tpl = op.get("template") or None
            if tpl and builtin(tpl) is not None:
                items = list(builtin(tpl))
                tpl = None
            elif tpl:
                if role != "super" and tpl not in admin_logbooks(orig, user):
                    raise OpError(f"You can only copy logbooks you administer, not '{tpl}'")
                items = [(k, v) for k, v in sec_or_fail(cfg, tpl).items() if norm(k) not in COPY_SKIP]
            comment = str(op.get("comment", "")).strip()
            items = ([("Comment", comment)] if comment else []) + items
            extra = dict(op.get("set") or {})
            if role != "super":
                if any(norm(k) == "subdir" for k in extra):
                    raise OpError("Only managers can set Subdir")
                admins = [u.strip() for u in (extra.get("Admin user") or "").split(",") if u.strip()]
                extra["Admin user"] = join_list([user] + [a for a in admins if a != user])
                logins = [u.strip() for u in (extra.get("Login user") or "").split(",") if u.strip()]
                if logins and user not in logins:
                    extra["Login user"] = join_list([user] + logins)
            for k, v in extra.items():
                items = [(ik, iv) for ik, iv in items if norm(ik) != norm(k)]
                if v is not None:
                    items.append((k, v))
            group = op.get("group")
            after = None
            if group:
                g = next((g for g in cfg.groups() if g["name"].lower() == group.lower()), None)
                if not g:
                    raise OpError(f"No group '{group}'")
                existing = [m for m in g["members"] if cfg.section(m)]
                after = existing[-1] if existing else None
            new_sec = cfg.add_section(name, items, after=after or (tpl if tpl else None))
            ensure_author(cfg, new_sec)  # policy: locked, prefilled Author in every logbook
            if group:
                groups = cfg.groups()
                for g in groups:
                    if g["name"].lower() == group.lower():
                        g["members"].append(name)
                cfg.set_groups(groups)
            return cfg

        if kind == "set_logbook_groups":  # place one logbook in groups; other members untouched
            check_section_perm(orig, user, role, op["name"])
            name = sec_or_fail(cfg, op["name"]).name
            want = {str(g).lower() for g in op.get("groups", [])}
            groups = cfg.groups()
            unknown = want - {g["name"].lower() for g in groups}
            if unknown:
                raise OpError(f"No group '{sorted(unknown)[0]}'")
            for g in groups:
                has = any(m.lower() == name.lower() for m in g["members"])
                if g["name"].lower() in want and not has:
                    g["members"].append(name)
                elif g["name"].lower() not in want and has:
                    g["members"] = [m for m in g["members"] if m.lower() != name.lower()]
            cfg.set_groups(groups)
            return cfg

        need_super(role)
        if kind == "delete_logbook":
            name = sec_or_fail(cfg, op["name"]).name
            cfg.remove_section(name)
            cfg.replace_group_member(name, None)
        elif kind == "rename_logbook":
            sec = sec_or_fail(cfg, op["name"])
            old, new = sec.name, valid_name(op["new"])
            if new.lower() != old.lower() and (name_taken(cfg, new)):
                raise OpError(f"A logbook or group called '{new}' already exists")
            cfg.rename_section(old, new)
            if op.get("keep_data", True) and not sec.get("Subdir"):
                # entries live in <Logbook dir>/<old name>; keep pointing there
                sec.set("Subdir", old, after="Comment")
            cfg.replace_group_member(old, new)
        elif kind == "set_groups":
            groups = []
            names = set()
            for g in op["groups"]:
                gname = valid_name(g["name"])
                if gname.lower() in names:
                    raise OpError(f"Duplicate group '{gname}'")
                names.add(gname.lower())
                groups.append({"name": gname, "top": bool(g.get("top")),
                               "members": [m.strip() for m in g.get("members", []) if m.strip()]})
            cfg.set_groups(groups)
        elif kind == "group_remove_member":
            groups = cfg.groups()
            for g in groups:
                if g["name"].lower() == op["group"].lower():
                    g["members"] = [m for m in g["members"] if m.lower() != op["member"].lower()]
            cfg.set_groups(groups)
        elif kind == "move_section":
            sec = sec_or_fail(cfg, op["name"])
            cfg.sections.remove(sec)
            ref = cfg.section(op["after"]) if op.get("after") else None
            pos = cfg.sections.index(ref) + 1 if ref else len(cfg.sections)
            cfg.sections.insert(pos, sec)
        elif kind == "raw":
            cfg = ElogConfig(str(op["text"]))
        elif kind == "restore":
            cfg = ElogConfig(store.backup_text(op["backup"]))
        else:
            raise OpError(f"Unknown operation '{kind}'")
        return cfg

    def describe(ops):
        return [{k: v for k, v in o.items() if k != "text"} for o in ops]

    @app.post("/api/apply")
    @require_login
    def apply(user, role, cfg, etag):
        data = request.get_json(force=True, silent=True) or {}
        ops = data.get("ops") or []
        if not ops:
            return jsonify(error="Nothing to do"), 400
        if data.get("etag") != etag:
            return jsonify(error="elogd.cfg changed since you loaded it. Reload and try again.",
                           conflict=True), 409
        old_text = cfg.text()
        orig = ElogConfig(old_text)
        new_cfg = ElogConfig(old_text)
        try:
            for op in ops:
                new_cfg = apply_op(new_cfg, op, user, role, orig)
        except (OpError, KeyError, ValueError, FileNotFoundError) as e:
            return jsonify(error=str(e).strip("'\"")), 400
        new_text = new_cfg.text()
        users = store.users(orig) or None
        # issues the logbook already had are not "new" just because it was renamed
        renames = {o["name"].lower(): " ".join(str(o.get("new", "")).split())
                   for o in ops if o.get("op") == "rename_logbook" and o.get("name")}
        before = set()
        for i in lint(orig, users):
            sec, msg = i["section"], i["message"]
            new_name = renames.get(sec.lower())
            if new_name:
                sec, msg = new_name, msg.replace(f"'{i['section']}'", f"'{new_name}'")
            before.add((sec, msg))
        new_issues = [i for i in lint(new_cfg, users) if (i["section"], i["message"]) not in before]
        warnings = []
        if role != "super":
            lost = sorted(set(admin_logbooks(orig, user)) - set(admin_logbooks(new_cfg, user)))
            if lost:
                warnings.append(f"You will no longer be admin of {', '.join(lost)} and will not be able "
                                "to see or edit it here afterwards.")
        # Config check: a change may not introduce errors. Managers can override deliberately.
        blocking = [i for i in new_issues if i["level"] == "error"]
        can_force = role == "super"
        if data.get("dry_run"):
            return jsonify(diff=unified_diff(old_text, new_text), new_issues=new_issues, warnings=warnings,
                           blocking=bool(blocking), can_force=can_force)
        if blocking and not (can_force and data.get("force")):
            return jsonify(error="The configuration check failed: " + "; ".join(
                f"[{i['section']}] {i['message']}" for i in blocking), new_issues=new_issues), 400
        try:
            new_etag = store.write(new_text, etag, user, data.get("summary") or ops[0].get("op"),
                                   {"ops": describe(ops)})
        except ConflictError as e:
            return jsonify(error=str(e), conflict=True), 409
        return jsonify(ok=True, etag=new_etag, new_issues=new_issues, reload=reload_elogd(settings))

    @app.post("/api/reload")
    @require_login
    def reload(user, role, cfg, etag):
        if role != "super":  # saves reload elogd for everyone; the manual button is for managers
            abort(403)
        return jsonify(reload_elogd(settings))

    # ---- history ------------------------------------------------------
    @app.get("/api/history")
    @require_login
    def history(user, role, cfg, etag):
        if role != "super":
            return jsonify(backups=[], audit=[a for a in store.audit() if a.get("user") == user])
        return jsonify(backups=store.backups(), audit=store.audit())

    @app.get("/api/history/<backup_id>")
    @require_login
    def history_diff(user, role, cfg, etag, backup_id):
        if role != "super":
            abort(403)
        try:
            old = store.backup_text(backup_id)
        except (FileNotFoundError, OSError):
            abort(404)
        return jsonify(diff=unified_diff(old, cfg.text(), backup_id, "current"))

    return app


def main():
    import argparse
    p = argparse.ArgumentParser(description="ELOG Manager web GUI (settings: see elogmgr/settings.py)")
    p.add_argument("--env-file", help="read settings from this .env file (default: ./.env if present)")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    a = p.parse_args()
    env_file = a.env_file or (".env" if os.path.exists(".env") else None)
    if env_file:
        load_env_file(env_file)
    s = from_env()
    try:
        app = create_app(s)
    except SetupError as e:
        sys.exit(f"ELOG Manager cannot start: {e}")
    host, port = a.host or s["host"], a.port or s["port"]
    try:
        from waitress import serve
        print(f"ELOG Manager on http://{host}:{port} (waitress), config {s['elogd_cfg']}")
        serve(app, host=host, port=port)
    except ImportError:
        app.run(host=host, port=port)


if __name__ == "__main__":
    main()
