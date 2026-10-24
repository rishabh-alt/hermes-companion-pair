"""Pairing offer creation and the QR payload contract (spec section 7).

An offer is a one-time, 256-bit secret with a 300-second TTL. Only the
SHA-256 hash is stored; the plaintext secret exists only inside the QR
payload handed to the host owner (dashboard tab or CLI terminal).
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import socket
import time
import uuid
from urllib.parse import urlparse

from .store import (MAX_OFFERS_PER_HOUR, OFFER_TTL_SECONDS, Store,
                    hash_secret)

logger = logging.getLogger(__name__)

REDEEM_PATH = "/companion/redeem"
REDEEM_MODE = "gateway"


class OfferLimitError(Exception):
    """More than MAX_OFFERS_PER_HOUR offers created in the last hour."""


def _random_secret() -> str:
    """43-char base64url, 256 bits, CSPRNG."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _is_loopback_host(host: str) -> bool:
    host = host.lower().strip("[]")
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def discover_routes(dashboard_port: int = 9119, gateway_port: int = 8642) -> list[dict]:
    """Best-effort route discovery for the QR payload (spec section 7).

    Returns dashboard URLs with optional gateway_url splits, ordered by
    host preference: Tailscale IPv4 first, then LAN IPv4. Empty list when
    nothing is discoverable (the caller surfaces that, never invents).
    """
    routes: list[dict] = []
    seen: set[str] = set()

    def add(kind: str, ip: str) -> None:
        if ip and ip not in seen:
            seen.add(ip)
            routes.append({
                "kind": kind,
                "url": f"http://{ip}:{dashboard_port}",
                "gateway_url": f"http://{ip}:{gateway_port}",
            })

    # Tailscale IPv4, if the CLI is present.
    try:
        import subprocess

        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5
        ).stdout.strip().split()
        for ip in out:
            add("tailscale", ip)
    except Exception:
        pass
    # LAN IPv4 via the UDP-connect trick (sends no traffic).
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            add("lan", sock.getsockname()[0])
        finally:
            sock.close()
    except Exception:
        pass
    return routes


def check_routes(routes: list[dict]) -> str | None:
    """Bind/reachability check (spec section 7, stolen from the peer).

    Returns a warning string when the advertised routes are unusable, else
    None. Warnings only: offer creation is never blocked, and nothing is
    ever rebound silently.
    """
    if not routes:
        return ("No host address could be discovered — phones will not be "
                "able to reach this host from the QR code alone.")
    hosts = []
    for route in routes:
        try:
            hosts.append(urlparse(route["url"]).hostname or "")
        except Exception:
            hosts.append("")
    if hosts and all(_is_loopback_host(h) for h in hosts if h):
        return ("Hermes is only listening on localhost — phones can't "
                "reach it. Bind the dashboard/gateway beyond loopback "
                "before sharing this code.")
    # Reachability probe from the host itself (best-effort, 0.5 s each).
    reachable = 0
    for route in routes:
        try:
            parsed = urlparse(route["url"])
            host, port = parsed.hostname, parsed.port or 9119
            if not host:
                continue
            with socket.create_connection((host, port), timeout=0.5):
                reachable += 1
        except Exception:
            continue
    if reachable == 0:
        return ("None of the advertised addresses answered a probe from "
                "this host — the code may still work from the phone, but "
                "check the dashboard is actually listening.")
    return None


def create_offer(store: Store, host_name: str, routes: list[dict],
                 gateway_port: int = 8642,
                 now: int | None = None) -> tuple[dict, str]:
    """Create an offer and return (qr_payload, plaintext_secret).

    The plaintext secret is returned ONLY here, to be shown to the host
    owner in the dashboard tab / CLI terminal. It is never logged.
    """
    now = int(time.time()) if now is None else now
    store.purge_expired_offers(now)
    if store.offers_created_last_hour(now) >= MAX_OFFERS_PER_HOUR:
        raise OfferLimitError(
            f"offer rate limit reached ({MAX_OFFERS_PER_HOUR}/hour)")
    pair_id = str(uuid.uuid4())
    secret = _random_secret()
    expires_at = now + OFFER_TTL_SECONDS
    store.insert_offer(pair_id, hash_secret(secret), now, expires_at)
    logger.info("[hermes-companion-pair] offer created pair_id=%s", pair_id)
    payload = {
        "v": 1,
        "type": "hermes-companion-pair",
        "host_name": host_name,
        "routes": routes,
        "redeem": {"mode": REDEEM_MODE, "path": REDEEM_PATH, "port": gateway_port},
        "pair_id": pair_id,
        "secret": secret,
        "expires_at": expires_at,
    }
    return payload, secret


def payload_to_json(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
