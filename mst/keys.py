"""Per-app API keys. Plaintext is shown once; only a SHA-256 hash is stored."""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass

from mst.store import Store

PREFIX_LEN = 12
LAST_USED_INTERVAL = 60  # seconds; avoids a write per request


class KeyExists(Exception):
    pass


@dataclass(frozen=True)
class KeyInfo:
    app: str
    prefix: str
    created_at: float
    last_used_at: float | None
    revoked_at: float | None


APP_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def create_key(store: Store, app: str, *, rotate: bool = False, internal: bool = False) -> str:
    """`internal` keys (names starting with '_') belong to mst itself, e.g. LiteLLM's upstream key."""
    if not APP_NAME.fullmatch(app):
        raise ValueError(f"app name {app!r} must be letters, digits, '.', '_' or '-'")
    if app.startswith("_") and not internal:
        raise ValueError(f"app names starting with '_' are reserved for mst")
    plaintext = "mst-" + secrets.token_urlsafe(32)
    # Check and write in one write transaction, so two racing `mst serve` runs can't both
    # "create" a key and leave the first one printed but already overwritten.
    store.db.execute("BEGIN IMMEDIATE")
    try:
        row = store.db.execute("SELECT revoked_at FROM keys WHERE app = ?", (app,)).fetchone()
        if row and row["revoked_at"] is None and not rotate:
            raise KeyExists(f"{app} already has a key. Use --rotate to replace it (the old key stops working).")
        store.db.execute(
            """INSERT INTO keys (app, prefix, key_hash, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(app) DO UPDATE SET prefix=excluded.prefix, key_hash=excluded.key_hash,
                 created_at=excluded.created_at, last_used_at=NULL, revoked_at=NULL""",
            (app, plaintext[:PREFIX_LEN], _hash(plaintext), time.time()),
        )
    except BaseException:
        store.db.execute("ROLLBACK")
        raise
    store.db.execute("COMMIT")
    return plaintext


def verify_key(store: Store, plaintext: str) -> str | None:
    if not plaintext:
        return None
    row = store.db.execute(
        "SELECT app, last_used_at FROM keys WHERE key_hash = ? AND revoked_at IS NULL",
        (_hash(plaintext),),
    ).fetchone()
    if not row:
        return None
    now = time.time()
    if row["last_used_at"] is None or now - row["last_used_at"] >= LAST_USED_INTERVAL:
        store.db.execute("UPDATE keys SET last_used_at = ? WHERE app = ?", (now, row["app"]))
    return row["app"]


def revoke_key(store: Store, app: str) -> bool:
    return store.db.execute(
        "UPDATE keys SET revoked_at = ? WHERE app = ? AND revoked_at IS NULL", (time.time(), app)
    ).rowcount > 0


def list_keys(store: Store) -> list[KeyInfo]:
    return [
        KeyInfo(r["app"], r["prefix"], r["created_at"], r["last_used_at"], r["revoked_at"])
        for r in store.db.execute("SELECT * FROM keys ORDER BY app")
    ]
