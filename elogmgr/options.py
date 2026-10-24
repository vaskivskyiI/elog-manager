"""Catalog of elogd.cfg options.

options.json holds the facts extracted from ELOG's reference
(tools/build_options.py): name, value syntax, default, section, doc link.
Options added by the companion ELOG fork are appended to options.json
with "source": "fork" and carry their own scope and description; a
rebuild keeps them. This module adds a value type, our own one-line
descriptions for the common options, and lookups that understand pattern
options such as "Options <attribute>" and conditional keys such as
"{1} Subst SN".
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

from .elogcfg import norm, strip_condition

HERE = os.path.dirname(os.path.abspath(__file__))

# Real options that the reference does not list as headings
EXTRA = [
    {"name": "Top group <name>", "spec": "<list>", "section": "Top groups", "default": None,
     "doc": "https://elog.psi.ch/elog/config.html#top-groups"},
    {"name": "Usr", "spec": "<user>", "section": "Global options", "default": None, "doc": None},
    {"name": "Grp", "spec": "<group>", "section": "Global options", "default": None, "doc": None},
    {"name": "Execute edit", "spec": "<command>", "section": "Attributes", "default": None, "doc": None},
    {"name": "Execute delete", "spec": "<command>", "section": "Attributes", "default": None, "doc": None},
]
FIXES = {"email all": {"spec": "<list>"},
         # defaults the reference states in prose rather than as "the default is"
         "reverse sort": {"default": "0"}, "refresh": {"default": "0"}, "enable attachments": {"default": "1"}}

GLOBAL_ONLY_SECTIONS = {"Global options", "Groups of logbooks", "Top groups", "Mirroring",
                        "Kerberos authentication", "LDAP authentication", "PAM authentication"}
GLOBAL_ONLY_NAMES = {"password file", "login expiration", "self register", "usr", "grp", "hosts allow",
                     "hosts deny", "max content length"}

# Our own short descriptions; the rest link to the reference.
DESCRIPTIONS = {
    "comment": "Description shown next to the logbook on the start page",
    "page title": "Browser title of an entry page; may use $attribute",
    "list page title": "Browser title of the entry list",
    "entries per page": "Entries shown per page in the list",
    "reverse sort": "Show newest entries first",
    "refresh": "Reload the list page every N seconds (0 = never)",
    "restrict edit": "Users may only edit their own entries",
    "restrict edit time": "Entries can be edited only this many hours after creation",
    "admin restrict edit time": "Same limit for admin users (0 = no limit)",
    "allow html": "Allow raw HTML in entries (lets users embed scripts)",
    "thread display": "One-line summary of an entry in threaded view; may use $attribute",
    "quick filter": "Attributes offered as filter boxes above the list",
    "list display": "Columns of the entry list",
    "attributes": "Fields of an entry",
    "required attributes": "Fields that must be filled in",
    "locked attributes": "Fields users cannot change (e.g. Author)",
    "extendable options": "Drop-downs where users may add new values",
    "fixed attributes reply": "Fields copied unchanged into replies",
    "fixed attributes edit": "Fields that cannot be changed when editing",
    "propagate attributes": "Fields whose change is copied to the whole thread",
    "login user": "Who may open the logbook (empty = every registered user)",
    "admin user": "Who may change the logbook configuration",
    "guest menu commands": "Commands for anonymous visitors; set to allow read access without login",
    "menu commands": "Commands shown on an entry page",
    "list menu commands": "Commands shown on the list page",
    "subdir": "Data directory below 'Logbook dir' (default: the logbook name)",
    "thumbnail size": "Size of image thumbnails in pixels",
    "time format": "strftime format for dates, e.g. %d.%m.%Y %H:%M",
    "date format": "strftime format for date-only attributes",
    "message width": "Width of the text editor in characters",
    "message height": "Height of the text editor in lines",
    "summary lines": "Lines of text shown per entry in summary mode",
    "attachment lines": "Lines of text attachments shown inline",
    "display mode": "Default list view",
    "enable attachments": "Allow file attachments",
    "save drafts": "Keep unsaved entries as drafts",
    "autosave": "Save drafts every N seconds",
    "suppress default": "Default state of 'suppress email notification'",
    "quote on reply": "Quote the original text in replies",
    "list after submit": "Go to the list instead of the entry after submitting",
    "hidden": "Hide this logbook from the start page",
    "use lock": "Warn when someone else is editing the same entry",
    "allow branching": "Allow replies to replies (threads with branches)",
    "theme": "Theme directory under themes/",
    "preset text": "Initial text of a new entry",
    "max content length": "Maximum upload size in bytes",
    "self register": "0 off, 1 open, 2 notify admins, 3 admins approve, 4 user confirms by email",
    "login expiration": "Hours until users must log in again",
    "url": "Public URL of ELOG (needed behind a reverse proxy)",
    "smtp host": "Mail server for notifications",
    "use email from": "Sender address of notification emails",
    "welcome title": "Text above the logbook list on the start page",
    "bottom text": "Footer text or file",
    "top text": "Header text or file",
    "help url": "Link target of the Help button",
}
# Shown on the logbook Overview tab, in this order
COMMON = ["Comment", "Page title", "Thread display", "Entries per page", "Display mode", "Reverse sort",
          "Refresh", "Restrict edit", "Restrict edit time", "Allow HTML", "Enable attachments",
          "Quote on reply", "List after submit", "Time format", "Thumbnail size", "Preset text", "Subdir"]


def _type(spec: str) -> tuple[str, list[str]]:
    s = spec.strip().strip("<>[]'*").strip()
    alts = [a.strip() for a in re.split(r"\s*\|\s*", s)]
    if len(alts) > 1 and all(re.fullmatch(r"[\w-]+", a) for a in alts):
        return ("bool", []) if alts == ["0", "1"] else ("enum", alts)
    if re.fullmatch(r"<?(number|hours|seconds|bytes|n|port|days|size|x|cols)>?", spec.strip(), re.I):
        return "number", []
    if "user list" in spec:
        return "users", []
    if "list" in spec.lower():
        return "list", []
    return "string", []


def _pattern(name: str) -> re.Pattern | None:
    if "<" not in name:
        return None
    rx = re.sub(r"<[^>]*>", ".+", re.escape(" ".join(name.split())).replace("\\ ", " "))
    return re.compile(rx, re.I)


@lru_cache(maxsize=1)
def official() -> list[dict]:
    with open(os.path.join(HERE, "options.json"), encoding="utf-8") as f:
        raw = json.load(f) + EXTRA
    out, seen = [], {}
    for o in raw:
        key = norm(o["name"])
        scope = o.get("scope") or ("global" if o["section"] in GLOBAL_ONLY_SECTIONS or key in GLOBAL_ONLY_NAMES
                                   else "any")
        if key in seen:  # documented twice (e.g. Page title: start page and entry page) -> valid in both
            if scope == "any":
                seen[key]["scope"] = "any"
            continue
        o = {**o, **FIXES.get(key, {})}
        typ, values = _type(o["spec"])
        out.append({
            **o,
            "type": typ,
            "values": values,
            "scope": scope,
            "pattern": "<" in o["name"],
            "desc": o.get("desc") or DESCRIPTIONS.get(key, ""),
        })
        seen[key] = out[-1]
    return out


# ---------------------------------------------------------------------
# Custom options: site-specific or undocumented keys that a manager has
# registered, kept in <state dir>/custom_options.json and re-read when the
# file changes.
# ---------------------------------------------------------------------

CUSTOM_TYPES = {"string": "<string>", "bool": "0|1", "number": "<number>", "list": "<list>",
                "users": "<user list>", "enum": None}
_custom = {"path": None, "mtime": None, "items": [], "version": 0}


def set_custom_path(path: str | None) -> None:
    _custom.update(path=path, mtime=None, items=[], version=_custom["version"] + 1)


def _normalize_custom(o: dict) -> dict:
    typ = o.get("type") if o.get("type") in CUSTOM_TYPES else "string"
    values = [v.strip() for v in o.get("values") or [] if str(v).strip()] if typ == "enum" else []
    name = " ".join(str(o.get("name", "")).split())
    return {
        "name": name,
        "spec": "|".join(values) if typ == "enum" else CUSTOM_TYPES[typ],
        "section": "Custom options", "doc": None,
        "default": (str(o["default"]).strip() or None) if o.get("default") is not None else None,
        "type": typ, "values": values,
        "scope": "global" if o.get("scope") == "global" else "any",
        "pattern": "<" in name,
        "desc": str(o.get("desc") or "").strip(),
        "custom": True,
    }


def custom_options() -> list[dict]:
    path = _custom["path"]
    if not path or not os.path.exists(path):
        if _custom["items"]:
            _custom.update(mtime=None, items=[], version=_custom["version"] + 1)
        return []
    mtime = os.path.getmtime(path)
    if mtime != _custom["mtime"]:
        try:
            with open(path, encoding="utf-8") as f:
                items = [_normalize_custom(o) for o in json.load(f) if isinstance(o, dict) and o.get("name")]
        except (OSError, ValueError):
            items = []
        _custom.update(mtime=mtime, items=items, version=_custom["version"] + 1)
    return _custom["items"]


def save_custom_options(items: list[dict]) -> None:
    path = _custom["path"]
    if not path:
        raise ValueError("No state directory for custom options")
    keep = [{k: o[k] for k in ("name", "type", "values", "scope", "default", "desc")} for o in items]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    _custom["mtime"] = None  # re-read on next use


def catalog() -> list[dict]:
    return official() + custom_options()


_index_cache: dict = {}


def _index():
    token = _custom["version"]  # changes on every re-read, even within one mtime tick
    if _index_cache.get("token", object()) == token and "data" in _index_cache:
        return _index_cache["data"]
    exact, patterns = {}, []
    for o in catalog():
        if o["pattern"]:
            patterns.append((_pattern(o["name"]), o))
        else:
            exact.setdefault(norm(o["name"]), o)
    patterns.sort(key=lambda p: -len(p[1]["name"]))  # most specific first
    _index_cache.update(token=token, data=(exact, patterns))
    return exact, patterns


def lookup(key: str) -> dict | None:
    _, bare = strip_condition(key)
    custom_options()  # pick up changes to the custom file
    exact, patterns = _index()
    k = norm(bare)
    if k in exact:
        return exact[k]
    for rx, o in patterns:
        if rx.fullmatch(" ".join(bare.split())):
            return o
    return None


def check_value(opt: dict, value: str) -> str | None:
    """Return a problem description, or None if the value fits the option's type."""
    v = value.strip()
    if opt["pattern"] or not v:
        return None
    if opt["type"] == "bool" and v not in ("0", "1"):
        return f"must be 0 or 1, not '{v}'"
    if opt["type"] == "number":
        if opt.get("integer"):
            return None if re.fullmatch(r"\d+", v) else f"must be a whole number, not '{v}'"
        try:
            float(v)
        except ValueError:
            return f"must be a number, not '{v}'"
    if opt["type"] == "enum" and v.lower() not in (x.lower() for x in opt["values"]):
        return f"must be one of {', '.join(opt['values'])}, not '{v}'"
    return None
