"""hermes-companion-pair — one-scan QR pairing for Hermes Agent.

SKELETON (Step-0 spike complete; see NOTES.md for the verified architecture).

Wiring, per the spike:
  * Mechanism D — redeem endpoint on the gateway api_server:
    ``ctx.register_platform_handler("api_server", _wire_gateway_routes)``
    hands us the api_server's aiohttp ``web.Application``; we register
    ``POST /companion/redeem`` on it. The route validates the one-time
    pairing secret itself (no global auth middleware exists on the
    api_server; core routes check API_SERVER_KEY per-handler).
  * Mechanism B — dashboard sessions for paired devices:
    ``ctx.register_dashboard_auth_provider(CompanionAuthProvider())``
    validates issued per-device credentials and returns dashboard sessions.
  * Dashboard tab (owner-facing, behind dashboard auth): create offers,
    list devices, revoke — served from dashboard/plugin_api.py.
  * CLI: ``hermes companion pair|devices|revoke|dashboard|install-service``.

State lives in the plugin data dir (SQLite): offers + devices, shared by the
gateway and dashboard processes.
"""

from __future__ import annotations

import logging
import threading
import weakref

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "0.1.0"

# Route wiring is keyed on the aiohttp app INSTANCE (peer's WeakSet pattern):
# a second api_server connect onto a different app must still get the routes,
# while double-wiring the same app stays a no-op.
_wired_apps: "weakref.WeakSet" = weakref.WeakSet()
_wire_lock = threading.Lock()


def _wire_gateway_routes(native, adapter) -> None:
    """Factory for ctx.register_platform_handler("api_server", ...).

    `native` is the api_server's aiohttp web.Application.
    TODO(build): register POST /companion/redeem -> pairing.redeem handler.
    """
    with _wire_lock:
        if native is not None and native in _wired_apps:
            return
        if native is None:
            logger.debug("[hermes-companion-pair] api_server native is None; redeem route not mounted")
            return
        # from .pairing import register_redeem_route
        # register_redeem_route(native)
        _wired_apps.add(native)
        logger.info("[hermes-companion-pair] redeem route mounted on api_server")


def register(ctx) -> None:
    # Mechanism D: redeem endpoint on the gateway.
    ctx.register_platform_handler("api_server", _wire_gateway_routes)

    # Mechanism B: per-device dashboard sessions.
    # from .auth_provider import CompanionAuthProvider
    # ctx.register_dashboard_auth_provider(CompanionAuthProvider())

    # CLI surface.
    # from .cli import register_cli
    # register_cli(ctx)

    # Dashboard supervision (spec §5.6) starts on the gateway lifecycle hook.
    logger.info("[hermes-companion-pair] registered (skeleton)")
