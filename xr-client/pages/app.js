// Wires robot connection, pose logic and rendering together, plus the one-button UI.
//
// Flow: open page -> silent HF sign-in -> auto-connect to the robot (stays asleep)
//   -> tap Wake up: robot wakes, its camera and microphone switch on
//   -> tap Start: VR, the robot follows your head. Sleep puts it back to sleep (motors, camera, mic off).
// Only "Start" needs a tap: browsers allow entering VR only from a user gesture.

import { createRobot } from "./robot.js";
import { createScene } from "./scene.js";
import { HeadMirror, Recenter, headsetToRobot, robotToHeadset } from "./pose.js";
import { WantToTalk } from "./gestures.js";
import { SpeakerTracker } from "./speaker.js";
import { captionsUrl, createCaptions, setCaptionsUrl } from "./captions.js";
import { createFaces } from "./faces.js";
import { FaceSpeakers } from "./speakers.js";
import { createNotes } from "./notes.js";
import { createMic } from "./mic.js";
import { explainVolume, volumeCommand } from "./voicecmd.js";

// HF OAuth app (huggingface.co/settings/applications), redirect URL = this page's URL.
const HF_CLIENT_ID = "37472ae1-2bae-4d97-be66-ef7446028c40";

const params = new URLSearchParams(location.search);
const cfg = {
  sendHz: Number(params.get("hz") || 50),
  vfovDeg: Number(params.get("vfov") || 54),   // Lite camera at 1920x1080 (estimate from K + crop 1.115, measure!); sim: ?vfov=80
  distM: Number(params.get("dist") || 3),
  smoothing: Number(params.get("smooth") || 0.35),
};

const $ = (id) => document.getElementById(id);
const video = $("video");

// The log also goes to localStorage, so it survives a crashed tab: after a reload the Debug panel
// shows how the previous session ended (crash diagnosis on the headset without a cable).
const LOG_KEY = "reachy-xr-log", PREV_KEY = "reachy-xr-log-previous";
let logLines = [], logDirty = false;
try {
  const prev = localStorage.getItem(LOG_KEY);
  if (prev) localStorage.setItem(PREV_KEY, prev);
  localStorage.removeItem(LOG_KEY);
  $("prev-log").textContent = localStorage.getItem(PREV_KEY) ?? "(none)";
} catch {}
setInterval(() => {
  if (!logDirty) return;
  logDirty = false;
  try { localStorage.setItem(LOG_KEY, logLines.join("\n")); } catch {}
}, 2000);

function log(...a) {
  const line = `${new Date().toLocaleTimeString()} ${a.join(" ")}`;
  console.log(line);
  $("log").textContent = (line + "\n" + $("log").textContent).slice(0, 6000);
  logLines.push(line);
  if (logLines.length > 300) logLines = logLines.slice(-300);
  logDirty = true;
}
addEventListener("error", (e) => log("ERROR", e.message, `${e.filename}:${e.lineno}`));
addEventListener("unhandledrejection", (e) => log("UNHANDLED", e.reason?.message ?? e.reason));

// ---------------------------------------------------------------- status
const status = { user: "-", robot: "-", motors: "-", ice: "-", video: "-", xr: "off", send: 0, cmd: [0, 0, 0], body: 0, meas: [0, 0, 0], captions: "-", sound: "muted", doa: "none", mic: "off", volume: "-", micKbps: 0 };
let sentCount = 0;
setInterval(() => { status.send = sentCount; sentCount = 0; }, 1000);
function statusText() {
  const f = (v) => v.map((x) => x.toFixed(1).padStart(6)).join(" ");
  return [
    `robot ${status.robot}   motors ${status.motors}   ice ${status.ice}   video ${status.video}   send ${status.send} Hz   mic ${status.mic} ${status.micKbps.toFixed(0)} kbps   volume ${status.volume}`,
    `cmd  r/p/y ${f(status.cmd)}   body ${status.body.toFixed(1)}   speaker target ${speaker.target.toFixed(0)} base ${speaker.base.toFixed(0)}`,
    `meas r/p/y ${f(status.meas)}   captions ${status.captions}   ${faces.stats()}   robot sound ${status.sound}   doa ${status.doa}`,
  ].join("\n");
}
// Heartbeat every 5 s: if the page dies, the last lines show memory, video and connection state.
setInterval(() => {
  const mem = performance.memory ? `heap ${(performance.memory.usedJSHeapSize / 1e6).toFixed(0)} MB` : "heap ?";
  log("alive", mem, `xr ${status.xr}`, `robot ${status.robot}`, `ice ${status.ice}`, `motors ${status.motors}`,
    `send ${status.send} Hz`, scene.videoStats());
}, 5000);
document.addEventListener("visibilitychange", () => log("page", document.visibilityState));
// Deployed version: the Pages workflow stamps module URLs with the commit (app.js?v=<sha>).
const VERSION = new URL(import.meta.url).searchParams.get("v") ?? "local";
$("version").textContent = `version ${VERSION}`;
log("page loaded, version", VERSION);
// New deploy? Browsers may keep an old page for a while (GitHub Pages caches ~10 min, the PWA even longer).
// Check at start and every minute; reload right away while nobody uses the robot, else just say so.
async function checkForUpdate() {
  if (VERSION === "local") return;
  try {
    const latest = (await (await fetch(`version.txt?t=${Date.now()}`, { cache: "no-store" })).text()).trim();
    if (!/^[0-9a-f]{7}$/.test(latest) || latest === VERSION) return;
    if (!awake && status.xr === "off") { log("new version", latest, "-> reloading"); location.reload(); }
    else flash(`🔄 New version ${latest}: reload when you are done`);
  } catch {}
}
setTimeout(checkForUpdate, 3000);
setInterval(checkForUpdate, 60000);
setInterval(() => { $("debug-text").textContent = `user ${status.user}   xr ${status.xr}   version ${VERSION}\n` + statusText(); }, 200);

function show(state, message) {
  $("message").textContent = message;
  $("wake").hidden = state !== "asleep";
  $("start").hidden = state !== "awake";
  $("sleep").hidden = state !== "awake";
  $("talk").hidden = state !== "awake";
  $("mute").hidden = state !== "awake";
  $("mic").hidden = state !== "awake";
  $("volume-box").hidden = state !== "awake";
  $("signin").hidden = state !== "signed-out";
  $("retry").hidden = state !== "failed";
}

// ---------------------------------------------------------------- wiring
const recenter = new Recenter();
let mirror = new HeadMirror({ smoothing: cfg.smoothing });
let awake = false;   // head targets only once the wake-up motion is done, so they don't fight it
let wantRecenter = true;
let lastSend = 0;
const talk = new WantToTalk();

// Speaker following is always on: the robot slowly turns to whoever speaks (DoA), and the headset
// rotation is added ON TOP of that base, so you can always look elsewhere. Straight ahead = the speaker.
const speaker = new SpeakerTracker();
let talkCenter = null;   // while waving: turn to the center of all recent speakers (turn_to_speaker.py)

/** Smooth base yaw for this tick (degrees, robot frame). */
function stepBase(dt, nowS) {
  if (!talk.active(nowS)) talkCenter = null;
  speaker.override = talkCenter;
  const base = speaker.step(dt);
  // pitch for the framing (see frameFocus), smoothly at ≤ 30 °/s
  basePitch += Math.max(-30 * dt, Math.min(30 * dt, targetPitch - basePitch));
  return base;
}

/** Mirrored pose + "I want to talk" gesture -> robot. Body swing stays inside the head/body window. */
function send(t, nowS) {
  const g = talk.step(nowS);
  const bodyYaw = Math.max(t.yaw - 60, Math.min(t.yaw + 60, t.bodyYaw + g.bodyOffset));
  if (robot.setHead({ ...t, bodyYaw, antennas: g.antennas })) sentCount++;
  status.cmd = [t.roll, t.pitch, t.yaw];
  status.body = bodyYaw;
}

// Robot sound (its microphone, i.e. the room) is muted by default: otherwise you hear yourself twice,
// once directly and once through the robot. Unmuting must happen inside a tap / VR select.
let robotMuted = true;
function setRobotMuted(m) {
  robotMuted = m;
  if (awake) robot.setAudio(!m);
  status.sound = m ? "muted" : "on";
  $("mute").textContent = m ? "🔇 Robot muted: tap to unmute" : "🔊 Mute robot";
}

// Two-way audio, like a video call: your mic (headset, or the laptop's mic in the browser) goes to the
// robot speaker while Reachy is awake. On by default; the big mic button mutes you.
const mic = createMic({
  getPeerConnection: () => robot.peerConnection,
  onStatus: (s) => { Object.assign(status, s); renderMicButton(); },
  log,
});
function renderMicButton() {
  const el = $("mic");
  el.classList.toggle("muted", mic.muted || status.mic !== "on");
  el.querySelector(".mic-label").textContent =
    status.mic === "blocked" ? "Mic blocked: tap to retry" : status.mic === "no channel" ? "No audio channel"
      : status.mic === "off" ? "Mic off" : status.mic === "starting" ? "Allow mic…" : mic.muted ? "Unmute" : "Mute";
  el.title = mic.muted ? "You are muted. Tap (or press M) to talk to the room." : "The room hears you. Tap (or press M) to mute.";
}
// Live level ring on the mic button: you see that the mic hears you, like in a call app.
setInterval(() => {
  const lvl = mic.level();
  $("mic").style.setProperty("--lvl", lvl.toFixed(2));
}, 80);

/** Robot speaker volume 0-100 (voice command, slider). */
function applyVolume(v, source) {
  return robot.setVolume(v)
    .then((got) => {
      const val = got ?? v;
      status.volume = `${val}%`;
      $("volume").value = val; $("volume-val").textContent = `${val}%`;
      log(`robot volume ${val}% (${source})`);
      return val;
    })
    .catch((e) => { log("set volume failed:", e?.message ?? e); return null; });
}

function toggleMic() {
  if (!awake) return;
  if (status.mic === "blocked" || status.mic === "off" || status.mic === "starting") { mic.start(); return; }   // retry permission inside the tap
  mic.setMuted(!mic.muted);
}

// "Reachy, volume 5" from anyone in the room, in any language -> robot speaker volume 50 %.
const volumeDone = new Set();
function onFinalCaption(msg) {
  if (volumeDone.has(msg.id)) return;
  const v = volumeCommand(msg);
  if (v === null) {
    // A volume word without a command: say why, so a failed "Reachy, volume 9" can be diagnosed.
    const why = explainVolume(msg.text) ?? explainVolume(msg.translation);
    if (why) log(`volume word heard, no command (${why}): "${msg.text}"`);
    return;
  }
  volumeDone.add(msg.id);
  log(`voice command: volume ${v}% ("${msg.text}")`);
  applyVolume(v, "voice").then((val) => { if (val !== null) flash(`🔊 Reachy volume ${val}%`); });
}
let flashTimer = 0;
function flash(text) {
  const el = $("toast");
  el.textContent = text; el.hidden = false;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.hidden = true; }, 3000);
}

function wantToTalk() {
  if (!awake) return;
  const nowS = performance.now() / 1000;
  if (!talk.active(nowS)) talkCenter = speaker.center(nowS);
  talk.trigger(nowS);
  log("I want to talk");
}

// Outside VR the headset sends nothing: follow the speaker (and play gestures) from here.
setInterval(() => {
  if (!awake || status.xr !== "off" || !robot.connected) return;
  const dt = 1 / 50;
  const baseYaw = stepBase(dt, performance.now() / 1000);
  send(mirror.step([0, basePitch, baseYaw], dt), performance.now() / 1000);
}, 1000 / 50);

const robot = createRobot({
  clientId: HF_CLIENT_ID,
  log,
  onStatus: (s) => {
    for (const k of ["robot", "ice", "motors"]) if (k in s && s[k] !== status[k]) log(k, "->", s[k]);
    Object.assign(status, s);
  },
  onMeasuredHead: (roll, pitch, yaw) => {
    status.meas = [roll, pitch, yaw];
    speaker.pushHeadYaw(performance.now() / 1000, yaw);
    // The VR room turns with the base: the window shows where the robot looks RELATIVE to the speaker.
    scene.setRobotHead(recenter.toWorld(robotToHeadset(roll, pitch, yaw - speaker.base)));
  },
  onDoa: (angle, speech) => {
    status.doa = `${(90 - angle * 180 / Math.PI).toFixed(0)}° ${speech ? "SPEECH" : "quiet"}`;   // relative to the head, + = left
    // Buffered, and only used for speech the backend confirms (neural VAD + text): noise never turns the robot.
    const now = performance.now() / 1000;
    doaBuf.push([now, angle, speech]);
    while (doaBuf.length && doaBuf[0][0] < now - 6) doaBuf.shift();
    if (awake && now - lastSpeechS < 1.5) flushDoa(now - 1.5);
  },
});

let captions = null, notes = null;   // created after the scene (they need its three.js groups)
let debugOn = false;                 // VR debug panel (More -> Debug)
const scene = createScene({
  video,
  vfovDeg: cfg.vfovDeg,
  distM: cfg.distM,
  statusText,
  onHeadsetPose: (q, now) => {
    if (wantRecenter) { recenter.set(q); wantRecenter = false; notes.layout(); log("recentered"); }
    if (!awake || status.xr === "off" || !robot.connected || now - lastSend < 1000 / cfg.sendHz) return;
    const dt = (now - lastSend) / 1000;
    lastSend = now;
    const raw = headsetToRobot(recenter.toRelative(q));
    raw[2] += stepBase(dt, now / 1000);   // user's head rotation on top of the speaker direction
    raw[1] += basePitch;                  // ... and on top of the framing pitch
    send(mirror.step(raw, dt), now / 1000);
  },
  // Head-locked buttons in VR: point (controller ray / hand pinch) and select. Select elsewhere = recenter.
  vrButtons: [
    { kind: "mic", muted: () => mic.muted || status.mic !== "on", label: () => (mic.muted ? "Muted" : status.mic === "on" ? "Mic on" : "Mic off"), onClick: toggleMic },
    { icon: "🙋", label: "Talk", onClick: wantToTalk },
    { icon: "💬", label: () => ({ both: "Both", translation: "Translated", original: "Original" })[captions?.mode ?? "both"],
      onClick: () => captions.cycleMode() },
    { icon: "📝", label: "Notes", active: () => !!notes?.visible, onClick: () => notes.toggle() },
    { icon: () => (robotMuted ? "🔇" : "🔊"), label: "Sound", active: () => !robotMuted, onClick: () => setRobotMuted(!robotMuted) },
    { icon: "⟳", label: "Recenter", more: true, onClick: () => { wantRecenter = true; } },
    { icon: "🐞", label: "Debug", more: true, active: () => debugOn, onClick: () => { debugOn = !debugOn; scene.toggleDebug(); } },
    { icon: "✕", label: "Exit VR", more: true, onClick: () => scene.exitVR() },
  ],
  onFrame: () => captions?.follow(),
  log,
  onSelect: () => { wantRecenter = true; },
  onEnd: () => { log("VR session ended"); status.xr = "off"; show("awake", "Reachy is awake. Tap Start to look around again, or Sleep."); },
});

// Speech bubbles over the speaker's head: faces in the camera image + mouth movement + mic direction.
const hfovDeg = 2 * Math.atan(Math.tan(cfg.vfovDeg / 2 * Math.PI / 180) * 16 / 9) * 180 / Math.PI;
const faceSpeakers = new FaceSpeakers({ hfovDeg });
// Someone is speaking (a caption arrived). If we know their face, the robot turns exactly there; the mic
// direction alone is only used while speech is confirmed. In silence the target stays: Reachy keeps
// looking at the last person who spoke.
let lastSpeechS = 0;
const doaBuf = [];      // [t, angle, speech] of the last seconds
let doaPushedUntil = 0;
/** Feed the buffered mic directions since `fromS` into the speaker tracker (each reading once, in order). */
function flushDoa(fromS) {
  // speech = true: the backend confirmed speech for this time span; the mic array's own speech flag is
  // false most of the time (measured: 8 of 8 readings while someone talked), the angle is still good
  for (const [t, a] of doaBuf) if (t > doaPushedUntil && t >= fromS) speaker.pushDoa(t, a, true);
  doaPushedUntil = performance.now() / 1000;
}
// Instant "someone is speaking" from the backend's neural VAD (~0.1 s after the first word, no text yet):
// the mic directions count right away, and the speaking face in view becomes the focus person.
function onVadEvent(msg) {
  if (!msg.speaking || !awake) return;
  lastSpeechS = performance.now() / 1000;
  flushDoa(lastSpeechS - 0.6);
  const last = doaBuf[doaBuf.length - 1];
  const tr = faceSpeakers.pick(last ? 90 - last[1] * 180 / Math.PI : null);   // mouth movement + mic direction
  if (tr && tr.seen === faceSpeakers.lastT) {
    if (tr.pid !== focusPid) speaker.speakers.push([lastSpeechS, speaker.target]);
    focusPid = tr.pid;
    frameFocus();
  }
}

function onSpeechCaption(msg, track) {
  lastSpeechS = performance.now() / 1000;
  if (!awake) return;
  // the caption confirms speech for its whole duration: use the mic directions from that time
  flushDoa(lastSpeechS - (msg.t_end - msg.t_start) - 0.7);
  if (!track) return;
  if (track.pid !== focusPid) speaker.speakers.push([lastSpeechS, speaker.target]);   // for "I want to talk"
  focusPid = track.pid;
  frameFocus();
}

// Framing: Reachy keeps the person who spoke last (also while everyone is quiet) in the picture, head
// centred left/right and 1/3 from the top, like a camera operator. World direction of the face =
// measured head pose + its angle in the image, so the user's own headset rotation stays on top.
let focusPid = null, basePitch = 0, targetPitch = 0;
const tanV = Math.tan(cfg.vfovDeg / 2 * Math.PI / 180);
const FRAME_UP = Math.atan((0.5 - 1 / 3) * 2 * tanV) * 180 / Math.PI;   // head 1/3 from the top = this far above the axis
/** Robot head yaw when a camera frame was taken (t = capture time, s): frames lag the pose stream by ~0.1-0.2 s,
 *  and using the current yaw for an old frame overshoots while the robot turns. */
function headYawAt(t, latencyS = 0.12) {
  const h = speaker.headHist;
  for (let i = h.length - 1; i >= 0; i--) if (h[i][0] <= t - latencyS) return h[i][1];
  return h.length ? h[0][1] : status.meas[2];
}

function frameFocus() {
  if (!awake || focusPid == null) return;
  const tr = faceSpeakers.tracks.find((t) => t.pid === focusPid && t.seen === faceSpeakers.lastT);
  if (!tr) return;
  const yaw = Math.max(-150, Math.min(150, headYawAt(faceSpeakers.lastT) + faceSpeakers.angleDeg(tr)));
  if (Math.abs(yaw - speaker.target) > 3) speaker.target = yaw;   // small deadband: no jitter
  const up = Math.atan((0.5 - tr.cy) * 2 * tanV) * 180 / Math.PI;   // face above the image centre (deg)
  const pitch = status.meas[1] - (up - FRAME_UP);                   // pitch + = look down
  if (Math.abs(pitch - targetPitch) > 2) targetPitch = Math.max(-20, Math.min(20, pitch));
}
const faces = createFaces({
  getSource: () => (awake ? scene.videoFrame() : null),
  onFaces: (list, t) => { faceSpeakers.focusPid = focusPid; faceSpeakers.update(list, t, headYawAt(t)); frameFocus(); },
  log,
});
notes = createNotes({ three: scene.three, recenter, distM: cfg.distM, cardEl: $("notes") });
captions = createCaptions({
  three: scene.three,
  distM: cfg.distM,
  vfovDeg: cfg.vfovDeg,
  speakers: faceSpeakers,
  listEl: $("captions"),
  overlayEl: $("live-caption"),
  onSummary: (msg) => notes.update(msg),
  onFinal: onFinalCaption,
  onSpeech: onSpeechCaption,
  onVad: onVadEvent,
  log,
  onStatus: (s) => Object.assign(status, s),
});
$("captions-url").value = captionsUrl();
$("captions-url").onchange = (e) => { setCaptionsUrl(e.target.value.trim()); captions.reconnect(); };
$("caption-mode").value = captions.mode;
$("caption-mode").onchange = (e) => captions.setMode(e.target.value);
$("show-faces").checked = captions.showFaces;
$("show-faces").onchange = (e) => { captions.showFaces = e.target.checked; };

// Status chips in the page header.
setInterval(() => {
  const chip = (id, state, text) => { const el = $(id); el.dataset.state = state; el.lastChild.textContent = text; };
  const robotOn = status.ice === "connected";
  chip("chip-robot", robotOn ? "ok" : status.robot === "-" ? "off" : "wait", robotOn ? `Robot · ${awake ? "awake" : "asleep"}` : "Robot");
  chip("chip-camera", awake && status.video !== "-" ? "ok" : "off", "Camera");
  chip("chip-captions", status.captions === "on" ? "ok" : status.captions === "connecting" ? "wait" : "off", "Captions");
  chip("chip-xr", status.xr === "off" ? "off" : "ok", status.xr === "desktop" ? "Preview" : "VR");
}, 500);

video.onplaying = () => { status.video = `${video.videoWidth}x${video.videoHeight}`; };
video.hidden = true;   // camera "off" while the robot sleeps
robot.attachVideo(video);

// ---------------------------------------------------------------- UI flow
async function connect() {
  show("busy", "Connecting to Reachy Mini…");
  try {
    const res = await robot.connect();
    show("asleep", `Connected to ${res.robotName ?? "Reachy Mini"}. Tap Wake up.`);
  } catch (e) {
    log("connect failed:", e?.message ?? e);
    show("failed", `Could not connect: ${e?.message ?? e}. Is the robot on and its daemon signed in to Hugging Face?`);
  }
}

const xrOk = scene.xrSupported();
$("start").onclick = async () => {
  // Synchronously inside the tap: the SDK unmutes the video (robot audio), and browsers only allow
  // unmuted playback and requestSession() during a user gesture. Awaiting anything first loses it.
  video.play().catch((e) => log("video.play:", e?.message ?? e));
  const entering = scene.enterVR();
  if (!(await xrOk)) {
    // No headset: same scene in the browser window, for debugging on a laptop.
    entering.catch(() => {});
    scene.enterDesktop();
    wantRecenter = true;
    status.xr = "desktop";
    show("awake", "Desktop preview: drag to look around (the robot follows), click the buttons, R = recenter, Esc = exit.");
    return;
  }
  try {
    await entering;
    wantRecenter = true;
    status.xr = "on";
  } catch (e) {
    log("enter VR failed:", e?.message ?? e);
  }
};
$("wake").onclick = async () => {
  mic.start();   // right in the tap: the first time the browser asks for microphone permission
  // Inside the tap: browsers allow unmuted playback (robot microphone) only during a user gesture.
  robot.setAudio(!robotMuted);
  video.play().catch((e) => log("video.play:", e?.message ?? e));
  show("busy", "Reachy is waking up…");
  await robot.wake();
  mirror = new HeadMirror({ smoothing: cfg.smoothing });   // start from neutral, where the wake-up motion ends
  speaker.reset();
  focusPid = null; basePitch = 0; targetPitch = 0;
  lastSend = performance.now();
  awake = true;
  video.hidden = false;
  robot.getVolume().then((v) => {
    if (v == null) return;
    status.volume = `${v}%`; $("volume").value = v; $("volume-val").textContent = `${v}%`;
    log(`robot speaker volume ${v}%`);
    if (v < 10) flash(`🔈 Reachy speaker is at ${v}% – turn it up with the slider or "Reachy, volume 7"`);
  }).catch(() => {});
  show("awake", `Reachy is awake: camera is on, robot sound ${robotMuted ? "muted" : "on"}. Put on the headset, look straight ahead and tap Start.`);
};
$("sleep").onclick = async () => {
  awake = false;
  mic.stop();
  robot.setAudio(false);
  video.hidden = true;
  show("busy", "Reachy is going to sleep…");
  await robot.sleep();
  show("asleep", "Reachy is asleep. Tap Wake up to start again.");
};
$("talk").onclick = wantToTalk;
$("mute").onclick = () => setRobotMuted(!robotMuted);
$("mic").onclick = toggleMic;
$("volume").oninput = (e) => { $("volume-val").textContent = `${e.target.value}%`; };
$("volume").onchange = (e) => applyVolume(Number(e.target.value), "slider");
addEventListener("keydown", (e) => { if ((e.key === "m" || e.key === "M") && !e.target.closest?.("input, select, textarea")) toggleMic(); });
renderMicButton();
setRobotMuted(true);
$("signin").onclick = () => robot.signIn();
$("retry").onclick = connect;
$("recenter").onclick = () => { wantRecenter = true; };
$("signout").onclick = () => { robot.signOut(); location.reload(); };

show("busy", "Signing in…");
const signIn = await robot.signInState();
status.user = signIn;
if (signIn === "signed-in") await connect();
else if (signIn === "signed-out") show("signed-out", "Sign in with Hugging Face to reach your robot.");
