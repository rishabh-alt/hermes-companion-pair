# hermes-companion-pair — build notes

## Step-0 spike results (2026-10-23)

Sources: NousResearch/hermes-agent source (current main), the production peer
plugin tawaresachin/hermes-mobile-plugin v0.0.38 (source read in full at the
package level), Nous plugin/dashboard docs. Live probe of the target dashboard
was attempted but the host was unreachable at spike time — install-time
verification on Hermes 0.22.2 remains the final proof for Q1.

### Q1: Can a plugin add routes to the gateway api_server? — YES. Mechanism D confirmed.

- The hook is `ctx.register_platform_handler("api_server", factory)`. The
  factory receives the api_server's aiohttp `web.Application` ("native") plus
  the adapter, and the plugin registers its own routes directly on the app —
  i.e. on the gateway, port 8642, outside the dashboard auth gate.
- Production-proven: the peer plugin mounts its STT/TTS, system, commands,
  skills, update and providers routes exactly this way.
- Wiring must be guarded per app instance (peer's WeakSet pattern): a second
  connect of the same app must not double-register; a fresh app must.
- Auth shape: no global auth middleware exists in
  `gateway/platforms/api_server.py`; core routes validate `API_SERVER_KEY`
  per-handler. Plugin-registered routes therefore control their own auth —
  the redeem route validates `pair_id` + one-time secret itself
  (timing-safe compare). No API key is needed pre-pairing.

### Q2: Can the gateway issue per-device keys? — NO. Spec §6.1 item 6 resolved.

- `API_SERVER_KEY` is a single key per profile
  (`gateway/config.py:_has_usable_api_server_key`, min length 16; the adapter
  holds one `self._api_key`). There is no multi-key or per-device key system.
- Decision (option b, stated openly, never silent): redeem returns the host's
  gateway key with `gateway_token_scope: "global"` — the companion app
  already models this field. Per-device revocation lives at the
  pairing/dashboard layer; rotating `API_SERVER_KEY` is the host-wide kill
  switch. This limitation goes in the README in plain words.

### Q3: Can a paired device get a dashboard session without the password? — YES. Mechanism B confirmed.

- `hermes_cli/dashboard_auth/` is a real provider framework:
  `DashboardAuthProvider` protocol (`Session`, `TokenPrincipal`, `LoginStart`,
  typed errors), providers stack and are listed on `/login`, registered via
  `ctx.register_dashboard_auth_provider()`.
- The existing "basic" provider ships inside this framework
  (`plugins/dashboard_auth/`) — the same provider the target install runs.
- Plan: register a `companion` provider whose login validates an issued
  per-device credential against the plugin store and returns a session.
  The gateway and dashboard processes share state through the plugin data
  directory (SQLite), the same way the peer shares files via its plugin dir.

### Q4: Can redemption ride dashboard plugin routes? — NO (as documented; not needed).

- Dashboard plugin API routes sit behind the dashboard's auth gate
  (unauthenticated → 401 before the plugin route runs). Dashboard tab
  endpoints are therefore owner-facing only: create offer, list devices,
  revoke. Redemption never touches the dashboard.

## Chosen architecture

- **Offer creation** (owner, authenticated): dashboard Companion tab or
  `hermes companion pair` CLI → offer `{pair_id, secret_hash, expires_at}`
  in the plugin store; QR payload per spec §7 with `redeem.mode = "gateway"`
  and the redeem path on the gateway (8642). Bind check at creation: warn in
  the UI if Hermes is loopback-only; never rebind silently.
- **Redeem** (phone, pre-auth): POST pair_id + secret + device name to the
  gateway route (Mechanism D) → validate hash, 300 s TTL, single-use burn,
  ≤5 failed attempts → mint per-device credential → respond with device id +
  credential, gateway URL + key (`gateway_token_scope: "global"`), dashboard
  URL, host version info. The app then establishes its dashboard session via
  the companion auth provider (Mechanism B) using the device credential.
- **Supervision** (spec §5.6): ensure-on-start for the dashboard, modelled on
  the peer's `supervisor.ensure_running()` watchdog pattern (theirs watches
  the gateway; ours watches the dashboard): probe-before-spawn, pidfile in
  plugin data dir, never fight a manually started dashboard, log every
  auto-start, config opt-out.
- **Peer lessons kept**: QR artifacts on disk get 0600 perms; headless hosts
  print the payload/path instead of opening a browser; regenerate command
  for address changes. Peer's security model (long-lived key inside the QR)
  is NOT adopted — ours stays one-time secret → per-device credential.

## v1 implemented (2026-10-24)

- Store: SQLite in the plugin data dir (`offers`, `devices`, `sessions`
  tables); secrets hashed at rest, never logged.
- Redeem: `POST /companion/redeem` on the gateway api_server via
  `ctx.register_platform_handler("api_server", ...)` (Mechanism D).
- Dashboard sessions: `CompanionAuthProvider` (`companion`) via
  `ctx.register_dashboard_auth_provider()` — password seam
  (device_id + credential -> session) and token seam (bearer credential
  -> TokenPrincipal). Framework shapes follow
  `hermes_cli/dashboard_auth/base.py`; if that module is missing on an
  older host, local dataclasses with identical field names are used and
  registration degrades to a warning, never a crash.
- CLI: `hermes companion pair|qr|devices|revoke|dashboard|install-service`.
- Dashboard tab: `dashboard/plugin_api.py` (FastAPI router) +
  hand-written vanilla-JS IIFE `dashboard/dist/index.js` (no build step)
  + `dist/style.css` (theme vars only).
- Dashboard API mount follows the manifest name:
  `/api/plugins/hermes-companion-pair/*` (spec section 5.2's
  `/api/plugins/companion-pair/` shorthand is superseded by the
  `<name>` rule and the shipped achievements example).
- Dashboard tab path is `/companion-pair` at position `after:skills`
  (spec section 5.1); if the tab does not appear on a host, the anchor
  is the first thing to check.
- Redeem error codes: unknown pair_id and wrong secret both answer
  `pairing_invalid` (no oracle); already-redeemed answers
  `pairing_used`; expired answers `pairing_expired`. The used/expired
  states describe the *offer*, not the secret, and match the companion
  app's section 8.5 messages.
- QR encoder is hand-rolled (byte mode, EC level M, fixed mask 0) and
  verified module-for-module against the reference `qrcode` library on
  8 payloads including a realistic 463-byte pairing payload.
- NOT yet verified without a live Hermes (tested on the user's Mac
  next): redeem route answering on a real gateway, auth provider
  session flow through the real dashboard middleware, tab rendering
  inside the real dashboard (the IIFE's register/mount assumptions are
  documented in the file), supervision spawn on a real gateway start.

## v2 — desktop sidebar page (2026-10-25)

- New file: `desktop/plugin.js` (single hand-written file, no npm
  toolchain, no build step — same rule as the web dashboard tab).
  It renders the Companion pairing UI natively in the Hermes desktop
  app: Pair button, QR display, countdown, copy-payload, device list
  with revoke, dashboard status line. Behavior mirrors
  `dashboard/dist/index.js`; the web tab stays as the fallback for
  headless/web users.
- Mechanism, proven from the peer plugin PIR8-Software/Hermes-OR-Picker
  (`catalog/desktop/plugin.js`, 71KB): a JS module importing
  ROUTES_AREA, SIDEBAR_NAV_AREA, PALETTE_AREA, host from
  '@hermes/plugin-sdk' plus { jsx, jsxs } from 'react/jsx-runtime'
  and hooks from 'react', with a default export { id, name,
  defaultEnabled: true, register(ctx) { ctx.registerMany([...]) } }.
  Our page registers: page at path '/companion-pair' (same path as
  the web tab), sidebar entry { label: 'Companion', codicon: 'key' },
  palette command 'Open Companion Pairing' (host.navigate(PAGE)).
- Backend calls use ctx.rest with plugin-relative paths, exactly the
  OR Picker convention (its api() helper: ctx.rest(path, { method,
  timeoutMs, body }); call sites use '/models', '/credits', …).
  Our page calls '/offers' (POST), '/devices' (GET),
  '/devices/{id}' (DELETE), '/dashboard-status' (GET) — the same
  routes dashboard/plugin_api.py already serves. One honest gap:
  whether the desktop SDK's ctx.rest proxy targets the dashboard
  plugin_api mount is inferred from OR Picker's shape, not proven
  against a live host; if the page's calls 404 on first run, the fix
  is centralized in the file's api() helper (one line).
- Discovery (from OR Picker's install.sh, NOT assumed): the desktop
  app loads desktop plugins from
  $HERMES_HOME/desktop-plugins/<plugin-id>/plugin.js, and the user
  reloads via Cmd/Ctrl+K → Reload desktop plugins. `hermes plugins
  update` does NOT install the sidebar page; activation needs:
    mkdir -p ~/.hermes/desktop-plugins/hermes-companion-pair
    cp desktop/plugin.js ~/.hermes/desktop-plugins/hermes-companion-pair/plugin.js
  Whether the desktop app ALSO auto-discovers a desktop/ entry inside
  the installed plugin directory is UNCONFIRMED.
- QR square guarantee (the web tab's unscannable QR is the open bug
  this page must not repeat): the backend SVG already has a square
  viewBox and white background rect; this page additionally forces
  equal width/height attributes (320×320) and equal CSS dimensions,
  pins preserveAspectRatio, renders inside a fixed 320×320 box on a
  white background, and applies no CSS filter near the element.
  The web tab's QR bug is deliberately NOT fixed here — it stays
  open pending the user's answer about which surface produced it.
- Verification in the VM: node --check passes (the file uses
  jsx()/jsxs() calls, zero JSX angle-bracket literals, so the check
  is a real syntax pass); braces/parens/brackets balanced; all three
  registration areas present with consistent paths/labels; zero raw
  fetch calls (ctx.rest only); zero console.* lines (no secret
  logging). NOT verified without a real desktop app (no
  @hermes/plugin-sdk in this VM): page load, sidebar entry
  appearance, ctx.rest routing, QR scannability — all belong to the
  live test on the user's Mac.

## Web tab blank fix (2026-10-25)

- Symptom on the user's Mac: the web dashboard Companion tab was
  registered in navigation but rendered blank.
- Root cause: dashboard/dist/index.js registered an object
  { mount, render } with window.__HERMES_PLUGINS__ and fell back to
  searching for container elements the dashboard never creates. The
  mount contract was inferred, not verified.
- Verified contract from a working dashboard tab plugin
  (stainlu/hermes-labyrinth, dashboard/dist/index.js, line 4290):
  window.__HERMES_PLUGINS__.register(name, Component) with a React
  component, React taken from window.__HERMES_PLUGIN_SDK__.React.
  dashboard/dist/index.js was rewritten to that contract (React,
  no JSX, no build step; same UI, API base, and class names).
  node --check passes. Live tab rendering still belongs to the
  user's Mac test (hard-refresh the dashboard tab after update).

## install.sh (2026-10-25, corrected from Argon's draft)

- Argon's draft copied core/, cli/, gateway/ — directories that do
  not exist in this repo — and never installed companion_pairing/
  (the real package: cli.py, gateway_config.py, redeem.py, store.py,
  etc.). Installed from that draft, the plugin would be broken on
  arrival. The committed install.sh installs the real layout:
  plugin.yaml, __init__.py, LICENSE, companion_pairing/,
  dashboard/; desktop/plugin.js goes only to desktop-plugins/.
- Argon's draft also rewrote config.yaml via a YAML load/dump with
  no backup. The committed script does NOT rewrite configs (Hermes
  owns that schema); it prints the `hermes plugins enable` step and
  the secondary-profile check instead.
- install.sh is safe to run from a repo clone OR from the install
  dir itself (after `hermes plugins update`): same-file copies are
  skipped, and the desktop page is copied BEFORE the agent-side
  desktop/ dir is removed. bash -n clean, ASCII only.
- Post-update caveat (do not forget): `hermes plugins update`
  re-syncs the repo (including desktop/) into the install dir, so
  re-run install.sh after every update or the marker trap returns.
