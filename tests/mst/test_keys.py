import pytest

from mst import keys
from mst.store import Store


@pytest.fixture
def store():
    return Store.open()


def test_new_key_format_and_only_hash_stored(store):
    key = keys.create_key(store, "notes")
    assert key.startswith("mst-") and len(key) >= 36
    row = store.db.execute("SELECT * FROM keys WHERE app='notes'").fetchone()
    assert row["prefix"] == key[:12]
    assert key not in "".join(str(v) for v in tuple(row))
    assert len(row["key_hash"]) == 64


def test_verify_returns_app_and_rejects_unknown(store):
    key = keys.create_key(store, "notes")
    assert keys.verify_key(store, key) == "notes"
    assert keys.verify_key(store, key + "x") is None
    assert keys.verify_key(store, "") is None


def test_last_used_updates_at_most_once_a_minute(store, monkeypatch):
    key = keys.create_key(store, "notes")
    t = [1000.0]
    monkeypatch.setattr(keys.time, "time", lambda: t[0])
    keys.verify_key(store, key)
    t[0] = 1030.0
    keys.verify_key(store, key)
    assert keys.list_keys(store)[0].last_used_at == 1000.0
    t[0] = 1061.0
    keys.verify_key(store, key)
    assert keys.list_keys(store)[0].last_used_at == 1061.0


def test_revoke_disables_key(store):
    key = keys.create_key(store, "notes")
    assert keys.revoke_key(store, "notes") is True
    assert keys.verify_key(store, key) is None
    assert keys.revoke_key(store, "notes") is False
    assert keys.revoke_key(store, "nope") is False
    assert keys.list_keys(store)[0].revoked_at is not None


def test_second_key_refused_unless_rotate(store):
    old = keys.create_key(store, "notes")
    with pytest.raises(keys.KeyExists, match="--rotate"):
        keys.create_key(store, "notes")
    new = keys.create_key(store, "notes", rotate=True)
    assert new != old
    assert keys.verify_key(store, old) is None
    assert keys.verify_key(store, new) == "notes"


def test_new_key_after_revoke_allowed(store):
    keys.create_key(store, "notes")
    keys.revoke_key(store, "notes")
    new = keys.create_key(store, "notes")
    assert keys.verify_key(store, new) == "notes"
