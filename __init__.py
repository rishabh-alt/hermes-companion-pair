"""hermes-companion-pair — one-scan QR pairing for Hermes Agent (v1).

Wiring (verified in NOTES.md, Step-0 spike):
  * Mechanism D — redeem endpoint on the gateway api_server:
    ``ctx.register_platform_handler("api_server", factory)`` hands us the
    api_server's aiohttp ``web.Application``; we register
    ``POST /companion/redeem`` on it. The route validates the one-time
    pairing secret itself (the api_server has no global auth middleware;
    core routes check API_SERVER_KEY per-handler).
  * Mechanism B — dashboard sessions for paired devices:
    ``ctx.register_dashboard_auth_provider(CompanionAuthProvider())``
    turns a per-device credential into a dashboard session via the
    password seam (device_id + credential) and the token seam (bearer).
  * CLI: ``hermes companion pair|qr|devices|revoke|dashboard|install-service``.
  * Dashboard tab: ``dashboard/plugin_api.py`` + ``dashboard/dist/index.js``,
    mounted by the dashboard plugin system (see dashboard/manifest.json).
  * Supervision (spec 5.6): when the api_server platform connects, a
    daemon thread runs the dashboard ensure-on-start check once per
    process. It never blocks gateway startup and never spawns when the
    operator opted out.

Import strategy: the Hermes plugin loader loads this file as a package
with the plugin dir on its submodule search path, so relative imports
work. The sys.path fallback below covers loaders that import this file
standalone (e.g. some validate probes).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import weakref

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "1.0.0"

try:
    from .companion_pairing import auth_provider as _auth_provider_mod
    from .companion_pairing import cli as _cli_mod
    from .companion_pairing import gateway_config as _gateway_config_mod
    from .companion_pairing import redeem as _redeem_mod
    from .companion_pairing import store as _store_mod
    from .companion_pairing import supervision as _supervision_mod
except ImportError:  # standalone-file loader fallback (peer pattern)
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    from companion_pairing import auth_provider as _auth_provider_mod
    from companion_pairing import cli as _cli_mod
    from companion_pairing import gateway_config as _gateway_config_mod
    from companion_pairing import redeem as _redeem_mod
    from companion_pairing import store as _store_mod
    from companion_pairing import supervision as _supervision_mod

# Route wiring is keyed on the aiohttp app INSTANCE (peer's WeakSet
# pattern): a second api_server connect onto a different app must still
# get its routes, while double-wiring the same app stays a no-op.
_wired_apps: "weakref.WeakSet" = weakref.WeakSet()
_wire_lock = threading.Lock()
_supervision_started = False


def _run_supervision_once() -> None:
    global _supervision_started
    if _supervision_started:
        return
    _supervision_started = True
    try:
        data_dir = _store_mod.resolve_data_dir()
        result = _supervision_mod.ensure_dashboard(data_dir)
        logger.info("[hermes-companion-pair] dashboard supervision: %s", result)
    except Exception:
        logger.exception("[hermes-companion-pair] supervision check failed")


def _wire_gateway_routes(native, adapter=None) -> None:
    """Factory for ctx.register_platform_handler("api_server", ...).

    ``native`` is the api_server's aiohttp web.Application; ``adapter``
    is the platform adapter (used for gateway key/port resolution).
    Synchronous by the platform API; the route handler itself is async.
    """
    with _wire_lock:
        if native is None:
            logger.debug(
                "[hermes-companion-pair] api_server native is None;"
                " redeem route not mounted on this run")
            return
        if native in _wired_apps:
            logger.debug(
                "[hermes-companion-pair] redeem route already mounted"
                " on this app; skipping")
            return
        try:
            gateway_info = _gateway_config_mod.resolve_gateway_info(adapter)
            _redeem_mod.register_redeem_route(
                native, _store_mod.Store, gateway_info)
            _wired_apps.add(native)
            logger.info(
                "[hermes-companion-pair] redeem route mounted on api_server")
        except Exception:
            logger.exception(
                "[hermes-companion-pair] failed to mount redeem route")
    # Supervision runs once, off the gateway's startup path.
    try:
        thread = threading.Thread(target=_run_supervision_once, daemon=True)
        thread.start()
    except Exception:
        pass


def register(ctx) -> None:
    register_platform_handler = getattr(ctx, "register_platform_handler", None)
    if callable(register_platform_handler):
        try:
            register_platform_handler("api_server", _wire_gateway_routes)
        except Exception:
            logger.exception(
                "[hermes-companion-pair] platform handler registration failed")
    else:
        logger.warning(
            "[hermes-companion-pair] this host has no"
            " register_platform_handler; gateway redeem route unavailable")

    register_auth_provider = getattr(ctx, "register_dashboard_auth_provider", None)
    if callable(register_auth_provider):
        try:
            register_auth_provider(
                _auth_provider_mod.CompanionAuthProvider())
        except Exception:
            logger.exception(
                "[hermes-companion-pair] auth provider registration failed")
    else:
        logger.warning(
            "[hermes-companion-pair] this host has no"
            " register_dashboard_auth_provider; device dashboard sessions"
            " unavailable")

    register_cli_command = getattr(ctx, "register_cli_command", None)
    if callable(register_cli_command):
        try:
            register_cli_command(
                "companion",
                "Pair phones with QR codes; manage paired devices",
                _cli_mod.register_cli,
                _cli_mod.companion_command,
            )
        except Exception:
            logger.exception(
                "[hermes-companion-pair] CLI registration failed")
    else:
        logger.warning(
            "[hermes-companion-pair] this host has no register_cli_command; "
            "`hermes companion` unavailable")

    logger.info("[hermes-companion-pair] registered (v1)")
