"""F-024: `mxc keys rotate` and POST /admin/keys/{app}/rotate."""
import json

from typer.testing import CliRunner

from mxconsole import cli, keys
from mxconsole.store import Store
from test_console import ADMIN
from test_gateway import auth, client, http, key, store, sup  # noqa: F401  (shared fixtures)

runner = CliRunner()


async def test_rotate_returns_a_new_secret_once_and_revokes_the_old(http, key):
    assert (await http.get("/v1/models", headers=auth(key))).status_code == 200
    r = await http.post("/admin/keys/myapp/rotate", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["app"] == "myapp" and body["key"].startswith("mst-") and body["key"] != key
    assert (await http.get("/v1/models", headers=auth(key))).status_code == 401  # old one is dead
    assert (await http.get("/v1/models", headers=auth(body["key"]))).status_code == 200
    listed = (await http.get("/admin/keys", headers=ADMIN)).json()
    assert [k["app"] for k in listed] == ["myapp"] and body["key"] not in json.dumps(listed)  # shown once


async def test_rotate_unknown_or_internal_app_is_404(http, store):
    assert (await http.post("/admin/keys/nope/rotate", headers=ADMIN)).status_code == 404
    keys.create_key(store, "_litellm", internal=True)
    assert (await http.post("/admin/keys/_litellm/rotate", headers=ADMIN)).status_code == 404


async def test_rotate_is_admin_only(http, key):
    assert (await http.post("/admin/keys/myapp/rotate")).status_code == 403
    assert (await http.post("/admin/keys/myapp/rotate", headers={**ADMIN, "X-Forwarded-For": "10.0.0.2"})).status_code == 403
    assert (await http.get("/v1/models", headers=auth(key))).status_code == 200  # nothing changed


def test_cli_keys_rotate():
    store = Store.open()
    old = keys.create_key(store, "notes")
    r = runner.invoke(cli.app, ["keys", "rotate", "notes", "--json"])
    assert r.exit_code == 0, r.output
    body = json.loads(r.output)
    assert body["app"] == "notes" and body["key"].startswith("mst-") and body["key"] != old
    assert keys.verify_key(store, old) is None and keys.verify_key(store, body["key"]) == "notes"
    text = runner.invoke(cli.app, ["keys", "rotate", "notes"])
    assert "not be shown again" in text.output and "mst-" in text.output
    missing = runner.invoke(cli.app, ["keys", "rotate", "nope"])
    assert missing.exit_code == 1 and "no key for nope" in missing.output
