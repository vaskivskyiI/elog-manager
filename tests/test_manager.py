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
                    for n in ["alice", "bob", "carol", "grace"])
    (tmp_path / "passwords.pwd").write_text(f"<list>{users}</list>", encoding="utf-8")
    marker = tmp_path / "reloaded"
    reload_cmd = f'"{sys.executable}" -c "open(r\'{marker}\', \'a\').write(\'x\')"'
    app = create_app({"elogd_cfg": str(tmp_path / "elogd.cfg"), "password_file": str(tmp_path / "passwords.pwd"),
                      "state_dir": str(tmp_path / "state"), "secret_key": "x", "secure_cookies": False,
                      "reload_mode": "command", "reload_command": reload_cmd, "account_settle": 0})
    c = app.test_client()
    c.tmp = tmp_path
    return c


def login(c, user):
    return c.post("/api/login", json={"user": user, "password": "demo"})


def test_login_roles(client):
    assert client.post("/api/login", json={"user": "alice", "password": "bad"}).status_code == 401
    assert login(client, "grace").status_code == 200  # every ELOG account may sign in
    assert client.get("/api/me").json["role"] == "user"
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
    assert os.listdir(client.tmp / "state" / "backups")
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


def test_users_only_see_their_logbooks(client):
    login(client, "bob")
    st = client.get("/api/state").json
    assert sorted(l["name"] for l in st["logbooks"]) == ["STM", "STM-test", "stm001", "stm002", "stm003", "stm004", "stm005"]
    g = {x["name"]: x["members"] for x in st["groups"]}
    assert g["Instruments"] == ["STM"] and g["Experiments"] == ["STM Experiments", "Transport", "Raman"]
    assert all(i["section"] != "global" for i in st["issues"])
    assert not any(k.lower() in ("password file", "admin user") for k, _ in st["global"])
    assert "email" not in st["users"][0]
    assert client.get("/api/section/Laser%20A").status_code == 404


def test_new_user_creates_logbook_and_becomes_admin(client):
    login(client, "grace")
    st = client.get("/api/state").json
    assert st["logbooks"] == [] and "builtin:Instrument" in st["templates"]
    # cannot copy someone else's logbook
    bad = {"op": "create_logbook", "name": "g1", "template": "stm005"}
    assert client.post("/api/apply", json={"etag": st["etag"], "ops": [bad]}, headers=H).status_code == 400
    op = {"op": "create_logbook", "name": "Grace setup", "template": "builtin:Instrument", "group": "Instruments",
          "comment": "My setup", "set": {"Login user": "carol", "Admin user": "carol"}}
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": [op]}, headers=H)
    assert r.status_code == 200, r.json
    c = ElogConfig((client.tmp / "elogd.cfg").read_text(encoding="utf-8"))
    s = c.section("Grace setup")
    assert s.get("Admin user") == "grace, carol" and s.get("Login user") == "grace, carol"
    assert s.get("Attributes") == "Author, Category, Subject"
    assert c.groups_of("Grace setup") == ["Instruments"]
    assert client.get("/api/me").json["role"] == "logbook_admin"
    st = client.get("/api/state").json
    assert [l["name"] for l in st["logbooks"]] == ["Grace setup"]
    # move it to another group; other members of the groups stay as they are
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": [
        {"op": "set_logbook_groups", "name": "Grace setup", "groups": ["Playground"]}]}, headers=H)
    assert r.status_code == 200, r.json
    c = ElogConfig((client.tmp / "elogd.cfg").read_text(encoding="utf-8"))
    g = {x["name"]: x["members"] for x in c.groups()}
    assert g["Playground"] == ["Playground", "Grace setup"] and g["Instruments"] == ["Laser A", "STM", "Cryostat"]
    # removing yourself as admin is allowed but warned about
    st = client.get("/api/state").json
    dry = client.post("/api/apply", json={"etag": st["etag"], "dry_run": True, "ops": [
        {"op": "list_remove", "section": "Grace setup", "key": "Admin user", "items": ["grace"]}]}, headers=H).json
    assert dry["warnings"]


def test_option_catalog():
    from elogmgr.options import catalog, check_value, lookup
    assert len(catalog()) > 200
    assert lookup("{1}Subst SN")["name"] == "Subst <attribute>"
    assert lookup("Options Category")["name"] == "Options <attribute>"
    assert lookup("Page title")["scope"] == "any"  # documented for [global] and for logbooks
    assert lookup("Password file")["scope"] == "global"
    assert lookup("extendable attributes") is None
    assert check_value(lookup("Reverse sort"), "yes") and not check_value(lookup("Reverse sort"), "1")
    assert check_value(lookup("Display mode"), "list") and not check_value(lookup("Display mode"), "threaded")
    assert check_value(lookup("Entries per page"), "fifty") and not check_value(lookup("Restrict edit time"), "0.5")


def test_config_check_blocks_new_errors(client):
    bad = [{"op": "set", "section": "stm005", "key": "Entries per page", "value": "fifty"}]
    login(client, "bob")
    st = client.get("/api/state").json
    dry = client.post("/api/apply", json={"etag": st["etag"], "ops": bad, "dry_run": True}, headers=H).json
    assert dry["blocking"] and not dry["can_force"]
    assert client.post("/api/apply", json={"etag": st["etag"], "ops": bad, "force": True}, headers=H).status_code == 400
    login(client, "alice")  # managers may override deliberately
    assert client.post("/api/apply", json={"etag": st["etag"], "ops": bad}, headers=H).status_code == 400
    assert client.post("/api/apply", json={"etag": st["etag"], "ops": bad, "force": True}, headers=H).status_code == 200


def test_author_is_required_locked_and_prefilled(client):
    login(client, "bob")
    st = client.get("/api/state").json
    unlock = [{"op": "set", "section": "STM", "key": "Locked Attributes", "value": "SN"}]
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": unlock}, headers=H)
    assert r.status_code == 400 and "Author must be locked" in r.json["error"]
    # without the [global] defaults, new logbooks get them in their own section
    login(client, "alice")
    st = client.get("/api/state").json
    r = client.post("/api/apply", json={"etag": st["etag"], "force": True, "ops": [
        {"op": "delete_key", "section": "global", "key": "locked attributes"},
        {"op": "delete_key", "section": "global", "key": "preset Author"},
        {"op": "create_logbook", "name": "plain", "template": "builtin:Simple"}]}, headers=H)
    assert r.status_code == 200, r.json
    s = ElogConfig((client.tmp / "elogd.cfg").read_text(encoding="utf-8")).section("plain")
    assert s.get("Locked Attributes") == "Author" and s.get("Preset Author") == "$long_name"
    # the one-click fix repairs an existing logbook
    st = client.get("/api/state").json
    fix = next(i["fix"] for i in st["issues"] if i["section"] == "r001" and i.get("fix", {}).get("op") == "ensure_author")
    assert client.post("/api/apply", json={"etag": st["etag"], "ops": [fix]}, headers=H).status_code == 200
    assert not any(i["section"] == "r001" and "Author" in i["message"] for i in client.get("/api/state").json["issues"])


def test_names_are_unique_and_series_continue(client):
    login(client, "grace")  # sees no logbooks, but suggestions and checks cover all of them
    assert client.get("/api/check_name?group=STM%20Experiments").json["suggestion"] == "stm006"
    assert client.get("/api/check_name?name=stm002").json["available"] is False
    assert client.get("/api/check_name?name=raman").json["available"] is False  # a group name
    assert client.get("/api/check_name?name=stm006").json["available"] is True
    st = client.get("/api/state").json
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": [
        {"op": "create_logbook", "name": "STM002", "template": "builtin:Simple"}]}, headers=H)
    assert r.status_code == 400 and "already exists" in r.json["error"]


def test_startup_explains_unwritable_state_dir(tmp_path, monkeypatch):
    from elogmgr.settings import SetupError
    shutil.copy(REF, tmp_path / "elogd.cfg")
    with pytest.raises(SetupError, match="not found"):
        create_app({"elogd_cfg": str(tmp_path / "missing.cfg"), "state_dir": str(tmp_path / "s")})

    def denied(path, *a, **kw):
        raise PermissionError(13, "Permission denied", path)
    monkeypatch.setattr(os, "makedirs", denied)
    with pytest.raises(SetupError) as e:
        create_app({"elogd_cfg": str(tmp_path / "elogd.cfg")})
    assert "sudo install -d -o elog -g elog" in str(e.value) and "ELOGMGR_STATE_DIR" in str(e.value)


def account(c, **body):
    return c.post("/api/accounts", json=body, headers=H)


def test_manager_creates_account_like_elogd(client):
    from elogmgr.elogpwd import find_user, parse
    login(client, "grace")
    assert account(client, op="create", name="dora", password="longenough").status_code == 400
    login(client, "alice")
    assert account(client, op="create", name="dora", password="short").status_code == 400
    assert account(client, op="create", name="bad name", password="longenough").status_code == 400
    r = account(client, op="create", name="dora", password="longenough", full_name="Dóra Čuk", email="d@example.org")
    assert r.status_code == 200, r.json
    raw = (client.tmp / "passwords.pwd").read_bytes()
    assert raw.startswith(b'<?xml version="1.0" encoding="ISO-8859-1"?>')
    assert "Dóra Čuk".encode() in raw and b"&#" not in raw  # elogd does not decode numeric references
    u = find_user(parse(raw), "dora")
    assert [c.tag for c in u] == ["name", "password", "full_name", "last_logout", "last_activity", "email",
                                  "inactive", "email_notify"]
    assert account(client, op="create", name="dora", password="longenough").status_code == 400  # exists
    assert login(client, "dora").status_code == 401  # password is not "demo"
    assert client.post("/api/login", json={"user": "dora", "password": "longenough"}).status_code == 200
    assert client.get("/api/state").json["me"]["account"]["full_name"] == "Dóra Čuk"


def test_own_account_and_passwords(client):
    login(client, "carol")
    assert account(client, op="update", name="carol", full_name="Carol C", email="c@example.org").status_code == 200
    assert account(client, op="update", name="bob", full_name="x").status_code == 400  # someone else
    assert account(client, op="update", name="carol", inactive=True).status_code == 400
    assert account(client, op="password", name="carol", current="wrong", password="newpassword").status_code == 400
    assert account(client, op="password", name="carol", current="demo", password="newpassword").status_code == 200
    assert login(client, "carol").status_code == 401
    login(client, "alice")  # managers reset other people's passwords without knowing them
    assert account(client, op="password", name="carol", password="demodemo").status_code == 200
    assert client.post("/api/login", json={"user": "carol", "password": "demodemo"}).status_code == 200


def test_deactivate_and_delete(client):
    other = client.application.test_client()
    other.post("/api/login", json={"user": "carol", "password": "demo"})
    assert other.get("/api/state").status_code == 200
    login(client, "alice")
    assert account(client, op="update", name="alice", inactive=True).status_code == 400  # not yourself
    assert account(client, op="update", name="carol", inactive=True).status_code == 200
    assert other.get("/api/state").status_code == 401  # running session ends
    assert login(client, "carol").status_code == 401
    assert account(client, op="delete", name="alice").status_code == 400
    assert account(client, op="delete", name="carol").status_code == 200
    assert "carol" not in (client.tmp / "passwords.pwd").read_text(encoding="latin-1")


def test_account_change_survives_elogd_writing_old_copy(client, monkeypatch):
    """elogd writes its in-memory copy back when a user is active; the change is re-applied."""
    import elogmgr.accounts as accounts
    pwd = client.tmp / "passwords.pwd"
    original = pwd.read_bytes()
    calls = []

    def fake_reload(settings):
        calls.append(1)
        if len(calls) == 1:
            pwd.write_bytes(original)  # elogd flushed its old tree before it saw the signal
        return {"ok": True, "message": "elogd reloaded"}
    monkeypatch.setattr(accounts, "reload_elogd", fake_reload)
    login(client, "alice")
    r = account(client, op="create", name="erin", password="longenough")
    assert r.status_code == 200, r.json
    assert len(calls) == 2 and b"<name>erin</name>" in pwd.read_bytes()


def test_env_settings(tmp_path, monkeypatch):
    from elogmgr.settings import from_env, load_env_file
    (tmp_path / ".env").write_text("ELOG_DIR=elog\nELOGMGR_BEHIND_PROXY=0\nELOGMGR_RELOAD=none\n", encoding="utf-8")
    for k in ("ELOG_DIR", "ELOGD_CFG", "ELOGMGR_BEHIND_PROXY", "ELOGMGR_RELOAD"):
        monkeypatch.delenv(k, raising=False)
    load_env_file(str(tmp_path / ".env"))
    s = from_env()
    assert s["elogd_cfg"] == str(tmp_path / "elog" / "elogd.cfg")
    assert s["behind_proxy"] is False and s["secure_cookies"] is False and s["reload_mode"] == "none"


def test_reload_button_and_failures(client):
    login(client, "bob")
    assert client.post("/api/reload", headers=H).status_code == 403  # manual reload is for managers
    login(client, "alice")
    assert client.post("/api/reload", headers=H).json["ok"]
    assert reload_elogd({"reload_mode": "command", "reload_command": f'"{sys.executable}" -c "raise SystemExit(3)"'})["ok"] is False
    assert reload_elogd({"reload_mode": "signal", "elogd_process": "no-such-elogd-xyz"})["ok"] is False


def test_nested_groups_lint():
    c = ElogConfig(ref_text())
    msgs = [i["message"] for i in lint(c)]
    # "Group Playground = Playground" names the logbook, not the group itself
    assert not any("contains itself" in m for m in msgs)
    groups = c.groups()
    for g in groups:
        if g["name"] == "STM Experiments":
            g["members"].append("Experiments")  # Experiments -> STM Experiments -> Experiments
    c.set_groups(groups)
    msgs = [i["message"] for i in lint(c)]
    assert any("'Experiments' contains itself" in m for m in msgs)


def test_write_falls_back_to_in_place(client, monkeypatch):
    import tempfile
    def no_tmp(*a, **kw):
        raise PermissionError("directory not writable")
    monkeypatch.setattr(tempfile, "mkstemp", no_tmp)
    login(client, "alice")
    st = client.get("/api/state").json
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": [
        {"op": "set", "section": "r001", "key": "Comment", "value": "written in place"}]}, headers=H)
    assert r.status_code == 200, r.json
    assert "comment = written in place" in (client.tmp / "elogd.cfg").read_text(encoding="utf-8")


def test_reload_refusal_is_explained():
    from elogmgr.reload import denied_message
    msg = denied_message(74359, (1000, 1000, 1000), 1000, "docker-default (enforce)")
    assert "AppArmor" in msg and "apparmor=unconfined" in msg
    msg = denied_message(74359, (0, 1000, 0), 1000, "unconfined")  # elogd changed only its effective uid
    assert "real uid 0" in msg
    msg = denied_message(74359, (1001, 1001, 1001), 1000, "")
    assert "uid 1001" in msg and "ELOG_UID" in msg


def test_rename_keeps_data_dir(client):
    login(client, "alice")
    st = client.get("/api/state").json
    r = client.post("/api/apply", json={"etag": st["etag"], "ops": [
        {"op": "rename_logbook", "name": "r001", "new": "Raman 001"}]}, headers=H)
    assert r.status_code == 200
    c = ElogConfig((client.tmp / "elogd.cfg").read_text(encoding="utf-8"))
    assert c.section("Raman 001").get("Subdir") == "r001"
    assert c.groups_of("Raman 001") == ["Raman"]
