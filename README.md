# Hermes Companion Pair

One-scan QR pairing for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

Connecting a phone to a self-hosted Hermes currently means finding two
addresses, copying a gateway token by hand, and signing in to the dashboard
separately. This plugin replaces all of that with one QR scan:

1. Open the **Companion** tab in your Hermes dashboard (or run
   `hermes companion pair`).
2. Scan the QR code with the Hermes Companion app.
3. Done — the phone receives a **per-device credential** you can revoke at
   any time from the same tab.

No extra daemon, no cloud relay, no account. Public plugin APIs only.

## How it works

- The QR carries routes to your host plus a **one-time, 256-bit secret**
  (5-minute TTL, single use). The secret is never a credential itself.
- Redemption happens on the gateway via a plugin route; a successful scan
  mints a per-device credential (hashed at rest, shown once) and provisions
  both the gateway and the dashboard session in the same flow.
- The plugin also keeps the dashboard running (probe-before-spawn
  supervision, logged, with a config opt-out), so pairing works when you
  pick up your phone — not only right after you've started things by hand.

## One honest limitation

Hermes's gateway authenticates with a **single `API_SERVER_KEY`** — there is
no per-device key system in the gateway itself. A paired phone therefore
receives that key (its scope is reported to the app as `global`). Revoking a
device cuts off its pairing credential and dashboard session immediately;
rotating `API_SERVER_KEY` remains the host-wide kill switch for gateway
access. This is stated here rather than papered over.

## Install

```
hermes plugins install rishabh-alt/hermes-companion-pair
```

Then restart Hermes (plugin API routes mount at dashboard startup).
Requires Hermes 0.20.0–0.22.x.

## Companion app

Pairing is built for [Hermes Companion](https://github.com/rishabh-alt/hermes-mobile-companion)
(v1.0+, "Pair with QR code" on the connect screen). Manual address/token
entry keeps working as a fallback.

## License

MIT
