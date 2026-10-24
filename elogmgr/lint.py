"""Consistency checks for elogd.cfg."""
from __future__ import annotations

import re

from .elogcfg import (ATTR_LIST_KEYS, BUILTIN_ATTRS, ElogConfig, attribute_key_target, norm,
                      split_list, strip_condition)

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

        unused = sorted(t for t in option_tags if t and t not in cond_used)
        if unused:
            issues.append(_issue("info", s.name, "Option tags {" + ",".join(unused) + "} are set but no "
                                                 "conditional '{n} key' uses them; they can be removed"))
        if not is_global and not s.items():
            issues.append(_issue("warning", s.name, "Logbook has no settings"))
    return issues
