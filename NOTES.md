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
