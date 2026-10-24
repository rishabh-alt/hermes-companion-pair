"""Durable store for hermes-companion-pair: pairing offers, paired devices,
and dashboard sessions issued to devices.

State lives in SQLite under the plugin data directory so it survives
``hermes plugins update`` and follows the active profile. The gateway
process and the dashboard process both open it; every operation uses a
short-lived connection (WAL mode) so no long-lived handle crosses a
process boundary.

Security rules, enforced here and nowhere else:
  * Secrets are NEVER stored in plaintext. Only SHA-256 hex digests.
  * Comparisons of presented secrets use ``hmac.compare_digest``.
  * No function in this module logs a secret or a credential.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import time
from pathlib import Path

PLUGIN_NAME = "hermes-companion-pair"

OFFER_TTL_SECONDS = 300
MAX_FAILED_ATTEMPTS = 5
MAX_OFFERS_PER_HOUR = 10

SESSION_ACCESS_TTL_SECONDS = 24 * 3600
SESSION_REFRESH_TTL_SECONDS = 30 * 24 * 3600


def hash_secret(secret: str) -> str:
    """SHA-256 hex digest of a presented secret. 256-bit secrets make
    SHA-256 an adequate hash at rest (spec section 6.1)."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secrets_equal(presented: str, stored_hash: str) -> bool:
    """Timing-safe comparison of a presented secret against a stored hash."""
    try:
        return hmac.compare_digest(hash_secret(presented), stored_hash)
    except Exception:
        return False


def resolve_data_dir(explicit: Path | str | None = None) -> Path:
    """Return (and create) the plugin data directory.

    Resolution order:
      1. explicit path (tests / operator override),
      2. ``HERMES_COMPANION_PAIR_DATA_DIR`` env var (tests / operators),
      3. Hermes' own ``plugins.plugin_storage.plugin_data_dir`` (runtime),
      4. ``~/.hermes/plugin-data/<name>/`` computed locally (fallback used
         by dashboard plugin_api.py when the plugins package is not importable).
    """
    if explicit is not None:
        path = Path(explicit)
    elif os.environ.get("HERMES_COMPANION_PAIR_DATA_DIR"):
        path = Path(os.environ["HERMES_COMPANION_PAIR_DATA_DIR"])
    else:
        try:
            from plugins.plugin_storage import plugin_data_dir  # type: ignore

            return Path(plugin_data_dir(PLUGIN_NAME))
        except Exception:
            path = Path.home() / ".hermes" / "plugin-data" / PLUGIN_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    pair_id TEXT PRIMARY KEY,
    secret_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    failed_attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    device_name TEXT NOT NULL,
    credential_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
    access_token_hash TEXT PRIMARY KEY,
    refresh_token_hash TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""


class Store:
    """Thin SQLite wrapper. One short connection per operation."""

    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = resolve_data_dir(data_dir)
        self.db_path = self.data_dir / "pairing.db"
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # -- offers ---------------------------------------------------------

    def offers_created_last_hour(self, now: int | None = None) -> int:
        now = int(time.time()) if now is None else now
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM offers WHERE created_at > ?",
                (now - 3600,),
            ).fetchone()
        return int(row["n"])

    def insert_offer(self, pair_id: str, secret_hash: str, created_at: int,
                     expires_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO offers (pair_id, secret_hash, created_at,"
                " expires_at, used, failed_attempts) VALUES (?, ?, ?, ?, 0, 0)",
                (pair_id, secret_hash, created_at, expires_at),
            )

    def get_offer(self, pair_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM offers WHERE pair_id = ?", (pair_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_offer_used(self, pair_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE offers SET used = 1 WHERE pair_id = ?", (pair_id,))

    def bump_failed_attempt(self, pair_id: str) -> int:
        """Increment failed_attempts; returns the new count."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE offers SET failed_attempts = failed_attempts + 1"
                " WHERE pair_id = ?",
                (pair_id,),
            )
            row = conn.execute(
                "SELECT failed_attempts FROM offers WHERE pair_id = ?",
                (pair_id,),
            ).fetchone()
        return int(row["failed_attempts"]) if row is not None else 0

    def delete_offer(self, pair_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM offers WHERE pair_id = ?", (pair_id,))

    def purge_expired_offers(self, now: int | None = None) -> int:
        now = int(time.time()) if now is None else now
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM offers WHERE expires_at <= ? AND used = 0", (now,)
            )
            return cur.rowcount or 0

    # -- devices --------------------------------------------------------

    def insert_device(self, device_id: str, device_name: str,
                      credential_hash: str, created_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO devices (device_id, device_name, credential_hash,"
                " created_at, revoked, last_seen) VALUES (?, ?, ?, ?, 0, ?)",
                (device_id, device_name, credential_hash, created_at, created_at),
            )

    def get_device(self, device_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def find_device_by_credential_hash(self, credential_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE credential_hash = ?",
                (credential_hash,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_devices(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT device_id, device_name, created_at, revoked, last_seen"
                " FROM devices ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def touch_device(self, device_id: str, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        with self._connect() as conn:
            conn.execute(
                "UPDATE devices SET last_seen = ? WHERE device_id = ?",
                (now, device_id),
            )

    def revoke_device(self, device_id: str) -> bool:
        """Delete the credential + device record. Also drops its sessions.
        Returns True if a device was removed."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM devices WHERE device_id = ?", (device_id,)
            )
            conn.execute(
                "DELETE FROM sessions WHERE device_id = ?", (device_id,)
            )
            return (cur.rowcount or 0) > 0

    def rotate_device_credential(self, device_id: str,
                                 new_credential_hash: str) -> bool:
        """Replace a device's credential (old one stops working at once).
        Returns True if the device exists and is not revoked."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE devices SET credential_hash = ?"
                " WHERE device_id = ? AND revoked = 0",
                (new_credential_hash, device_id),
            )
            conn.execute(
                "DELETE FROM sessions WHERE device_id = ?", (device_id,)
            )
            return (cur.rowcount or 0) > 0

    # -- dashboard sessions ----------------------------------------------

    def insert_session(self, access_token_hash: str, refresh_token_hash: str,
                       device_id: str, created_at: int, expires_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (access_token_hash, refresh_token_hash,"
                " device_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (access_token_hash, refresh_token_hash, device_id,
                 created_at, expires_at),
            )

    def get_session_by_access_hash(self, access_token_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE access_token_hash = ?",
                (access_token_hash,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_session_by_refresh_hash(self, refresh_token_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE refresh_token_hash = ?",
                (refresh_token_hash,),
            ).fetchone()
        return dict(row) if row is not None else None

    def rotate_session_access(self, old_access_hash: str,
                              new_access_hash: str, new_expires_at: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET access_token_hash = ?, expires_at = ?"
                " WHERE access_token_hash = ?",
                (new_access_hash, new_expires_at, old_access_hash),
            )
            return (cur.rowcount or 0) > 0

    def delete_session_by_refresh_hash(self, refresh_token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE refresh_token_hash = ?",
                (refresh_token_hash,),
            )
