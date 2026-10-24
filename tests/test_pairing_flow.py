"""Throwaway functional test for hermes-companion-pair v1.

Covers: offer create -> redeem success -> replay rejected -> expired
rejected -> 5-failed-attempts burn -> unknown/wrong-secret generic
rejection; auth provider password/token/session flows + revoke; QR
oracle check; root register() wiring with fake ctx/app; CLI pair/devices/
revoke; supervision basics. No live Hermes needed.
"""
import asyncio
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.expanduser("~/workspace/hermes-companion-pair"))

TMP = tempfile.mkdtemp(prefix="hcomp-test-")
os.environ["HERMES_COMPANION_PAIR_DATA_DIR"] = TMP
os.environ["HERMES_COMPANION_AUTOSTART_DASHBOARD"] = "0"

# Fake aiohttp (not installed here) so the route adapter can be tested.
class _FakeResponse:
    def __init__(self, data, status):
        self.data, self.status = data, status

class _FakeWeb:
    @staticmethod
    def json_response(data, status=200):
        return _FakeResponse(data, status)

_fake_aiohttp = SimpleNamespace(web=_FakeWeb)
sys.modules["aiohttp"] = _fake_aiohttp
sys.modules["aiohttp.web"] = _FakeWeb

from companion_pairing import auth_provider as ap_mod  # noqa: E402
from companion_pairing import cli as cli_mod  # noqa: E402
from companion_pairing import offers as offers_mod  # noqa: E402
from companion_pairing import redeem as redeem_mod  # noqa: E402
from companion_pairing import supervision as sup_mod  # noqa: E402
from companion_pairing.gateway_config import GatewayInfo  # noqa: E402
from companion_pairing.qr import encode_matrix  # noqa: E402
from companion_pairing.store import Store  # noqa: E402
import companion_pairing  # noqa: E402,F401
import importlib  # noqa: E402
root_init = importlib.import_module("__init__") if False else None

PASS = []
def check(name, cond):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)

store = Store()
NOW = 1761247000
GW = GatewayInfo(gateway_key="test-gateway-key-1234567890", gateway_port=8642,
                 dashboard_port=9119, dashboard_host="127.0.0.1",
                 host_name="testhost")

# 1. offer create
routes = [{"kind": "lan", "url": "http://192.168.1.42:9119",
           "gateway_url": "http://192.168.1.42:8642"}]
payload, secret = offers_mod.create_offer(store, "testhost", routes,
                                          gateway_port=8642, now=NOW)
check("offer payload shape", payload["v"] == 1
      and payload["type"] == "hermes-companion-pair"
      and payload["redeem"] == {"mode": "gateway", "path": "/companion/redeem", "port": 8642}
      and len(payload["secret"]) == 43
      and payload["expires_at"] == NOW + 300)
check("secret stored hashed only", store.get_offer(payload["pair_id"])["secret_hash"] != secret)

# 2. redeem success
resp = redeem_mod.redeem_offer(store, payload["pair_id"], secret, "Rishab's S24 Ultra",
                               GW, request_host="192.168.1.42:8642",
                               gateway_url="http://192.168.1.42:8642", now=NOW + 10)
check("redeem success fields", resp["device_id"] and len(resp["device_credential"]) == 43
      and resp["gateway_token_scope"] == "global"
      and resp["gateway_key"] == "test-gateway-key-1234567890"
      and resp["dashboard_url"] == "http://192.168.1.42:9119")
DEV, CRED = resp["device_id"], resp["device_credential"]

# 3. replay -> pairing_used
try:
    redeem_mod.redeem_offer(store, payload["pair_id"], secret, "x", GW, now=NOW + 20)
    check("replay rejected pairing_used", False)
except redeem_mod.RedeemError as e:
    check("replay rejected pairing_used", e.code == "pairing_used")

# 4. expired -> pairing_expired
p2, s2 = offers_mod.create_offer(store, "testhost", routes, now=NOW)
try:
    redeem_mod.redeem_offer(store, p2["pair_id"], s2, "x", GW, now=NOW + 301)
    check("expired rejected pairing_expired", False)
except redeem_mod.RedeemError as e:
    check("expired rejected pairing_expired", e.code == "pairing_expired")

# 5. five failed attempts -> burn -> pairing_invalid afterwards
p3, s3 = offers_mod.create_offer(store, "testhost", routes, now=NOW)
codes = []
for _ in range(5):
    try:
        redeem_mod.redeem_offer(store, p3["pair_id"], "wrong-secret-xxxxxxxxxxxxxxxxxxxxxxxxx", "x", GW, now=NOW)
    except redeem_mod.RedeemError as e:
        codes.append(e.code)
try:
    redeem_mod.redeem_offer(store, p3["pair_id"], s3, "x", GW, now=NOW)
    codes.append("no-error")
except redeem_mod.RedeemError as e:
    codes.append(e.code)
check("5 fails burn (all pairing_invalid, offer gone)",
      codes == ["pairing_invalid"] * 6 and store.get_offer(p3["pair_id"]) is None)

# 6. unknown pair_id + wrong secret -> pairing_invalid (no oracle)
for pid, sec in [("no-such-id", s3), (p2["pair_id"], "wrong-secret-xxxxxxxxxxxxxxxxxxxxxxxxx")]:
    try:
        redeem_mod.redeem_offer(store, pid, sec, "x", GW, now=NOW)
        check(f"generic pairing_invalid for {pid[:12]}", False)
    except redeem_mod.RedeemError as e:
        check(f"generic pairing_invalid for {pid[:12]}", e.code == "pairing_invalid")

# 7. auth provider flows
prov = ap_mod.CompanionAuthProvider(store_factory=lambda: Store())
sess = prov.complete_password_login(username=DEV, password=CRED)
check("password login mints session", sess.provider == "companion"
      and sess.user_id == f"device:{DEV}" and sess.access_token and sess.refresh_token)
check("verify_session roundtrip", prov.verify_session(access_token=sess.access_token) is not None)
check("verify_token roundtrip", prov.verify_token(token=CRED) is not None
      and prov.verify_token(token=CRED).principal == f"device:{DEV}")
try:
    prov.complete_password_login(username=DEV, password="bad-credential-xxxxxxxxxxxxxxxxxxxx")
    check("bad credential rejected", False)
except ap_mod.InvalidCredentialsError:
    check("bad credential rejected", True)
check("verify_token unknown -> None", prov.verify_token(token="nope-nope-nope") is None)
sess2 = prov.refresh_session(refresh_token=sess.refresh_token)
check("refresh rotates access", sess2.access_token != sess.access_token)
prov.revoke_session(refresh_token=sess.refresh_token)
check("revoke_session kills session", prov.verify_session(access_token=sess2.access_token) is None)
check("device revoke kills token seam",
      (store.revoke_device(DEV), prov.verify_token(token=CRED))[1] is None)

# 8. QR oracle (one realistic payload; skipped if qrcode isn't installed)
try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    from qrcode.util import QRData, MODE_8BIT_BYTE
    _HAS_QRCODE = True
except ImportError:
    _HAS_QRCODE = False
def strip(m):
    rows = [r[:] for r in m]
    while rows and not any(rows[0]): rows.pop(0)
    while rows and not any(rows[-1]): rows.pop()
    while rows and not any(r[0] for r in rows): rows = [r[1:] for r in rows]
    while rows and not any(r[-1] for r in rows): rows = [r[:-1] for r in rows]
    return rows
if _HAS_QRCODE:
    data = offers_mod.payload_to_json(payload).encode()
    mine = encode_matrix(data)
    q = qrcode.QRCode(error_correction=ERROR_CORRECT_M, mask_pattern=0)
    q.add_data(QRData(data, mode=MODE_8BIT_BYTE)); q.make()
    ref = strip(q.get_matrix())
    check("QR matrix matches reference lib",
          len(mine) == len(ref) and all(a == b for ra, rb in zip(mine, ref) for a, b in zip(ra, rb)))
else:
    check("QR oracle skipped (qrcode not installed)", True)

# 9. root register() wiring with fake ctx + fake aiohttp app
import importlib.util
spec = importlib.util.spec_from_file_location(
    "hcomp_root", os.path.expanduser("~/workspace/hermes-companion-pair/__init__.py"),
    submodule_search_locations=[os.path.expanduser("~/workspace/hermes-companion-pair")])
root = importlib.util.module_from_spec(spec)
spec.loader.exec_module(root)

class FakeRouter:
    def __init__(self): self.routes = []
    def add_post(self, path, handler): self.routes.append((path, handler))

class FakeApp:
    def __init__(self): self.router = FakeRouter()

class FakeCtx:
    def __init__(self):
        self.platform = []; self.auth = []; self.cli = []
    def register_platform_handler(self, name, factory): self.platform.append((name, factory))
    def register_dashboard_auth_provider(self, provider): self.auth.append(provider)
    def register_cli_command(self, name, help_, setup, handler): self.cli.append((name, setup, handler))

ctx = FakeCtx()
root.register(ctx)
check("register() wires platform+auth+cli",
      ctx.platform and ctx.platform[0][0] == "api_server"
      and len(ctx.auth) == 1 and ctx.auth[0].name == "companion"
      and ctx.cli and ctx.cli[0][0] == "companion")
app1, app2 = FakeApp(), FakeApp()
factory = ctx.platform[0][1]
factory(app1, None); factory(app1, None); factory(app2, None)
check("WeakSet guard: same app wired once, second app wired",
      len(app1.router.routes) == 1 and len(app2.router.routes) == 1
      and app1.router.routes[0][0] == "/companion/redeem")

class FakeRequest:
    def __init__(self, body, host="192.168.1.42:8642"):
        self._body, self.host = body, host
    async def json(self): return self._body

async def run_handler():
    # NOTE: the aiohttp handler uses wall-clock time, so these offers are
    # created with real now (unlike the fixed-NOW cases above).
    p4, s4 = offers_mod.create_offer(store, "testhost", routes)
    handler = app1.router.routes[0][1]
    ok = await handler(FakeRequest({"pair_id": p4["pair_id"], "secret": s4,
                                    "device_name": "Handler Phone"}))
    # Replay of a consumed offer -> pairing_used (used is checked first).
    replay = await handler(FakeRequest({"pair_id": p4["pair_id"], "secret": "zzz",
                                        "device_name": "x"}))
    # Wrong secret on a FRESH offer -> generic pairing_invalid.
    p5, _s5 = offers_mod.create_offer(store, "testhost", routes)
    bad = await handler(FakeRequest({"pair_id": p5["pair_id"], "secret": "zzz",
                                     "device_name": "x"}))
    return ok, replay, bad
ok_resp, replay_resp, bad_resp = asyncio.run(run_handler())
check("aiohttp handler success + replay used + fresh wrong-secret invalid",
      ok_resp.status == 200 and ok_resp.data["device_id"]
      and replay_resp.status == 400 and replay_resp.data == {"error": "pairing_used"}
      and bad_resp.status == 400 and bad_resp.data == {"error": "pairing_invalid"})

# 10. CLI pair/devices/revoke (stdout capture)
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = cli_mod._print_pair()
out = buf.getvalue()
check("CLI pair prints QR + payload", rc == 0 and "pair_id" in out and "██" in out)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = cli_mod._print_devices()
check("CLI devices lists", rc == 0 and "Handler Phone" in buf.getvalue())
devs = store.list_devices()
rc = cli_mod._revoke(devs[0]["device_id"])
check("CLI revoke works", rc == 0 and store.get_device(devs[0]["device_id"]) is None)

# 11. supervision basics
check("probe closed port False", sup_mod.probe_dashboard("127.0.0.1", 59991, timeout=0.5) is False)
check("autostart disabled via env", sup_mod.ensure_dashboard(store.data_dir) == "disabled")
check("stop with no pidfile -> not-managed", sup_mod.stop_dashboard(store.data_dir) == "not-managed")

print()
failed = [n for n, ok in PASS if not ok]
print(f"{len(PASS) - len(failed)}/{len(PASS)} checks passed")
if failed:
    print("FAILED:", failed); sys.exit(1)
