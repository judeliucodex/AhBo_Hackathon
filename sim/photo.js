/* capture_photo (PRD §10.1, §25.2 PhotoCapture.swift, §26.3 browser iPad).
 *
 * Front camera via getUserMedia only when the tester has allowed it (panel "Camera" toggle) — a permission
 * prompt must never block a fall check. Otherwise the placeholder: `fall_placeholder.jpg` next to this page if
 * the platform serves one, else a dark JPEG drawn here. One frame, JPEG quality 0.7, longest side ≤ 1280 px,
 * then multipart POST /media/photo {file, incident_id} with the operator cookie → {ok, photo_id}.
 */

const MAX_SIDE = 1280;
const QUALITY = 0.7;

function toJpeg(canvas) {
  return new Promise((res, rej) => canvas.toBlob(b => b ? res(b) : rej(new Error("toBlob failed")), "image/jpeg", QUALITY));
}

function fit(w, h) {
  const k = Math.min(1, MAX_SIDE / Math.max(w, h));
  return [Math.round(w * k), Math.round(h * k)];
}

async function fromCamera() {
  const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 1280 } }, audio: false });
  try {
    const v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.srcObject = stream;
    await v.play();
    await new Promise(r => setTimeout(r, 300));            // let exposure settle for one frame
    const [w, h] = fit(v.videoWidth || 1280, v.videoHeight || 720);
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    c.getContext("2d").drawImage(v, 0, 0, w, h);
    return await toJpeg(c);
  } finally {
    stream.getTracks().forEach(t => t.stop());               // the camera is never left on
  }
}

async function placeholder() {
  try {
    const r = await fetch("fall_placeholder.jpg", { credentials: "same-origin" });
    if (r.ok && (r.headers.get("content-type") || "").startsWith("image/")) return { blob: await r.blob(), source: "placeholder_file" };
  } catch (e) { /* fall through to the drawn one */ }
  const c = document.createElement("canvas"); c.width = 1280; c.height = 720;
  const g = c.getContext("2d");
  const grad = g.createLinearGradient(0, 0, 0, 720);
  grad.addColorStop(0, "#1a1a1a"); grad.addColorStop(1, "#050505");
  g.fillStyle = grad; g.fillRect(0, 0, 1280, 720);
  g.fillStyle = "#333"; g.fillRect(0, 560, 1280, 160);                 // a floor
  g.fillStyle = "#666"; g.font = "28px system-ui, sans-serif";
  g.fillText("browser iPad (sim) — placeholder photo " + new Date().toISOString(), 32, 56);
  return { blob: await toJpeg(c), source: "placeholder_drawn" };
}

/**
 * @param {string} incidentId
 * @param {boolean} useCamera
 * @returns {Promise<{photo_id: string, source: string}>}
 */
export async function captureAndUpload(incidentId, useCamera) {
  let blob, source;
  if (useCamera && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    try { blob = await fromCamera(); source = "camera"; }
    catch (e) { ({ blob, source } = await placeholder()); source += " (camera failed: " + e.name + ")"; }
  } else {
    ({ blob, source } = await placeholder());
  }
  const fd = new FormData();
  fd.append("file", blob, "photo.jpg");
  fd.append("incident_id", incidentId);
  const r = await fetch("/media/photo", { method: "POST", body: fd, credentials: "same-origin" });
  if (!r.ok) throw new Error(`upload HTTP ${r.status}`);
  const j = await r.json();
  if (!j.ok || !j.photo_id) throw new Error("upload refused: " + (j.reason || "no photo_id"));
  return { photo_id: j.photo_id, source };
}
