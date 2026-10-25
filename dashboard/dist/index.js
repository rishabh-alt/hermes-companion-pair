/* Hermes Companion Pair — dashboard tab UI (React, no JSX, no build step).
 *
 * Mount contract VERIFIED against a live working dashboard tab plugin
 * (stainlu/hermes-labyrinth, dashboard/dist/index.js): the dashboard
 * exposes window.__HERMES_PLUGINS__.register(name, Component) and
 * window.__HERMES_PLUGIN_SDK__.React, and renders the registered React
 * component itself. The previous version of this file registered an
 * object { mount, render } and fell back to searching for container
 * elements the dashboard never creates — that is why the tab rendered
 * blank. This version registers the component directly, exactly like
 * the working precedent.
 *
 * API calls go through window.__HERMES_PLUGIN_SDK__.fetchJSON when
 * present (it injects the session token), else fetch with credentials,
 * against /api/plugins/hermes-companion-pair (same base the working
 * precedent uses for its own plugin).
 *
 * The tab never handles secrets beyond displaying the QR payload the
 * owner just created (the payload's one-time secret is shown only in
 * this owner-authenticated tab and the operator's terminal).
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK && SDK.React;
  if (!React) return;
  var useState = React.useState;
  var useEffect = React.useEffect;
  var useRef = React.useRef;

  var API_BASE = "/api/plugins/hermes-companion-pair";
  var QR_SIZE = 320;

  function api(path, options) {
    var sdk = window.__HERMES_PLUGIN_SDK__;
    if (sdk && typeof sdk.fetchJSON === "function") {
      return sdk.fetchJSON(API_BASE + path, options || {});
    }
    var opts = options || {};
    opts.credentials = "include";
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    return fetch(API_BASE + path, opts).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (text) {
          throw new Error("HTTP " + res.status + ": " + text);
        });
      }
      return res.json();
    });
  }

  function injectSquareQr(box) {
    if (!box) return;
    var svgEl = box.querySelector("svg");
    if (!svgEl) return;
    svgEl.setAttribute("width", String(QR_SIZE));
    svgEl.setAttribute("height", String(QR_SIZE));
    svgEl.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svgEl.style.display = "block";
    svgEl.style.width = QR_SIZE + "px";
    svgEl.style.height = QR_SIZE + "px";
    svgEl.style.filter = "none";
  }

  function CompanionPage() {
    var devicesState = useState([]);
    var devices = devicesState[0];
    var setDevices = devicesState[1];
    var statusState = useState("Checking dashboard status…");
    var statusLine = statusState[0];
    var setStatusLine = statusState[1];
    var errorState = useState("");
    var error = errorState[0];
    var setError = errorState[1];
    var offerState = useState(null);
    var offer = offerState[0];
    var setOffer = offerState[1];
    var expiresState = useState(0);
    var expiresAt = expiresState[0];
    var setExpiresAt = expiresState[1];
    var leftState = useState(null);
    var secondsLeft = leftState[0];
    var setSecondsLeft = leftState[1];
    var pairingState = useState(false);
    var pairing = pairingState[0];
    var setPairing = pairingState[1];
    var pollingState = useState(false);
    var polling = pollingState[0];
    var setPolling = pollingState[1];
    var pairedState = useState("");
    var pairedMsg = pairedState[0];
    var setPairedMsg = pairedState[1];
    var knownIdsRef = useRef({});
    var qrRef = useRef(null);

    function loadDevices() {
      return api("/devices").then(function (data) {
        var list = data.devices || [];
        setDevices(list);
        return list;
      });
    }

    function refreshStatus() {
      api("/dashboard-status").then(function (status) {
        setStatusLine("Dashboard: " + (status.running ? "running" : "stopped") +
          " · Auto-start: " + (status.autostart_disabled ? "off" : "on"));
      }).catch(function () {
        setStatusLine("Dashboard status unavailable.");
      });
    }

    useEffect(function () {
      refreshStatus();
      loadDevices().catch(function (err) {
        setError("Could not load devices: " + err.message);
      });
    }, []);

    useEffect(function () {
      if (!expiresAt) return undefined;
      function tick() {
        setSecondsLeft(expiresAt - Math.floor(Date.now() / 1000));
      }
      tick();
      var timer = setInterval(tick, 1000);
      return function () { clearInterval(timer); };
    }, [expiresAt]);

    useEffect(function () {
      if (!polling) return undefined;
      var timer = setInterval(function () {
        loadDevices().then(function (list) {
          var fresh = list.filter(function (d) { return !knownIdsRef.current[d.id]; });
          if (fresh.length > 0) {
            setPairedMsg("Paired: " + (fresh[0].name || fresh[0].id));
            setPolling(false);
          }
        }).catch(function () {});
      }, 2000);
      return function () { clearInterval(timer); };
    }, [polling]);

    useEffect(function () {
      injectSquareQr(qrRef.current);
    }, [offer]);

    function onPair() {
      setError("");
      setPairedMsg("");
      setPairing(true);
      var known = {};
      devices.forEach(function (d) { known[d.id] = true; });
      knownIdsRef.current = known;
      api("/offers", { method: "POST" }).then(function (data) {
        setPairing(false);
        setOffer(data);
        setExpiresAt(data.expires_at || 0);
        if (data.bind_warning) {
          setError(data.bind_warning);
        }
        setPolling(true);
      }).catch(function (err) {
        setPairing(false);
        setError("Could not create an offer: " + err.message);
      });
    }

    function onRevoke(dev) {
      setError("");
      api("/devices/" + encodeURIComponent(dev.id), { method: "DELETE" }).then(function () {
        return loadDevices();
      }).catch(function (err) {
        setError("Could not revoke device: " + err.message);
      });
    }

    var countdownText = "";
    if (offer && secondsLeft !== null) {
      countdownText = secondsLeft > 0 ? secondsLeft + "s remaining" : "Expired — generate a new code.";
    }

    var deviceRows = devices.length === 0
      ? [React.createElement("p", { key: "empty", className: "cp-empty" }, "No devices paired yet.")]
      : devices.map(function (dev) {
        return React.createElement("div", { key: dev.id, className: "cp-device" },
          React.createElement("span", { className: "cp-device-name" }, dev.name || dev.id),
          React.createElement("span", { className: "cp-device-meta" }, dev.id),
          React.createElement("button", {
            className: "cp-btn cp-btn-danger",
            onClick: function () { onRevoke(dev); }
          }, "Revoke"));
      });

    return React.createElement("div", { className: "cp-wrap" },
      React.createElement("h2", { className: "cp-title" }, "Companion"),
      React.createElement("p", { className: "cp-lead" },
        "Pair a phone with one QR scan. The code works once and expires in 5 minutes. " +
        "Revoke a device any time — its credential stops working immediately."),
      React.createElement("p", { className: "cp-status" }, statusLine),
      React.createElement("div", { className: error ? "cp-error" : "cp-error cp-hidden" }, error),
      pairedMsg ? React.createElement("p", { className: "cp-success" }, pairedMsg) : null,
      React.createElement("button", {
        className: "cp-btn",
        disabled: pairing,
        onClick: onPair
      }, pairing ? "Creating…" : "Pair new device"),
      offer ? React.createElement("div", { className: "cp-offer" },
        React.createElement("div", {
          className: "cp-qr",
          ref: qrRef,
          style: { width: QR_SIZE + "px", height: QR_SIZE + "px", background: "#fff" },
          dangerouslySetInnerHTML: { __html: offer.qr_svg || "" }
        }),
        React.createElement("p", { className: "cp-countdown" }, countdownText),
        React.createElement("pre", { className: "cp-payload" }, JSON.stringify(offer.payload, null, 2))
      ) : null,
      React.createElement("h3", { className: "cp-subtitle" }, "Paired devices"),
      React.createElement("div", { className: "cp-devices" }, deviceRows)
    );
  }

  var plugins = window.__HERMES_PLUGINS__;
  if (plugins && typeof plugins.register === "function") {
    plugins.register("hermes-companion-pair", CompanionPage);
  }
})();
