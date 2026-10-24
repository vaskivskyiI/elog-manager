"""ELOG Manager: a web GUI for editing elogd.cfg."""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, jsonify, request, send_from_directory, session

from .elogcfg import ElogConfig, Line, join_list, norm
from .lint import lint
from .reload import reload_elogd
from .store import ConflictError, Store, unified_diff

HERE = os.path.dirname(os.path.abspath(__file__))
NAME_RE = re.compile(r"^[^\[\],=#;/\\]+$")
COPY_SKIP = {"subdir", "comment"}


class OpError(Exception):
    pass


def load_settings() -> dict:
    path = os.environ.get("ELOGMGR_SETTINGS", os.path.join(os.getcwd(), "settings.json"))
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    base = os.path.dirname(os.path.abspath(path))
    for k in ("elogd_cfg", "password_file", "backup_dir", "audit_log"):
        if s.get(k) and not os.path.isabs(s[k]):
            s[k] = os.path.join(base, s[k])
    return s


def create_app(settings: dict | None = None) -> Flask:
    settings = settings or load_settings()
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=settings.get("secret_key") or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=bool(settings.get("secure_cookies", True)),
        SESSION_COOKIE_NAME="elogmgr",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=float(settings.get("session_hours", 12))),
    )
    if settings.get("behind_proxy"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    if not settings.get("password_file"):
        cfg, _ = Store(settings).load()
        pf = cfg.global_.get("Password file")
        if pf:
            settings["password_file"] = pf if os.path.isabs(pf) else os.path.join(
                cfg.global_.get("Logbook dir", os.path.dirname(settings["elogd_cfg"])), pf)
    store = Store(settings)
    failures: dict[str, list[float]] = defaultdict(list)

    # ---- roles ---------------------------------------------------------
    def super_admins(cfg: ElogConfig) -> set[str]:
        explicit = settings.get("manager_admins")
        return set(explicit) if explicit else set(cfg.global_.get_list("Admin user"))

    def admin_logbooks(cfg: ElogConfig, user: str) -> list[str]:
        return [s.name for s in cfg.logbook_sections() if user in s.get_list("Admin user")]

    def role_of(cfg: ElogConfig, user: str) -> str | None:
        allowed = settings.get("allowed_users")
        if allowed and user not in allowed:
            return None
        if user in super_admins(cfg):
            return "super"
        if settings.get("allow_logbook_admins", True) and admin_logbooks(cfg, user):
            return "logbook_admin"
        return None

    def current_user() -> str | None:
        if settings.get("auth_mode") == "header":
            return request.headers.get(settings.get("auth_header", "X-Remote-User")) or None
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
            role = role_of(cfg, user)
            if role is None:
                return jsonify(error=f"User '{user}' is not allowed to manage this ELOG"), 403
            return fn(user, role, cfg, etag, *a, **kw)
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
        if settings.get("auth_mode") == "header":
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
        cfg, _ = store.load()
        if role_of(cfg, name) is None:
            return jsonify(error=f"'{name}' is not a manager. Ask an ELOG admin to add you as "
                                 "admin of a logbook or to the manager_admins list."), 403
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
            return jsonify(user=None, auth_mode=settings.get("auth_mode", "password_file"))
        cfg, _ = store.load()
        return jsonify(user=user, role=role_of(cfg, user))

    # ---- state --------------------------------------------------------
    def build_state(user, role, cfg, etag):
        users = store.users()
        mine = set(admin_logbooks(cfg, user))
        logbooks = []
        for s in cfg.logbook_sections():
            logbooks.append({
                "name": s.name,
                "items": s.items(),
                "groups": cfg.groups_of(s.name),
                "editable": role == "super" or s.name in mine,
            })
        issues = lint(cfg, users or None)
        return {
            "etag": etag,
            "me": {"name": user, "role": role},
            "global": cfg.global_.items(),
            "groups": cfg.groups(),
            "logbooks": logbooks,
            "users": [u.public() for u in sorted(users.values(), key=lambda u: u.name.lower())],
            "issues": issues,
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
        if not sec:
            abort(404)
        return jsonify(text=sec.text(), etag=etag)

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
            raise OpError("Only ELOG manager admins can do this")

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

    def apply_op(cfg: ElogConfig, op: dict, user: str, role: str, orig: ElogConfig) -> ElogConfig:
        kind = op.get("op")
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

        need_super(role)
        if kind == "create_logbook":
            name = valid_name(op["name"])
            if cfg.section(name):
                raise OpError(f"Logbook '{name}' already exists")
            items: list[tuple[str, str]] = []
            tpl = op.get("template")
            if tpl:
                items = [(k, v) for k, v in sec_or_fail(cfg, tpl).items() if norm(k) not in COPY_SKIP]
            items = [("Comment", str(op.get("comment", "")).strip())] + items
            for k, v in (op.get("set") or {}).items():
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
            cfg.add_section(name, items, after=after or (tpl if tpl else None))
            if group:
                groups = cfg.groups()
                for g in groups:
                    if g["name"].lower() == group.lower():
                        g["members"].append(name)
                cfg.set_groups(groups)
        elif kind == "delete_logbook":
            name = sec_or_fail(cfg, op["name"]).name
            cfg.remove_section(name)
            cfg.replace_group_member(name, None)
        elif kind == "rename_logbook":
            sec = sec_or_fail(cfg, op["name"])
            old, new = sec.name, valid_name(op["new"])
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
        users = store.users() or None
        before = {(i["section"], i["message"]) for i in lint(orig, users)}
        new_issues = [i for i in lint(new_cfg, users) if (i["section"], i["message"]) not in before]
        if role != "super" and not admin_logbooks(new_cfg, user) and not data.get("confirm_lockout"):
            return jsonify(error="This change would remove you as admin of all your logbooks; "
                                 "you would lose access to the manager."), 400
        if data.get("dry_run"):
            return jsonify(diff=unified_diff(old_text, new_text), new_issues=new_issues)
        try:
            new_etag = store.write(new_text, etag, user, data.get("summary") or ops[0].get("op"),
                                   {"ops": describe(ops)})
        except ConflictError as e:
            return jsonify(error=str(e), conflict=True), 409
        return jsonify(ok=True, etag=new_etag, new_issues=new_issues, reload=reload_elogd(settings))

    @app.post("/api/reload")
    @require_login
    def reload(user, role, cfg, etag):
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
    p = argparse.ArgumentParser(description="ELOG Manager web GUI")
    p.add_argument("--settings", help="settings.json path")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    a = p.parse_args()
    if a.settings:
        os.environ["ELOGMGR_SETTINGS"] = a.settings
    s = load_settings()
    app = create_app(s)
    host = a.host or s.get("host", "127.0.0.1")
    port = a.port or int(s.get("port", 8090))
    try:
        from waitress import serve
        print(f"ELOG Manager on http://{host}:{port} (waitress)")
        serve(app, host=host, port=port)
    except ImportError:
        app.run(host=host, port=port)


if __name__ == "__main__":
    main()
