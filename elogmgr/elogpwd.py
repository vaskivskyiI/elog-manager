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


def load_users(path: str) -> dict[str, ElogUser]:
    tree = ET.parse(path)
    users: dict[str, ElogUser] = {}
    for u in tree.getroot().iter("user"):
        def t(tag: str) -> str:
            el = u.find(tag)
            return (el.text or "").strip() if el is not None else ""
        pw = u.find("password")
        user = ElogUser(
            name=t("name"), full_name=t("full_name"), email=t("email"),
            inactive=t("inactive") == "1", last_activity=t("last_activity"),
            password=(pw.text or "").strip() if pw is not None else "",
            encoding=pw.get("encoding", "") if pw is not None else "",
        )
        if user.name:
            users[user.name] = user
    return users
