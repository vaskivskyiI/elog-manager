import os
import shutil
import sys

import pytest

from elogmgr.app import create_app
from elogmgr.elogcfg import ElogConfig
from elogmgr.elogpwd import ElogUser, sha256_crypt
from elogmgr.lint import lint
from elogmgr.reload import reload_elogd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "examples", "elogd.sample.cfg")
H = {"X-Requested-With": "elogmgr"}


def ref_text():
    with open(REF, "rb") as f:
        return f.read().decode("utf-8")


def test_roundtrip_is_byte_identical():
    t = ref_text()
    assert ElogConfig(t).text() == t


def test_sha256_crypt_matches_openssl():
    # openssl passwd -5 -salt saltstring 'Hello world!'
    assert sha256_crypt("Hello world!", "saltstring") == "5B8vYYiY.CVt1RlTTf8KbXBH3hsxY/GNooZaBBGWEc5"
    u = ElogUser("a", password=sha256_crypt("pw"), encoding="SHA256")
    assert u.check("pw") and not u.check("nope")


def test_groups_and_edits_keep_comments():
    c = ElogConfig(ref_text())
    g = {x["name"]: x for x in c.groups()}
    assert g["Transport"]["members"][:2] == ["tr001", "tr002"]
    c.replace_group_member("stm005", "stm005b")
    c.rename_section("stm005", "stm005b")
    assert "Group STM Experiments = stm001, stm002, stm003, stm004, stm005b" in c.text()
    assert "#### Sandbox" in c.text()
    c.remove_section("Playground")
    assert "[Playground]" not in c.text() and "#### Sandbox" in c.text()


def test_set_preserves_key_spelling_and_position():
    c = ElogConfig(ref_text())
    s = c.section("stm001")
    s.set("ENTRIES PER PAGE", "75")
    assert "entries per page = 75" in s.text()
    s.set("Restrict edit", "1", after="Login user")
    lines = s.text().splitlines()
    assert lines[lines.index("Restrict edit = 1") - 1].startswith("Login user")


def test_lint_finds_known_problems():
    msgs = [i["message"] for i in lint(ElogConfig(ref_text()), {"alice": 1, "bob": 1})]
    assert any("tr003" in m for m in msgs)
    assert any("'extendable attributes' is not an ELOG option" in m for m in msgs)
    assert any("'alcie'" in m for m in msgs)
    assert any("'Bob'" in m and "case" in m for m in msgs)
    assert not any("refers to" in m for m in msgs), msgs  # no false attribute warnings


@pytest.fixture
def client(tmp_path):
    shutil.copy(REF, tmp_path / "elogd.cfg")
    pw = sha256_crypt("demo")
    users = "".join(f"<user><name>{n}</name><password encoding=\"SHA256\">{pw}</password></user>"
                    for n in ["alice", "bob", "carol"])
    (tmp_path / "passwords.pwd").write_text(f"<list>{users}</list>", encoding="utf-8")
    marker = tmp_path / "reloaded"
    reload_cmd = f'"{sys.executable}" -c "open(r\'{marker}\', \'a\').write(\'x\')"'
    app = create_app({"elogd_cfg": str(tmp_path / "elogd.cfg"), "password_file": str(tmp_path / "passwords.pwd"),
                      "backup_dir": str(tmp_path / "bk"), "secret_key": "x", "secure_cookies": False,
                      "reload_mode": "command", "reload_command": reload_cmd})
    c = app.test_client()
    c.tmp = tmp_path
    return c


def login(c, user):
    return c.post("/api/login", json={"user": user, "password": "demo"})


def test_login_roles(client):
    assert client.post("/api/login", json={"user": "alice", "password": "bad"}).status_code == 401
    assert login(client, "carol").status_code == 403  # not an admin anywhere
    assert login(client, "bob").status_code == 200
    assert client.get("/api/me").json["role"] == "logbook_admin"
    assert login(client, "alice").status_code == 200
    assert client.get("/api/me").json["role"] == "super"


def test_create_logbook_in_group_with_template(client):
    login(client, "alice")
    st = client.get("/api/state").json
    op = {"op": "create_logbook", "name": "stm006", "template": "stm005", "group": "STM Experiments",
          "comment": "New sample", "set": {"Login user": "bob, carol"}}
    dry = client.post("/api/apply", json={"etag": st["etag"], "ops": [op], "dry_run": True}, headers=H).json
    assert "+[stm006]" in dry["diff"]
    assert not (client.tmp / "reloaded").exists()  # dry run does not reload
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": [op]}, headers=H)
    assert r.status_code == 200, r.json
    assert r.json["reload"]["ok"] and (client.tmp / "reloaded").read_text() == "x"
    c = ElogConfig((client.tmp / "elogd.cfg").read_text(encoding="utf-8"))
    s = c.section("stm006")
    assert s.get("Comment") == "New sample" and s.get("Login user") == "bob, carol"
    assert s.get("Attributes") == "Author, Category, File, Sample, Tip, RunN, Subject"
    assert "stm006" in c.groups_of("stm006") or c.groups_of("stm006") == ["STM Experiments"]
    # placed right after stm005 so the file stays organised
    names = c.logbook_names()
    assert names.index("stm006") == names.index("stm005") + 1
    assert os.listdir(client.tmp / "bk")
    # stale etag is rejected
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": [op]}, headers=H)
    assert r.status_code == 409


def test_logbook_admin_limits(client):
    login(client, "bob")
    st = client.get("/api/state").json
    ok = client.post("/api/apply", json={"etag": st["etag"], "ops": [
        {"op": "list_add", "section": "stm005", "key": "Login user", "items": ["carol"]}]}, headers=H)
    assert ok.status_code == 200
    st = client.get("/api/state").json
    for op in ({"op": "set", "section": "Laser A", "key": "Comment", "value": "x"},
               {"op": "delete_logbook", "name": "stm005"},
               {"op": "set", "section": "global", "key": "Admin user", "value": "bob"}):
        r = client.post("/api/apply", json={"etag": st["etag"], "ops": [op]}, headers=H)
        assert r.status_code == 400, op
    assert client.post("/api/apply", json={"etag": st["etag"], "ops": []}).status_code == 403  # no CSRF header


def test_reload_button_and_failures(client):
    login(client, "bob")
    assert client.post("/api/reload", headers=H).json["ok"]
    assert reload_elogd({"reload_mode": "command", "reload_command": f'"{sys.executable}" -c "raise SystemExit(3)"'})["ok"] is False
    assert reload_elogd({"reload_mode": "signal", "elogd_process": "no-such-elogd-xyz"})["ok"] is False


def test_rename_keeps_data_dir(client):
    login(client, "alice")
    st = client.get("/api/state").json
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": [
        {"op": "rename_logbook", "name": "r001", "new": "Raman 001"}]}, headers=H)
    assert r.status_code == 200
    c = ElogConfig((client.tmp / "elogd.cfg").read_text(encoding="utf-8"))
    assert c.section("Raman 001").get("Subdir") == "r001"
    assert c.groups_of("Raman 001") == ["Raman"]
