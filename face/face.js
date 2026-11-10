/* Ah Bo face engine: production version of the approved demo face.
 *
 * The face is a passive renderer. Native code (Swift, in a WKWebView) or the dev
 * harness sends it commands; it renders them and reports a few events back.
 *
 *   In:  window.AhBo.apply(cmd)      cmd = object or JSON string (see CONTRACT below)
 *   Out: AhBoBridge.emit(evt)        defined in bridge.js
 *
 * Idle life (blink, double blink, glance) and the birth "pop" are copied from the
 * demo with the same timings. Do not change timings, sizes or colours.
 *
 * CONTRACT (every command has a "type"; unknown types are ignored and reported as an error event)
 *   mood        {mood:"idle"|"listening"|"thinking"|"curious"|"warm"|"alarm"|"sleepy"}
 *   expr        {expr:"blink"|"happy"|"alert"|"wake"}               one-shot expression
 *   heard       {text, final?:bool}                                 her words, dim, above the caption ("" clears)
 *   say_start   {id, text?, from?:"ahbo"|"family", fromName?, segments?, durationSec?, paged?}
 *   say_update  {id, text?, revealed?}                               streaming text and/or chars revealed so far
 *   say_end     {id}                                                 audio finished; reveal all, fade in 2 s
 *   level       {v:0..1}                                             mouth amplitude from native audio (~20 Hz)
 *   card        {card:{id, kind, label, small?, icon?, when?, persistent?, pill?, wave?, photo?}}
 *   card_update {id, patch:{label?, small?, playing?, icon?}}
 *   card_clear  {id?}                                                no id = clear all non-persistent cards
 *   steps       {group:{id, title?, steps:[{id, label, doneLabel?, state?}]}}
 *   step        {group, id, state:"pending"|"active"|"done"|"failed", label?}
 *   steps_clear {group?}                                             no group = clear all groups
 *   ok_button   {show:bool, label?}
 *   countdown   {id, n}                                              emits countdown_done {id}
 *   flash       {}
 *   birth       {}                                                   eyes pop in, left then right
 *   offline     {on:bool}
 *   reset       {}
 *
 * EVENTS (web -> native)
 *   ready {version} | tap {target:"face"|"card", id?} | ok_tapped {} | countdown_done {id}
 *   say_done {id} | birth_done {} | error {message}
 */
(function(){
  "use strict";
  const VERSION = "1.0.0";
  const $ = id => document.getElementById(id);
  const stage = $("stage"), face = $("face"), eyesEl = $("eyes"), mouth = $("mouth"),
        heardEl = $("heard"), caption = $("caption"), cardsEl = $("cards"),
        stepsEl = $("steps"), okBtn = $("okbtn"), offlineEl = $("offline");
  const eyeEls = [...document.querySelectorAll(".eye")];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const emit = evt => { try { window.AhBoBridge.emit(evt); } catch(e){ /* bridge missing: dev only */ } };

  const MOODS = ["idle","listening","thinking","curious","warm","alarm","sleepy"];
  let mood = "idle";
  let busy = false;          // true while speaking or in a scripted scene: no glancing

  /* ---------- Audio (only for UI sounds: pop, beep, shutter; speech is native) ---------- */
  let actx = null;
  function audio(){
    if (!actx){ try { actx = new (window.AudioContext || window.webkitAudioContext)(); } catch(e){ actx = null; } }
    try { if (actx && actx.state !== "running") actx.resume(); } catch(e){}
    return actx;
  }
  ["pointerdown","keydown","touchstart"].forEach(t => addEventListener(t, audio, { once:true, passive:true }));
  function popSound(freq){
    const a = audio(); if (!a) return;
    const t = a.currentTime, o = a.createOscillator(), g = a.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(freq, t); o.frequency.exponentialRampToValueAtTime(freq * 2.2, t + .09);
    g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(.35, t + .012); g.gain.exponentialRampToValueAtTime(.0001, t + .16);
    o.connect(g).connect(a.destination); o.start(t); o.stop(t + .18);
  }
  function tone(freq, at, len, vol = .25, type = "sine"){
    const a = audio(); if (!a) return;
    const t = a.currentTime + at, o = a.createOscillator(), g = a.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(vol, t + .02); g.gain.exponentialRampToValueAtTime(.0001, t + len);
    o.connect(g).connect(a.destination); o.start(t); o.stop(t + len + .02);
  }
  function shutter(){
    const a = audio(); if (!a) return;
    const b = a.createBuffer(1, Math.floor(a.sampleRate * .08), a.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
    const s = a.createBufferSource(), g = a.createGain(); g.gain.value = .5;
    s.buffer = b; s.connect(g).connect(a.destination); s.start();
  }

  /* ---------- Idle life: blinking + glancing (demo timings) ---------- */
  function blink(){
    eyeEls.forEach(e => e.classList.add("blink"));
    setTimeout(() => eyeEls.forEach(e => e.classList.remove("blink")), 110);
  }
  (function blinkLoop(){
    const slow = mood === "sleepy" ? 2.2 : 1;
    const next = (2200 + Math.random() * 3200) * slow;
    setTimeout(() => { blink(); if (mood !== "sleepy" && Math.random() < .25) setTimeout(blink, 220); blinkLoop(); }, next);
  })();
  (function glanceLoop(){
    setTimeout(() => {
      if (!busy && mood === "idle" && !reduce){
        const x = (Math.random() * 2 - 1) * 5, y = (Math.random() * 2 - 1) * 2.5;
        eyesEl.style.transform = Math.random() < .35 ? "" : `translate(${x}vmin, ${y}vmin)`;
      }
      glanceLoop();
    }, 1600 + Math.random() * 2400);
  })();

  function setMood(m){
    if (!MOODS.includes(m)) throw new Error("unknown mood " + m);
    MOODS.forEach(k => face.classList.remove(k));
    mood = m;
    if (m !== "idle") face.classList.add(m);
    if (m !== "idle") eyesEl.style.transform = "";   // CSS mood transforms take over
  }
  function happy(ms = 1400){
    if (mood === "alarm") return;
    face.classList.add("happy");
    setTimeout(() => face.classList.remove("happy"), ms);
  }
  function alert(ms = 350){
    face.classList.add("alert");
    setTimeout(() => face.classList.remove("alert"), ms);
  }
  // Wake: a little jump, widen, then smile (demo wake(), without the speech)
  function wake(){
    face.classList.add("alert");
    eyesEl.style.transform = "translateY(-2vmin)";
    setTimeout(() => { eyesEl.style.transform = ""; }, 260);
    setTimeout(() => { face.classList.remove("alert"); face.classList.add("happy"); }, 500);
    setTimeout(() => face.classList.remove("happy"), 1300);
  }

  /* ---------- Birth: eyes pop in one at a time (demo birth()) ---------- */
  function burstAt(e){
    const r = e.getBoundingClientRect(), size = e.offsetWidth;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const ring = document.createElement("div"); ring.className = "burst";
    ring.style.cssText = `left:${cx}px;top:${cy}px;width:${size}px;height:${size}px`;
    document.body.append(ring); setTimeout(() => ring.remove(), 850);
    if (reduce) return;
    for (let k = 0; k < 10; k++){
      const a = k / 10 * Math.PI * 2 + Math.random() * .4, d = size * (.75 + Math.random() * .45);
      const s = document.createElement("i"); s.className = "spark";
      s.style.cssText = `left:${cx}px;top:${cy}px;--dx:${Math.cos(a) * d}px;--dy:${Math.sin(a) * d}px`;
      document.body.append(s); setTimeout(() => s.remove(), 800);
    }
  }
  function popEye(i, freq){
    const e = eyeEls[i];
    e.classList.add("born", "pop"); popSound(freq); burstAt(e);
    setTimeout(() => e.classList.remove("pop"), 1150);
  }
  function scene(steps){ let t = 0; steps.forEach(([d, fn]) => { t += d; setTimeout(fn, t); }); }
  function birth(){
    busy = true;
    face.classList.remove("happy", "alert", "talking");
    eyesEl.style.transform = "";
    eyeEls.forEach(e => e.classList.remove("born", "pop"));
    face.classList.add("unborn");
    scene([
      [1500, () => popEye(0, 520)],
      [1300, () => popEye(1, 660)],
      [700,  blink],
      [500,  () => { eyesEl.style.transform = "translate(-4vmin,0)"; }],
      [700,  () => { eyesEl.style.transform = "translate(4vmin,0)"; }],
      [700,  () => { eyesEl.style.transform = ""; face.classList.add("happy"); }],
      [1400, () => { face.classList.remove("happy", "unborn"); busy = false; emit({ type:"birth_done" }); }],
    ]);
  }

  /* ---------- "Heard" line: her words ---------- */
  let heardTimer = 0;
  function heard(text, final){
    clearTimeout(heardTimer);
    heardEl.textContent = text || "";
    heardEl.classList.toggle("on", !!text);
    if (text && final) heardTimer = setTimeout(() => heardEl.classList.remove("on"), 6000);
  }

  /* ---------- Caption (demo buildCaption / revealUntil, extended for streaming) ---------- */
  // say = {id, text, tokens, revealedTo, segments, durationSec, t0, paged, from, ended}
  let say = null, captionTimer = 0, raf = 0;
  const SENT_END = /[。？！?!]$/;
  function clearCaption(){
    clearTimeout(captionTimer); cancelAnimationFrame(raf);
    caption.classList.remove("gone", "family"); delete caption.dataset.from; caption.innerHTML = "";
  }
  function appendTokens(fromIdx){
    // Adds spans for say.text[fromIdx..]; one span per character (Cantonese has no spaces)
    const parts = Array.from(say.text.slice(fromIdx));
    let idx = fromIdx;
    let sent = say.tokens.length ? say.tokens[say.tokens.length - 1].sent + (say.lastWasEnd ? 1 : 0) : 0;
    parts.forEach(p => {
      const s = document.createElement("span"); s.className = "w"; s.textContent = p;
      caption.append(s);
      say.tokens.push({ el:s, start:idx, end:idx + p.length, sent });
      idx += p.length;
      say.lastWasEnd = SENT_END.test(p);
      if (say.lastWasEnd) sent++;
    });
    say.builtTo = idx;
    applyPaging();
  }
  function applyPaging(){
    if (!say) return;
    const paged = say.pagedOverride != null ? say.pagedOverride : say.text.length > 36;
    if (!paged){ say.tokens.forEach(t => { t.el.hidden = false; }); return; }
    const lit = say.tokens.filter(t => t.start < say.revealedTo);
    const cur = (lit.pop() || say.tokens[0] || { sent:0 }).sent;
    say.tokens.forEach(t => { t.el.hidden = t.sent !== cur; });
  }
  function revealUntil(charIdx){
    if (!say || charIdx <= say.revealedTo) return;
    say.revealedTo = charIdx;
    say.tokens.forEach(t => { if (t.start < charIdx) t.el.classList.add("on"); });
    applyPaging();
    // Cards with a "when" phrase light up as it is spoken
    [...cardsEl.children].forEach(c => {
      if (c.classList.contains("on") || !c.dataset.when) return;
      const at = say.text.indexOf(c.dataset.when);
      if (at > -1 && at < charIdx) c.classList.add("on");
    });
  }
  function sayStart(c){
    if (!c.id) throw new Error("say_start needs id");
    clearCaption();
    busy = true;
    say = { id:c.id, text:c.text || "", tokens:[], revealedTo:0, builtTo:0, lastWasEnd:false,
            segments:c.segments || null, durationSec:c.durationSec || 0, t0:performance.now(),
            from:c.from || "ahbo", ended:false, pagedOverride:c.paged != null ? !!c.paged : null };
    if (say.from === "family"){ caption.classList.add("family"); caption.dataset.from = c.fromName || "家人"; }
    appendTokens(0);
    if (say.from === "ahbo"){ alert(); startMouth(); }
    if (say.segments || say.durationSec) raf = requestAnimationFrame(clockLoop);
    else revealUntil(say.text.length + 1);   // streaming mode: native drives say_update
  }
  // Audio-clock reveal for fixed clips (segments: [firstChar, endChar, startSec, endSec])
  function clockLoop(){
    if (!say || say.ended) return;
    const t = (performance.now() - say.t0) / 1000;
    let upto = 0; speakingNow = false;
    if (say.segments){
      for (const [s, e, a, b] of say.segments){
        if (t >= b) upto = e;
        else if (t >= a){ upto = s + (t - a) / (b - a) * (e - s); speakingNow = true; }
      }
    } else {
      upto = Math.min(1, t / say.durationSec) * say.text.length; speakingNow = upto < say.text.length;
    }
    revealUntil(Math.ceil(upto));
    raf = requestAnimationFrame(clockLoop);
  }
  function sayUpdate(c){
    if (!say || c.id !== say.id) return;           // stale update for an interrupted line
    if (typeof c.text === "string" && c.text.length >= say.builtTo && c.text.startsWith(say.text.slice(0, say.builtTo))){
      say.text = c.text; appendTokens(say.builtTo);
    } else if (typeof c.text === "string" && c.text !== say.text){
      // Text was rewritten (rare): rebuild
      const keep = say.revealedTo; caption.innerHTML = ""; say.tokens = []; say.revealedTo = 0;
      say.text = c.text; say.lastWasEnd = false; appendTokens(0); revealUntil(Math.min(keep, say.text.length));
    }
    if (typeof c.revealed === "number") revealUntil(c.revealed);
    else if (!say.segments && !say.durationSec) revealUntil(say.text.length + 1);
    if (say.from === "ahbo") speakingNow = true;
  }
  function sayEnd(c){
    if (!say || (c.id && c.id !== say.id)) return;
    const s = say;
    s.ended = true; cancelAnimationFrame(raf);
    revealUntil(s.text.length + 1);
    [...cardsEl.children].forEach(el => el.classList.add("on"));
    speakingNow = false; stopMouth(); busy = false;
    captionTimer = setTimeout(() => caption.classList.add("gone"), 2000);
    if (s.from === "ahbo") happy();
    emit({ type:"say_done", id:s.id });
  }

  /* ---------- Mouth: native audio level if available, else the demo's random flap ---------- */
  let mouthTimer = 0, speakingNow = false, lastLevelAt = 0, levelV = 0;
  function startMouth(){
    face.classList.add("talking");
    let up = false;
    clearTimeout(mouthTimer);
    const tick = () => {
      let h, w;
      if (performance.now() - lastLevelAt < 300){
        h = 1.2 + levelV * 4.4; w = 8.5 - levelV * 1.5;
      } else {
        up = speakingNow ? !up : false;
        h = up ? 2.4 + Math.random() * 3.2 : 0.9 + Math.random() * 0.8;
        w = up ? 7 + Math.random() * 2.5 : 8.5;
      }
      mouth.style.height = h + "vmin"; mouth.style.width = w + "vmin";
      mouth.style.transition = "height .09s ease, width .09s ease, opacity .25s";
      mouthTimer = setTimeout(tick, 85 + Math.random() * 75);
    };
    tick();
  }
  function stopMouth(){
    clearTimeout(mouthTimer);
    mouth.style.height = "1.2vmin"; mouth.style.width = "8vmin";
    face.classList.remove("talking");
  }
  function level(v){
    levelV = Math.max(0, Math.min(1, Number(v) || 0)); lastLevelAt = performance.now();
    speakingNow = levelV > .04;
  }

  /* ---------- Cards ---------- */
  const KIND_ICON = { med:"", event:"🏥", plan:"🚌", voice_note:"🎙", incident:"⚠️", action:"📞",
                      message:"💬", story:"📖", today:"☀️", photo:"📷", activity:"🎤", info:"ℹ️" };
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, ch => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[ch]));
  function cssVar(v){ return v && v.startsWith("--") ? getComputedStyle(document.documentElement).getPropertyValue(v).trim() : v; }
  function cardHTML(k){
    let lead;
    if (k.kind === "med"){
      const [a, b] = k.pill || ["--pill-a", "--pill-a"];
      lead = `<span class="pill"><i style="background:${esc(cssVar(a))}"></i><i style="background:${esc(cssVar(b))}"></i></span>`;
    } else if (k.photo){
      lead = `<img class="photo" alt="" src="${esc(k.photo)}">`;
    } else {
      lead = `<span class="icon">${esc(k.icon || KIND_ICON[k.kind] || "•")}</span>`;
    }
    const wave = (k.wave || k.kind === "voice_note") ? `<span class="wave"><i></i><i></i><i></i><i></i><i></i></span>` : "";
    const small = k.small ? ` <small>${esc(k.small)}</small>` : "";
    return `${lead}<span><span class="lbl">${esc(k.label)}</span>${small}${wave}</span>`;
  }
  function card(k){
    if (!k || !k.id || !k.label) throw new Error("card needs id and label");
    let c = cardsEl.querySelector(`[data-id="${CSS.escape(k.id)}"]`);
    if (!c){ c = document.createElement("div"); cardsEl.append(c); }
    c.className = "chip"; c.dataset.id = k.id; c.dataset.kind = k.kind || "info";
    if (k.persistent) c.classList.add("persistent");
    if (k.kind === "incident") c.classList.add("incident");
    if (k.when) c.dataset.when = k.when; else delete c.dataset.when;
    c.innerHTML = cardHTML(k);
    // While Ah Bo is speaking, a card with a "when" phrase waits until that phrase is
    // revealed (or until say_end). Otherwise it appears on the next frame.
    const live = say && !say.ended && k.when;
    const at = live ? say.text.indexOf(k.when) : -1;
    if (!live || (at > -1 && at < say.revealedTo)) requestAnimationFrame(() => requestAnimationFrame(() => c.classList.add("on")));
  }
  cardsEl.addEventListener("click", ev => {
    const c = ev.target.closest(".chip"); if (!c) return;
    ev.stopPropagation(); emit({ type:"tap", target:"card", id:c.dataset.id });
  });
  function cardUpdate(id, patch){
    const c = cardsEl.querySelector(`[data-id="${CSS.escape(id)}"]`); if (!c) return;
    if (patch.label != null) c.querySelector(".lbl").textContent = patch.label;
    if (patch.small != null){
      let s = c.querySelector("small");
      if (!s){ s = document.createElement("small"); c.querySelector(".lbl").after(" ", s); }
      s.textContent = patch.small;
    }
    if (patch.icon != null){ const i = c.querySelector(".icon"); if (i) i.textContent = patch.icon; }
    if (patch.playing != null) c.classList.toggle("playing", !!patch.playing);
    c.classList.add("on");
  }
  function cardClear(id){
    const list = id ? [cardsEl.querySelector(`[data-id="${CSS.escape(id)}"]`)] : [...cardsEl.querySelectorAll(".chip:not(.persistent)")];
    list.filter(Boolean).forEach(c => { c.classList.remove("on"); setTimeout(() => c.remove(), 420); });
  }

  /* ---------- Steps: check-mark progress ---------- */
  const TICK_SVG = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle class="ring" cx="12" cy="12" r="10.5"/><circle class="disc" cx="12" cy="12" r="10.5"/><path class="mark" d="M7 12.5l3.3 3.3L17.2 8.6"/><path class="cross" d="M8.5 8.5l7 7M15.5 8.5l-7 7"/></svg>`;
  const STEP_STATES = ["pending","active","done","failed"];
  function stepEl(s){
    const li = document.createElement("li");
    li.className = "step " + (STEP_STATES.includes(s.state) ? s.state : "pending");
    li.dataset.id = s.id;
    if (s.doneLabel) li.dataset.doneLabel = s.doneLabel;
    li.innerHTML = `<span class="tick">${TICK_SVG}</span><span class="label"></span>`;
    li.querySelector(".label").textContent = (s.state === "done" && s.doneLabel) ? s.doneLabel : s.label;
    return li;
  }
  function stepsGroup(g){
    if (!g || !g.id || !Array.isArray(g.steps)) throw new Error("steps needs group.id and group.steps");
    let el = stepsEl.querySelector(`.stepgroup[data-id="${CSS.escape(g.id)}"]`);
    const fresh = !el;
    if (fresh){ el = document.createElement("section"); el.className = "stepgroup"; el.dataset.id = g.id; }
    el.innerHTML = (g.title ? `<h2 class="steps-title"></h2>` : "") + `<ol class="steplist"></ol>`;
    if (g.title) el.querySelector(".steps-title").textContent = g.title;
    const ol = el.querySelector("ol");
    g.steps.forEach(s => ol.append(stepEl(s)));
    if (fresh) stepsEl.append(el);
    document.body.classList.add("has-steps");
  }
  function step(c){
    const g = stepsEl.querySelector(`.stepgroup[data-id="${CSS.escape(c.group)}"]`); if (!g) return;
    let li = g.querySelector(`.step[data-id="${CSS.escape(c.id)}"]`);
    if (!li){ li = stepEl({ id:c.id, label:c.label || "", state:"pending" }); g.querySelector("ol").append(li); }
    if (!STEP_STATES.includes(c.state)) throw new Error("bad step state " + c.state);
    if (c.label) li.querySelector(".label").textContent = c.label;
    if (c.state === "done" && li.dataset.doneLabel && !c.label) li.querySelector(".label").textContent = li.dataset.doneLabel;
    STEP_STATES.forEach(k => li.classList.remove(k));
    void li.offsetWidth;                 // restart the check-mark draw animation
    li.classList.add(c.state);
  }
  function stepsClear(group){
    const list = group ? [stepsEl.querySelector(`.stepgroup[data-id="${CSS.escape(group)}"]`)] : [...stepsEl.children];
    list.filter(Boolean).forEach(g => { g.classList.add("leaving"); setTimeout(() => {
      g.remove(); if (!stepsEl.children.length) document.body.classList.remove("has-steps");
    }, 420); });
  }

  /* ---------- Fall check widgets ---------- */
  function okButton(show, label){
    if (label) okBtn.textContent = label;
    okBtn.classList.toggle("on", !!show);
  }
  okBtn.addEventListener("click", ev => { ev.stopPropagation(); emit({ type:"ok_tapped" }); });
  function countdown(id, n){
    const c = document.createElement("div"); c.className = "chip count on"; c.dataset.id = id || "countdown";
    cardsEl.append(c);
    const tick = k => {
      if (!c.isConnected) return;               // cleared early (she said 我冇事)
      if (k === 0){ c.remove(); emit({ type:"countdown_done", id }); return; }
      c.innerHTML = `<span class="icon">⏳</span><span class="num">${k}</span>`;
      tone(1046, 0, .12, .2);
      setTimeout(() => tick(k - 1), 1000);
    };
    tick(Math.max(1, Math.min(60, n | 0)));
  }
  function flash(){
    const f = document.createElement("div"); f.className = "flash";
    document.body.append(f); setTimeout(() => f.remove(), 550);
    shutter();
  }

  function reset(){
    clearCaption(); say = null; stopMouth(); busy = false;
    heard("", false); okButton(false);
    cardsEl.innerHTML = ""; stepsEl.innerHTML = ""; document.body.classList.remove("has-steps");
    face.classList.remove("happy", "alert", "unborn"); eyeEls.forEach(e => e.classList.remove("born", "pop"));
    setMood("idle"); eyesEl.style.transform = "";
  }

  /* ---------- Command dispatch ---------- */
  function apply(cmd){
    try {
      if (typeof cmd === "string") cmd = JSON.parse(cmd);
      if (!cmd || typeof cmd.type !== "string") throw new Error("command needs a type");
      switch (cmd.type){
        case "mood":        setMood(cmd.mood); if (cmd.mood === "alarm"){ tone(880, 0, .18, .3, "square"); tone(660, .22, .18, .3, "square"); } break;
        case "expr": {
          const fx = { blink, happy:() => happy(), alert:() => alert(), wake }[cmd.expr];
          if (!fx) throw new Error("unknown expr " + cmd.expr);
          fx(); break;
        }
        case "heard":       heard(cmd.text, !!cmd.final); break;
        case "say_start":   sayStart(cmd); break;
        case "say_update":  sayUpdate(cmd); break;
        case "say_end":     sayEnd(cmd); break;
        case "level":       level(cmd.v); break;
        case "card":        card(cmd.card); break;
        case "card_update": cardUpdate(cmd.id, cmd.patch || {}); break;
        case "card_clear":  cardClear(cmd.id); break;
        case "steps":       stepsGroup(cmd.group); break;
        case "step":        step(cmd); break;
        case "steps_clear": stepsClear(cmd.group); break;
        case "ok_button":   okButton(cmd.show, cmd.label); break;
        case "countdown":   countdown(cmd.id, cmd.n); break;
        case "flash":       flash(); break;
        case "birth":       birth(); break;
        case "offline":     offlineEl.classList.toggle("on", !!cmd.on); break;
        case "reset":       reset(); break;
        default: throw new Error("unknown command type " + cmd.type);
      }
      return true;
    } catch(e){
      emit({ type:"error", message:String(e && e.message || e) });
      return false;
    }
  }

  // Taps anywhere on the face = touch-to-talk (native decides what to do)
  stage.addEventListener("click", () => emit({ type:"tap", target:"face" }));
  // Dev harness / iframe parent can drive the face with postMessage({ahbo: cmd})
  addEventListener("message", ev => { if (ev.data && ev.data.ahbo) apply(ev.data.ahbo); });

  window.AhBo = { apply, version:VERSION, get state(){ return { mood, speaking:!!(say && !say.ended), sayId:say && say.id }; } };
  emit({ type:"ready", version:VERSION });
})();
