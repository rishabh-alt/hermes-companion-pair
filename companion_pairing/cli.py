"""CLI surface: ``hermes companion <subcommand>`` (spec section 5.4).

Registered via ``ctx.register_cli_command("companion", ...)``; the setup
function follows the shipped-plugin pattern (argparse subparsers with a
``dest``), and the handler dispatches on the parsed namespace.

Operator-screen rule: ``pair``/``qr`` print the QR payload (which
contains the one-time secret by design) to the operator's own terminal.
Secrets are never written to a log file by this module.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time

from .gateway_config import resolve_gateway_info
from .offers import (check_routes, create_offer, discover_routes,
                     payload_to_json)
from .qr import encode_matrix, matrix_to_ascii
from .store import Store
from .supervision import (dashboard_endpoint, dashboard_status,
                          ensure_dashboard, service_install_plan,
                          stop_dashboard)


def _store() -> Store:
    return Store()


def _host_name() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "hermes-host"


def _print_pair() -> int:
    store = _store()
    _dhost, dport = dashboard_endpoint()
    info = resolve_gateway_info()
    routes = discover_routes(dashboard_port=dport,
                             gateway_port=info.gateway_port)
    try:
        payload, _secret = create_offer(store, _host_name(), routes,
                                        gateway_port=info.gateway_port)
    except Exception as exc:
        print(f"could not create offer: {exc}", file=sys.stderr)
        return 1
    warning = check_routes(routes)
    print("Pair this device — scan the code with the Companion app")
    print(f"  pair_id:    {payload['pair_id']}")
    print(f"  expires in: 300s (at {time.strftime('%H:%M:%S', time.localtime(payload['expires_at']))})")
    if warning:
        print(f"  WARNING: {warning}")
    print()
    print(matrix_to_ascii(encode_matrix(payload_to_json(payload).encode())))
    print()
    print("Payload (paste fallback):")
    print(payload_to_json(payload))
    return 0


def _print_devices() -> int:
    devices = _store().list_devices()
    if not devices:
        print("No paired devices.")
        return 0
    print(f"{'ID':<38} {'NAME':<24} {'PAIRED':<12} {'LAST SEEN':<12} {'STATE'}")
    for dev in devices:
        paired = time.strftime("%Y-%m-%d", time.localtime(dev["created_at"]))
        last = (time.strftime("%Y-%m-%d", time.localtime(dev["last_seen"]))
                if dev["last_seen"] else "-")
        state = "revoked" if dev["revoked"] else "active"
        print(f"{dev['device_id']:<38} {dev['device_name']:<24} {paired:<12} {last:<12} {state}")
    return 0


def _revoke(device_id: str) -> int:
    if _store().revoke_device(device_id):
        print(f"Revoked device {device_id}. Its credential no longer logs in.")
        return 0
    print(f"No such device: {device_id}", file=sys.stderr)
    return 1


def _dashboard(action: str) -> int:
    store_dir = _store().data_dir
    if action == "status":
        status = dashboard_status(store_dir)
        print(json.dumps(status, indent=2))
        return 0
    if action == "start":
        result = ensure_dashboard(store_dir, force=True)
        print(f"dashboard: {result}")
        return 0 if result in ("running", "already-running", "started") else 1
    if action == "stop":
        result = stop_dashboard(store_dir)
        print(f"dashboard: {result}")
        return 0 if result == "stopped" else 1
    print(f"unknown dashboard action: {action}", file=sys.stderr)
    return 2


def _install_service() -> int:
    try:
        kind, target, text = service_install_plan()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"This will install a {kind} service that keeps `hermes dashboard` running:")
    print(f"  file: {target}")
    print("  --- unit preview ---")
    print(text.rstrip())
    print("  --- end preview ---")
    print("It will NOT change any dashboard bind address or Hermes setting.")
    answer = input("Install this service? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted. Nothing was written.")
        return 0
    from pathlib import Path

    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"Wrote {target}")
    import subprocess

    if kind == "launchd":
        cmd = ["launchctl", "load", target]
        manual = f"launchctl load {target}"
    else:
        cmd = ["systemctl", "--user", "enable", "--now",
               "hermes-dashboard.service"]
        manual = "systemctl --user enable --now hermes-dashboard.service"
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("Service enabled.")
    except Exception:
        print("Could not enable automatically. Run this yourself:")
        print(f"  {manual}")
    return 0


def register_cli(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="companion_action")
    pair = subs.add_parser("pair", help="Create a pairing offer and print its QR code")
    pair.set_defaults(_handler=lambda a: _print_pair())
    qr = subs.add_parser("qr", help="Regenerate a pairing QR with current addresses")
    qr.set_defaults(_handler=lambda a: _print_pair())
    devices = subs.add_parser("devices", help="List paired devices")
    devices.set_defaults(_handler=lambda a: _print_devices())
    revoke = subs.add_parser("revoke", help="Revoke a paired device")
    revoke.add_argument("device_id")
    revoke.set_defaults(_handler=lambda a: _revoke(a.device_id))
    dash = subs.add_parser("dashboard", help="Dashboard supervision: status|start|stop")
    dash.add_argument("action", choices=["status", "start", "stop"])
    dash.set_defaults(_handler=lambda a: _dashboard(a.action))
    install = subs.add_parser("install-service",
                              help="Install a launchd/systemd unit for the dashboard")
    install.set_defaults(_handler=lambda a: _install_service())


def companion_command(args) -> int:
    handler = getattr(args, "_handler", None)
    if handler is None:
        print("usage: hermes companion {pair|qr|devices|revoke|dashboard|install-service}",
              file=sys.stderr)
        return 2
    try:
        return int(handler(args) or 0)
    except Exception as exc:  # CLI must fail with a message, never a traceback
        print(f"companion: {exc}", file=sys.stderr)
        return 1
