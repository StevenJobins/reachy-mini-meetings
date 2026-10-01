// Speech bubbles: live captions from backend/ (`reachy-captions`, WebSocket port 8766), shown in VR
// and as a list on the page. Protocol: backend/README.md. Same id = update (partial -> final -> + translation).
//
// Placement: with a speaker direction (azimuth_deg, robot base frame, + = left) the bubble floats in the
// room in that direction, so it lands on the speaker in the video once the robot looks there. Without one
// it is a subtitle at the bottom of the video window.
//
// The page is served over https, so Chrome only allows ws://localhost (headset: adb reverse tcp:8766 tcp:8766).
// Wireless: expose the caption server over wss (e.g. cloudflared) and paste the URL in the Debug panel.

import * as THREE from "three";

const URL_KEY = "reachy-xr-captions-url";
const DEFAULT_URL = "ws://localhost:8766";
const SHOW_S = 10;          // a final bubble stays this long after its last update
const PARTIAL_S = 5;        // a partial without updates disappears after this
const MAX_BUBBLES = 3;
const W = 1024, H = 384;    // bubble canvas, drawn bottom-anchored
const PLANE_W = 1.2;        // metres

export function captionsUrl() {
  try { return localStorage.getItem(URL_KEY) || DEFAULT_URL; } catch { return DEFAULT_URL; }
}

export function setCaptionsUrl(url) {
  try { url ? localStorage.setItem(URL_KEY, url) : localStorage.removeItem(URL_KEY); } catch {}
}

function wrap(ctx, text, maxW) {
  const lines = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > maxW && line) { lines.push(line); line = word; } else line = next;
  }
  if (line) lines.push(line);
  return lines;
}

function draw(ctx, msg) {
  const main = msg.translation || msg.text;
  const sub = msg.translation ? `${msg.lang}: ${msg.text}` : "";
  const pad = 28, maxW = W - 2 * pad;
  ctx.clearRect(0, 0, W, H);

  ctx.font = `${msg.final ? "" : "italic "}52px system-ui, sans-serif`;
  let mainLines = wrap(ctx, main, maxW);
  ctx.font = "30px system-ui, sans-serif";
  let subLines = sub ? wrap(ctx, sub, maxW) : [];
  // keep the end of long utterances: that's what is being said right now
  mainLines = mainLines.slice(-3);
  subLines = subLines.slice(-2);

  const h = pad * 2 + mainLines.length * 62 + subLines.length * 38;
  const top = H - h;
  ctx.font = `${msg.final ? "" : "italic "}52px system-ui, sans-serif`;
  let w = Math.max(...mainLines.map((l) => ctx.measureText(l).width));
  ctx.font = "30px system-ui, sans-serif";
  w = Math.max(w, ...subLines.map((l) => ctx.measureText(l).width)) + 2 * pad;
  const left = (W - w) / 2;   // centred, so short bubbles sit right on the speaker
  ctx.fillStyle = msg.final ? "rgba(255,255,255,0.92)" : "rgba(255,255,255,0.7)";
  ctx.beginPath();
  ctx.roundRect(left, top, w, h, 36);
  ctx.fill();

  let y = top + pad;
  ctx.textBaseline = "top";
  ctx.fillStyle = msg.final ? "#111" : "#444";
  ctx.font = `${msg.final ? "" : "italic "}52px system-ui, sans-serif`;
  for (const l of mainLines) { ctx.fillText(l, left + pad, y); y += 62; }
  ctx.fillStyle = "#666";
  ctx.font = "30px system-ui, sans-serif";
  for (const l of subLines) { ctx.fillText(l, left + pad, y); y += 38; }
}

export function createCaptions({ three, recenter, distM, vfovDeg, listEl, log, onStatus }) {
  const room = new THREE.Group();           // bubbles with a speaker direction
  three.scene.add(room);
  const bubbles = new Map();                // id -> { msg, mesh, ctx, tex, until }
  const screenH = 2 * distM * Math.tan(vfovDeg / 2 * Math.PI / 180);
  const planeH = PLANE_W * H / W;
  const finals = [];                        // for the page list

  function bubble(id) {
    let b = bubbles.get(id);
    if (b) return b;
    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(PLANE_W, planeH),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthTest: false }));
    mesh.renderOrder = 10;
    b = { mesh, ctx: canvas.getContext("2d"), tex };
    bubbles.set(id, b);
    return b;
  }

  function remove(id) {
    const b = bubbles.get(id);
    if (!b) return;
    b.mesh.removeFromParent();
    b.mesh.geometry.dispose(); b.mesh.material.dispose(); b.tex.dispose();
    bubbles.delete(id);
  }

  function layout() {
    const recenterYaw = new THREE.Euler().setFromQuaternion(
      new THREE.Quaternion(recenter.q0.x, recenter.q0.y, recenter.q0.z, recenter.q0.w), "YXZ").y;
    const newestFirst = [...bubbles.values()].sort((a, b) => b.msg.id - a.msg.id);
    newestFirst.forEach((b, i) => {
      const az = b.msg.azimuth_deg;
      if (az == null) {
        // subtitle: bottom of the video window, older ones stacked above
        three.robotView.add(b.mesh);
        b.mesh.position.set(0, -screenH / 2 + planeH / 2 + 0.05 + i * planeH, -distM + 0.05);
        b.mesh.rotation.set(0, 0, 0);
      } else {
        const stack = newestFirst.slice(0, i).filter((o) => o.msg.azimuth_deg != null &&
          Math.abs(o.msg.azimuth_deg - az) < 25).length;
        const a = recenterYaw + az * Math.PI / 180, d = distM * 0.9, y = 0.4 + stack * planeH;
        room.add(b.mesh);
        b.mesh.position.set(-Math.sin(a) * d, y, -Math.cos(a) * d);
        b.mesh.lookAt(0, y, 0);
      }
    });
  }

  function renderList() {
    if (!listEl) return;
    const live = [...bubbles.values()].filter((b) => !b.msg.final).map((b) => b.msg);
    listEl.replaceChildren(...[...finals.slice(-5), ...live].map((m) => {
      const div = document.createElement("div");
      div.className = m.final ? "final" : "partial";
      div.textContent = m.translation ? `${m.translation}  (${m.lang}: ${m.text})` : m.text;
      return div;
    }));
  }

  function onCaption(msg) {
    if (msg.final) {
      const i = finals.findIndex((m) => m.id === msg.id);
      if (msg.text) i >= 0 ? (finals[i] = msg) : finals.push(msg);
      else if (i >= 0) finals.splice(i, 1);
      finals.splice(0, finals.length - 20);
    }
    if (!msg.text) { remove(msg.id); layout(); renderList(); return; }
    // replayed history on (re)connect: list only, no bubbles
    if (msg.final && msg.t_end < Date.now() / 1000 - SHOW_S) { renderList(); return; }
    const b = bubble(msg.id);
    b.msg = msg;
    b.until = performance.now() + 1000 * (msg.final ? SHOW_S : PARTIAL_S);
    draw(b.ctx, msg);
    b.tex.needsUpdate = true;
    for (const id of [...bubbles.keys()].sort((x, y) => y - x).slice(MAX_BUBBLES)) remove(id);
    layout();
    renderList();
  }

  setInterval(() => {
    const now = performance.now();
    let changed = false;
    for (const [id, b] of bubbles) if (now > b.until) { remove(id); changed = true; }
    if (changed) { layout(); renderList(); }
  }, 500);

  let ws = null;
  function connect() {
    const url = captionsUrl();
    onStatus({ captions: "connecting" });
    try { ws = new WebSocket(url); } catch (e) {
      log("captions:", e?.message ?? e);
      onStatus({ captions: "bad url" });
      return;
    }
    ws.onopen = () => { onStatus({ captions: "on" }); log("captions connected", url); };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "caption") onCaption(msg);
    };
    ws.onclose = () => { onStatus({ captions: "off" }); setTimeout(connect, 3000); };
  }
  connect();

  return {
    /** Reconnect, e.g. after the URL changed. */
    reconnect() { if (ws) { ws.onclose = null; ws.close(); } connect(); },
    /** After recenter: re-place the room bubbles. */
    layout,
  };
}
