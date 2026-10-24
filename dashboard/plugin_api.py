"""Hermes Companion Pair dashboard backend.

Mounted by the dashboard plugin system at
``/api/plugins/hermes-companion-pair/*`` (mount follows the manifest
``name``; see NOTES.md). Every route here sits behind the dashboard's
normal auth gate — these are owner-only surfaces (create offers, list
and revoke devices), which is exactly right: offer creation is proof of
already owning the host (spec section 9).

Standalone-module note: the dashboard loads this file on its own (not
as part of the plugin package), so it cannot use relative imports. It
locates the plugin root from its own path and imports the
``companion_pairing`` package from there, the same sys.path pattern the
peer plugin uses.
"""

from __future__ import annotations

import base64
import logging
import secrets
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from companion_pairing.gateway_config import dashboard_endpoint  # noqa: E402
from companion_pairing.offers import (check_routes, create_offer,  # noqa: E402
                                      discover_routes, payload_to_json)
from companion_pairing.qr import encode_matrix, matrix_to_svg  # noqa: E402
from companion_pairing.store import Store  # noqa: E402
from companion_pairing.supervision import (dashboard_status,  # noqa: E402
                                           ensure_dashboard, stop_dashboard)

logger = logging.getLogger(__name__)

router = APIRouter()


def _store() -> Store:
    return Store()


def _host_name() -> str:
    import socket

    try:
        return socket.gethostname()
    except Exception:
        return "hermes-host"


@router.post("/offers")
async def create_pairing_offer():
    """Create a pairing offer; returns the QR payload, an SVG rendering
    of it, and any bind warning. Owner-authenticated by the gate."""
    store = _store()
    _dhost, dport = dashboard_endpoint()
    from companion_pairing.gateway_config import resolve_gateway_info

    info = resolve_gateway_info()
    routes = discover_routes(dashboard_port=dport,
                             gateway_port=info.gateway_port)
    try:
        payload, _secret = create_offer(store, _host_name(), routes,
                                        gateway_port=info.gateway_port)
    except Exception as exc:
        # Rate limit and store errors stay generic to the caller.
        raise HTTPException(status_code=429, detail=str(exc))
    warning = check_routes(routes)
    qr_svg = matrix_to_svg(
        encode_matrix(payload_to_json(payload).encode()))
    logger.info("[hermes-companion-pair] offer created pair_id=%s",
                payload["pair_id"])
    return {
        "payload": payload,
        "expires_at": payload["expires_at"],
        "bind_warning": warning,
        "qr_svg": qr_svg,
    }


@router.get("/devices")
async def list_paired_devices():
    """Paired devices, public fields only. Secrets are never returned."""
    devices = _store().list_devices()
    return {
        "devices": [
            {
                "id": d["device_id"],
                "name": d["device_name"],
                "paired_at": d["created_at"],
                "last_seen_at": d["last_seen"],
                "credential_state": "revoked" if d["revoked"] else "active",
            }
            for d in devices
        ]
    }


@router.delete("/devices/{device_id}")
async def revoke_paired_device(device_id: str):
    if _store().revoke_device(device_id):
        logger.info("[hermes-companion-pair] device revoked device_id=%s",
                    device_id)
        return {"revoked": True, "device_id": device_id}
    raise HTTPException(status_code=404, detail="unknown device")


@router.post("/devices/{device_id}/rotate")
async def rotate_device_credential(device_id: str):
    """Rotate a device's credential (re-pair without removing the entry).
    The new credential is returned exactly once; the old one dies at once."""
    credential = base64.urlsafe_b64encode(
        secrets.token_bytes(32)).decode().rstrip("=")
    from companion_pairing.store import hash_secret

    if _store().rotate_device_credential(device_id, hash_secret(credential)):
        logger.info("[hermes-companion-pair] device credential rotated"
                    " device_id=%s", device_id)
        return {"device_id": device_id, "device_credential": credential}
    raise HTTPException(status_code=404, detail="unknown device")


@router.get("/dashboard-status")
async def get_dashboard_status():
    return dashboard_status(_store().data_dir)


@router.post("/dashboard-start")
async def start_dashboard():
    result = ensure_dashboard(_store().data_dir, force=True)
    return {"dashboard": result}


@router.post("/dashboard-stop")
async def stop_dashboard_route():
    result = stop_dashboard(_store().data_dir)
    return {"dashboard": result}


@router.get("/health")
async def plugin_health():
    return {"plugin": "hermes-companion-pair", "ok": True}
