// MediKeep Epic sign-in relay (sandbox prototype).
// The Epic app has one fixed redirect URI (this page); each MediKeep install puts its own
// callback URL in `state` as "<base64url(callback URL)>.<random>". This page validates that
// URL and forwards the authorization response to it. It never stores or sends the code
// anywhere else.
"use strict";

var CALLBACK_PATH = "/api/v1/health-connections/callback";
var STORE_KEY = "medikeep-relay-hosts";
var FORWARD = ["code", "state", "error", "error_description", "error_uri"];

function b64urlDecode(s) {
  if (!/^[A-Za-z0-9_-]+$/.test(s)) return null;
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  try { return atob(s); } catch (e) { return null; }
}

// Returns the install's callback URL object, or null when state does not name an
// acceptable target (https, exact callback path, no userinfo/query/fragment).
function targetFromState(state) {
  if (typeof state !== "string" || state.length > 2048) return null;
  var dot = state.indexOf(".");
  if (dot < 1 || dot === state.length - 1) return null;
  var raw = b64urlDecode(state.slice(0, dot));
  if (raw === null) return null;
  var u;
  try { u = new URL(raw); } catch (e) { return null; }
  // Reject anything URL parsing had to normalize (backslashes, odd encodings, whitespace,
  // upper-case hosts): installs must send their callback URL in canonical form.
  if (u.href !== raw) return null;
  if (u.protocol !== "https:") return null;
  if (u.username || u.password) return null;
  if (u.pathname !== CALLBACK_PATH || u.search || u.hash) return null;
  return u;
}

function buildForward(target, params) {
  var out = new URL(target.href);
  FORWARD.forEach(function (k) {
    var v = params.get(k);
    if (v !== null) out.searchParams.set(k, v);
  });
  return out.href;
}

function knownHosts() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); } catch (e) { return []; }
}

function remember(host) {
  try {
    var hosts = knownHosts();
    if (hosts.indexOf(host) < 0) hosts.push(host);
    localStorage.setItem(STORE_KEY, JSON.stringify(hosts));
  } catch (e) { /* private window: just ask again next time */ }
}

function main() {
  // Epic may answer in the query (default) or the fragment (response_mode=fragment).
  var params = new URLSearchParams(location.search.length > 1 ? location.search : location.hash.slice(1));
  // Drop the code from the address bar and history before anything else happens.
  history.replaceState(null, "", location.pathname);

  var target = targetFromState(params.get("state"));
  if (!target || (!params.get("code") && !params.get("error"))) {
    document.getElementById("bad").hidden = false;
    return;
  }
  var dest = buildForward(target, params);
  if (knownHosts().indexOf(target.host) >= 0) {
    location.replace(dest);
    return;
  }
  document.getElementById("host").textContent = target.host;
  document.getElementById("confirm").hidden = false;
  document.getElementById("go").addEventListener("click", function () {
    if (document.getElementById("rem").checked) remember(target.host);
    location.replace(dest);
  });
  document.getElementById("stop").addEventListener("click", function () {
    document.getElementById("confirm").hidden = true;
    document.getElementById("stopped").hidden = false;
  });
}

if (typeof module !== "undefined") {
  module.exports = { targetFromState: targetFromState, buildForward: buildForward };
} else {
  main();
}
