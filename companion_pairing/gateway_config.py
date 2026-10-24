"""Gateway/dashboard coordinate resolution for redeem responses.

The redeem response must hand the phone the gateway URL + key and the
dashboard URL (spec section 6.1, NOTES.md Q2). Resolution is best-effort
and read-only, in this order:

  1. the api_server adapter object handed to the platform handler,
  2. environment variables (``API_SERVER_KEY`` and friends),
  3. the active profile's ``~/.hermes/config.yaml`` (parsed defensively;
     a missing or unparseable config yields None, never an exception).

A missing gateway key is reported as ``gateway_key=None`` — the app can
still use its dashboard session, and the operator sees the gap instead
of receiving a fabricated credential.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_GATEWAY_PORT = 8642
DEFAULT_DASHBOARD_PORT = 9119


@dataclass
class GatewayInfo:
    gateway_key: str | None = None
    gateway_port: int = DEFAULT_GATEWAY_PORT
    dashboard_port: int = DEFAULT_DASHBOARD_PORT
    dashboard_host: str = "127.0.0.1"
    host_name: str = ""


def _adapter_attr(adapter: object, *names: str):
    for name in names:
        value = getattr(adapter, name, None)
        if value:
            return value
    return None


def _read_config_text() -> str:
    candidates = []
    home = os.environ.get("HERMES_HOME")
    if home:
        candidates.append(Path(home) / "config.yaml")
    candidates.append(Path.home() / ".hermes" / "config.yaml")
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text()
        except Exception:
            continue
    return ""


def _scan_config_value(text: str, *keys: str) -> str | None:
    """Tiny indentation-tolerant scan for `key: value` lines. Not a YAML
    parser by design: config layout varies across versions, and a wrong
    guess here must degrade to None, not crash."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for key in keys:
            if stripped.startswith(key + ":"):
                value = stripped[len(key) + 1:].strip().strip("\"'")
                if value:
                    return value
    return None


def resolve_gateway_info(adapter: object | None = None) -> GatewayInfo:
    info = GatewayInfo()
    try:
        import socket

        info.host_name = socket.gethostname()
    except Exception:
        pass
    config_text = _read_config_text()

    # Gateway key: adapter -> env -> config.
    key = None
    if adapter is not None:
        key = _adapter_attr(adapter, "_api_key", "api_key", "api_server_key")
        cfg = _adapter_attr(adapter, "config")
        if key is None and isinstance(cfg, dict):
            key = cfg.get("api_key") or cfg.get("api_server_key")
    if key is None:
        key = os.environ.get("API_SERVER_KEY")
    if key is None and config_text:
        key = _scan_config_value(
            config_text, "api_server_key", "api_key", "gateway_key")
    info.gateway_key = str(key) if key else None

    # Ports: adapter -> env -> config -> defaults.
    if adapter is not None:
        port = _adapter_attr(adapter, "port", "bind_port", "_port")
        if port:
            try:
                info.gateway_port = int(port)
            except (TypeError, ValueError):
                pass
    if os.environ.get("HERMES_API_SERVER_PORT"):
        try:
            info.gateway_port = int(os.environ["HERMES_API_SERVER_PORT"])
        except ValueError:
            pass
    if os.environ.get("HERMES_DASHBOARD_PORT"):
        try:
            info.dashboard_port = int(os.environ["HERMES_DASHBOARD_PORT"])
        except ValueError:
            pass
    if config_text:
        dport = _scan_config_value(config_text, "dashboard_port")
        if dport:
            try:
                info.dashboard_port = int(dport)
            except ValueError:
                pass
        dhost = _scan_config_value(config_text, "dashboard_host")
        if dhost:
            info.dashboard_host = dhost
    return info


def dashboard_url_for(info: GatewayInfo, request_host: str | None = None) -> str | None:
    """Dashboard URL for the redeem response. Prefers the host the phone
    actually reached (gateway and dashboard usually share it); falls back
    to the configured dashboard host."""
    host = None
    if request_host:
        host = request_host.split(":")[0]
    elif info.dashboard_host:
        host = info.dashboard_host
    if not host:
        return None
    return f"http://{host}:{info.dashboard_port}"
