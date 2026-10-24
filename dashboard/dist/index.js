/* Hermes Companion Pair — dashboard tab UI (vanilla JS IIFE, no build step).
 *
 * Loading assumption (documented, not verified against a live dashboard):
 * the dashboard loads this file as the tab entry script and exposes
 * window.__HERMES_PLUGINS__.register(name, component). This script
 * registers a mount-style component AND falls back to mounting into a
 * container element if one exists, so either hosting shape renders.
 * API calls go through window.__HERMES_PLUGIN_SDK__.fetchJSON when
 * present (it injects the session token), else fetch with credentials.
 *
 * The tab never handles secrets beyond displaying the QR payload the
 * owner just created (the payload's one-time secret is shown only in
 * this owner-authenticated tab and the operator's terminal).
 */
(function () {
  "use strict";

  var API_BASE = "/api/plugins/hermes-companion-pair";

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

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function render(root) {
    if (root.getAttribute("data-companion-pair-mounted")) return;
    root.setAttribute("data-companion-pair-mounted", "1");
    root.innerHTML = "";

    var wrap = el("div", "cp-wrap");
    wrap.appendChild(el("h2", "cp-title", "Companion"));
    wrap.appendChild(el("p", "cp-lead",
      "Pair a phone with one QR scan. The code works once and expires in 5 minutes. " +
      "Revoke a device any time — its credential stops working immediately."));

    var statusLine = el("p", "cp-status", "Checking dashboard status…");
    wrap.appendChild(statusLine);

    var errorBox = el("div", "cp-error cp-hidden");
    wrap.appendChild(errorBox);

    function showError(message) {
      errorBox.textContent = message;
      errorBox.classList.remove("cp-hidden");
    }
    function clearError() {
      errorBox.textContent = "";
      errorBox.classList.add("cp-hidden");
    }

    var pairBtn = el("button", "cp-btn", "Pair new device");
    wrap.appendChild(pairBtn);

    var offerBox = el("div", "cp-offer cp-hidden");
    var offerQr = el("div", "cp-qr");
    var offerCountdown = el("p", "cp-countdown");
    var offerWarning = el("p", "cp-warning cp-hidden");
    var offerPayload = el("pre", "cp-payload");
    var copyBtn = el("button", "cp-btn cp-btn-secondary", "Copy payload");
    var closeBtn = el("button", "cp-btn cp-btn-secondary", "Close");
    offerBox.appendChild(offerQr);
    offerBox.appendChild(offerCountdown);
    offerBox.appendChild(offerWarning);
    offerBox.appendChild(offerPayload);
    offerBox.appendChild(copyBtn);
    offerBox.appendChild(closeBtn);
    wrap.appendChild(offerBox);

    var deviceHeader = el("h3", "cp-subtitle", "Paired devices");
    wrap.appendChild(deviceHeader);
    var deviceList = el("div", "cp-devices");
    wrap.appendChild(deviceList);

    root.appendChild(wrap);

    var pollTimer = null;
    var knownDeviceIds = {};
    var offerExpiresAt = 0;
    var countdownTimer = null;

    function stopPolling() {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }
    function stopCountdown() {
      if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    }
    function closeOffer() {
      offerBox.classList.add("cp-hidden");
      stopPolling();
      stopCountdown();
    }
    closeBtn.addEventListener("click", closeOffer);

    copyBtn.addEventListener("click", function () {
      var text = offerPayload.textContent || "";
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).catch(function () {});
      } else {
        var ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch (e) {}
        document.body.removeChild(ta);
      }
    });

    function renderDevices(devices) {
      deviceList.innerHTML = "";
      if (!devices || devices.length === 0) {
        deviceList.appendChild(el("p", "cp-empty", "No devices paired yet."));
        return;
      }
      devices.forEach(function (dev) {
        var row = el("div", "cp-device");
        var name = el("span", "cp-device-name", dev.name || dev.id);
        var meta = el("span", "cp-device-meta",
          "paired " + (dev.paired_at ? new Date(dev.paired_at * 1000).toLocaleString() : "—") +
          " · " + (dev.credential_state || "active"));
        row.appendChild(name);
        row.appendChild(meta);
        if (dev.credential_state !== "revoked") {
          var revokeBtn = el("button", "cp-btn cp-btn-danger", "Revoke");
          revokeBtn.addEventListener("click", function () {
            if (!window.confirm("Revoke " + (dev.name || dev.id) + "? Its credential will stop working.")) return;
            clearError();
            api("/devices/" + encodeURIComponent(dev.id), { method: "DELETE" })
              .then(loadDevices)
              .catch(function (err) { showError("Revoke failed: " + err.message); });
          });
          row.appendChild(revokeBtn);
        }
        deviceList.appendChild(row);
      });
    }

    function loadDevices() {
      return api("/devices").then(function (data) {
        var devices = data.devices || [];
        renderDevices(devices);
        return devices;
      });
    }

    function refreshStatus() {
      api("/dashboard-status").then(function (status) {
        statusLine.textContent =
          "Dashboard: " + (status.reachable ? "running" : "not reachable") +
          " (" + status.host + ":" + status.port + ")" +
          " · Auto-start: " + (status.autostart_disabled ? "off" : "on");
      }).catch(function () {
        statusLine.textContent = "Dashboard status unavailable.";
      });
    }

    pairBtn.addEventListener("click", function () {
      clearError();
      pairBtn.disabled = true;
      api("/offers", { method: "POST" }).then(function (data) {
        pairBtn.disabled = false;
        offerQr.innerHTML = data.qr_svg || "";
        offerPayload.textContent = JSON.stringify(data.payload, null, 2);
        offerExpiresAt = data.expires_at || 0;
        if (data.bind_warning) {
          offerWarning.textContent = data.bind_warning;
          offerWarning.classList.remove("cp-hidden");
        } else {
          offerWarning.classList.add("cp-hidden");
        }
        offerBox.classList.remove("cp-hidden");
        stopCountdown();
        countdownTimer = setInterval(function () {
          var left = offerExpiresAt - Math.floor(Date.now() / 1000);
          if (left <= 0) {
            offerCountdown.textContent = "Expired — generate a new code.";
            closeOffer();
            return;
          }
          offerCountdown.textContent = "Expires in " + left + "s";
        }, 1000);
        offerCountdown.textContent = "Expires in 300s";
        loadDevices().then(function (devices) {
          knownDeviceIds = {};
          devices.forEach(function (d) { knownDeviceIds[d.id] = true; });
        });
        stopPolling();
        pollTimer = setInterval(function () {
          loadDevices().then(function (devices) {
            var fresh = devices.filter(function (d) { return !knownDeviceIds[d.id]; });
            if (fresh.length > 0) {
              closeOffer();
              offerCountdown.textContent = "";
              var done = el("p", "cp-success", "Paired: " + (fresh[0].name || fresh[0].id));
              wrap.insertBefore(done, offerBox);
            }
          }).catch(function () {});
        }, 2000);
      }).catch(function (err) {
        pairBtn.disabled = false;
        showError("Could not create an offer: " + err.message);
      });
    });

    refreshStatus();
    loadDevices().catch(function (err) { showError("Could not load devices: " + err.message); });
  }

  function tryMount() {
    var candidates = [
      document.getElementById("companion-pair-root"),
      document.querySelector("[data-plugin-tab='hermes-companion-pair']"),
      document.querySelector("[data-companion-pair]")
    ];
    for (var i = 0; i < candidates.length; i++) {
      if (candidates[i]) { render(candidates[i]); return true; }
    }
    return false;
  }

  var plugins = window.__HERMES_PLUGINS__;
  if (plugins && typeof plugins.register === "function") {
    try {
      plugins.register("hermes-companion-pair", { mount: render, render: render });
    } catch (e) { /* host uses a different component shape; fallback below */ }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tryMount);
  } else {
    tryMount();
  }
})();
