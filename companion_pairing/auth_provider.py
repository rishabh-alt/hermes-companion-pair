"""Companion dashboard auth provider (Mechanism B, spec section 6).

A paired phone holds a per-device credential, not the owner's password.
This provider turns that credential into a normal dashboard session
through the two seams the ``hermes_cli.dashboard_auth`` framework
documents:

  * password seam: ``complete_password_login`` with username = device_id
    and password = device_credential -> Session (the app logs in with
    the credential exactly like a password, and receives session cookies);
  * token seam: ``verify_token`` accepts the credential as a bearer token
    -> TokenPrincipal (for direct dashboard API calls).

Framework-shape note (assumption, documented): the provider protocol is
``DashboardAuthProvider`` with ``Session`` / ``TokenPrincipal`` /
``LoginStart`` dataclasses and ``InvalidCredentialsError`` /
``ProviderError`` / ``RefreshExpiredError`` errors, as shipped in
``hermes_cli/dashboard_auth/base.py`` on 0.22.x. If that module is not
importable (older host), local dataclasses with identical field names
are used instead and registration degrades to a logged warning rather
than crashing the plugin.

Sessions are minted here, not by Hermes: access tokens live 24 h,
refresh tokens 30 days; both are stored hashed, and revoking a device
deletes its sessions at once (spec section 6.1 item 5 caveat still
applies: an already-issued session cookie is only cut off when its own
access token is checked — revocation guarantees no *new* login).
"""

from __future__ import annotations

import base64
import logging
import secrets
import time
from dataclasses import dataclass

from .store import (SESSION_ACCESS_TTL_SECONDS,
                    SESSION_REFRESH_TTL_SECONDS, Store, hash_secret,
                    secrets_equal)

logger = logging.getLogger(__name__)

try:  # Framework present on 0.22.x (verified in the spike).
    from hermes_cli.dashboard_auth.base import (  # type: ignore
        DashboardAuthProvider,
        InvalidCredentialsError,
        ProviderError,
        RefreshExpiredError,
        Session,
        TokenPrincipal,
    )
    _FRAMEWORK = True
except Exception:  # pragma: no cover - older hosts without the framework
    _FRAMEWORK = False

    @dataclass(frozen=True)
    class Session:  # type: ignore[no-redef]
        user_id: str
        email: str
        display_name: str
        org_id: str
        provider: str
        expires_at: int
        access_token: str
        refresh_token: str
        issuer: str = ""

    @dataclass(frozen=True)
    class TokenPrincipal:  # type: ignore[no-redef]
        principal: str
        provider: str
        scopes: tuple = ()

    class InvalidCredentialsError(Exception):
        pass

    class ProviderError(Exception):
        pass

    class RefreshExpiredError(Exception):
        pass

    DashboardAuthProvider = object  # type: ignore[assignment,misc]


def _new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


class CompanionAuthProvider(DashboardAuthProvider):  # type: ignore[valid-type,misc]
    """Dashboard auth provider for paired devices."""

    name = "companion"
    display_name = "Companion device"
    supports_password = True
    supports_token = True
    supports_session = True

    def __init__(self, store_factory=None):
        # store_factory lets tests inject a temp-dir store; default is the
        # real plugin data dir (resolved lazily per call so profile
        # switches are followed).
        self._store_factory = store_factory or (lambda: Store())

    def _store(self) -> Store:
        return self._store_factory()

    # -- OAuth legs: not how this provider works -------------------------

    def start_login(self, *, redirect_uri: str):
        raise ProviderError(
            "Companion provider authenticates paired devices, not browser OAuth")

    def complete_login(self, *, code: str, state: str, code_verifier: str,
                       redirect_uri: str):
        raise ProviderError(
            "Companion provider authenticates paired devices, not browser OAuth")

    # -- password seam: device_id + device_credential -> Session ---------

    def complete_password_login(self, *, username: str, password: str) -> Session:
        store = self._store()
        device = store.get_device(username)
        if device is None or device["revoked"]:
            raise InvalidCredentialsError("unknown device")
        if not secrets_equal(password, device["credential_hash"]):
            raise InvalidCredentialsError("bad device credential")
        now = int(time.time())
        access = _new_token()
        refresh = _new_token()
        store.insert_session(
            hash_secret(access), hash_secret(refresh), device["device_id"],
            now, now + SESSION_ACCESS_TTL_SECONDS,
        )
        store.touch_device(device["device_id"], now)
        logger.info(
            "[hermes-companion-pair] device session issued device_id=%s",
            device["device_id"],
        )
        return Session(
            user_id=f"device:{device['device_id']}",
            email="",
            display_name=device["device_name"],
            org_id="",
            provider="companion",
            expires_at=now + SESSION_ACCESS_TTL_SECONDS,
            access_token=access,
            refresh_token=refresh,
            issuer="",
        )

    # -- session seam -----------------------------------------------------

    def verify_session(self, *, access_token: str):
        store = self._store()
        row = store.get_session_by_access_hash(hash_secret(access_token))
        if row is None or int(row["expires_at"]) <= int(time.time()):
            return None
        device = store.get_device(row["device_id"])
        if device is None or device["revoked"]:
            return None
        return Session(
            user_id=f"device:{device['device_id']}",
            email="",
            display_name=device["device_name"],
            org_id="",
            provider="companion",
            expires_at=int(row["expires_at"]),
            access_token=access_token,
            refresh_token="",
            issuer="",
        )

    def refresh_session(self, *, refresh_token: str):
        store = self._store()
        row = store.get_session_by_refresh_hash(hash_secret(refresh_token))
        if row is None:
            raise RefreshExpiredError("unknown refresh token")
        device = store.get_device(row["device_id"])
        if device is None or device["revoked"]:
            raise RefreshExpiredError("device revoked")
        now = int(time.time())
        new_access = _new_token()
        if not store.rotate_session_access(
                row["access_token_hash"], hash_secret(new_access),
                now + SESSION_ACCESS_TTL_SECONDS):
            raise RefreshExpiredError("session not found")
        return Session(
            user_id=f"device:{device['device_id']}",
            email="",
            display_name=device["device_name"],
            org_id="",
            provider="companion",
            expires_at=now + SESSION_ACCESS_TTL_SECONDS,
            access_token=new_access,
            refresh_token=refresh_token,
            issuer="",
        )

    def revoke_session(self, *, refresh_token: str) -> None:
        try:
            self._store().delete_session_by_refresh_hash(
                hash_secret(refresh_token))
        except Exception:
            pass  # best-effort by protocol; never raise

    # -- token seam: device credential as bearer token --------------------

    def verify_token(self, *, token: str):
        store = self._store()
        device = store.find_device_by_credential_hash(hash_secret(token))
        if device is None or device["revoked"]:
            return None  # never raise: the seam falls through to others
        store.touch_device(device["device_id"])
        return TokenPrincipal(
            principal=f"device:{device['device_id']}",
            provider="companion",
            scopes=(),
        )
