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
import { volumeCommand } from "./voicecmd.js";

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
const status = { user: "-", robot: "-", motors: "-", ice: "-", video: "-", xr: "off", send: 0, cmd: [0, 0, 0], body: 0, meas: [0, 0, 0], captions: "-", sound: "muted", follow: "on", doa: "none", mic: "off", volume: "-" };
let sentCount = 0;
setInterval(() => { status.send = sentCount; sentCount = 0; }, 1000);
function statusText() {
  const f = (v) => v.map((x) => x.toFixed(1).padStart(6)).join(" ");
  return [
    `robot ${status.robot}   motors ${status.motors}   ice ${status.ice}   video ${status.video}   send ${status.send} Hz   mic ${status.mic}   volume ${status.volume}`,
    `cmd  r/p/y ${f(status.cmd)}   body ${status.body.toFixed(1)}   speaker ${status.follow} target ${speaker.target.toFixed(0)} base ${speaker.base.toFixed(0)}`,
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
setInterval(() => { $("debug-text").textContent = `user ${status.user}   xr ${status.xr}   version ${VERSION}\n` + statusText(); }, 200);

function show(state, message) {
  $("message").textContent = message;
  $("wake").hidden = state !== "asleep";
  $("start").hidden = state !== "awake";
  $("sleep").hidden = state !== "awake";
  $("talk").hidden = state !== "awake";
  $("mute").hidden = state !== "awake";
  $("mic").hidden = state !== "awake";
  $("follow").hidden = state !== "awake";
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

// Speaker following has priority: the robot slowly turns to whoever speaks (DoA), and the headset
// rotation is added ON TOP of that base. Looking straight ahead in VR = looking at the speaker.
const speaker = new SpeakerTracker();
let follow = true;
let frozenBase = null;   // follow off: keep the base where it was
let talkCenter = null;   // while waving: turn to the center of all recent speakers (turn_to_speaker.py)

function setFollow(on) {
  follow = on;
  frozenBase = on ? null : speaker.base;
  status.follow = on ? "on" : "off";
  $("follow").textContent = on ? "🎯 Following speaker: tap to stop" : "🎯 Follow speaker";
  log("follow speaker", status.follow);
}

/** Smooth base yaw for this tick (degrees, robot frame). */
function stepBase(dt, nowS) {
  if (!talk.active(nowS)) talkCenter = null;
  speaker.override = talkCenter ?? frozenBase;
  const base = speaker.step(dt);
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
    status.mic === "blocked" ? "Mic blocked" : status.mic === "no channel" ? "No audio channel" : mic.muted ? "Unmute" : "Mute";
  el.title = mic.muted ? "You are muted. Tap (or press M) to talk to the room." : "The room hears you. Tap (or press M) to mute.";
}
function toggleMic() {
  if (!awake) return;
  if (status.mic === "blocked" || status.mic === "off") { mic.start(); return; }   // retry permission inside the tap
  mic.setMuted(!mic.muted);
}

// "Reachy, volume 5" from anyone in the room, in any language -> robot speaker volume 50 %.
const volumeDone = new Set();
function onFinalCaption(msg) {
  if (volumeDone.has(msg.id)) return;
  const v = volumeCommand(msg);
  if (v === null) return;
  volumeDone.add(msg.id);
  log(`voice command: volume ${v}% ("${msg.text}")`);
  robot.setVolume(v)
    .then((got) => { status.volume = `${got ?? v}%`; flash(`🔊 Reachy volume ${got ?? v}%`); })
    .catch((e) => log("set volume failed:", e?.message ?? e));
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
  send(mirror.step([0, 0, stepBase(dt, performance.now() / 1000)], dt), performance.now() / 1000);
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
    if (awake) speaker.pushDoa(performance.now() / 1000, angle, speech);
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
    send(mirror.step(raw, dt), now / 1000);
  },
  // Head-locked buttons in VR: point (controller ray / hand pinch) and select. Select elsewhere = recenter.
  vrButtons: [
    { kind: "mic", muted: () => mic.muted || status.mic !== "on", label: () => (mic.muted ? "Muted" : status.mic === "on" ? "Mic on" : "Mic off"), onClick: toggleMic },
    { icon: "🙋", label: "Talk", onClick: wantToTalk },
    { icon: "🎯", label: "Follow", active: () => follow, onClick: () => setFollow(!follow) },
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
const faces = createFaces({
  getSource: () => (awake ? scene.videoFrame() : null),
  onFaces: (list, t) => faceSpeakers.update(list, t),
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
  setFollow(follow);
  lastSend = performance.now();
  awake = true;
  video.hidden = false;
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
addEventListener("keydown", (e) => { if ((e.key === "m" || e.key === "M") && !e.target.closest?.("input, select, textarea")) toggleMic(); });
renderMicButton();
$("follow").onclick = () => setFollow(!follow);
setFollow(true);
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
