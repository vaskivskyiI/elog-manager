"""Build elogmgr/options.json from ELOG's option reference.

    curl -o config.html https://elog.psi.ch/elog/config.html
    python tools/build_options.py config.html

Only facts are extracted (option name, value syntax, default, section and
a link to the documentation); the descriptions shown in the manager are
our own short summaries in elogmgr/options_extra.py.
"""
import html
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = "https://elog.psi.ch/elog/config.html"

src = open(sys.argv[1], encoding="utf-8").read()
tokens = re.finditer(r'<h([23]) id="([^"]+)"[^>]*>(.*?)</h\1>', src, re.S)

options = []
section = ""
heads = list(tokens)
for i, m in enumerate(heads):
    level, anchor, title = m.group(1), m.group(2), html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip()
    if level == "2":
        section = title
        continue
    if "=" not in title:
        continue
    body_end = heads[i + 1].start() if i + 1 < len(heads) else len(src)
    body = html.unescape(re.sub(r"<[^>]+>", " ", src[m.end():body_end]))
    body = " ".join(body.split())
    name, spec = (s.strip() for s in title.split("=", 1))
    default = None
    d = re.search(r"[Tt]he default (?:value )?is:? ?\"?([^\".,;)]+)", body) or re.search(r"\(default:? ([^)]+)\)", body)
    if d:
        default = d.group(1).strip().strip("'\"")
    options.append({"name": name, "spec": spec, "section": section, "doc": f"{DOC}#{anchor}", "default": default})

# Options that are only named inside the prose, e.g. <code>Use Email From = &lt;string&gt;</code>
known = {" ".join(o["name"].lower().split()) for o in options}
patterns = [re.sub(r"<[^>]*>", ".+", re.escape(" ".join(o["name"].split())).replace("\\ ", " "))
            for o in options if "<" in o["name"]]
h2s = [(m.start(), html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip(), m.group(2))
       for m in heads if m.group(1) == "2"]
for m in re.finditer(r"<code>([^<]+=[^<]*)</code>", src):
    text = html.unescape(m.group(1)).strip()
    name, spec = (s.strip() for s in text.split("=", 1))
    if not re.fullmatch(r"[A-Z][A-Za-z0-9 <>{}_-]{1,45}", name) or not spec or spec == "...":
        continue
    if " ".join(name.lower().split()) in known or "_" in name:
        continue
    # skip worked examples of pattern options ("Options town" is an instance of "Options <attribute>")
    if any(re.fullmatch(p, name, re.I) for p in patterns):
        continue
    sec = [h for h in h2s if h[0] < m.start()]
    title, anchor = (sec[-1][1], sec[-1][2]) if sec else ("", "")
    known.add(" ".join(name.lower().split()))
    options.append({"name": name, "spec": spec, "section": title, "doc": f"{DOC}#{anchor}", "default": None})

out = os.path.join(ROOT, "elogmgr", "options.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(options, f, indent=1, ensure_ascii=False)
print(f"{len(options)} options -> {out}")
