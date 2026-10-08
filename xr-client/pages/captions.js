// Speech bubbles: live captions from backend/ (`reachy-captions`, WebSocket port 8766), shown in VR
// and on the page. Protocol: backend/README.md. Same id = update (partial -> final -> + translation).
//
// Placement: always above a head in the video window. faces.js finds the people (also when the face is cut
// off: head estimated from the shoulders), speakers.js picks the one who talks (mouth movement + mic
// direction), the bubble follows that person every frame and points at the head. Until someone is
// visible it waits at the top of the window in the mic direction. One bubble per person; detected heads
// get a thin frame (toggle in Settings).
//
// Where the server is: an address set in Settings, else automatically ws://localhost:8766 (laptop browser, or
// headset with adb reverse tcp:8766 tcp:8766) and, if that fails, the wireless tunnel (`reachy-captions
// --tunnel`) whose current wss address the backend posts to ntfy.sh (backend/.../tunnel.py). The tunnel only
// lets in an allowed Hugging Face account: the page sends its HF sign-in right after connecting.

import * as THREE from "three";

const URL_KEY = "reachy-xr-captions-url";
const MODE_KEY = "reachy-xr-caption-mode";
const FACES_KEY = "reachy-xr-show-faces";
const LOCAL_URL = "ws://localhost:8766";
const TUNNEL_TOPIC = "reachy-meetings-xr-captions";   // keep in sync with backend/.../tunnel.py
export const MODES = ["both", "translation", "original"];
const SHOW_S = 8;           // a final bubble stays this long after its last update
const PARTIAL_S = 5;        // a partial without updates disappears after this
const MAX_BUBBLES = 3;
const W = 1024, H = 448, TAIL = 36;   // bubble canvas, drawn bottom-anchored, tail below
const PLANE_W = 1.2;        // metres
const COLORS = ["#ff9500", "#38bdf8", "#4ade80", "#f472b6", "#a78bfa", "#facc15"];

function load(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } }
function save(key, value) { try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch {} }

export function captionsUrl() { return load(URL_KEY, ""); }   // "" = automatic

async function tunnelUrl() {
  const r = await fetch(`https://ntfy.sh/${TUNNEL_TOPIC}/json?poll=1&since=latest`, { cache: "no-store" });
  const line = (await r.text()).trim().split("\n").pop();
  return line ? JSON.parse(line).message : null;
}
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
  // tail pointing down at the head
  ctx.moveTo(W / 2 - 26, H - TAIL - 1); ctx.lineTo(W / 2, H - 4); ctx.lineTo(W / 2 + 26, H - TAIL - 1);
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

export function createCaptions({ three, distM, vfovDeg, speakers, listEl, overlayEl, onSummary, onFinal, onSpeech, onVad, onMe, onAudio, log, onStatus }) {
  const bubbles = new Map();                // id -> { msg, mesh, ctx, tex, until, trackId, pid, color, label, last }
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
    b = { mesh, ctx: canvas.getContext("2d"), tex, trackId: null, pid: null, color: "#9ca3af", label: "", last: null };
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

  const tanHalf = Math.tan((2 * Math.atan(Math.tan(vfovDeg / 2 * Math.PI / 180) * 16 / 9)) / 2);
  let lastAnchor = { cx: 0.5, top: 0.02 };   // where the last speaker was, if nobody is visible

  function setSpeaker(b, tr) {
    b.trackId = tr.id;
    b.pid = tr.pid ?? tr.id;
    b.color = COLORS[(b.pid - 1) % COLORS.length];
    b.label = `Speaker ${b.pid}`;
    b.msg.meta = { color: b.color, label: b.label };
    redraw(b);
    renderPage();
  }

  /** The head this bubble belongs to, as {cx, top} in the image (0..1). Never "nowhere". */
  function anchor(b) {
    let tr = b.trackId != null ? speakers?.get(b.trackId) : null;
    if (!tr && b.trackId != null) {
      // face lost for a moment (blur, head turn, robot turning): back with a new track -> same person, else nearest
      const same = speakers.tracks.find((t) => t.pid === b.pid);
      const near = b.last && speakers.tracks.reduce((best, t) => (!best || Math.abs(t.cx - b.last.cx) < Math.abs(best.cx - b.last.cx) ? t : best), null);
      tr = same ?? (near && Math.abs(near.cx - b.last.cx) < 0.3 ? near : null);
      if (tr) b.trackId = tr.id;
    }
    if (!tr && b.trackId == null && speakers) {
      tr = speakers.pick(b.msg.doa_deg);   // first time someone is visible for this utterance
      if (tr) setSpeaker(b, tr);
    }
    if (tr) return (b.last = lastAnchor = { cx: tr.cx, top: tr.top });
    if (b.last) return b.last;
    // nobody visible yet: towards the voice (mic direction), at the top of the window
    const doa = b.msg.doa_deg;
    const cx = doa == null ? lastAnchor.cx : 0.5 - Math.tan(doa * Math.PI / 180) / (2 * tanHalf);
    return { cx: Math.max(0.1, Math.min(0.9, cx)), top: 0.02 };
  }

  /** Position in the video window just above the head (heads cut off at the top: above the window edge). */
  function place(b, jump) {
    const a = anchor(b);
    const x = Math.max(-0.45, Math.min(0.45, a.cx - 0.5)) * screenW;
    const y = (0.5 - Math.max(-0.05, a.top)) * screenH + planeH / 2 + 0.03;
    const target = new THREE.Vector3(x, y, -distM + 0.05 + (b.msg.id % 5) * 0.005);
    if (jump || b.mesh.parent !== three.robotView) { three.robotView.add(b.mesh); b.mesh.position.copy(target); }
    else b.mesh.position.lerp(target, 0.25);
  }

  // Detected people: thin frame around the (estimated) head in the speaker colour, toggle in Settings.
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
      box.material.color.set(COLORS[((tr.pid ?? tr.id) - 1) % COLORS.length]);
    });
  }

  /** Every frame: bubbles glide after their person; one bubble per person (the newest wins). */
  function follow() {
    if (speakers) updateBoxes();
    const newestFirst = [...bubbles.values()].sort((a, b) => b.msg.id - a.msg.id);
    const owners = new Set();
    for (const b of newestFirst) {
      place(b, false);
      if (b.pid == null) continue;
      if (owners.has(b.pid)) remove(b.msg.id); else owners.add(b.pid);
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
    if (!msg.text) { remove(msg.id); renderPage(); return; }
    // replayed history on (re)connect: list only, no bubbles
    if (msg.final && msg.t_end < Date.now() / 1000 - SHOW_S) { renderPage(); return; }
    if (msg.final) onFinal?.(msg);   // fresh finished utterance (voice commands)
    const b = bubble(msg.id);
    b.msg = msg;
    b.until = performance.now() + 1000 * (msg.final ? SHOW_S : PARTIAL_S);
    msg.meta = { color: b.color, label: b.label };   // keep the speaker for the page list
    const isNew = !b.mesh.parent;
    place(b, isNew);   // picks the speaker on first sight
    // One bubble per speaker (and one not yet on a person): the new utterance replaces the old one.
    for (const [id, o] of bubbles) if (id < msg.id && o.pid === b.pid) remove(id);
    for (const id of [...bubbles.keys()].sort((x, y) => y - x).slice(MAX_BUBBLES)) remove(id);
    redraw(b);
    renderPage();
    onSpeech?.(msg, b.trackId != null ? speakers?.get(b.trackId) ?? null : null);
  }

  setInterval(() => {
    const now = performance.now();
    let changed = false;
    for (const [id, b] of bubbles) if (now > b.until) { remove(id); changed = true; }
    if (changed) renderPage();
  }, 500);

  let ws = null, tryLocal = true, timer = null;
  async function connect() {
    clearTimeout(timer);
    let url = captionsUrl();
    if (!url) {
      if (tryLocal) url = LOCAL_URL;
      else url = await tunnelUrl().catch((e) => { log("captions: tunnel lookup failed:", e?.message ?? e); return null; });
      tryLocal = !tryLocal;
      if (!url) { onStatus({ captions: "off" }); timer = setTimeout(connect, 3000); return; }
    }
    onStatus({ captions: "connecting" });
    try { ws = new WebSocket(url); } catch (e) {
      log("captions:", e?.message ?? e);
      onStatus({ captions: "bad url" });
      return;
    }
    const sock = ws;
    sock.onopen = () => {
      let token = null;
      try { token = sessionStorage.getItem("hf_token"); } catch {}
      sock.send(JSON.stringify({ type: "auth", hf_token: token }));   // only checked through the tunnel
      tryLocal = url === LOCAL_URL;   // reconnect the same way first
      onStatus({ captions: "on" }); log("captions connected", url);
    };
    sock.binaryType = "arraybuffer";   // binary frames = room audio (see backend/README.md)
    sock.onmessage = (e) => {
      if (typeof e.data !== "string") { onAudio?.(e.data); return; }
      const msg = JSON.parse(e.data);
      if (msg.type === "caption") onCaption(msg);
      else if (msg.type === "summary") onSummary?.(msg);
      else if (msg.type === "vad") onVad?.(msg);
      else if (msg.type === "me") onMe?.(msg);
    };
    sock.onclose = (e) => {
      if (e.code === 4001) log("captions: tunnel refused this HF account (backend --allow-hf)");
      onStatus({ captions: e.code === 4001 ? "not allowed" : "off" });
      timer = setTimeout(connect, e.code === 4001 ? 5000 : tryLocal ? 3000 : 300);
    };
  }
  connect();

  return {
    /** Reconnect, e.g. after the URL changed. */
    /** Your voice for "translate me" (binary int16 PCM, 16 kHz). */
    sendVoice(buf) { if (ws?.readyState === WebSocket.OPEN) ws.send(buf); },
    reconnect() { if (ws) { ws.onclose = null; ws.close(); } tryLocal = true; connect(); },
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
