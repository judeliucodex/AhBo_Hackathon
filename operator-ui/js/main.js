// Operator console entry (§12, §13): wires the SSE stream to the panels, the live face mirror and the
// overrides. Every console action goes through POST /operator/api/* as an operator.* Kernel event (INV-8);
// nothing here touches the DB. Esc = Panic (§12.2). 404/501 endpoints read "not available yet".
import { API, getJSON, post, trigger, override, clockPost, toast, setAuthLostHandler, showUnavailable } from "./api.js";
import { h, clear, $, fmtHHMM, short } from "./dom.js";
import { Mirror } from "./mirror.js";
import { renderState, incidentActive } from "./state_panel.js";
import { Transcript } from "./transcript.js";
import { TOOLS, cannedFor } from "./canned.js";

const stateBody = $("#state-body"), traceBody = $("#trace-body"), healthBody = $("#health-body");
const outboxBody = $("#outbox-body"), knobsBody = $("#knobs-body"), wakeBody = $("#wake-body");
const transcript = new Transcript($("#transcript-body"), { openTrace: id => openTrace(id) });
const mirror = new Mirror($("#mirror"), {
  onFaceReady: () => { if (pendingSnapshot) mirror.replaySnapshot(pendingSnapshot.ui); },
  onStatus: t => { $("#mirror-status").textContent = t; },
});
let pendingSnapshot = null;     // latest snapshot whose ui the mirror should be showing
let snap = null;                // latest GET /operator/api/state payload (via SSE `state` or boot fetch)
let clips = {};                 // fixed clips from GET /operator/api/fixed
let outboxRows = new Map();     // id -> row (rendered newest first)
let streamHadError = false;     // replay the snapshot after a reconnect (we may have missed ui events)

// ---------------------------------------------------------------- top bar + banner
// The virtual clock's wall time in HKT, from a Date (local ticking between snapshots).
function fmtHKT(d) {
  const t = new Date(d.getTime() + 8 * 3600 * 1000);
  return `${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")}`;
}

function clockChip(nowMs) {
  const off = snap && snap.clock_offset_s ? ` ${snap.clock_offset_s > 0 ? "+" : ""}${Math.round(snap.clock_offset_s)}s` : "";
  $("#top-clock").textContent = fmtHKT(new Date(nowMs)) + off;
}

function renderTop() {
  if (!snap) return;
  clockChip(new Date(snap.now).getTime());
  const badges = clear($("#top-badges"));
  if (snap.profile) badges.append(h("span.badge", "profile: " + snap.profile));
  if (Array.isArray(snap.fake) && snap.fake.length) {
    badges.append(h("span.badge.fake", { title: "faked adapters: " + snap.fake.join(", ") },
      "fake mode · " + snap.fake.join(", ")));
  }
  if (incidentActive(snap)) badges.append(h("span.badge.alarm", "INCIDENT " + (snap.incident.state || "")));
}

// tick locally between snapshots so the clock chip stays alive; corrected on every `state`
let clockBaseMs = null, clockReal = 0;
setInterval(() => { if (clockBaseMs != null) clockChip(clockBaseMs + (Date.now() - clockReal)); }, 1000);

function authLost() {
  const b = $("#banner");
  b.hidden = false;
  clear(b, "登入過期 — session expired. ",
    h("button.btn", { type: "button", onclick: () => { location.href = "/login?next=/operator/"; } }, "Log in again"));
}
setAuthLostHandler(authLost);

// ---------------------------------------------------------------- mirror panel
function replayMirror(s) {
  pendingSnapshot = s;
  if (mirror.faceReady) mirror.replaySnapshot(s.ui);
}

// ---------------------------------------------------------------- scenarios panel (§13)
function buildScenarios() {
  const body = $("#scenarios-body");
  const names = [["S1", "S1"], ["S2", "S2"], ["S3", "S3"], ["S4", "S4"], ["S4ok", "S4 OK"]];
  const bts = {};
  body.append(
    h("div.row", names.map(([id]) => (bts[id] = h("button.btn.primary", { type: "button", dataset: { scenario: id },
      onclick: e => post(`${API}/scenario/${id}`, null, { label: `scenario ${id}`, button: e.currentTarget }) }, id)))),
    h("div.row", h("span.dim", "Prepare (clock anchor only):"),
      names.map(([id, label]) => h("button.btn.mini", { type: "button", dataset: { anchor: id },
        onclick: e => clockPost({ anchor: id }, { label: `anchor ${id}`, button: e.currentTarget }) }, label))),
    h("p.empty", "Each scenario resets, jumps to its clock anchor, then drives her lines / the beacon event through the real paths."),
  );
}

// ---------------------------------------------------------------- triggers panel (§12.1 item 3)
function buildTriggers() {
  const body = $("#triggers-body");
  const slotSel = h("select", { id: "dose-slot-select", title: "which dose to fire now" });
  const checkSel = h("select", { id: "checkin-select" },
    ["morning", "afternoon", "evening"].map(c => h("option", { value: c, selected: c === "morning" ? "" : null }, c)));
  const famInput = h("input.grow", { type: "text", id: "family-text-input", placeholder: "阿珍訊息內容…", maxlength: "120" });

  body.append(
    h("div.row",
      h("button.btn.alarm", { type: "button", id: "trigger-fall", onclick: e => trigger({ kind: "fall" }, { button: e.currentTarget }) }, "模擬跌倒 (fall)"),
      h("button.btn.alarm", { type: "button", id: "trigger-sos", onclick: e => trigger({ kind: "sos" }, { button: e.currentTarget }) }, "SOS")),
    h("div.row", h("label", "dose now"), slotSel,
      h("button.btn", { type: "button", id: "trigger-dose", onclick: e =>
        trigger({ kind: "dose_now", slot_id: slotSel.value || null }, { button: e.currentTarget }) }, "食藥 now")),
    h("div.row", h("label", "check-in"), checkSel,
      h("button.btn", { type: "button", id: "trigger-checkin", onclick: e =>
        trigger({ kind: "checkin", checkin: checkSel.value }, { button: e.currentTarget }) }, "fire")),
    h("div.row", h("span.badge.fake", "fake mode"), h("span.dim", "示範用 Demo:"),
      h("button.btn", { type: "button", id: "trigger-voice-note", title: "inject the seeded demo voice note from 阿珍",
        onclick: e => trigger({ kind: "voice_note", voice_id: "vm_demo" }, { button: e.currentTarget }) }, "inject voice note")),
    h("div.row", famInput,
      h("button.btn", { type: "button", id: "trigger-family-text", title: "inject a family text from 阿珍",
        onclick: e => trigger({ kind: "family_text", text: famInput.value }, { button: e.currentTarget }) }, "inject text")),
    h("div.row",
      h("button.btn", { type: "button", id: "trigger-plan", onclick: e => trigger({ kind: "plan_now" }, { button: e.currentTarget }) }, "run 07:00 plan now"),
      h("button.btn.primary", { type: "button", id: "trigger-reset", title: "reload seed/demo.yaml, re-anchor times, clear incidents/sessions/cards/queues",
        onclick: e => post(`${API}/reset`, null, { label: "Reset demo", button: e.currentTarget }) }, "Reset demo"),
      h("button.btn", { type: "button", id: "trigger-warmup", onclick: e => trigger({ kind: "warmup" }, { button: e.currentTarget }) }, "Warm-up"),
      h("button.btn", { type: "button", id: "trigger-birth", onclick: e => trigger({ kind: "birth" }, { button: e.currentTarget }) }, "Birth")),
  );
  return {
    refreshSlots() {
      const doses = Array.isArray(snap && snap.doses) ? snap.doses : [];
      const first = slotSel.value;
      clear(slotSel, doses.map(d => h("option", { value: d.slot_id },
        `${d.time} ${d.slot_id}${Array.isArray(d.meds) && d.meds.length ? " · " + d.meds.map(m => m.yue || m).join("、") : ""}`)));
      if (doses.some(d => d.slot_id === first)) slotSel.value = first;
      slotSel.disabled = !doses.length;
    },
  };
}

// ---------------------------------------------------------------- virtual clock panel (§12.1 item 5, §13.3)
function buildClock() {
  const body = $("#clock-body");
  const hhmm = h("input.hhmm", { type: "text", inputmode: "numeric", placeholder: "07:58", title: "set the virtual clock to HH:MM HKT" });
  const off = h("input.num#clock-offset-input", { type: "number", step: "1", placeholder: "seconds", title: "offset in seconds (can be negative)" });
  body.append(
    h("div.row", h("span.dim", "now:"), h("b.mono#clock-now", "—"), h("span.dim#clock-offset-label", "")),
    h("div.row", h("label", "set"), hhmm,
      h("button.btn", { type: "button", id: "clock-set", onclick: e => {
        if (!/^\d{1,2}:\d{2}$/.test(hhmm.value.trim())) { toast("use HH:MM", "fail"); return; }
        clockPost({ set: hhmm.value.trim() }, { button: e.currentTarget });
      } }, "set")),
    h("div.row", h("label", "offset"), off,
      h("button.btn", { type: "button", id: "clock-offset", onclick: e =>
        clockPost({ offset_s: Number(off.value || 0) }, { button: e.currentTarget }) }, "apply"),
      h("button.btn.mini", { type: "button", id: "clock-back-now", onclick: e =>
        clockPost({ offset_s: 0 }, { label: "clock back to now", button: e.currentTarget }) }, "back to now")),
    h("p.empty", "Drives the scheduler and every tool (clock.now() is the only time source)."));
  return {
    refresh(s) {
      $("#clock-now").textContent = fmtHHMM(s.now) || "—";
      $("#clock-offset-label").textContent = s.clock_offset_s ? `offset ${Math.round(s.clock_offset_s)} s` : "real time";
    },
  };
}

// ---------------------------------------------------------------- overrides panel (§12.1 item 4)
function buildOverrides() {
  const body = $("#overrides-body");
  const toolSel = h("select", { id: "force-tool-select" }, TOOLS.map(t => h("option", { value: t }, t)));
  const varSel = h("select", { id: "force-variant-select" });
  const jsonTa = h("textarea", { id: "force-json", spellcheck: "false" });

  function loadVariant() {
    const canned = Object.entries(cannedFor(toolSel.value));
    clear(varSel, canned.map(([k]) => h("option", { value: k }, k)));
    const v = cannedFor(toolSel.value)[varSel.value];
    jsonTa.value = JSON.stringify(v, null, 2);
  }
  toolSel.addEventListener("change", loadVariant);

  const whichSel = h("select", { id: "provider-select" }, ["stt", "llm", "tts"].map(x => h("option", { value: x }, x)));
  const muteBtn = h("button.btn", { type: "button", id: "override-mute", onclick: e =>
    override({ kind: (snap && snap.knobs && snap.knobs.muted) ? "unmute" : "mute" }, { button: e.currentTarget }) }, "mute");
  const sayTa = h("textarea", { id: "say-text", spellcheck: "false", placeholder: "用廣東話打一句，阿寶會照講（字幕標明 operator）…" });
  const interruptBtn = h("button.btn", { type: "button", id: "override-interrupt", onclick: e =>
    override({ kind: "interruption", on: !(snap && snap.knobs && snap.knobs.interruption_enabled) }, { button: e.currentTarget }) }, "interruption: ?");

  body.append(
    h("div.sect", h("h3", "Incident"),
      h("div.row",
        h("button.btn", { type: "button", id: "override-ok", onclick: e => override({ kind: "incident_ok" }, { button: e.currentTarget }) }, "mark OK"),
        h("button.btn.alarm", { type: "button", id: "override-escalate", onclick: e => override({ kind: "incident_escalate" }, { button: e.currentTarget }) }, "escalate"),
        h("button.btn", { type: "button", id: "override-resolve", onclick: e => override({ kind: "incident_resolve" }, { button: e.currentTarget }) }, "resolve"))),
    h("div.sect", h("h3", "Force tool result"),
      h("div.row", toolSel, varSel),
      jsonTa,
      h("div.row", h("button.btn", { type: "button", id: "force-apply", onclick: e => {
        let result;
        try { result = JSON.parse(jsonTa.value); } catch (err) { toast("bad JSON: " + err.message, "fail"); return; }
        override({ kind: "force_tool_result", tool: toolSel.value, result }, { button: e.currentTarget });
      } }, "force it"), h("span.dim", "canned results; edit the JSON if needed"))),
    h("div.sect", h("h3", "Fixed clips"),
      h("div.row", h("select.grow#clip-select", { id: "clip-select" }, h("option", { value: "" }, "loading…")),
        h("button.btn", { type: "button", id: "override-play-fixed", onclick: e => {
          const id = $("#clip-select").value;
          if (!id) { toast("no clip selected", "fail"); return; }
          override({ kind: "play_fixed", clip_id: id }, { button: e.currentTarget });
        } }, "play"))),
    h("div.sect", h("h3", "Audio"),
      h("div.row", muteBtn, interruptBtn,
        h("span.dim", "mute → agent paused; interruption = she can talk over Ah Bo"))),
    h("div.sect", h("h3", "Say a line"),
      h("div.row", h("span.badge", "operator"), h("span.dim", "caption is labelled as the operator's, not Ah Bo's")),
      sayTa,
      h("div.row", h("button.btn", { type: "button", id: "override-say", onclick: e => {
        const text = sayTa.value.trim();
        if (!text) { toast("type a line first", "fail"); return; }
        override({ kind: "say", text }, { button: e.currentTarget });
      } }, "say it"))),
    h("div.sect", h("h3", "Provider fallback"),
      h("div.row", whichSel,
        h("button.btn", { type: "button", id: "override-provider", onclick: e =>
          override({ kind: "provider", which: whichSel.value, to: "fallback" }, { button: e.currentTarget }) }, "switch to fallback"),
        h("span.dim", "next call uses the fallback route"))),
  );
  loadVariant();

  return {
    refreshClips() {
      const sel = $("#clip-select");
      const ids = Object.keys(clips);
      clear(sel, ids.length ? ids.map(id => h("option", { value: id }, `${id} · ${short(clips[id].text || "", 30)}`))
        : [h("option", { value: "" }, "no clips (not available yet)")]);
    },
    refresh(s) {
      const m = !!(s.knobs && s.knobs.muted);
      muteBtn.textContent = m ? "unmute" : "mute";
      muteBtn.classList.toggle("on", m);
      const intr = !!(s.knobs && s.knobs.interruption_enabled);
      interruptBtn.textContent = `interruption: ${intr ? "on" : "off"}`;
      interruptBtn.classList.toggle("on", intr);
    },
  };
}

// ---------------------------------------------------------------- outbox panel (§12.1 item 6, §26.3)
function renderOutbox() {
  if (!outboxRows.size) { clear(outboxBody, h("p.empty", "nothing sent yet")); return; }
  const rows = [...outboxRows.values()].sort((a, b) =>
    String(b.ts || "").localeCompare(String(a.ts || "")) || String(b.id).localeCompare(String(a.id)));
  clear(outboxBody, rows.map(r => {
    const p = r.payload || {};
    const kids = [h("div.hd",
      h("span.badge", r.channel),
      h("span.badge", r.kind || ""),
      r.to_contact ? h("span.dim", r.to_contact) : null,
      h("span.dim", r.status || ""),
      r.fake ? h("span.badge.fake", "fake mode") : null)];
    if (p.text) kids.push(h("div.txt", p.text));
    if (p.twiml) kids.push(h("pre", String(p.twiml)));
    if (p.photo_id) kids.push(h("img", { src: `/media/photo/${encodeURIComponent(p.photo_id)}`, alt: "photo", loading: "lazy" }));
    if (Array.isArray(p.screenshots) && p.screenshots.length) {
      kids.push(h("div.btns", p.screenshots.map(f =>
        h("img", { src: `/media/screens/${encodeURIComponent(String(f).replace(/^.*[\\/]/, ""))}`, alt: "screenshot", loading: "lazy" }))));
    }
    const press = button => post(`${API}/outbox/${encodeURIComponent(r.id)}/press`, { button }, { label: `press ${button} (${r.id})` });
    const btns = [];
    for (const b of Array.isArray(r.buttons) ? r.buttons : []) {
      if (b && b.id) btns.push(h("button.btn.mini", { type: "button", onclick: () => press(b.id) }, b.text || b.id));
    }
    if (r.channel === "twilio") btns.push(h("button.btn.mini", { type: "button", onclick: () => press("press1") }, "press 1 (答咗)"));
    if (btns.length) kids.push(h("div.btns", btns));
    return h("div.obx" + (/ALERT|ESCALATE/.test(String(r.kind)) ? ".alert" : ""), ...kids);
  }));
}

async function loadOutbox() {
  try {
    const data = await getJSON(`${API}/outbox`);
    outboxRows = new Map((data.items || []).map(r => [r.id, r]));
    renderOutbox();
  } catch (e) { showUnavailable(outboxBody, e, "outbox"); }
}

// ---------------------------------------------------------------- knobs panel (§12.1 item 8, §20.1–20.2)
function buildKnobs() {
  const body = knobsBody;
  const FLASH = ["off", "low", "high", "max"];
  const SONNET = ["off", "low", "medium", "high", "xhigh", "max"];
  const flashSeg = h("span.seg#flash-thinking-seg");
  const sonnetSeg = h("span.seg#sonnet-thinking-seg");

  function seg(segEl, values, key) {
    const current = snap && snap.knobs ? snap.knobs[key] : null;
    clear(segEl, values.map(v => h("button.btn" + (v === current ? ".on" : ""), { type: "button",
      dataset: { value: v, knob: key },
      onclick: async e => {
        const r = await override({ kind: "knobs", [key]: v }, { button: e.currentTarget });
        if (r.data && r.data.knobs && snap) { Object.assign(snap.knobs, r.data.knobs); seg(flashSeg, FLASH, "flash_thinking"); seg(sonnetSeg, SONNET, "sonnet_thinking"); }
      } }, v)));
  }
  function refresh() {
    seg(flashSeg, FLASH, "flash_thinking");
    seg(sonnetSeg, SONNET, "sonnet_thinking");
  }
  body.append(
    h("div.sect", h("h3", "Flash thinking (next turn)"), h("div.row", flashSeg),
      h("p.empty", "applies from the next turn, never mid-generation")),
    h("div.sect", h("h3", "Sonnet thinking (next deep task)"), h("div.row", sonnetSeg),
      h("p.empty", "off…max · effort levels for deep tasks / morning plan")));
  return { refresh };
}

// ---------------------------------------------------------------- wake panel (§20.4.5 rule 6)
function renderWake() {
  const devices = Array.isArray(snap && snap.devices) ? snap.devices : [];
  const ipad = devices.find(d => d.online && d.wake_engine && d.wake_engine !== "none" && !d.sim);
  const badge = $("#wake-badge");
  badge.textContent = ipad ? "iPad connected" : "no iPad";
  badge.classList.toggle("alarm", !ipad);
  if (!ipad) {
    clear(wakeBody,
      h("p.empty", "no iPad connected — wake scores, counters and sliders appear when one is"),
      h("p.empty", "（老實顯示：而家冇 iPad 連住，所以冇 wake 數據）"));
    return;
  }
  clear(wakeBody,
    h("div.kv",
      [["device", ipad.device_id], ["wake engine", ipad.wake_engine], ["battery", ipad.battery],
       ["ack", ipad.ack_latency_ms != null ? Math.round(ipad.ack_latency_ms) + " ms" : null]]
        .map(([k, v]) => [h("span.k", k), v == null ? "—" : String(v)]).flat()),
    h("p.empty", "Stage 1/2 live scores, wake/reject counters, T1 & strictness sliders, missed-wake tagging: not available yet (needs a wake-stats endpoint)."));
}

// ---------------------------------------------------------------- health panel (§12.1 item 7, §13.2)
function renderHealth(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const red = list.filter(r => r.status === "red").length;
  const st = $("#health-status");
  st.textContent = red ? `${red} red` : `${list.length} rows`;
  st.style.color = red ? "var(--amber)" : "";
  clear(healthBody, h("table", h("thead", h("tr", [h("th", "item"), h("th", "status"), h("th", "detail")])),
    h("tbody", list.map(r => h("tr", null, h("td", r.item),
      h(`td.st-${r.status === "red" ? "red" : r.status === "fake" ? "fake" : "green"}`, r.status),
      h("td", String(r.detail ?? "")))))));
}

async function loadHealth() {
  try { renderHealth((await getJSON(`${API}/health`)).rows); }
  catch (e) { showUnavailable(healthBody, e, "health"); }
}

// ---------------------------------------------------------------- trace panel (§15, GET /operator/api/trace)
const traceInput = h("input.grow", { type: "text", placeholder: "correlation id (inc_…, turn_…, sess_…)", spellcheck: "false" });
const traceLimit = h("input.num", { type: "number", value: "200", min: "1", max: "2000", title: "max rows" });

function traceSect(title, rows, cols) {
  return h("details.sect", rows.length && rows.length <= 30 ? { open: "" } : null,
    h("summary", h("b", `${title} (${rows.length})`)),
    rows.length ? h("table", h("thead", h("tr", cols.map(c => h("th", c[0])))),
      h("tbody", rows.map(r => h("tr", null, cols.map(c => {
        const v = c[1](r);
        return h("td", v instanceof Node ? v : (v == null ? "—" : String(v)));
      }))))) : h("p.empty", "none"));
}

async function fetchTrace() {
  const id = traceInput.value.trim();
  const limit = Math.min(Math.max(Number(traceLimit.value) || 200, 1), 2000);
  const qs = new URLSearchParams({ limit: String(limit) });
  if (id) qs.set("correlation_id", id);
  try {
    const data = await getJSON(`${API}/trace?${qs}`);
    clear(traceBody,
      h("div.row", traceInput, traceLimit, h("button.btn", { type: "button", id: "trace-fetch", onclick: fetchTrace }, "fetch")),
      traceSect("events", data.events || [], [
        ["ts", r => fmtHHMM(r.ts)], ["type", r => h("b", r.type)], ["source", r => r.source || "—"],
        ["payload", r => h("span.mono", short(r.payload ?? r.correlation_id ?? "", 90))]]),
      traceSect("commands", data.commands || [], [
        ["ts", r => fmtHHMM(r.ts)], ["type", r => h("b", r.type)], ["target", r => r.target || "—"],
        ["payload", r => h("span.mono", short(r.payload ?? "", 90))]]),
      traceSect("spans", data.spans || [], [
        ["start", r => fmtHHMM(r.start_ts)], ["name", r => h("b", r.name || r.span || "—")],
        ["detail", r => h("span.mono", short(r.attrs ?? r.detail ?? "", 90))]]));
  } catch (e) { showUnavailable(traceBody, e, "trace"); }
}

function openTrace(id) {
  traceInput.value = id;
  fetchTrace();
  $("#p-trace").scrollIntoView({ behavior: "smooth", block: "start" });
}

function initTrace() {
  clear(traceBody, h("div.row", traceInput, traceLimit,
    h("button.btn", { type: "button", id: "trace-fetch", onclick: fetchTrace }, "fetch")));
}

function applySnapshot(s) {
  snap = s;
  clockBaseMs = new Date(s.now).getTime();
  clockReal = Date.now();
  renderTop();
  renderState(stateBody, snap, { openTrace });
  trig.refreshSlots();
  clockPanel.refresh(snap);
  overr.refresh(snap);
  knobs.refresh();
  renderWake();
  pendingSnapshot = snap;
  if (mirror.holding) replayMirror(snap);     // first snapshot (or the boot fetch failed earlier)
}

// ---------------------------------------------------------------- SSE stream (CONTRACTS §9)
function connectStream() {
  const conn = $("#conn");
  const es = new EventSource("/operator/stream");
  conn.dataset.state = "connecting";
  conn.textContent = "stream: connecting…";
  es.addEventListener("open", () => {
    conn.dataset.state = "open";
    conn.textContent = "stream: live";
    if (streamHadError) { streamHadError = false; if (snap) replayMirror(snap); }   // resync after a gap
  });
  es.addEventListener("error", () => {
    streamHadError = true;
    conn.dataset.state = es.readyState === EventSource.CLOSED ? "error" : "connecting";
    conn.textContent = "stream: " + (es.readyState === EventSource.CLOSED ? "closed — reload" : "reconnecting…");
  });
  es.addEventListener("state", ev => applySnapshot(JSON.parse(ev.data)));
  es.addEventListener("kernel_event", ev => {
    const o = JSON.parse(ev.data);
    transcript.kernelEvent(o);
    mirror.kernelEvent(o);
  });
  es.addEventListener("command", ev => mirror.command(JSON.parse(ev.data)));
  es.addEventListener("ui", ev => mirror.ui(JSON.parse(ev.data)));
  es.addEventListener("turn", ev => transcript.turn(JSON.parse(ev.data)));
  es.addEventListener("outbox", ev => {
    const row = JSON.parse(ev.data);
    if (row && row.id) { outboxRows.set(row.id, row); renderOutbox(); }
  });
  es.addEventListener("health", ev => renderHealth(JSON.parse(ev.data).rows));
}

// ---------------------------------------------------------------- Esc = Panic (§12.2)
async function panic() {
  toast("PANIC — stopping all audio", "panic");
  await post(`${API}/panic`, null, { label: "PANIC (Esc)" });
  transcript.sys("panic: stop_all sent, session closed, face reset", true);
}
addEventListener("keydown", e => { if (e.key === "Escape" && !e.repeat) void panic(); });
$("#panic-btn").addEventListener("click", () => void panic());
$("#mirror-resync").addEventListener("click", () => { if (snap) replayMirror(snap); });
$("#transcript-clear").addEventListener("click", () => transcript.clearView());
$("#outbox-refresh").addEventListener("click", () => void loadOutbox());
$("#health-refresh").addEventListener("click", () => void loadHealth());

// ---------------------------------------------------------------- boot
const trig = buildTriggers();
const clockPanel = buildClock();
const overr = buildOverrides();
const knobs = buildKnobs();
buildScenarios();
initTrace();

(async function boot() {
  connectStream();                       // the mirror holds ui events until the snapshot arrives
  try {
    const s = await getJSON(`${API}/state`);
    applySnapshot(s);
    replayMirror(snap);
  } catch (e) { showUnavailable(stateBody, e, "state"); }
  try {
    const f = await getJSON(`${API}/fixed`);
    clips = f.clips || {};
  } catch { clips = {}; }
  mirror.setClips(clips);
  overr.refreshClips();
  void loadOutbox();
  void loadHealth();
})();
