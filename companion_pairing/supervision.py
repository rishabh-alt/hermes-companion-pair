"""Dashboard supervision (spec section 5.6): the dashboard must simply
*be there*, because the companion app is built on dashboard APIs.

Rules, all load-bearing:
  * Probe before spawn; a dashboard that already answers (including one
    the user started by hand) is left completely alone.
  * Spawn ``hermes dashboard`` detached with the user's *existing*
    configuration only when the probe fails. Never change the bind
    address or any dashboard setting silently.
  * A pidfile in the plugin data dir records plugin-spawned dashboards
    so ``stop`` only ever kills what the plugin started.
  * Every auto-start is logged. Opt out with
    ``companion.autostart_dashboard: false`` in config (or the
    ``HERMES_COMPANION_AUTOSTART_DASHBOARD=0`` env var).
  * The OS-service path (launchd / systemd) is offered via
    ``install-service`` but never enabled silently: the operator sees
    the plan, confirms, and gets manual fallback instructions either way.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import socket
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PIDFILE_NAME = "dashboard.pid"
AUTOSTART_LOG_NAME = "dashboard-autostart.log"


def autostart_disabled(config_text: str = "") -> bool:
    """True when the operator opted out. Checks config text (both the
    top-level ``companion:`` section and the plugin's own section) and
    the env var. A regex scan, not a YAML parse: config layout varies
    across versions and a missed opt-out must fail open to *not*
    spawning, never the reverse."""
    if os.environ.get("HERMES_COMPANION_AUTOSTART_DASHBOARD", "") in (
            "0", "false", "no", "off"):
        return True
    if config_text and re.search(
            r"autostart_dashboard\s*:\s*(false|no|off)\b",
            config_text, re.IGNORECASE):
        return True
    return False


def read_hermes_config_text() -> str:
    candidates = []
    if os.environ.get("HERMES_HOME"):
        candidates.append(Path(os.environ["HERMES_HOME"]) / "config.yaml")
    candidates.append(Path.home() / ".hermes" / "config.yaml")
    for path in candidates:
        try:
            if path.is_file():
                return path.read_text()
        except Exception:
            continue
    return ""


def dashboard_endpoint(config_text: str = "") -> tuple[str, int]:
    """Configured dashboard host/port, best-effort. Defaults are
    Hermes' own (127.0.0.1:9119) — used only for probing and for a
    bare ``hermes dashboard`` spawn, never written back anywhere."""
    host, port = "127.0.0.1", 9119
    if not config_text:
        config_text = read_hermes_config_text()
    for line in config_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dashboard_host:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            if value:
                host = value
        elif stripped.startswith("dashboard_port:"):
            try:
                port = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                pass
    if os.environ.get("HERMES_DASHBOARD_HOST"):
        host = os.environ["HERMES_DASHBOARD_HOST"]
    if os.environ.get("HERMES_DASHBOARD_PORT"):
        try:
            port = int(os.environ["HERMES_DASHBOARD_PORT"])
        except ValueError:
            pass
    return host, port


def probe_dashboard(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_pidfile(data_dir: Path) -> int | None:
    try:
        text = (data_dir / PIDFILE_NAME).read_text().strip()
        return int(text) if text else None
    except Exception:
        return None


def dashboard_status(data_dir: Path) -> dict:
    host, port = dashboard_endpoint()
    pid = _read_pidfile(data_dir)
    return {
        "host": host,
        "port": port,
        "reachable": probe_dashboard(host, port),
        "managed_pid": pid,
        "managed_alive": bool(pid and _pid_alive(pid)),
        "autostart_disabled": autostart_disabled(read_hermes_config_text()),
    }


def ensure_dashboard(data_dir: Path, force: bool = False) -> str:
    """Ensure-on-start. Returns one of: 'disabled', 'running',
    'already-running', 'started', 'skipped' (spawn failed; details logged).
    ``force=True`` is the explicit ``dashboard start`` operator command:
    it bypasses the autostart opt-out (an explicit start is not an
    auto-start)."""
    if not force and autostart_disabled(read_hermes_config_text()):
        return "disabled"
    host, port = dashboard_endpoint()
    if probe_dashboard(host, port):
        return "running"
    pid = _read_pidfile(data_dir)
    if pid and _pid_alive(pid):
        # A plugin-spawned dashboard that does not answer the probe yet
        # (starting up, or bound elsewhere): do not spawn a second one.
        return "already-running"
    try:
        log_path = data_dir / AUTOSTART_LOG_NAME
        log_file = open(log_path, "ab")
        proc = subprocess.Popen(
            ["hermes", "dashboard"],
            stdout=log_file, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True,
        )
        (data_dir / PIDFILE_NAME).write_text(str(proc.pid))
        logger.info(
            "[hermes-companion-pair] dashboard auto-started pid=%s"
            " (host=%s port=%s, log=%s)",
            proc.pid, host, port, log_path,
        )
        return "started"
    except Exception:
        logger.exception(
            "[hermes-companion-pair] dashboard auto-start failed")
        return "skipped"


def stop_dashboard(data_dir: Path) -> str:
    """Stop ONLY a plugin-spawned dashboard. Refuses to touch a
    manually started one ('not-managed')."""
    pid = _read_pidfile(data_dir)
    if pid is None:
        return "not-managed"
    try:
        if _pid_alive(pid):
            os.kill(pid, signal.SIGTERM)
            logger.info(
                "[hermes-companion-pair] dashboard stopped pid=%s", pid)
    finally:
        try:
            (data_dir / PIDFILE_NAME).unlink()
        except Exception:
            pass
    return "stopped"


# -- OS service units -----------------------------------------------------

def launchd_plist_text() -> str:
    hermes_bin = _which_hermes()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!-- hermes-companion-pair: keeps `hermes dashboard` running for phone pairing.
     Installed by `hermes companion install-service` only after operator
     confirmation. Remove with: launchctl unload ~/Library/LaunchAgents/com.hermes.dashboard.plist -->
<plist version="1.0">
<dict>
  <key>Label</key><string>com.hermes.dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>{hermes_bin}</string>
    <string>dashboard</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{Path.home()}/.hermes/dashboard-service.log</string>
  <key>StandardErrorPath</key><string>{Path.home()}/.hermes/dashboard-service.log</string>
</dict>
</plist>
"""


def systemd_unit_text() -> str:
    hermes_bin = _which_hermes()
    return f"""# hermes-companion-pair: keeps `hermes dashboard` running for phone pairing.
# Installed by `hermes companion install-service` only after operator
# confirmation. Remove with: systemctl --user disable --now hermes-dashboard.service
[Unit]
Description=Hermes dashboard (for Companion phone pairing)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={hermes_bin} dashboard
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _which_hermes() -> str:
    import shutil

    found = shutil.which("hermes")
    return found or "hermes"


def service_install_plan() -> tuple[str, str, str]:
    """Return (kind, target_path, unit_text) for this OS, or raise."""
    if sys.platform == "darwin":
        target = (Path.home() / "Library" / "LaunchAgents"
                  / "com.hermes.dashboard.plist")
        return "launchd", str(target), launchd_plist_text()
    if sys.platform.startswith("linux"):
        target = (Path.home() / ".config" / "systemd" / "user"
                  / "hermes-dashboard.service")
        return "systemd", str(target), systemd_unit_text()
    raise RuntimeError(f"install-service is not supported on {sys.platform}")
