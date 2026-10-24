"""Read ELOG's XML password file and verify passwords the way elogd does.

elogd (3.x) stores `sha256_crypt(password, "$5$") + 4`, i.e. a SHA-256
crypt hash with an empty salt and 5000 rounds, minus the "$5$$" prefix.
Very old files store base64 or plain text.  The file is only read here;
account management stays in ELOG itself.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ORDER = [(0, 10, 20), (21, 1, 11), (12, 22, 2), (3, 13, 23), (24, 4, 14),
          (15, 25, 5), (6, 16, 26), (27, 7, 17), (18, 28, 8), (9, 19, 29)]


def _repeat(digest: bytes, length: int) -> bytes:
    return (digest * (length // 32 + 1))[:length]


def sha256_crypt(password: str, salt: str = "", rounds: int = 5000) -> str:
    """SHA-256 crypt (Drepper), returns only the 43-char hash part."""
    p = password.encode("utf-8")
    s = salt.encode("utf-8")[:16]
    b = hashlib.sha256(p + s + p).digest()
    a = hashlib.sha256(p + s)
    a.update(_repeat(b, len(p)))
    n = len(p)
    while n:
        a.update(b if n & 1 else p)
        n >>= 1
    a = a.digest()
    p_seq = _repeat(hashlib.sha256(p * len(p)).digest(), len(p))
    s_seq = _repeat(hashlib.sha256(s * (16 + a[0])).digest(), len(s))
    c = a
    for i in range(rounds):
        h = hashlib.sha256(p_seq if i & 1 else c)
        if i % 3:
            h.update(s_seq)
        if i % 7:
            h.update(p_seq)
        h.update(c if i & 1 else p_seq)
        c = h.digest()
    out = []
    for i0, i1, i2 in _ORDER:
        w = (c[i0] << 16) | (c[i1] << 8) | c[i2]
        for _ in range(4):
            out.append(_ITOA64[w & 0x3F])
            w >>= 6
    w = (c[31] << 8) | c[30]
    for _ in range(3):
        out.append(_ITOA64[w & 0x3F])
        w >>= 6
    return "".join(out)


@dataclass
class ElogUser:
    name: str
    full_name: str = ""
    email: str = ""
    inactive: bool = False
    last_activity: str = ""
    password: str = ""
    encoding: str = ""

    def public(self) -> dict:
        return {"name": self.name, "full_name": self.full_name, "email": self.email,
                "inactive": self.inactive, "last_activity": self.last_activity}

    def check(self, password: str) -> bool:
        if self.inactive or not self.password:
            return False
        enc = self.encoding.upper()
        stored = self.password
        if enc == "SHA256" or (not enc and len(stored) == 43):
            if stored.startswith("$5$"):
                parts = stored.split("$")
                cand = sha256_crypt(password, parts[2])
                return hmac.compare_digest(cand, parts[3])
            return hmac.compare_digest(sha256_crypt(password), stored)
        if enc in ("", "BASE64"):
            try:
                if hmac.compare_digest(base64.b64decode(stored).decode("utf-8", "replace"), password):
                    return True
            except Exception:
                pass
        return enc == "NONE" and hmac.compare_digest(stored, password)


# ---------------------------------------------------------------------
# Reading and writing the file the way elogd (mxml) does
#
# mxml declares ISO-8859-1 but stores the bytes it was given (UTF-8 from the
# browser), and it only decodes &lt; &gt; &amp; &quot; &apos; - a numeric
# reference like &#269; would show up literally.  So the file is handled as
# raw bytes: every byte becomes one character (latin-1), text for display is
# decoded as UTF-8, and new text is stored as UTF-8 bytes.
# ---------------------------------------------------------------------

def from_file(s: str) -> str:
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def to_file(s: str) -> str:
    return s.encode("utf-8").decode("latin-1")


def parse(raw: bytes) -> ET.Element:
    text = raw.decode("latin-1")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)  # parse the bytes as they are, whatever is declared
    return ET.fromstring(text)


def _esc(s: str, attr: bool = False) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;") if attr else s


def serialize(root: ET.Element) -> bytes:
    """Write like mxml_write_tree: ISO-8859-1 header, two-space indent, <empty/> elements."""
    out = ['<?xml version="1.0" encoding="ISO-8859-1"?>\n']

    def write(el: ET.Element, depth: int) -> None:
        ind = "  " * depth
        attrs = "".join(f' {k}="{_esc(v, True)}"' for k, v in el.attrib.items())
        children = list(el)
        value = (el.text or "").strip() if not children else ""
        if children:
            out.append(f"{ind}<{el.tag}{attrs}>\n")
            for c in children:
                write(c, depth + 1)
            out.append(f"{ind}</{el.tag}>\n")
        elif value:
            out.append(f"{ind}<{el.tag}{attrs}>{_esc(value)}</{el.tag}>\n")
        else:
            out.append(f"{ind}<{el.tag}{attrs}/>\n")

    write(root, 0)
    return "".join(out).encode("latin-1")


def _user_from(u: ET.Element) -> ElogUser:
    def t(tag: str) -> str:
        el = u.find(tag)
        return from_file((el.text or "").strip()) if el is not None else ""
    pw = u.find("password")
    return ElogUser(
        name=t("name"), full_name=t("full_name"), email=t("email"),
        inactive=t("inactive") == "1", last_activity=t("last_activity"),
        password=(pw.text or "").strip() if pw is not None else "",
        encoding=pw.get("encoding", "") if pw is not None else "",
    )


def load_users(path: str) -> dict[str, ElogUser]:
    with open(path, "rb") as f:
        root = parse(f.read())
    users: dict[str, ElogUser] = {}
    for u in root.iter("user"):
        user = _user_from(u)
        if user.name:
            users[user.name] = user
    return users


# ---------------------------------------------------------------------
# Account changes.  Each can be applied to a tree and checked on a freshly
# read tree, so a change that elogd overwrote can be detected and redone.
# ---------------------------------------------------------------------

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
MIN_PASSWORD = 8


class AccountError(ValueError):
    pass


def find_user(root: ET.Element, name: str) -> ET.Element | None:
    for u in root.iter("user"):
        el = u.find("name")
        if el is not None and from_file((el.text or "").strip()) == name:
            return u
    return None


def _set_child(u: ET.Element, tag: str, value: str, after: str | None = None) -> None:
    el = u.find(tag)
    if el is None:
        el = ET.Element(tag)
        kids = list(u)
        pos = next((i + 1 for i, k in enumerate(kids) if k.tag == after), len(kids)) if after else len(kids)
        u.insert(pos, el)
    el.text = value


def check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Passwords need at least {MIN_PASSWORD} characters")


def validate_profile(full_name: str | None, email: str | None) -> None:
    if full_name is not None and ("<" in full_name or ">" in full_name or len(full_name) > 100):
        raise AccountError("Full name must not contain < or > and be at most 100 characters")
    if email and not EMAIL_RE.match(email):
        raise AccountError(f"'{email}' is not a valid e-mail address")


class Change:
    """An idempotent change to the password file."""
    description = ""

    def apply(self, root: ET.Element) -> None:
        raise NotImplementedError

    def done(self, root: ET.Element) -> bool:
        raise NotImplementedError


class CreateUser(Change):
    def __init__(self, name: str, password: str, full_name: str = "", email: str = "", inactive: bool = False):
        if not USERNAME_RE.match(name):
            raise AccountError("User names may contain letters, digits and . _ @ - (max. 64 characters)")
        check_password_strength(password)
        validate_profile(full_name, email)
        self.name, self.full_name, self.email, self.inactive = name, full_name, email, inactive
        self.hash = sha256_crypt(password)
        self.description = f"create account {name}"

    def apply(self, root):
        if find_user(root, self.name) is not None:
            raise AccountError(f"An account '{self.name}' already exists")
        u = ET.SubElement(root, "user")  # same fields and order as elogd's own "new user"
        ET.SubElement(u, "name").text = to_file(self.name)
        pw = ET.SubElement(u, "password", {"encoding": "SHA256"})
        pw.text = self.hash
        ET.SubElement(u, "full_name").text = to_file(self.full_name)
        ET.SubElement(u, "last_logout").text = "0"
        ET.SubElement(u, "last_activity").text = "0"
        ET.SubElement(u, "email").text = to_file(self.email)
        ET.SubElement(u, "inactive").text = "1" if self.inactive else "0"
        ET.SubElement(u, "email_notify")

    def done(self, root):
        return find_user(root, self.name) is not None


class UpdateUser(Change):
    def __init__(self, name: str, full_name: str | None = None, email: str | None = None,
                 inactive: bool | None = None):
        validate_profile(full_name, email)
        self.name, self.full_name, self.email, self.inactive = name, full_name, email, inactive
        self.description = f"update account {name}"

    def _user(self, root):
        u = find_user(root, self.name)
        if u is None:
            raise AccountError(f"No account '{self.name}'")
        return u

    def apply(self, root):
        u = self._user(root)
        if self.full_name is not None:
            _set_child(u, "full_name", to_file(self.full_name), after="password")
        if self.email is not None:
            _set_child(u, "email", to_file(self.email), after="last_activity")
        if self.inactive is not None:
            _set_child(u, "inactive", "1" if self.inactive else "0", after="email")

    def done(self, root):
        cur = _user_from(self._user(root))
        return ((self.full_name is None or cur.full_name == self.full_name)
                and (self.email is None or cur.email == self.email)
                and (self.inactive is None or cur.inactive == self.inactive))


class SetPassword(Change):
    def __init__(self, name: str, password: str):
        check_password_strength(password)
        self.name, self.hash = name, sha256_crypt(password)
        self.description = f"set password of {name}"

    def apply(self, root):
        u = find_user(root, self.name)
        if u is None:
            raise AccountError(f"No account '{self.name}'")
        pw = u.find("password")
        if pw is None:
            pw = ET.Element("password")
            u.insert(1, pw)
        pw.attrib.clear()
        pw.set("encoding", "SHA256")
        pw.text = self.hash

    def done(self, root):
        u = find_user(root, self.name)
        pw = u.find("password") if u is not None else None
        return pw is not None and (pw.text or "").strip() == self.hash


class DeleteUser(Change):
    def __init__(self, name: str):
        self.name = name
        self.description = f"delete account {name}"

    def apply(self, root):
        u = find_user(root, self.name)
        if u is None:
            raise AccountError(f"No account '{self.name}'")
        root.remove(u)

    def done(self, root):
        return find_user(root, self.name) is None
