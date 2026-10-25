#!/usr/bin/env bash
# hermes-companion-pair installer.
#
# Why this script exists (not just `hermes plugins install`):
# If desktop/plugin.js ever lands inside
# ~/.hermes/plugins/hermes-companion-pair/desktop/, the Desktop app's
# unified-package reconciler stamps
# ~/.hermes/desktop-plugins/hermes-companion-pair/.hermes-desktop-plugin.json,
# which forces the sidebar entry to opt-in (defaultEnabled: false) and
# hides it -- even though desktop/plugin.js says defaultEnabled: true.
# This script installs the two halves in their correct places and
# removes that trap.
#
# Usage (either works):
#   From a repo clone:   bash install.sh
#   From the installed plugin dir (after `hermes plugins update`):
#                        bash ~/.hermes/plugins/hermes-companion-pair/install.sh
# After ANY future `hermes plugins update`, re-run this script:
# updates re-sync the repo (including desktop/) into the install dir,
# which re-arms the marker trap.
#
# This script deliberately does NOT rewrite config.yaml. Hermes owns
# that schema, and YAML load/dump round-trips can mangle it. Enablement
# is left to the `hermes plugins enable` command (see the notes at
# the end). Back up any config file before hand-editing it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_NAME="hermes-companion-pair"
AGENT_PLUGIN_DIR="$HERMES_HOME/plugins/$PLUGIN_NAME"
DESKTOP_PLUGIN_DIR="$HERMES_HOME/desktop-plugins/$PLUGIN_NAME"

mkdir -p "$AGENT_PLUGIN_DIR" "$DESKTOP_PLUGIN_DIR"

copy_file() {
  # copy_file <src-file> <dst-dir>; skips when src and dst are the
  # same file (install-dir mode, where SCRIPT_DIR == AGENT_PLUGIN_DIR).
  local src="$1" dst_dir="$2" dst
  dst="$dst_dir/$(basename "$1")"
  if [ -e "$src" ]; then
    if [ -e "$dst" ] && [ "$src" -ef "$dst" ]; then
      echo "Already in place: $dst"
    else
      cp "$src" "$dst"
      echo "Installed $dst"
    fi
  fi
}

copy_dir() {
  # copy_dir <src-dir> <dst-parent>; replaces the dest dir, skips
  # when src and dst are the same directory (install-dir mode).
  local src="$1" parent="$2" dst
  dst="$parent/$(basename "$src")"
  if [ -d "$src" ]; then
    if [ -e "$dst" ] && [ "$src" -ef "$dst" ]; then
      echo "Already in place: $dst"
    else
      rm -rf "$dst"
      cp -R "$src" "$dst"
      find "$dst" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
      echo "Installed $dst"
    fi
  fi
}

# 1. Desktop sidebar page FIRST (before step 2 removes the agent-side
# desktop/ dir -- in install-dir mode that dir is also the source).
if [ -f "$SCRIPT_DIR/desktop/plugin.js" ]; then
  cp "$SCRIPT_DIR/desktop/plugin.js" "$DESKTOP_PLUGIN_DIR/plugin.js"
  echo "Installed $DESKTOP_PLUGIN_DIR/plugin.js"
else
  echo "WARNING: $SCRIPT_DIR/desktop/plugin.js not found; sidebar page not installed" >&2
fi
rm -f "$DESKTOP_PLUGIN_DIR/.hermes-desktop-plugin.json"

# 2. Agent/dashboard/CLI/gateway files. desktop/ is deliberately NOT
# copied into AGENT_PLUGIN_DIR (see the header comment), and any
# copy left there by a previous install/update is removed.
copy_file "$SCRIPT_DIR/plugin.yaml" "$AGENT_PLUGIN_DIR"
copy_file "$SCRIPT_DIR/__init__.py" "$AGENT_PLUGIN_DIR"
copy_file "$SCRIPT_DIR/LICENSE" "$AGENT_PLUGIN_DIR"
copy_dir "$SCRIPT_DIR/companion_pairing" "$AGENT_PLUGIN_DIR"
copy_dir "$SCRIPT_DIR/dashboard" "$AGENT_PLUGIN_DIR"
rm -rf "$AGENT_PLUGIN_DIR/desktop"

cat <<'EOF'

Installed hermes-companion-pair.

Enablement (not done by this script -- Hermes owns config schema):
  hermes plugins enable hermes-companion-pair
If you use secondary profiles, make sure it is enabled there too
(check `hermes plugins --help` for a profile flag, or inspect
~/.hermes/profiles/*/config.yaml plugins.enabled). Back up any
config file before hand-editing it.

Then, to mount the backend routes:
  1. Fully quit Hermes Desktop (Cmd+Q) and relaunch it. A Cmd+K
     reload alone does not remount backend routes.
  2. If the Companion sidebar entry is missing, Cmd+K -> Reload
     desktop plugins, or toggle it in Capabilities -> Plugins.
  3. Open Companion -> Pair new device, and scan with the app.

After any future `hermes plugins update`, re-run this script:
updates re-sync desktop/ into the install dir and re-arm the
marker trap this script removes.
EOF
