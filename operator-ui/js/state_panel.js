// Live state panel (§12.1 item 1): incident, doses, actions, deep tasks, agent mode, session, arbiter queue,
// devices (ack latency, battery, unacked P0), beacons, spend vs caps. Read-only.
import { h, clear, fmtTime, pct, short } from "./dom.js";
import { redact } from "./api.js";

const CLOSED_INCIDENT = /^(closed|resolved|cancelled|ok|done)$/i;
// caps.yaml key for each spend key
const CAP_KEY = { llm_usd: "llm_usd", deep_tasks: "deep_tasks", twilio_calls: "twilio_calls",
  playwright_actions: "playwright_actions", sessions: "sessions_per_day" };

export function incidentActive(s) {
  return !!(s && s.incident && !CLOSED_INCIDENT.test(String(s.incident.state || "")));
}

function sect(title, ...body) { return h("div.sect", h("h3", title), ...body); }
const idLink = (id, openTrace) => id ? h("span.idlink.mono", { on: { click: () => openTrace(id) }, title: "open trace" }, id) : "—";

function kv(pairs) {
  const box = h("div.kv");
  for (const [k, v] of pairs) {
    if (v === undefined) continue;
    box.append(h("span.k", k), h("span", v == null || v === "" ? "—" : v));
  }
  return box;
}

function table(cols, rows, rowClass) {
  if (!rows.length) return h("p.empty", "none");
  return h("table",
    h("thead", h("tr", cols.map(c => h("th", c[0])))),
    h("tbody", rows.map(r => h("tr", { class: rowClass ? rowClass(r) : "" }, cols.map(c => {
      const v = c[1](r);
      return h("td", v instanceof Node ? v : (v == null || v === "" ? "—" : String(v)));
    })))));
}

function list(v) { return Array.isArray(v) ? v : v && typeof v === "object" ? Object.values(v) : []; }

export function renderState(el, s, { openTrace }) {
  if (!s) return;
  const panel = el.closest(".panel");
  const active = incidentActive(s);
  panel.classList.toggle("alarm", active);
  const out = [];

  // Incident
  const inc = s.incident;
  if (inc) {
    const tl = list(inc.timeline);
    out.push(sect("Incident", h("div" + (active ? ".incident-box" : ""),
      kv([["id", idLink(inc.id, openTrace)], ["kind", inc.kind], ["state", h("b", inc.state || "—")],
        ["source", inc.source], ["opened", fmtTime(inc.opened_at)], ["vision", inc.vision_note || "—"]]),
      tl.length ? h("details", h("summary.dim", `timeline (${tl.length})`),
        h("pre", tl.map(t => typeof t === "string" ? t : `${fmtTime(t.ts || t.at)} ${t.state || t.type || t.step || ""} ${short(t.detail ?? t.note ?? "", 80)}`).join("\n"))) : null)));
  } else {
    out.push(sect("Incident", h("p.empty", "none")));
  }

  // Agent mode + session
  const sess = s.session;
  out.push(sect("Agent / session",
    sess ? kv([["mode", h("span.mode-pill", sess.mode || "—")], ["agent", sess.agent_state || "—"],
      ["session", idLink(sess.id, openTrace)], ["reason", sess.reason], ["text only", sess.text_only ? "yes (fake LiveKit)" : "no (LiveKit)"]])
      : kv([["mode", h("span.mode-pill", (s.knobs && s.knobs.muted) ? "paused (muted)" : "no session")]]),
    s.knobs && s.knobs.muted ? h("p", h("span.badge.alarm", "MUTED")) : null));

  // Arbiter
  const q = list(s.arbiter && s.arbiter.queue);
  out.push(sect(`Arbiter queue (${q.length})`, q.length ? h("pre", q.map(x => short(redact(x), 160)).join("\n")) : h("p.empty", "empty")));

  // Doses
  out.push(sect("Doses today", table([
    ["slot", d => d.slot_id], ["time", d => d.time], ["state", d => d.state],
    ["meds", d => list(d.meds).map(m => typeof m === "string" ? m : (m.yue || m.id)).join("、")]],
  list(s.doses), d => /missed|overdue/i.test(d.state || "") ? "alarm" : "")));

  // Actions + deep tasks
  out.push(sect("Actions", table([
    ["id", a => idLink(a.id, openTrace)], ["kind", a => a.kind], ["target", a => a.target || a.title],
    ["status", a => a.status]], list(s.actions), a => /failed|needs_human/.test(a.status || "") ? "alarm" : "")));
  out.push(sect("Deep tasks", table([
    ["id", d => idLink(d.id || d.task_id, openTrace)], ["status", d => d.status], ["goal", d => short(d.goal || "", 60)]],
  list(s.deep_tasks))));

  // Devices
  out.push(sect("Devices", table([
    ["device", d => d.device_id], ["online", d => d.online ? "online" : "offline"], ["last seen", d => fmtTime(d.last_seen)],
    ["battery", d => pct(d.battery)], ["ack", d => d.ack_latency_ms == null ? "—" : `${Math.round(d.ack_latency_ms)} ms`],
    ["wake", d => d.wake_engine], ["P0", d => d.unacked_p0 ? h("b", `${d.unacked_p0} unacked`) : "0"]],
  list(s.devices), d => (d.unacked_p0 || !d.online) ? "alarm" : "")));

  // Beacons
  out.push(sect("Beacons", table([
    ["beacon", b => b.id || b.beacon_id], ["last seen", b => fmtTime(b.last_seen)], ["battery", b => pct(b.battery)],
    ["rssi", b => b.rssi], ["lost", b => b.lost ? "LOST" : ""]],
  list(s.beacons), b => b.lost ? "alarm" : "")));

  // Spend vs caps
  const spend = s.spend || {}, caps = s.caps || {};
  const meters = Object.keys({ ...CAP_KEY, ...spend }).filter(k => k in spend || CAP_KEY[k] in caps).map(k => {
    const v = Number(spend[k] || 0), cap = Number(caps[CAP_KEY[k] || k]);
    const ratio = cap > 0 ? Math.min(1, v / cap) : 0;
    const over = cap > 0 && v >= cap;
    const fmt = x => k.endsWith("usd") ? `$${Number(x).toFixed(2)}` : String(x);
    return h("div.meter" + (over ? ".over" : ""), h("span", k), h("div.bar", h("i", { style: `width:${ratio * 100}%` })),
      h("span.mono", `${fmt(v)} / ${cap > 0 ? fmt(cap) : "—"}`));
  });
  out.push(sect("Spend today vs caps", meters.length ? meters : h("p.empty", "no data")));

  clear(el, ...out);
}
