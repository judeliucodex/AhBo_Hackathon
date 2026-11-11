/* "Talk (browser mic)": real voice from a laptop browser when LiveKit is not faked (PRD §26.3, §23.5,
 * §25.2 VoiceSession.swift). Loaded only when the tester turns the toggle on.
 *
 * livekit-client is pinned to an exact version on cdn.jsdelivr.net (VERIFIED to exist: 2.22.4, published
 * 2026-10-09; dist/livekit-client.esm.mjs is self-contained, no bare imports). Symbols used, checked in
 * that version's typings: Room, RoomEvent.{TrackSubscribed, ParticipantAttributesChanged, Disconnected},
 * Track.Kind.Audio, room.connect(url, token), room.startAudio(), room.registerTextStreamHandler(topic, cb),
 * TextStreamReader (async-iterable deltas, .info.attributes), localParticipant.setMicrophoneEnabled(),
 * participant.isAgent / .attributes / .audioLevel. Attribute names (livekit-agents 1.8.11 types.py):
 * lk.transcription, lk.segment_id, lk.transcribed_track_id, lk.transcription_final, lk.agent.state.
 *
 * Without LiveKit keys this path cannot be exercised end to end.
 */

export const LIVEKIT_CLIENT_URL = "https://cdn.jsdelivr.net/npm/livekit-client@2.22.4/dist/livekit-client.esm.mjs";

export class Talk {
  /** @param {{face:(cmd:object)=>void, log:(s:string)=>void, error:(code:string, detail:string)=>void}} o */
  constructor(o) { Object.assign(this, o); this.room = null; this.lk = null; this.seg = null; this.levelTimer = 0; }

  get joined() { return !!this.room; }

  async join(session) {
    if (this.room) return;
    const r = await fetch("/session", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: session.reason || "touch", mode: session.mode || "companion" }),
    });
    if (!r.ok) throw new Error(`POST /session HTTP ${r.status}`);
    const s = await r.json();
    if (s.text_only) { this.log("LiveKit is faked on the server (text_only): use the text box"); return; }
    if (!this.lk) this.lk = await import(LIVEKIT_CLIENT_URL);
    const { Room, RoomEvent, Track } = this.lk;
    const room = new Room({ adaptiveStream: false, dynacast: false });
    this.room = room;
    room.on(RoomEvent.TrackSubscribed, (track, pub, participant) => {
      if (track.kind !== Track.Kind.Audio) return;
      const el = track.attach(); el.hidden = true; document.body.append(el);
      if (participant.isAgent) this.agent = participant;
    });
    room.on(RoomEvent.ParticipantAttributesChanged, (changed, participant) => {
      if (!("lk.agent.state" in changed)) return;
      this.agent = participant;
      const st = changed["lk.agent.state"];
      if (st === "speaking") this._startLevels(); else this._stopLevels();
      if (st !== "speaking" && this.seg) this._endSeg();          // barge-in or done: end the line now
    });
    room.on(RoomEvent.Disconnected, () => { this._cleanup(); this.log("LiveKit room left"); });
    room.registerTextStreamHandler("lk.transcription", (reader, info) => this._onTranscript(reader, info));
    await room.connect(s.livekit_url, s.token);
    await room.startAudio();
    await room.localParticipant.setMicrophoneEnabled(true);
    this.log(`joined LiveKit room ${s.room}`);
  }

  async leave() {
    const room = this.room;
    this._cleanup();
    if (room) await room.disconnect();
  }

  _cleanup() { this._stopLevels(); if (this.seg) this._endSeg(); this.room = null; }

  _micTrackSid() {
    const pubs = this.room && this.room.localParticipant ? [...this.room.localParticipant.audioTrackPublications.values()] : [];
    return pubs.length ? pubs[0].trackSid : null;
  }

  async _onTranscript(reader, info) {
    const a = reader.info.attributes || {};
    const segId = a["lk.segment_id"] || reader.info.id;
    const mine = a["lk.transcribed_track_id"] && a["lk.transcribed_track_id"] === this._micTrackSid();
    const local = this.room && info.identity === this.room.localParticipant.identity;
    let text = "";
    if (mine || local) {                                   // her words → heard
      for await (const delta of reader) { text += delta; this.face({ type: "heard", text }); }
      this.face({ type: "heard", text, final: a["lk.transcription_final"] === "true" });
      return;
    }
    // Ah Bo's speech: streaming caption, paced to playout by the agent (sync transcription)
    if (!this.seg || this.seg.id !== segId) {
      if (this.seg) this._endSeg();
      this.seg = { id: segId };
      this.face({ type: "say_start", id: segId });
    }
    for await (const delta of reader) {
      if (!this.seg || this.seg.id !== segId) return;      // cut by barge-in
      text += delta;
      this.face({ type: "say_update", id: segId, text, revealed: text.length });   // JS length = UTF-16 units
    }
    if (this.seg && this.seg.id === segId) this._endSeg();
  }

  _endSeg() { const id = this.seg.id; this.seg = null; this.face({ type: "say_end", id }); }

  _startLevels() {
    clearInterval(this.levelTimer);
    this.levelTimer = setInterval(() => {
      if (this.agent) this.face({ type: "level", v: Math.min(1, Math.round((this.agent.audioLevel || 0) * 100) / 100) });
    }, 50);
  }

  _stopLevels() { clearInterval(this.levelTimer); this.levelTimer = 0; }
}
