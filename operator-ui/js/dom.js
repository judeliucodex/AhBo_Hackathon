// Tiny DOM helpers (no framework, no build step).

/** h("div.cls#id", {attr…, on:{click}}, ...children) */
export function h(spec, attrs, ...children) {
  const m = /^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i.exec(spec) || [];
  const el = document.createElement(m[1] || "div");
  for (const part of (m[2] || "").match(/[.#][\w-]+/g) || []) {
    if (part[0] === ".") el.classList.add(part.slice(1)); else el.id = part.slice(1);
  }
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs); attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
    else if (k === "class") el.className += " " + v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "text") el.textContent = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) { el.replaceChildren(); append(el, children); return el; }
export const $ = (sel, root = document) => root.querySelector(sel);

export function fmtTime(ts) {
  if (!ts) return "—";
  const m = /T(\d\d:\d\d(:\d\d)?)/.exec(String(ts));
  return m ? m[1] : String(ts);
}

export function fmtHHMM(ts) {
  const m = /T(\d\d:\d\d)/.exec(String(ts || ""));
  return m ? m[1] : "--:--";
}

export function pct(v) { return v == null || v === "" ? "—" : `${Math.round(Number(v) * 100)}%`; }

export function short(v, n = 140) {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s && s.length > n ? s.slice(0, n - 1) + "…" : s ?? "";
}
