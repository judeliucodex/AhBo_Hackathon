/* Ah Bo face <-> host bridge. Load BEFORE face.js.
 *
 * Hosts, detected in this order:
 *  1. iPad app (WKWebView): events go to window.webkit.messageHandlers.ahbo.postMessage(evt).
 *     Native sends commands with webView.evaluateJavaScript("window.AhBo.apply(<json>)").
 *  2. Inside an iframe (dev.html): events go to parent.postMessage({ahboEvent: evt}, "*").
 *  3. Plain browser tab: events are logged to the console and dispatched as a
 *     window CustomEvent("ahbo:event", {detail: evt}).
 *
 * The face never talks to the network. All data comes from the host.
 */
(function(){
  "use strict";
  const native = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.ahbo;
  const framed = window.parent && window.parent !== window;
  const mode = native ? "native" : framed ? "iframe" : "browser";
  function emit(evt){
    if (!evt || typeof evt.type !== "string") return;
    if (mode === "native"){ native.postMessage(evt); return; }
    if (mode === "iframe"){ try { window.parent.postMessage({ ahboEvent: evt }, "*"); } catch(e){} }
    try { console.log("[ahbo:event]", JSON.stringify(evt)); } catch(e){}
    window.dispatchEvent(new CustomEvent("ahbo:event", { detail: evt }));
  }
  window.AhBoBridge = { emit, mode };
  // Kiosk: hide the cursor on the real device and in ?kiosk=1
  if (mode === "native" || /[?&]kiosk=1\b/.test(location.search)) document.documentElement.classList.add("kiosk");
  document.addEventListener("DOMContentLoaded", () => {
    if (document.documentElement.classList.contains("kiosk")) document.body.classList.add("kiosk");
  });
})();
