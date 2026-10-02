import pytest

from hotfire import keys
from hotfire.store import Store


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


def test_app_names_are_simple_identifiers():
    import pytest
    from hotfire.keys import create_key
    from hotfire.store import Store
    for bad in ["", "  ", "my app", "a/b"]:
        with pytest.raises(ValueError):
            create_key(Store.open(), bad)


def test_store_files_are_private_to_the_owner(hotfire_home):
    from hotfire.store import Store
    Store.open()
    assert (hotfire_home.stat().st_mode & 0o777) == 0o700
    assert ((hotfire_home / "mst.db").stat().st_mode & 0o777) == 0o600


def test_concurrent_first_keys_never_hand_out_a_dead_key(tmp_path):
    """Two processes racing to create the same app's key: exactly one wins; the other gets KeyExists."""
    import threading
    import pytest
    from hotfire.keys import KeyExists, create_key, verify_key
    from hotfire.store import Store
    results, barrier = [], threading.Barrier(8)
    # One connection each, like separate `hotfire serve` processes. Opened up front: a thread that
    # failed before the barrier would leave the others waiting forever (this test once hung).
    stores = [Store.open() for _ in range(8)]

    def worker(store):
        barrier.wait(timeout=30)
        try:
            results.append(create_key(store, "default"))
        except KeyExists:
            results.append(None)

    threads = [threading.Thread(target=worker, args=(s,)) for s in stores]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    issued = [k for k in results if k]
    assert len(issued) == 1
    assert verify_key(Store.open(), issued[0]) == "default"
