"""Redemption (Mechanism D): POST /companion/redeem on the gateway
api_server (spec sections 6, 6.1, 9).

Error-code policy (spec sections 8.5 + 9, reconciled):
  * Unknown pair_id and wrong secret are indistinguishable:
    ``pairing_invalid``. No oracle on the secret.
  * An offer already redeemed answers ``pairing_used``; an offer past its
    TTL answers ``pairing_expired``. These states describe the *offer*,
    not the secret, and the companion app maps them to distinct messages.
  * After 5 failed attempts the offer is deleted, so further attempts
    answer ``pairing_invalid`` like any unknown offer.
  * Any unexpected internal error burns the offer (best-effort) and
    answers ``pairing_invalid`` — fail closed, never leak internals.

The business logic in :func:`redeem_offer` is pure over the store so it
is testable without a live Hermes; :func:`register_redeem_route` is the
thin aiohttp adapter.
"""

from __future__ import annotations

import base64
import logging
import secrets
import time
import uuid

from .gateway_config import GatewayInfo, dashboard_url_for
from .store import MAX_FAILED_ATTEMPTS, Store, hash_secret, secrets_equal

logger = logging.getLogger(__name__)

REDEEM_PATH = "/companion/redeem"


class RedeemError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _new_device_credential() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def redeem_offer(store: Store, pair_id: str, secret: str, device_name: str,
                 gateway_info: GatewayInfo,
                 request_host: str | None = None,
                 gateway_url: str | None = None,
                 now: int | None = None) -> dict:
    """Redeem one offer. Returns the redeem response dict (spec 6.1).

    Raises RedeemError with pairing_invalid / pairing_used /
    pairing_expired. Never logs a secret or credential.
    """
    now = int(time.time()) if now is None else now
    if not pair_id or not secret:
        raise RedeemError("pairing_invalid")
    offer = store.get_offer(pair_id)
    if offer is None:
        raise RedeemError("pairing_invalid")
    try:
        if offer["used"]:
            raise RedeemError("pairing_used")
        if now > int(offer["expires_at"]):
            raise RedeemError("pairing_expired")
        if not secrets_equal(secret, offer["secret_hash"]):
            attempts = store.bump_failed_attempt(pair_id)
            if attempts >= MAX_FAILED_ATTEMPTS:
                store.delete_offer(pair_id)
                logger.info(
                    "[hermes-companion-pair] offer burned after failed"
                    " attempts pair_id=%s",
                    pair_id,
                )
            raise RedeemError("pairing_invalid")
        device_id = str(uuid.uuid4())
        credential = _new_device_credential()
        store.mark_offer_used(pair_id)
        store.insert_device(device_id, device_name or "Unnamed device",
                            hash_secret(credential), now)
        logger.info(
            "[hermes-companion-pair] offer redeemed pair_id=%s device_id=%s",
            pair_id, device_id,
        )
    except RedeemError:
        raise
    except Exception:
        # Fail closed: burn the offer rather than leave a half-redeemed
        # state, then answer generically.
        try:
            store.delete_offer(pair_id)
        except Exception:
            pass
        logger.exception(
            "[hermes-companion-pair] redeem internal error pair_id=%s",
            pair_id,
        )
        raise RedeemError("pairing_invalid")

    response = {
        "device_id": device_id,
        "device_credential": credential,
        "gateway_url": gateway_url,
        "gateway_key": gateway_info.gateway_key,
        "gateway_token_scope": "global",
        "dashboard_url": dashboard_url_for(gateway_info, request_host),
        "host_name": gateway_info.host_name,
        "host_version": None,
        "config_version": None,
        "latest_config_version": None,
    }
    return response


def register_redeem_route(app, store_factory, gateway_info: GatewayInfo) -> None:
    """Mount POST /companion/redeem on an aiohttp web.Application.

    ``app`` is the api_server's application object (Mechanism D); the
    route validates the one-time secret itself because the api_server
    has no global auth middleware (NOTES.md Q1).
    """
    from aiohttp import web

    async def handler(request) -> "web.Response":
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "pairing_invalid"}, status=400)
        pair_id = body.get("pair_id") if isinstance(body, dict) else None
        secret = body.get("secret") if isinstance(body, dict) else None
        device_name = body.get("device_name") if isinstance(body, dict) else None
        gateway_url = None
        try:
            gateway_url = f"http://{request.host}"
        except Exception:
            pass
        try:
            store = store_factory()
            result = redeem_offer(
                store, pair_id or "", secret or "", device_name or "",
                gateway_info, request_host=request.host,
                gateway_url=gateway_url,
            )
            return web.json_response(result, status=200)
        except RedeemError as err:
            status = 410 if err.code == "pairing_expired" else 400
            return web.json_response({"error": err.code}, status=status)
        except Exception:
            return web.json_response({"error": "pairing_invalid"}, status=400)

    app.router.add_post(REDEEM_PATH, handler)
