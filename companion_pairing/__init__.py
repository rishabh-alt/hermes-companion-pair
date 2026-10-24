"""hermes-companion-pair implementation package.

Modules: store (SQLite offers/devices/sessions), offers (QR payload),
redeem (Mechanism D route logic), auth_provider (Mechanism B),
supervision (section 5.6), cli (``hermes companion``), qr (encoder),
gateway_config (redeem-response coordinates).
"""

from . import auth_provider, cli, gateway_config, offers, qr, redeem, store, supervision

__all__ = [
    "auth_provider",
    "cli",
    "gateway_config",
    "offers",
    "qr",
    "redeem",
    "store",
    "supervision",
]
