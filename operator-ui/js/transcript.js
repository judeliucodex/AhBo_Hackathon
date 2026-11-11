// Live transcript (§12.1 item 2): her lines with STT confidence, Ah Bo's replies, tool calls (collapsed),
// latency per stage. Fed by SSE `turn` and `kernel_event`. Operator-typed speech is labelled "operator" (INV-8).
import { h, clear, fmtTime, short } from "./dom.js";
import { pretty } from "./api.js";

const WHO = { user: "婆婆", her: "婆婆", resident: "婆婆", popo: "婆婆", "po-po": "婆婆",
  ahbo: "阿寶", assistant: "阿寶", agent: "阿寶", operator: "operator", family: "家人" };

export class Transcript {
  constructor(el, { openTrace }) {
    this.el = el; this.openTrace = openTrace;
    this.calls = new Map();     // call_id -> details element
    this.count = 0;
  }

  _stick() { return this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight < 40; }

  _add(node) {
    const stick = this._stick();
    if (!this.count++) clear(this.el);
    this.el.append(node);
    while (this.el.children.length > 400) this.el.firstChild.remove();
    if (stick) this.el.scrollTop = this.el.scrollHeight;
  }

  clearView() { this.count = 0; this.calls.clear(); clear(this.el, h("p.empty", "No turns yet.")); }

  turn(t) {
    if (!t || !t.text) return;
    const role = String(t.role || "").toLowerCase();
    const cls = role === "operator" ? "operator" : WHO[role] === "婆婆" ? "user" : WHO[role] === "阿寶" ? "ahbo" : "other";
    const meta = [];
    if (t.stt_confidence != null) {
      const c = Number(t.stt_confidence);
      meta.push(h("span.chip" + (c < 0.6 ? ".low" : ""), `STT ${c.toFixed(2)}`));
    }
    if (t.latency && typeof t.latency === "object") {
      for (const [k, v] of Object.entries(t.latency)) meta.push(h("span.chip", `${k} ${typeof v === "number" ? Math.round(v) + (/_s$/.test(k) ? "s" : "ms") : v}`));
    } else if (t.latency != null) meta.push(h("span.chip", `latency ${t.latency}`));
    if (t.turn_id) meta.push(h("span.chip.idlink", { on: { click: () => this.openTrace(t.turn_id) } }, t.turn_id));
    if (role === "operator" || t.operator) meta.unshift(h("span.chip.low", "typed by operator"));
    this._add(h("div.tline." + cls, { dataset: { role: cls } },
      h("span.who", WHO[role] || role || "?"),
      h("div", h("div.txt", t.text), meta.length ? h("div.meta", meta) : null)));
  }

  sys(text, alarm = false) {
    this._add(h("div.tline.sys" + (alarm ? ".alarm" : ""), h("span.who", "·"), h("div.txt", text)));
  }

  kernelEvent(ev) {
    if (!ev || !ev.type) return;
    const p = ev.payload || {};
    switch (ev.type) {
      case "agent.tool_call": {
        const d = h("details.tool", { dataset: { call: p.call_id || "" } },
          h("summary", h("b", p.name || "tool"), " ", h("span.dim", short(p.args || {}, 70)), h("span.res", " …")),
          h("pre", "args " + pretty(p.args || {})));
        if (p.call_id) this.calls.set(p.call_id, d);
        this._add(h("div.tline.sys", h("span.who", "tool"), d));
        break;
      }
      case "agent.tool_result": {
        const d = p.call_id && this.calls.get(p.call_id);
        const mark = p.ok === false ? ` ✕ ${(p.result && p.result.reason) || ""}` : " ✓";
        if (d) {
          d.querySelector(".res").textContent = mark;
          d.append(h("pre", "result " + pretty(p.result ?? {})));
        } else {
          this._add(h("div.tline.sys", h("span.who", "tool"),
            h("details.tool", h("summary", h("b", p.name || "tool"), mark), h("pre", pretty(p.result ?? {})))));
        }
        break;
      }
      case "session.opened": this._add(h("div.sep", `session opened · ${p.mode || ""} · ${p.reason || ""} · ${fmtTime(ev.ts)}`)); break;
      case "session.closed": this._add(h("div.sep", `session closed · ${p.reason || p.why || ""} · ${fmtTime(ev.ts)}`)); break;
      case "agent.concern": this.sys(`concern ${p.level}: ${p.category || ""} ${p.reason || ""}`, p.level !== "info"); break;
      case "operator.say": this.sys(`operator say: ${p.text || ""}`); break;
      default:
        if (ev.type.startsWith("quality.")) this.sys(`${ev.type} ${short(p, 100)}`, true);
        else if (ev.type.startsWith("operator.")) this.sys(`${ev.type}${Object.keys(p).length ? " " + short(p, 80) : ""}`);
        else if (ev.type === "sensor.fall_suspected" || ev.type === "sensor.sos_pressed") this.sys(`${ev.type} (${ev.source})`, true);
    }
  }
}
