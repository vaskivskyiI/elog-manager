"""Round-trip parser/editor for ELOG's elogd.cfg.

The file is kept as a list of lines so that comments, blank lines, key
spelling and ordering survive every edit.  Keys are matched
case-insensitively, as elogd does.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")
KV_RE = re.compile(r"^(?P<key>[^#;=][^=]*?)\s*=\s?(?P<value>.*?)\s*$")
COND_RE = re.compile(r"^\s*\{[^}]*\}\s*")

GROUP_PREFIX = "group "
TOP_GROUP_PREFIX = "top group "


def norm(key: str) -> str:
    return " ".join(key.split()).lower()


def split_list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def join_list(items) -> str:
    return ", ".join(items)


@dataclass
class Line:
    raw: str

    @property
    def kind(self) -> str:
        s = self.raw.strip()
        if not s:
            return "blank"
        if s[0] in "#;":
            return "comment"
        if SECTION_RE.match(self.raw):
            return "section"
        if KV_RE.match(self.raw):
            return "kv"
        return "other"

    @property
    def key(self) -> str | None:
        m = KV_RE.match(self.raw) if self.kind == "kv" else None
        return m.group("key").strip() if m else None

    @property
    def value(self) -> str | None:
        m = KV_RE.match(self.raw) if self.kind == "kv" else None
        return m.group("value") if m else None


@dataclass
class Section:
    name: str
    header: Line | None  # None for the implicit preamble
    lines: list[Line] = field(default_factory=list)

    # ---- key access -------------------------------------------------
    def items(self) -> list[tuple[str, str]]:
        return [(l.key, l.value) for l in self.lines if l.kind == "kv"]

    def _find(self, key: str) -> list[int]:
        k = norm(key)
        return [i for i, l in enumerate(self.lines) if l.kind == "kv" and norm(l.key) == k]

    def get(self, key: str, default: str | None = None) -> str | None:
        idx = self._find(key)
        return self.lines[idx[0]].value if idx else default

    def get_list(self, key: str) -> list[str]:
        v = self.get(key)
        return split_list(v) if v else []

    def _last_kv_index(self) -> int:
        for i in range(len(self.lines) - 1, -1, -1):
            if self.lines[i].kind == "kv":
                return i
        return -1

    def set(self, key: str, value: str, after: str | None = None) -> None:
        idx = self._find(key)
        if idx:
            old = self.lines[idx[0]]
            self.lines[idx[0]] = Line(f"{old.key} = {value}")
            for j in reversed(idx[1:]):  # drop duplicates, elogd only uses one
                del self.lines[j]
            return
        pos = self._last_kv_index() + 1
        if after:
            a = self._find(after)
            if a:
                pos = a[-1] + 1
        self.lines.insert(pos, Line(f"{key} = {value}"))

    def delete(self, key: str) -> bool:
        idx = self._find(key)
        for j in reversed(idx):
            del self.lines[j]
        return bool(idx)

    def rename_key(self, old: str, new: str) -> None:
        for i in self._find(old):
            self.lines[i] = Line(f"{new} = {self.lines[i].value}")

    def trailing_comment_start(self) -> int:
        """Index where the trailing comment/blank block starts.

        Comments after the last key usually introduce the *next* section
        (e.g. "#### Materials Group"), so they are kept when a section is
        removed.
        """
        last = self._last_kv_index()
        return last + 1

    def text(self) -> str:
        out = [self.header.raw] if self.header else []
        out += [l.raw for l in self.lines]
        return "\n".join(out)


class ElogConfig:
    def __init__(self, text: str):
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.trailing_newline = text.endswith(("\n", "\r\n"))
        self.preamble = Section("", None)
        self.sections: list[Section] = []
        cur = self.preamble
        for raw in text.splitlines():
            m = SECTION_RE.match(raw)
            if m and not raw.lstrip().startswith(("#", ";")):
                cur = Section(m.group("name").strip(), Line(raw))
                self.sections.append(cur)
            else:
                cur.lines.append(Line(raw))

    # ---- serialisation ------------------------------------------------
    def text(self) -> str:
        parts = [l.raw for l in self.preamble.lines]
        for s in self.sections:
            parts.append(s.header.raw)
            parts += [l.raw for l in s.lines]
        out = self.newline.join(parts)
        return out + self.newline if self.trailing_newline else out

    # ---- sections -----------------------------------------------------
    def section(self, name: str) -> Section | None:
        n = name.strip().lower()
        for s in self.sections:
            if s.name.lower() == n:
                return s
        return None

    @property
    def global_(self) -> Section:
        s = self.section("global")
        if s is None:
            s = Section("global", Line("[global]"))
            self.sections.insert(0, s)
        return s

    def logbook_sections(self) -> list[Section]:
        return [s for s in self.sections if not s.name.lower().startswith("global")]

    def logbook_names(self) -> list[str]:
        return [s.name for s in self.logbook_sections()]

    def add_section(self, name: str, items: list[tuple[str, str]], after: str | None = None,
                    comment: str | None = None) -> Section:
        if self.section(name):
            raise ValueError(f"Section [{name}] already exists")
        lines = [Line(f"{k} = {v}") for k, v in items] + [Line("")]
        sec = Section(name, Line(f"[{name}]"), lines)
        if after and self.section(after):
            ref = self.section(after)
            pos = self.sections.index(ref) + 1
            # make sure there is a separating blank line
            if ref.lines and ref.lines[-1].kind != "blank":
                ref.lines.append(Line(""))
        else:
            pos = len(self.sections)
            if self.sections and self.sections[-1].lines and self.sections[-1].lines[-1].kind != "blank":
                self.sections[-1].lines.append(Line(""))
        self.sections.insert(pos, sec)
        if comment:
            sec.lines.insert(0, Line(f"# {comment}"))
        return sec

    def remove_section(self, name: str) -> None:
        sec = self.section(name)
        if sec is None:
            raise KeyError(name)
        i = self.sections.index(sec)
        keep = sec.lines[sec.trailing_comment_start():]
        # trailing comments belong to the next section; hand them to the previous one
        prev = self.sections[i - 1] if i > 0 else self.preamble
        if any(l.kind == "comment" for l in keep):
            prev.lines.extend(keep)
        del self.sections[i]

    def rename_section(self, old: str, new: str) -> None:
        sec = self.section(old)
        if sec is None:
            raise KeyError(old)
        if self.section(new) and new.lower() != old.lower():
            raise ValueError(f"Section [{new}] already exists")
        indent = sec.header.raw[: len(sec.header.raw) - len(sec.header.raw.lstrip())]
        sec.header = Line(f"{indent}[{new}]")
        sec.name = new

    # ---- groups -------------------------------------------------------
    def groups(self) -> list[dict]:
        out = []
        for k, v in self.global_.items():
            nk = norm(k)
            if nk.startswith(TOP_GROUP_PREFIX):
                out.append({"name": k.split(None, 2)[2].strip(), "members": split_list(v), "top": True})
            elif nk.startswith(GROUP_PREFIX):
                out.append({"name": k.split(None, 1)[1].strip(), "members": split_list(v), "top": False})
        return out

    def set_groups(self, groups: list[dict]) -> None:
        """Replace all Group/Top group definitions, keeping their position."""
        g = self.global_
        positions = [i for i, l in enumerate(g.lines)
                     if l.kind == "kv" and norm(l.key).startswith((GROUP_PREFIX, TOP_GROUP_PREFIX))]
        insert_at = positions[0] if positions else None
        for i in reversed(positions):
            del g.lines[i]
        new = [Line(f"{'Top group' if grp.get('top') else 'Group'} {grp['name']} = {join_list(grp['members'])}")
               for grp in groups]
        if insert_at is None:
            insert_at = g._last_kv_index() + 1
            if new:
                new = [Line("")] + new
        g.lines[insert_at:insert_at] = new

    def groups_of(self, name: str) -> list[str]:
        n = name.lower()
        return [grp["name"] for grp in self.groups() if n in (m.lower() for m in grp["members"])]

    def replace_group_member(self, old: str, new: str | None) -> None:
        groups = self.groups()
        for grp in groups:
            members = []
            for m in grp["members"]:
                if m.lower() == old.lower():
                    if new:
                        members.append(new)
                else:
                    members.append(m)
            grp["members"] = members
        self.set_groups(groups)


# ---------------------------------------------------------------------
# Attribute-aware helpers
# ---------------------------------------------------------------------

# Keys whose value is a list of attribute names
ATTR_LIST_KEYS = [
    "Required Attributes", "Locked Attributes", "Extendable options", "Fixed Attributes Reply",
    "Fixed Attributes Edit", "Propagate Attributes", "Quick filter", "List display",
    "Show Attributes", "Show Attributes Edit", "Show attributes reply", "Hidden attributes",
    "Sort Attributes", "Search all logbooks attributes",
]
# Keys of the form "<prefix> <attribute>"
ATTR_PREFIXES = [
    "Preset on first reply", "Preset on reply", "Preset on edit", "Preset on duplicate", "Preset",
    "Subst on reply", "Subst on edit", "Subst on duplicate", "Subst",
    "Options", "MOptions", "ROptions", "IOptions", "Comment", "Tooltip", "Type", "Format",
    "Style", "Icon comment", "Change note", "Empty option",
]
BUILTIN_ATTRS = {"id", "date", "subtext", "text", "edit", "delete", "attachments", "entry time",
                 "author", "message id", "logbook", "date of", "entry date", "#"}


def strip_condition(key: str) -> tuple[str, str]:
    m = COND_RE.match(key)
    if m:
        return key[: m.end()], key[m.end():]
    return "", key


def attribute_key_target(key: str) -> tuple[str, str] | None:
    """For a key like 'Options Category' return ('Options', 'Category')."""
    _, k = strip_condition(key)
    nk = norm(k)
    for p in ATTR_PREFIXES:
        np_ = p.lower() + " "
        if nk.startswith(np_):
            return p, " ".join(k.split())[len(p) + 1:]
    return None
