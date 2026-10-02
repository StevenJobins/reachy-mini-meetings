// Speech bubbles: live captions from backend/ (`reachy-captions`, WebSocket port 8766), shown in VR
// and on the page. Protocol: backend/README.md. Same id = update (partial -> final -> + translation).
//
// Placement: above the speaker's face in the video window (faces.js + speakers.js pick the face; the
// bubble follows it every frame and points at the head), else as a subtitle at the bottom of the video
// window. Never somewhere else in the room: the mic direction alone is too unreliable for that, and a
// bubble outside the field of view is a missed bubble. Detected faces get a thin frame (toggleable).
//
// The page is served over https, so Chrome only allows ws://localhost (headset: adb reverse tcp:8766 tcp:8766).
// Wireless: expose the caption server over wss (e.g. cloudflared) and paste the URL in Settings.

import * as THREE from "three";

const URL_KEY = "reachy-xr-captions-url";
const MODE_KEY = "reachy-xr-caption-mode";
const FACES_KEY = "reachy-xr-show-faces";
const DEFAULT_URL = "ws://localhost:8766";
export const MODES = ["both", "translation", "original"];
const SHOW_S = 8;           // a final bubble stays this long after its last update
const PARTIAL_S = 5;        // a partial without updates disappears after this
const MAX_BUBBLES = 3;
const W = 1024, H = 448, TAIL = 36;   // bubble canvas, drawn bottom-anchored, tail below
const PLANE_W = 1.2;        // metres
const COLORS = ["#ff9500", "#38bdf8", "#4ade80", "#f472b6", "#a78bfa", "#facc15"];

function load(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } }
function save(key, value) { try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch {} }

export function captionsUrl() { return load(URL_KEY, DEFAULT_URL); }
export function setCaptionsUrl(url) { save(URL_KEY, url); }

/** What a bubble shows in the given mode: {main, sub}. */
export function bubbleText(msg, mode) {
  if (mode === "original" || !msg.translation) return { main: msg.text, sub: "" };
  return { main: msg.translation, sub: mode === "both" ? `${msg.lang}: ${msg.text}` : "" };
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

function draw(ctx, b, mode) {
  const { msg } = b;
  const { main, sub } = bubbleText(msg, mode);
  const pad = 30, maxW = W - 2 * pad - 24;
  const mainFont = `${msg.final ? "600" : "italic 500"} 52px system-ui, sans-serif`;
  const subFont = "32px system-ui, sans-serif", labelFont = "600 26px system-ui, sans-serif";
  ctx.clearRect(0, 0, W, H);

  ctx.font = mainFont;
  const mainLines = wrap(ctx, main, maxW).slice(-3);   // keep the end: that's what is being said now
  ctx.font = subFont;
  const subLines = sub ? wrap(ctx, sub, maxW).slice(-2) : [];
  const label = b.label ? [b.label] : [];

  const h = pad * 2 + label.length * 38 + mainLines.length * 62 + subLines.length * 40;
  ctx.font = mainFont;
  let w = Math.max(220, ...mainLines.map((l) => ctx.measureText(l).width));
  ctx.font = subFont;
  w = Math.min(W, Math.max(w, ...subLines.map((l) => ctx.measureText(l).width)) + 2 * pad + 24);
  const left = (W - w) / 2, top = H - TAIL - h;

  ctx.fillStyle = msg.final ? "rgba(255,255,255,0.95)" : "rgba(255,255,255,0.78)";
  ctx.beginPath();
  ctx.roundRect(left, top, w, h, 34);
  if (b.onFace) {   // tail pointing down at the head
    ctx.moveTo(W / 2 - 26, H - TAIL - 1); ctx.lineTo(W / 2, H - 4); ctx.lineTo(W / 2 + 26, H - TAIL - 1);
  }
  ctx.fill();
  ctx.fillStyle = b.color;   // speaker colour strip
  ctx.beginPath(); ctx.roundRect(left + 12, top + 18, 10, h - 36, 5); ctx.fill();

  let y = top + pad;
  const x = left + pad + 18;
  ctx.textBaseline = "top";
  if (label.length) { ctx.font = labelFont; ctx.fillStyle = b.color; ctx.fillText(b.label, x, y); y += 38; }
  ctx.font = mainFont; ctx.fillStyle = msg.final ? "#111" : "#444";
  for (const l of mainLines) { ctx.fillText(l, x, y); y += 62; }
  ctx.font = subFont; ctx.fillStyle = "#666";
  for (const l of subLines) { ctx.fillText(l, x, y); y += 40; }
}

export function createCaptions({ three, distM, vfovDeg, speakers, listEl, overlayEl, onSummary, onFinal, log, onStatus }) {
  const bubbles = new Map();                // id -> { msg, mesh, ctx, tex, until, trackId, color, label, onFace, target }
  const screenH = 2 * distM * Math.tan(vfovDeg / 2 * Math.PI / 180);
  const screenW = screenH * 16 / 9;
  const planeH = PLANE_W * H / W;
  const finals = [];                        // for the page list
  let mode = MODES.includes(load(MODE_KEY)) ? load(MODE_KEY) : "both";

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
    b = { mesh, ctx: canvas.getContext("2d"), tex, trackId: null, color: "#9ca3af", label: "", target: null };
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

  function redraw(b) { draw(b.ctx, b, mode); b.tex.needsUpdate = true; }

  /** Pick the speaker's face once per utterance (retried while it is still a partial). */
  function assignSpeaker(b) {
    if (b.trackId != null || !speakers) return;
    const tr = speakers.pick(b.msg.doa_deg);
    if (!tr) return;
    b.trackId = tr.id;
    b.color = COLORS[(tr.id - 1) % COLORS.length];
    b.label = `Speaker ${tr.id}`;
  }

  function layout() {
    const newestFirst = [...bubbles.values()].sort((a, b) => b.msg.id - a.msg.id);
    let subtitles = 0;
    newestFirst.forEach((b, i) => {
      const tr = b.trackId != null ? speakers.get(b.trackId) : null;
      if (b.trackId != null) {
        // on the face; older bubbles of the same speaker stack upwards
        const stack = newestFirst.slice(0, i).filter((o) => o.trackId === b.trackId).length;
        if (tr) b.target = new THREE.Vector3((tr.cx - 0.5) * screenW, (0.5 - tr.top) * screenH + planeH / 2 + 0.03,
          -distM + 0.05 + i * 0.01);
        if (b.target) {
          b.onFace = true;
          if (b.mesh.parent !== three.robotView) { three.robotView.add(b.mesh); b.mesh.position.copy(b.target); }
          b.mesh.rotation.set(0, 0, 0);
          b.stackY = stack * planeH * 0.85;
          return;
        }
      }
      b.onFace = false;
      b.target = null;
      three.robotView.add(b.mesh);   // subtitle: bottom of the video window
      b.mesh.position.set(0, -screenH / 2 + planeH / 2 + 0.05 + subtitles++ * planeH, -distM + 0.05);
      b.mesh.rotation.set(0, 0, 0);
    });
  }

  // Detected faces: thin frame in the speaker colour, so you can see who is recognised.
  const boxes = [];
  let showFaces = load(FACES_KEY, "on") === "on";
  function updateBoxes() {
    const tracks = showFaces ? speakers.tracks : [];
    while (boxes.length < tracks.length) {
      const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(1, 1)),
        new THREE.LineBasicMaterial({ transparent: true, opacity: 0.85, depthTest: false }));
      box.renderOrder = 9;
      three.robotView.add(box);
      boxes.push(box);
    }
    boxes.forEach((box, i) => {
      const tr = tracks[i];
      box.visible = !!tr;
      if (!tr) return;
      box.position.set((tr.cx - 0.5) * screenW, (0.5 - tr.cy) * screenH, -distM + 0.04);
      box.scale.set(tr.w * screenW, tr.h * screenH, 1);
      box.material.color.set(COLORS[(tr.id - 1) % COLORS.length]);
    });
  }

  /** Every frame: face bubbles glide after their face. */
  function follow() {
    if (!speakers) return;
    updateBoxes();
    for (const b of bubbles.values()) {
      if (b.trackId == null || !b.target) continue;
      let tr = speakers.get(b.trackId);
      if (!tr) {
        // face lost for a moment (blur, head turn, robot turning): it comes back with a new track id,
        // so take over the nearest face to where the bubble is
        const x = b.target.x / screenW + 0.5;
        tr = speakers.tracks.reduce((best, t) => (Math.abs(t.cx - x) < Math.abs((best?.cx ?? 9) - x) ? t : best), null);
        if (tr && Math.abs(tr.cx - x) < 0.3) b.trackId = tr.id; else tr = null;
      }
      if (tr) b.target.set((tr.cx - 0.5) * screenW, (0.5 - tr.top) * screenH + planeH / 2 + 0.03, b.target.z);
      b.mesh.position.lerp(new THREE.Vector3(b.target.x, b.target.y + (b.stackY ?? 0), b.target.z), 0.25);
    }
  }

  function renderPage() {
    const items = [...finals.slice(-6), ...[...bubbles.values()].filter((b) => !b.msg.final).map((b) => b.msg)];
    if (listEl) {
      listEl.replaceChildren(...items.map((m) => {
        const b = bubbles.get(m.id), meta = m.meta ?? b;
        const div = document.createElement("div");
        div.className = `line ${m.final ? "final" : "partial"}`;
        const who = document.createElement("span");
        who.className = "who";
        who.style.setProperty("--c", meta?.color ?? "#9ca3af");
        who.textContent = meta?.label || "Room";
        const { main, sub } = bubbleText(m, mode);
        const text = document.createElement("span");
        text.className = "text";
        text.textContent = main;
        div.append(who, text);
        if (sub) { const s = document.createElement("span"); s.className = "sub"; s.textContent = sub; div.append(s); }
        return div;
      }));
      listEl.scrollTop = listEl.scrollHeight;
    }
    if (overlayEl) {
      const newest = [...bubbles.values()].sort((a, b) => b.msg.id - a.msg.id)[0];
      overlayEl.textContent = newest ? bubbleText(newest.msg, mode).main : "";
      overlayEl.hidden = !newest;
    }
  }

  function onCaption(msg) {
    if (msg.final) {
      const i = finals.findIndex((m) => m.id === msg.id);
      if (msg.text) i >= 0 ? (finals[i] = msg) : finals.push(msg);
      else if (i >= 0) finals.splice(i, 1);
      finals.splice(0, finals.length - 30);
    }
    if (!msg.text) { remove(msg.id); layout(); renderPage(); return; }
    // replayed history on (re)connect: list only, no bubbles
    if (msg.final && msg.t_end < Date.now() / 1000 - SHOW_S) { renderPage(); return; }
    if (msg.final) onFinal?.(msg);   // fresh finished utterance (voice commands)
    const b = bubble(msg.id);
    b.msg = msg;
    b.until = performance.now() + 1000 * (msg.final ? SHOW_S : PARTIAL_S);
    assignSpeaker(b);
    msg.meta = { color: b.color, label: b.label };   // keep the speaker for the page list
    // One bubble per speaker (and one without a face): the new utterance replaces the old one.
    for (const [id, o] of bubbles) if (id < msg.id && o.trackId === b.trackId) remove(id);
    for (const id of [...bubbles.keys()].sort((x, y) => y - x).slice(MAX_BUBBLES)) remove(id);
    layout();
    redraw(b);
    renderPage();
  }

  setInterval(() => {
    const now = performance.now();
    let changed = false;
    for (const [id, b] of bubbles) if (now > b.until) { remove(id); changed = true; }
    if (changed) { layout(); renderPage(); }
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
      else if (msg.type === "summary") onSummary?.(msg);
    };
    ws.onclose = () => { onStatus({ captions: "off" }); setTimeout(connect, 3000); };
  }
  connect();

  return {
    /** Reconnect, e.g. after the URL changed. */
    reconnect() { if (ws) { ws.onclose = null; ws.close(); } connect(); },
    follow,
    get mode() { return mode; },
    setMode(m) {
      if (!MODES.includes(m)) return;
      mode = m;
      save(MODE_KEY, m);
      for (const b of bubbles.values()) redraw(b);
      renderPage();
    },
    cycleMode() { this.setMode(MODES[(MODES.indexOf(mode) + 1) % MODES.length]); return mode; },
    get showFaces() { return showFaces; },
    set showFaces(on) { showFaces = on; save(FACES_KEY, on ? "on" : "off"); },
  };
}
