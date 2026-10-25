"""Consistency checks for elogd.cfg."""
from __future__ import annotations

import re

from .elogcfg import (ATTR_LIST_KEYS, BUILTIN_ATTRS, ElogConfig, attribute_key_target, join_list, norm,
                      split_list, strip_condition)
from .options import check_value, in_scope, lookup

# Keys that look plausible but are not ELOG options -> the right spelling
MISSPELLED = {
    "extendable attributes": "Extendable options",
    "extendable attribute": "Extendable options",
    "required attribute": "Required Attributes",
    "locked attribute": "Locked Attributes",
    "login users": "Login user",
    "admin users": "Admin user",
    "quick filters": "Quick filter",
    "attribute": "Attributes",
}


def _issue(level, section, message, key=None, fix=None):
    d = {"level": level, "section": section, "message": message}
    if key:
        d["key"] = key
    if fix:
        d["fix"] = fix
    return d


def lint(cfg: ElogConfig, users: dict | None = None) -> list[dict]:
    issues = []
    logbooks = cfg.logbook_names()
    lb_lower = {n.lower(): n for n in logbooks}
    groups = cfg.groups()
    group_lower = {g["name"].lower() for g in groups}
    user_lower = {u.lower(): u for u in users} if users else None

    if not cfg.global_.get_list("Admin user"):
        issues.append(_issue("warning", "global", "No 'Admin user' in [global]: nobody can manage groups or "
                                                  "other people's logbooks in the manager", key="Admin user"))

    # duplicate sections
    seen = {}
    for s in cfg.sections:
        if s.name.lower() in seen:
            issues.append(_issue("error", s.name, f"Section [{s.name}] is defined twice; elogd uses only one"))
        seen[s.name.lower()] = True

    # groups
    grouped = set()
    for g in groups:
        for m in g["members"]:
            if m.lower() in lb_lower:
                grouped.add(m.lower())
            elif m.lower() not in group_lower:
                issues.append(_issue("error", "global",
                                     f"Group '{g['name']}' references '{m}', which is neither a logbook nor a group",
                                     key=("Top group " if g["top"] else "Group ") + g["name"],
                                     fix={"op": "group_remove_member", "group": g["name"], "member": m}))
    by_name = {g["name"].lower(): g for g in groups}

    def reaches(start: str, target: str, seen: set) -> bool:
        for m in by_name.get(start, {"members": []})["members"]:
            ml = m.lower()
            if ml in lb_lower:  # a logbook, even if a group has the same name
                continue
            if ml == target:
                return True
            if ml in by_name and ml not in lb_lower and ml not in seen:
                seen.add(ml)
                if reaches(ml, target, seen):
                    return True
        return False

    for g in groups:
        if reaches(g["name"].lower(), g["name"].lower(), set()):
            issues.append(_issue("error", "global", f"Group '{g['name']}' contains itself (directly or via subgroups)",
                                 key="Group " + g["name"]))
    if groups:
        for n in logbooks:
            if n.lower() not in grouped:
                issues.append(_issue("warning", n, f"Logbook '{n}' is not in any group; with groups defined "
                                                   "it is not listed on the selection page (URL access only)"))

    for s in cfg.sections:
        is_global = s.name.lower().startswith("global")
        keys_seen = {}
        attrs = [a.lower() for a in s.get_list("Attributes")] or \
                [a.lower() for a in cfg.global_.get_list("Attributes")]
        known_attrs = set(attrs) | BUILTIN_ATTRS
        option_tags = set()
        cond_used = set()

        for k, v in s.items():
            cond, bare = strip_condition(k)
            nk = norm(k)
            if cond:
                cond_used.update(t.strip() for t in cond.strip()[1:-1].split(","))
            if nk in keys_seen and not nk.startswith(("group ", "top group ")):
                issues.append(_issue("warning", s.name, f"'{k}' is set twice; only one value is used", key=k))
            keys_seen[nk] = True

            if norm(bare) in MISSPELLED:
                right = MISSPELLED[norm(bare)]
                issues.append(_issue("error", s.name, f"'{bare}' is not an ELOG option and is ignored; "
                                                      f"did you mean '{right}'?", key=k,
                                     fix={"op": "rename_key", "section": s.name, "key": k, "new": cond + right}))

            opt = lookup(k)
            if opt is None and norm(bare) not in MISSPELLED:
                issues.append(_issue("info", s.name, f"'{bare}' is not in ELOG's option reference; "
                                                     "check the spelling (it may also be undocumented)", key=k))
            elif opt is not None:
                problem = check_value(opt, v)
                if problem:
                    issues.append(_issue("error", s.name, f"'{k}' {problem}", key=k))
                if not in_scope(opt, is_global):
                    issues.append(_issue("warning", s.name, f"'{k}' only has an effect in [global]" if not is_global
                                         else f"'{k}' belongs in a logbook section; in [global] it applies to "
                                              "every logbook", key=k))
                if opt.get("deprecated"):
                    new, name = opt["deprecated"], " ".join(bare.split())
                    if "<" in new:  # "Text template Cooldown" -> "Template text Cooldown"
                        new = new.split("<")[0] + name[len(opt["name"].split("<")[0]):]
                    issues.append(_issue("info", s.name, f"'{name}' is the old name of '{new}'; "
                                                         "the Templates tab converts it", key=k))

            if nk.startswith("style ") and v:
                for decl in [d.strip() for d in v.split(";") if d.strip()]:
                    if ":" not in decl:
                        issues.append(_issue("warning", s.name, f"Malformed CSS '{decl}' in '{k}'", key=k))

            if nk in ("login user", "admin user") and user_lower is not None:
                for u in split_list(v):
                    if u in users:
                        continue
                    if u.lower() in user_lower:
                        issues.append(_issue("warning", s.name, f"'{u}' in '{k}' differs in case from the "
                                                                f"account '{user_lower[u.lower()]}'", key=k))
                    else:
                        issues.append(_issue("error", s.name, f"User '{u}' in '{k}' does not exist in the "
                                                              "password file", key=k))

            if is_global:
                continue

            if norm(bare) in [norm(x) for x in ATTR_LIST_KEYS]:
                for a in split_list(v):
                    if a.lower() not in known_attrs:
                        issues.append(_issue("warning", s.name, f"'{k}' references unknown attribute '{a}'", key=k))

            tgt = attribute_key_target(bare)
            if tgt and attrs:
                prefix, target = tgt
                tl = target.lower()
                if prefix == "Style":
                    ok = any(tl == a or tl.startswith(a + " ") for a in known_attrs)
                else:
                    ok = tl in known_attrs
                if not ok:
                    issues.append(_issue("warning", s.name, f"'{k}' refers to '{target}', which is not an attribute", key=k))
                if prefix.endswith("Options"):
                    option_tags.update(re.findall(r"\{([^}]*)\}", v))

        issues += named_list_issues(cfg, s, is_global)
        issues += template_issues(cfg, s, is_global)
        unused = sorted(t for t in option_tags if t and t not in cond_used)
        if unused:
            issues.append(_issue("info", s.name, "Option tags {" + ",".join(unused) + "} are set but no "
                                                 "conditional '{n} key' uses them; they can be removed"))
        if not is_global and not s.items():
            issues.append(_issue("warning", s.name, "Logbook has no settings"))
        if not is_global:
            problem = author_problem(cfg, s)
            if problem:
                issues.append(_issue("error", s.name, problem,
                                     fix={"op": "ensure_author", "section": s.name}))
    return issues


# "Text templates = A, B" names entries defined as "Text template A = ..." (same for custom pages)
NAMED_LISTS = {"Text templates": "Text template", "Custom pages": "Custom page"}


def named_list_issues(cfg: ElogConfig, sec, is_global: bool) -> list[dict]:
    """Logbook checks only: [global] lists and entries may be completed or used by the logbooks."""
    issues = []
    if is_global:
        return issues
    for list_key, prefix in NAMED_LISTS.items():
        for n in sec.get_list(list_key):
            key = f"{prefix} {n}"
            if sec.get(key) is None and cfg.global_.get(key) is None:
                issues.append(_issue("warning", sec.name, f"'{list_key}' lists '{n}', but '{key}' is not set",
                                     key=list_key))
        own = sec.get(list_key) is not None  # elogd falls back to [global] for the list
        listed = {norm(n) for n in (sec if own else cfg.global_).get_list(list_key)}
        p = norm(prefix) + " "
        for k, _ in sec.items():
            cond, bare = strip_condition(k)
            nk = norm(bare)
            if cond or not nk.startswith(p) or nk.startswith("custom page title "):
                continue
            name = " ".join(bare.split())[len(prefix) + 1:]
            if norm(name) not in listed:
                issues.append(_issue("info", sec.name, f"'{k}' is not offered because '{name}' is not in "
                                                       f"'{list_key}'", key=k))
    return issues


# Templates = A, B with optional "Template text A" and "Template attributes A" (both may be absent: the
# template then only opens a new entry with Subject = its name)
TEMPLATE_PREFIXES = ("Template text", "Template attributes")
PRESET_KEYS = ("template attributes", "quick entry attributes", "template attributes ", "quick entry ",
               "text template attributes ")


def template_issues(cfg: ElogConfig, sec, is_global: bool) -> list[dict]:
    """Logbook checks only, like named_list_issues."""
    issues = []
    if is_global:
        return issues
    own = sec.get("Templates") is not None
    listed = {norm(n) for n in (sec if own else cfg.global_).get_list("Templates")}
    attrs = {a.lower() for a in split_list(_effective(cfg, sec, "Attributes") or "")}
    for k, v in sec.items():
        cond, bare = strip_condition(k)
        nk = norm(bare)
        if cond:
            continue
        for prefix in TEMPLATE_PREFIXES:
            p = norm(prefix) + " "
            if nk.startswith(p):
                name = " ".join(bare.split())[len(prefix) + 1:]
                if norm(name) not in listed:
                    issues.append(_issue("info", sec.name, f"'{k}' is not offered because '{name}' is not in "
                                                           "'Templates'", key=k))
        # presets name fields of this logbook
        if (nk in ("template attributes", "quick entry attributes") or nk.startswith(PRESET_KEYS[2:]))                 and not nk.startswith("quick entry title") and attrs:
            for pair in split_list(v or ""):
                field = pair.split("=", 1)[0].strip()
                if "=" in pair and field.lower() not in attrs:
                    issues.append(_issue("warning", sec.name, f"'{k}' presets '{field}', which is not an "
                                                              "attribute of this logbook", key=k))
    return issues


# Policy: every logbook has an Author attribute that is filled in automatically and cannot be changed.
AUTHOR_PRESET = "$long_name"


def _effective(cfg: ElogConfig, sec, key: str) -> str | None:
    v = sec.get(key)
    return v if v is not None else cfg.global_.get(key)


def author_problem(cfg: ElogConfig, sec) -> str | None:
    attrs = split_list(_effective(cfg, sec, "Attributes") or "")
    if attrs and "author" not in (a.lower() for a in attrs):
        return "Every logbook needs an Author attribute"
    if "author" not in (a.lower() for a in split_list(_effective(cfg, sec, "Locked Attributes") or "")):
        return "Author must be locked (Locked Attributes) so entries cannot be signed with another name"
    if not (_effective(cfg, sec, "Preset Author") or "").strip():
        return "Author must be filled in automatically (Preset Author, e.g. $long_name)"
    return None


def ensure_author(cfg: ElogConfig, sec) -> None:
    """Make `sec` satisfy the Author policy, touching as little as possible."""
    attrs = split_list(_effective(cfg, sec, "Attributes") or "")
    if attrs and "author" not in (a.lower() for a in attrs):
        sec.set("Attributes", join_list(["Author"] + attrs))
    locked = split_list(_effective(cfg, sec, "Locked Attributes") or "")
    if "author" not in (a.lower() for a in locked):
        sec.set("Locked Attributes", join_list(["Author"] + locked))
    if not (_effective(cfg, sec, "Preset Author") or "").strip():
        sec.set("Preset Author", AUTHOR_PRESET)
