// HTTP to the operator API (CONTRACTS §9). Cookie auth is handled by the server; nothing here edits the DB.
// Every POST is logged in the "Action results" panel with its body and the answer (§12.2: every action
// shows its result). A 404 means "not available yet" (the platform may not have built that endpoint).
import { h, clear, fmtTime, short } from "./dom.js";

export const API = "/operator/api";

export class HttpError extends Error {
  constructor(status, path, body) {
    super(status === 404 ? "not available yet" : `HTTP ${status}`);
    this.status = status; this.path = path; this.body = body;
  }
  get notAvailable() { return this.status === 404 || this.status === 405 || this.status === 501; }
}

// ---------- secrets never reach the screen (rule 6) ----------
const SECRET_KEY = /(secret|token|passw|api[_-]?key|authorization|cookie|signature|private[_-]?key|auth[_-]?token)/i;
const SECRET_VAL = /^(sk-[\w-]{8,}|sk_[\w-]{8,}|xox[abp]-[\w-]+|AC[0-9a-f]{32}|\d{6,}:[\w-]{30,}|eyJ[\w-]{20,}\.[\w-]+\.[\w-]+|dt\.[\w.-]{20,})$/;

export function mask(s) {
  s = String(s);
  return s.length > 8 ? "••••" + s.slice(-4) : "••••";
}

/** Deep copy with secret-looking keys/values masked to the last 4 characters. */
export function redact(v, key = "") {
  if (v == null) return v;
  if (typeof v === "string") return (SECRET_KEY.test(key) || SECRET_VAL.test(v.trim())) ? mask(v) : v;
  if (typeof v !== "object") return SECRET_KEY.test(key) && v !== true && v !== false ? mask(v) : v;
  if (Array.isArray(v)) return v.map(x => redact(x, key));
  const out = {};
  for (const [k, x] of Object.entries(v)) out[k] = redact(x, k);
  return out;
}

export function pretty(v) { return JSON.stringify(redact(v), null, 2); }

let onAuthLost = () => {};
export function setAuthLostHandler(fn) { onAuthLost = fn; }

async function readBody(r) {
  const text = await r.text();
  try { return text ? JSON.parse(text) : null; } catch { return { raw: text.slice(0, 300) }; }
}

export async function getJSON(path) {
  const r = await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" }, cache: "no-store" });
  if (r.status === 401 || r.status === 403) onAuthLost();
  const body = await readBody(r);
  if (!r.ok) throw new HttpError(r.status, path, body);
  return body;
}

// ---------- action log ----------
const logEl = () => document.getElementById("actlog-body");
let logCount = 0;

function logAction(label, path, body, result) {
  const el = logEl(); if (!el) return;
  if (!logCount++) clear(el);
  const row = h("div.act" + (result.ok ? "" : ".fail"), { dataset: { path } },
    h("span.t", fmtTime(new Date().toISOString().replace("Z", ""))),
    h("div",
      h("span.res", result.ok ? "✓ " : "✕ "), h("b", label), " ",
      h("span.mid", result.summary),
      body != null ? h("div.body", `${path} ${short(redact(body), 220)}`) : h("div.body", path)));
  el.prepend(row);
  while (el.children.length > 80) el.lastChild.remove();
}

let toastTimer = 0;
export function toast(msg, cls = "") {
  const t = document.getElementById("toast"); if (!t) return;
  t.textContent = msg; t.className = "toast " + cls; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, cls === "panic" ? 3000 : 2200);
}

function summarize(status, data) {
  if (status === 404) return "not available yet (404)";
  if (status >= 400) return `HTTP ${status}${data && data.detail ? " · " + short(data.detail, 80) : ""}${data && data.reason ? " · " + data.reason : ""}`;
  if (!data) return "ok";
  if (data.ok === false) return `refused · ${data.reason || data.detail || "no reason"}`;
  const extra = Object.entries(data).filter(([k]) => k !== "ok").map(([k, v]) => `${k}: ${short(v, 50)}`).join(" · ");
  return extra ? "ok · " + extra : "ok";
}

/**
 * POST JSON and show the result: logs it, marks the button (if given) ✓/✕ for a few seconds,
 * and returns {ok, status, data}. Never throws.
 */
export async function post(path, body, { label, button } = {}) {
  label = label || path.replace(API + "/", "");
  let status = 0, data = null;
  try {
    const r = await fetch(path, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: body == null ? "{}" : JSON.stringify(body),
    });
    status = r.status;
    if (status === 401 || status === 403) onAuthLost();
    data = await readBody(r);
  } catch (e) {
    data = { reason: "network: " + (e && e.message || e) };
  }
  const ok = status >= 200 && status < 300 && !(data && data.ok === false);
  const summary = status ? summarize(status, data) : `failed · ${data.reason}`;
  logAction(label, path, body, { ok, summary });
  if (button) {
    button.classList.remove("res-ok", "res-fail");
    void button.offsetWidth;
    button.classList.add(ok ? "res-ok" : "res-fail");
    button.title = summary;
    clearTimeout(button._resTimer);
    button._resTimer = setTimeout(() => button.classList.remove("res-ok", "res-fail"), 4000);
  }
  if (!ok) toast(`${label}: ${summary}`, "fail");
  return { ok, status, data, summary };
}

export const trigger = (body, opts) => post(`${API}/trigger`, body, { label: "trigger " + body.kind, ...opts });
export const override = (body, opts) => post(`${API}/override`, body, { label: "override " + body.kind, ...opts });
export const clockPost = (body, opts) => post(`${API}/clock`, body, { label: "clock " + Object.keys(body).join(","), ...opts });

/** Show a "not available yet" / error note inside a panel body. */
export function showUnavailable(el, err, what = "") {
  const msg = err && err.notAvailable ? "not available yet" : `error: ${err && err.message || err}`;
  clear(el, h("p.na", (what ? what + ": " : "") + msg));
}
