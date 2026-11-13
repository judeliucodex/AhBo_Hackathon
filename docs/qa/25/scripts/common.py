"""QA 25 shared helpers: login, /sim/ page with a WebSocket hook, trace polling, screenshots."""
import json
import os
import time

BASE = os.environ.get("QA_BASE", "http://ahbo-qa25-api:8000")
PASSWORD = "qa-test-password"
OUT = os.environ.get("QA_OUT", "/scratch/out")
os.makedirs(OUT, exist_ok=True)

# Records every frame the page's WebSocket receives/sends (the task's "hook WebSocket in the page").
WS_HOOK = r"""
(() => {
  if (window.__wsHooked) return; window.__wsHooked = true;
  const Orig = window.WebSocket;
  window.__wsframes = [];
  const rec = (dir, data, extra) => {
    let obj = null;
    try { obj = typeof data === "string" ? JSON.parse(data) : null; } catch (e) { obj = { raw: String(data).slice(0, 200) }; }
    window.__wsframes.push(Object.assign({ t: Math.round(performance.now()), wall: Date.now(), dir, obj }, extra || {}));
  };
  class WS extends Orig {
    constructor(...a) {
      super(...a);
      this.addEventListener("open", () => rec("open", null, { url: String(a[0]) }));
      this.addEventListener("message", ev => rec("in", ev.data));
      this.addEventListener("close", ev => rec("close", null, { code: ev.code }));
    }
    send(d) { rec("out", d); return super.send(d); }
  }
  window.WebSocket = WS;
})();
"""


def new_context(browser, **kw):
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, **kw)
    ctx.add_init_script(WS_HOOK)
    return ctx


def login(ctx):
    r = ctx.request.post(BASE + "/login", form={"password": PASSWORD}, max_redirects=0)
    assert r.status in (200, 302, 303), f"login status {r.status}: {r.text()[:200]}"
    names = [c["name"] for c in ctx.cookies()]
    assert names, "no cookie after login"
    return names


def api(ctx, method, path, body=None):
    fn = getattr(ctx.request, method.lower())
    r = fn(BASE + path, data=json.dumps(body) if body is not None else None,
           headers={"Content-Type": "application/json"} if body is not None else None)
    try:
        return r.status, r.json()
    except Exception:
        return r.status, r.text()[:300]


def open_sim(ctx, query=""):
    page = ctx.new_page()
    page.goto(BASE + "/sim/" + query, wait_until="load")
    page.wait_for_function("document.getElementById('conn').dataset.state === 'online'", timeout=20000)
    # the face reports ready → at least one face command has been forwarded (replay starts with reset)
    page.wait_for_function("window.__simtrace.some(e => e.dir === 'face')", timeout=20000)
    page.wait_for_timeout(600)
    return page


def trace(page):
    return page.evaluate("window.__simtrace")


def wsframes(page):
    return page.evaluate("window.__wsframes")


def mark(page):
    """Index into the WS frame log; use with since= to only look at newer frames."""
    return page.evaluate("window.__wsframes.length")


JS_FIND = """
([since, src]) => {
  const pred = eval(src);
  const fr = window.__wsframes;
  for (let i = since; i < fr.length; i++) { const f = fr[i]; if (f.dir === 'in' && f.obj && pred(f.obj)) return i + 1; }
  return 0;
}
"""


def wait_in(page, pred_js, since=0, timeout=30.0, label=""):
    """Wait for an inbound WS frame matching the JS predicate `f => ...`. Returns the index after it (or raises)."""
    end = time.time() + timeout
    while time.time() < end:
        i = page.evaluate(JS_FIND, [since, pred_js])
        if i:
            return i
        page.wait_for_timeout(40)
    raise TimeoutError(f"no inbound frame for {label or pred_js} within {timeout}s")


JS_FIND_FACE = """
([since, src]) => {
  const pred = eval(src);
  const tr = window.__simtrace;
  for (let i = since; i < tr.length; i++) { const e = tr[i]; if (e.dir === 'face' && pred(e.obj)) return i + 1; }
  return 0;
}
"""


def wait_face(page, pred_js, since=0, timeout=30.0, label=""):
    """Wait for a face command (what the sim forwarded to the face iframe) matching `c => ...`."""
    end = time.time() + timeout
    while time.time() < end:
        i = page.evaluate(JS_FIND_FACE, [since, pred_js])
        if i:
            return i
        page.wait_for_timeout(40)
    raise TimeoutError(f"no face command for {label or pred_js} within {timeout}s")


def shot(page, name, sub=""):
    d = os.path.join(OUT, sub) if sub else OUT
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".png")
    page.screenshot(path=p)
    return p


def face_shot(page, name, sub=""):
    """Only the iPad bezel (the face), for side-by-side comparison with the kit screenshots."""
    d = os.path.join(OUT, sub) if sub else OUT
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".png")
    page.locator("#bezel").screenshot(path=p)
    return p


def dump(obj, name, sub=""):
    d = os.path.join(OUT, sub) if sub else OUT
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return p


def face_dom(page):
    """What the face actually renders (read from the iframe DOM)."""
    fr = page.frame_locator("#face")
    return fr.locator("body").evaluate("""() => {
      const q = s => Array.from(document.querySelectorAll(s));
      const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
                          return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.01; };
      const ok = document.getElementById('okbtn'); const okr = ok.getBoundingClientRect();
      return {
        mood: document.getElementById('face').className,
        bodyClass: document.body.className,
        heard: document.getElementById('heard').textContent,
        caption: document.getElementById('caption').textContent,
        cards: q('#cards .chip').map(c => ({ id: c.dataset.id, cls: c.className, text: c.textContent.trim() })),
        steps: q('#steps *').filter(e => e.children.length === 0 && e.textContent.trim()).map(e => ({ cls: e.className, pcls: e.parentElement.className, text: e.textContent.trim() })),
        stepsHtml: document.getElementById('steps').innerHTML,
        ok: { visible: vis(ok), cls: ok.className, text: ok.textContent.trim(), w: Math.round(okr.width), h: Math.round(okr.height) },
        offline: { cls: document.getElementById('offline').className, visible: vis(document.getElementById('offline')) },
        vw: innerWidth, vh: innerHeight,
      };
    }""")
