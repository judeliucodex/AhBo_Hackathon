/* Device channel for the browser iPad (CONTRACTS §2, PRD §10.1, §25.2 DeviceChannel.swift).
 *
 * wss://<host>/ws/device?sim=1 with the operator cookie. hello → welcome → replay; heartbeat every 10 s;
 * ack every command at once; de-duplicate commands by id (a resent command is acked again, never re-run);
 * reconnect with backoff 0.5, 1, 2, 4, 8 s (cap) ± 20 % jitter; after 30 s disconnected → onOffline(true).
 */

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/** A ULID (26 chars, Crockford base32: 48-bit ms time + 80 random bits). */
export function ulid(now = Date.now()) {
  let t = now, time = "";
  for (let i = 0; i < 10; i++) { time = CROCKFORD[t % 32] + time; t = Math.floor(t / 32); }
  const rnd = crypto.getRandomValues(new Uint8Array(16));
  let r = "";
  for (let i = 0; i < 16; i++) r += CROCKFORD[rnd[i] % 32];
  return time + r;
}

export const BACKOFF_S = [0.5, 1, 2, 4, 8];

export class DeviceChannel {
  /**
   * @param {object} o
   * @param {string} o.url            ws(s)://host/ws/device?sim=1
   * @param {() => object} o.hello    fields for the hello frame (device_id, app_version, fixed_cache_version, wake_engine)
   * @param {() => object} o.heartbeat fields for each heartbeat (battery, thermal, in_session)
   * @param {(cmd: object) => (void|{ok:boolean,error?:string})} o.onCommand  runs a server command (not welcome)
   * @param {(w: object) => void} o.onWelcome
   * @param {(state: string, detail?: string) => void} o.onState   connecting|open|online|closed|auth_failed
   * @param {(on: boolean) => void} o.onOffline
   * @param {(dir: string, frame: object) => void} o.onFrame       log hook
   * @param {number} [o.heartbeatMs=10000]
   * @param {number} [o.offlineAfterMs=30000]
   */
  constructor(o) {
    Object.assign(this, { heartbeatMs: 10000, offlineAfterMs: 30000 }, o);
    this.ws = null;
    this.attempt = 0;
    this.seen = new Map();           // command id → ack result (de-dup; insertion-ordered for pruning)
    this.hbTimer = 0;
    this.retryTimer = 0;
    this.offlineTimer = 0;
    this.offline = false;
    this.welcomed = false;
    this.stopped = false;
    this.disconnectedSince = Date.now();
  }

  start() {
    this.stopped = false;
    this._armOffline();
    this._connect();
  }

  /** Test/dev hook: drop the socket as if the network failed (reconnect logic runs as usual). */
  drop() { try { this.ws && this.ws.close(); } catch (e) { /* already closed */ } }

  get online() { return !!(this.ws && this.ws.readyState === WebSocket.OPEN && this.welcomed); }

  /** Send an iPad → server event. Returns false if the socket is not open (events are not queued). */
  send(type, fields = {}) {
    const frame = { id: ulid(), type, ...fields };
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.onFrame("drop", frame);
      return false;
    }
    this.ws.send(JSON.stringify(frame));
    this.onFrame("out", frame);
    return true;
  }

  _connect() {
    if (this.stopped) return;
    clearTimeout(this.retryTimer);
    this.welcomed = false;
    this.onState("connecting");
    let ws;
    try { ws = new WebSocket(this.url); } catch (e) { this._scheduleRetry(); return; }
    this.ws = ws;
    ws.onopen = () => {
      this.onState("open");
      this.send("hello", this.hello());
    };
    ws.onmessage = ev => this._onMessage(ev.data);
    ws.onclose = ev => {
      if (this.ws !== ws) return;
      clearInterval(this.hbTimer);
      const wasOnline = this.welcomed;
      this.welcomed = false;
      if (wasOnline) { this.disconnectedSince = Date.now(); this._armOffline(); }
      this.onState(ev.code === 4401 ? "auth_failed" : "closed", `code ${ev.code}`);
      this._scheduleRetry();
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  _scheduleRetry() {
    if (this.stopped) return;
    const base = BACKOFF_S[Math.min(this.attempt, BACKOFF_S.length - 1)];
    this.attempt++;
    const delay = base * 1000 * (0.8 + Math.random() * 0.4);
    this.nextRetryMs = Math.round(delay);
    this.retryTimer = setTimeout(() => this._connect(), delay);
  }

  _armOffline() {
    clearTimeout(this.offlineTimer);
    if (this.offline) return;
    const left = Math.max(0, this.offlineAfterMs - (Date.now() - this.disconnectedSince));
    this.offlineTimer = setTimeout(() => {
      if (!this.welcomed && !this.offline) { this.offline = true; this.onOffline(true); }
    }, left);
  }

  _onMessage(data) {
    let msg;
    try { msg = JSON.parse(data); } catch (e) { this.onFrame("bad", { raw: String(data).slice(0, 200) }); return; }
    if (!msg || typeof msg.type !== "string") return;
    this.onFrame("in", msg);
    if (msg.type === "welcome") {
      this.welcomed = true;
      this.attempt = 0;
      clearTimeout(this.offlineTimer);
      if (this.offline) { this.offline = false; this.onOffline(false); }
      clearInterval(this.hbTimer);
      this.hbTimer = setInterval(() => this.send("heartbeat", this.heartbeat()), this.heartbeatMs);
      this.onState("online");
      this.onWelcome(msg);
      return;
    }
    if (!msg.id) return;                       // every command carries an id
    if (this.seen.has(msg.id)) {               // resend: ack again, do not run twice
      this.onFrame("dup", { ref: msg.id });
      this.send("ack", { ref: msg.id, ...this.seen.get(msg.id) });
      return;
    }
    let res;
    try { res = this.onCommand(msg) || { ok: true }; }
    catch (e) { res = { ok: false, error: String(e && e.message || e).slice(0, 200) }; }
    this.seen.set(msg.id, res);
    if (this.seen.size > 1000) this.seen.delete(this.seen.keys().next().value);
    this.send("ack", { ref: msg.id, ...res });
  }
}
