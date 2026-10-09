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
import { CameraModel } from "./camera.js";

const URL_KEY = "reachy-xr-captions-url";
const MODE_KEY = "reachy-xr-caption-mode";
const FACES_KEY = "reachy-xr-show-faces";
const VOICE_KEY = "reachy-xr-voice-gender";
const MEETING_KEY = "reachy-xr-meeting-lang";   // the language Reachy speaks for you ("auto" = the room's)
const BUBBLE_KEY = "reachy-xr-bubble-lang";     // the language the bubbles are translated into
export const MEETING_LANGS = ["auto", "de", "en", "fr", "it", "es"];
export const BUBBLE_LANGS = ["de", "en", "fr", "it", "es"];
const LOCAL_URL = "ws://localhost:8766";
const TUNNEL_TOPIC = "reachy-meetings-xr-captions";   // keep in sync with backend/.../tunnel.py
export const MODES = ["both", "translation", "original"];
const SHOW_S = 8;           // a final bubble stays this long after its last update
const PARTIAL_S = 5;        // a partial without updates disappears after this
// "…" bubble as soon as the backend's VAD hears speech (~0.15 s), until the first text (~1 s): someone is talking
// here. Replaced by the first caption; gone after TYPING_S without one (cough, noise), or soon after speech ends.
const TYPING_ID = -1, TYPING_S = 1.5, TYPING_END_S = 0.6;
const MAX_BUBBLES = 3;
const W = 1024, H = 448, TAIL = 36;   // bubble canvas, drawn bottom-anchored, tail below
const PLANE_W = 1.2;        // metres
const COLORS = ["#ff9500", "#38bdf8", "#4ade80", "#f472b6", "#a78bfa", "#facc15"];

function load(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } }
function save(key, value) { try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch {} }

// Faces found by the backend (backend/.../vision.py, {"type": "faces"}): set by app.js.
let facesHandler = null;
let depthHandler = null;   // room-scan depth replies (set via captions.onDepth)
export function onBackendFaces(fn) { facesHandler = fn; }

export function captionsUrl() { return load(URL_KEY, ""); }   // "" = automatic

// The ntfy topic is public: anyone could post their own address and collect the sign-in token the page sends
// (code review 2026-10-08). So only addresses signed by a trusted backend key are used (backend tunnel.py:
// ECDSA P-256 over "url|ts", the key is logged when the backend starts). Another laptop's key: Settings.
const TRUSTED_KEYS = [
  "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEEndlhU0PUWdMn1Wbq4jJIFed2LAUrTExKrItI6k0dqPF0Hjq6DR/p7QzjsOetW9pfEA89bi0gZES5gWawzlbvA==",   // Dominic's Mac
];
const KEYS_KEY = "reachy-xr-trusted-tunnel-keys";
export function extraTunnelKeys() { return load(KEYS_KEY, ""); }
export function setExtraTunnelKeys(text) { save(KEYS_KEY, text.trim()); }
const TUNNEL_HOST = /^wss:\/\/[a-z0-9-]+\.trycloudflare\.com$/;
const MAX_AGE_S = 13 * 3600;   // the backend reposts every 30 min; ntfy keeps messages 12 h
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

async function tunnelUrl(log) {
  const r = await fetch(`https://ntfy.sh/${TUNNEL_TOPIC}/json?poll=1&since=latest`, { cache: "no-store" });
  const line = (await r.text()).trim().split("\n").pop();
  if (!line) return null;
  let m;
  try { m = JSON.parse(JSON.parse(line).message); } catch { log("captions: tunnel address not signed, ignored"); return null; }
  const trusted = [...TRUSTED_KEYS, ...extraTunnelKeys().split(/[\s,]+/).filter(Boolean)];
  if (!TUNNEL_HOST.test(m?.url ?? "") || !trusted.includes(m.key) || Math.abs(Date.now() / 1000 - m.ts) > MAX_AGE_S) {
    log("captions: tunnel address rejected (host, key or age)"); return null;
  }
  const key = await crypto.subtle.importKey("spki", b64(m.key), { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
  const ok = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, b64(m.sig),
    new TextEncoder().encode(`${m.url}|${m.ts}`));
  if (!ok) { log("captions: tunnel address signature invalid, ignored"); return null; }
  return m.url;
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

export function createCaptions({ three, distM, vfovDeg, cameraModel = null, speakers, listEl, overlayEl, onSummary, onFinal, onSpeech, onVad, onMe, onAudio, getToken, log, onStatus }) {
  const bubbles = new Map();                // id -> { msg, mesh, ctx, tex, until, trackId, pid, color, label, last }
  // Image coords (u, v 0..1) -> point on the video sphere (radius R, robotView frame), via the camera model,
  // so bubbles and face frames sit on the person also near the distorted edges of the wide-angle picture.
  let cam = cameraModel ?? CameraModel.pinhole(vfovDeg);
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  function onScreen(u, v, R) {
    const d = cam.unproject(u, v) ?? cam.unproject(clamp01(u), clamp01(v)) ?? [0, 0, 1];
    return new THREE.Vector3(d[0] * R, -d[1] * R, -d[2] * R);
  }
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
    const cx = doa == null ? lastAnchor.cx : cam.uForYaw(doa);
    return { cx: Math.max(0.1, Math.min(0.9, cx)), top: 0.02 };
  }

  /** Position in the video window just above the head (heads cut off at the top: above the window edge). */
  function place(b, jump) {
    const a = anchor(b);
    const target = onScreen(Math.max(0.05, Math.min(0.95, a.cx)), Math.max(-0.05, a.top), distM - 0.05 - (b.msg.id % 5) * 0.005);
    target.y += planeH / 2 + 0.03;
    if (jump || b.mesh.parent !== three.robotView) { three.robotView.add(b.mesh); b.mesh.position.copy(target); }
    else b.mesh.position.lerp(target, 0.25);
    b.mesh.lookAt(0, 0, 0);   // face the eye (the sphere is centred on it)
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
      const R = distM - 0.04;
      box.position.copy(onScreen(tr.cx, tr.cy, R));
      box.scale.set(onScreen(tr.cx - tr.w / 2, tr.cy, R).distanceTo(onScreen(tr.cx + tr.w / 2, tr.cy, R)),
        onScreen(tr.cx, tr.cy - tr.h / 2, R).distanceTo(onScreen(tr.cx, tr.cy + tr.h / 2, R)), 1);
      box.lookAt(0, 0, 0);
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

  function typing(vad) {
    const now = performance.now();
    if (!vad.speaking) {
      const b = bubbles.get(TYPING_ID);
      if (b) b.until = Math.min(b.until, now + 1000 * TYPING_END_S);
      return;
    }
    // only while no live (partial) bubble shows this speech already
    if ([...bubbles.values()].some((o) => !o.msg.final && o.msg.id !== TYPING_ID)) return;
    const isNew = !bubbles.has(TYPING_ID);
    const b = bubble(TYPING_ID);
    b.until = now + 1000 * TYPING_S;
    if (!isNew) return;
    b.msg = { id: TYPING_ID, final: false, text: "…", translation: null, doa_deg: null };
    place(b, true);
    redraw(b);
  }

  function renderPage() {
    const items = [...finals.slice(-6), ...[...bubbles.values()].filter((b) => !b.msg.final && b.msg.id !== TYPING_ID).map((b) => b.msg)];
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

  // Backend session (caption ids restart at 0 when it restarts) and its clock (backend times -> this page).
  let session = null, clockOffsetS = 0;
  function onHello(msg) {
    if (typeof msg.t === "number") clockOffsetS = Date.now() / 1000 - msg.t;   // incl. ~one network delay
    if (msg.session && msg.session !== session) {
      if (session) { finals.length = 0; renderPage(); log("captions: new backend session"); }
      session = msg.session;
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
    remove(TYPING_ID);
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
  let voiceGender = load(VOICE_KEY, "male");
  let generation = 0;   // a reconnect() during the tunnel lookup must not leave two live sockets
  let meetingLang = load(MEETING_KEY, "auto");
  let bubbleLang = load(BUBBLE_KEY, "");   // "" = the backend's --target (sent in hello)
  let serverTarget = "en";
  function sendLang() {
    if (ws?.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "lang", meeting: meetingLang, ...(bubbleLang ? { target: bubbleLang } : {}) }));
  }
  async function connect() {
    clearTimeout(timer);
    const gen = ++generation;
    let url = captionsUrl();
    if (!url) {
      if (tryLocal) url = LOCAL_URL;
      else url = await tunnelUrl(log).catch((e) => { log("captions: tunnel lookup failed:", e?.message ?? e); return null; });
      if (gen !== generation) return;
      tryLocal = !tryLocal;
      if (!url) { onStatus({ captions: "off" }); timer = setTimeout(connect, 3000); return; }
    }
    onStatus({ captions: "connecting" });
    try { ws = new WebSocket(url); } catch (e) {
      log("captions:", e?.message ?? e);
      onStatus({ captions: "bad url" });
      timer = setTimeout(connect, 3000);   // keep trying (a stored URL may be fixed in Settings)
      return;
    }
    const sock = ws;
    sock.onopen = () => {
      const token = getToken?.() ?? null;
      sock.send(JSON.stringify({ type: "auth", hf_token: token }));   // only checked through the tunnel
      sock.send(JSON.stringify({ type: "voice", gender: voiceGender }));
      sendLang();
      tryLocal = url === LOCAL_URL;   // reconnect the same way first
      onStatus({ captions: "on" }); log("captions connected", url);
    };
    sock.binaryType = "arraybuffer";   // binary frames = room audio (see backend/README.md)
    sock.onmessage = (e) => {
      if (typeof e.data !== "string") { onAudio?.(e.data); return; }
      const msg = JSON.parse(e.data);
      if (msg.type === "hello") { onHello(msg); serverTarget = msg.target || serverTarget; }
      else if (msg.type === "caption") onCaption(msg);
      else if (msg.type === "summary") onSummary?.(msg);
      else if (msg.type === "vad") { typing(msg); onVad?.(msg); }
      else if (msg.type === "me") onMe?.(msg);
      else if (msg.type === "faces") facesHandler?.(msg);
      else if (msg.type === "depth") depthHandler?.(msg);
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
    /** Backend session id (changes when the backend restarts) and backend clock: Date.now()/1000 - offset. */
    get session() { return session; },
    get clockOffsetS() { return clockOffsetS; },

    /** Reachy's voice for "translate me": "male" (Viktor) or "female" (Siri). Remembered, resent on reconnect. */
    get voiceGender() { return voiceGender; },
    setVoiceGender(g) {
      voiceGender = g;
      save(VOICE_KEY, g);
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "voice", gender: g }));
    },

    /** What Reachy speaks for you ("auto" = the language most spoken in the room) and what the bubbles are
     *  translated into. Remembered, resent on every (re)connect ({"type": "lang"}, backend/README.md). */
    get meetingLang() { return meetingLang; },
    setMeetingLang(l) { meetingLang = l; save(MEETING_KEY, l === "auto" ? "" : l); sendLang(); },
    cycleMeetingLang() { this.setMeetingLang(MEETING_LANGS[(MEETING_LANGS.indexOf(meetingLang) + 1) % MEETING_LANGS.length]); },
    get bubbleLang() { return bubbleLang || serverTarget; },
    setBubbleLang(l) { bubbleLang = l; save(BUBBLE_KEY, l); sendLang(); },
    cycleBubbleLang() { this.setBubbleLang(BUBBLE_LANGS[(BUBBLE_LANGS.indexOf(this.bubbleLang) + 1) % BUBBLE_LANGS.length]); },

    get connected() { return ws?.readyState === WebSocket.OPEN; },
    /** Page log lines -> the backend's headset log file. */
    /** Room-scan frame -> metric depth on the laptop (backend depth.py). false if not connected. */
    requestDepth(id, jpegBase64, w, h) {
      if (ws?.readyState !== WebSocket.OPEN) return false;
      ws.send(JSON.stringify({ type: "depth", id, w, h, jpeg: jpegBase64 }));
      return true;
    },
    set onDepth(fn) { depthHandler = fn; },

    sendLog(lines) { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "log", lines })); },

    /** Your voice for "translate me" (binary int16 PCM, 16 kHz). */
    sendVoice(buf) { if (ws?.readyState === WebSocket.OPEN) ws.send(buf); },
    /** You stopped sending (translate off, muted): the backend ends your sentence now instead of gluing it to the next. */
    sendVoiceEnd() { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "voice_end" })); },
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
    /** Another camera model (calibration): bubbles and face frames follow the new projection. */
    setCamera(c) { cam = c; },
    set showFaces(on) { showFaces = on; save(FACES_KEY, on ? "on" : "off"); },
  };
}
