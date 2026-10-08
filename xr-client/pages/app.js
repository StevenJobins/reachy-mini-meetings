// Wires robot connection, pose logic and rendering together, plus the one-button UI.
//
// Flow: open page -> silent HF sign-in -> auto-connect to the robot (stays asleep)
//   -> tap Wake up: robot wakes, its camera and microphone switch on
//   -> tap Start: VR, the robot follows your head. Sleep puts it back to sleep (motors, camera, mic off).
// Only "Start" needs a tap: browsers allow entering VR only from a user gesture.

import { createRobot } from "./robot.js";
import { createScene } from "./scene.js";
import { HeadMirror, Recenter, headsetToRobot, robotToHeadset } from "./pose.js";
import { Laugh, WantToTalk } from "./gestures.js";
import { SpeakerTracker } from "./speaker.js";
import { captionsUrl, createCaptions, setCaptionsUrl } from "./captions.js";
import { createFaces } from "./faces.js";
import { FaceSpeakers } from "./speakers.js";
import { createNotes } from "./notes.js";
import { createRoomAudio } from "./roomaudio.js";
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
const status = { user: "-", robot: "-", motors: "-", ice: "-", video: "-", xr: "off", send: 0, cmd: [0, 0, 0], body: 0, meas: [0, 0, 0], captions: "-", audioIn: "", sound: "muted", doa: "none", mic: "off", volume: "-", micKbps: 0, videoIn: "" };
let sentCount = 0;
setInterval(() => { status.send = sentCount; sentCount = 0; }, 1000);
function statusText() {
  const f = (v) => v.map((x) => x.toFixed(1).padStart(6)).join(" ");
  return [
    `robot ${status.robot}   motors ${status.motors}   ice ${status.ice}   video ${status.video} ${status.videoIn}   send ${status.send} Hz   mic ${status.mic} ${status.micKbps.toFixed(0)} kbps   volume ${status.volume}`,
    `cmd  r/p/y ${f(status.cmd)}   body ${status.body.toFixed(1)}   speaker target ${speaker.target.toFixed(0)} base ${speaker.base.toFixed(0)}`,
    `meas r/p/y ${f(status.meas)}   captions ${status.captions}   ${faces.stats()}   robot sound ${status.sound} ${roomAudio.stats()} ${status.audioIn}   doa ${status.doa}`,
  ].join("\n");
}
// Robot sound diagnostics (why is it choppy?): packets lost, jitter, playout buffer, and how much audio
// the browser had to conceal (fill in) over the last 2 s. Shown in the status line, logged with the heartbeat.
let audioPrev = null;
setInterval(async () => {
  const pc = robot.peerConnection;
  if (!pc) return;
  try {
    const stats = await pc.getStats();
    stats.forEach((r) => {
      if (r.type !== "inbound-rtp" || r.kind !== "audio") return;
      const p = audioPrev ?? r;
      const samples = r.totalSamplesReceived - p.totalSamplesReceived;
      // silentConcealed = gaps the sender left on purpose in silence (DTX): normal. Only the rest is audible.
      const audible = (r.concealedSamples - p.concealedSamples) - ((r.silentConcealedSamples ?? 0) - (p.silentConcealedSamples ?? 0));
      const stretch = (r.insertedSamplesForDeceleration ?? 0) - (p.insertedSamplesForDeceleration ?? 0)
        + (r.removedSamplesForAcceleration ?? 0) - (p.removedSamplesForAcceleration ?? 0);
      const bufMs = (r.jitterBufferDelay - p.jitterBufferDelay) / Math.max(1, r.jitterBufferEmittedCount - p.jitterBufferEmittedCount) * 1000;
      const pct = (n) => (samples > 0 ? (100 * n / samples).toFixed(0) : "-");
      status.audioIn = `| in: lost ${r.packetsLost} jitter ${(r.jitter * 1000).toFixed(0)}ms buf ${bufMs.toFixed(0)}ms dropouts ${pct(audible)}% stretched ${pct(stretch)}% level ${(r.audioLevel ?? 0).toFixed(2)}`;
      audioPrev = r;
    });
  } catch {}
}, 2000);

// Video diagnostics (why is it soft?): what actually arrives. The daemon's webrtcsink scales the picture down
// when its bitrate estimate drops below 2 Mbit/s (robot/scripts/patch_daemon_video.py raises the floor).
let videoPrev = null;
setInterval(async () => {
  const pc = robot.peerConnection;
  if (!pc) return;
  try {
    const stats = await pc.getStats();
    stats.forEach((r) => {
      if (r.type !== "inbound-rtp" || r.kind !== "video") return;
      const dt = videoPrev ? (r.timestamp - videoPrev.timestamp) / 1000 : 0;
      const kbps = dt > 0 ? (r.bytesReceived - videoPrev.bytesReceived) * 8 / 1000 / dt : 0;
      const codec = stats.get(r.codecId)?.mimeType?.replace("video/", "") ?? "?";
      status.videoIn = `${r.frameWidth ?? "?"}x${r.frameHeight ?? "?"} ${(r.framesPerSecond ?? 0).toFixed(0)}fps ${(kbps / 1000).toFixed(1)}Mbps ${codec} ${r.decoderImplementation ?? ""}`;
      videoPrev = r;
    });
  } catch {}
}, 2000);

// Heartbeat every 5 s: if the page dies, the last lines show memory, video and connection state.
setInterval(() => {
  const mem = performance.memory ? `heap ${(performance.memory.usedJSHeapSize / 1e6).toFixed(0)} MB` : "heap ?";
  log("alive", mem, status.audioIn, `xr ${status.xr}`, `robot ${status.robot}`, `ice ${status.ice}`, `motors ${status.motors}`,
    `send ${status.send} Hz`, scene.videoStats(), `in ${status.videoIn}`, roomAudio.stats());
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
  $("laugh").hidden = state !== "awake";
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
const laugh = new Laugh();

// Speaker following is always on: the robot slowly turns to whoever speaks (DoA), and the headset
// rotation is added ON TOP of that base, so you can always look elsewhere. Straight ahead = the speaker.
// 5 agreeing mic readings within 1 s for a new direction (3 in 0.6 s let single reflections turn the head)
const speaker = new SpeakerTracker({ confirmN: 5, confirmWindowS: 1.0 });
// Follow diagnostics: who moved the target (face / mic / search / talk), logged when it jumps > 4°.
let lastLoggedYaw = 0, lastLoggedPitch = 0;
function noteTarget(source) {
  if (Math.abs(speaker.target - lastLoggedYaw) > 4) { log(`follow yaw ${speaker.target.toFixed(0)} by ${source}`); lastLoggedYaw = speaker.target; }
  if (typeof targetPitch === "number" && Math.abs(targetPitch - lastLoggedPitch) > 4) { log(`follow pitch ${targetPitch.toFixed(0)} by ${source}`); lastLoggedPitch = targetPitch; }
}
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
  const l = laugh.step(nowS);
  const bodyYaw = Math.max(t.yaw - 60, Math.min(t.yaw + 60, t.bodyYaw + g.bodyOffset));
  // Laughing: its antenna wiggle wins over "I want to talk"; the chuckle stays inside the meeting limits.
  const L = mirror.lim;
  const pitch = Math.max(-L.pitchUp, Math.min(L.pitchDown, t.pitch + l.pitch));
  const roll = Math.max(-L.roll, Math.min(L.roll, t.roll + l.roll));
  const antennas = laugh.active(nowS) ? l.antennas : g.antennas;
  if (robot.setHead({ ...t, roll, pitch, bodyYaw, antennas })) sentCount++;
  status.cmd = [t.roll, t.pitch, t.yaw];
  status.body = bodyYaw;
}

// Robot sound (its microphone, i.e. the room) is muted by default: otherwise you hear yourself twice,
// once directly and once through the robot. Unmuting must happen inside a tap / VR select.
let robotMuted = true;
// Room sound comes from the caption backend's audio stream when it is available (the robot's own WebRTC audio
// drops ~55 % of the sound, daemon-side); the WebRTC audio is only the fallback when no stream arrives.
const roomAudio = createRoomAudio({ log });
let webrtcAudioOn = false;
function applyRobotAudio() {
  roomAudio.setEnabled(awake && !robotMuted);
  const want = awake && !robotMuted && !roomAudio.live;
  if (want !== webrtcAudioOn) { robot.setAudio(want); webrtcAudioOn = want; log("robot sound via", want ? "WebRTC (no stream)" : roomAudio.live ? "audio stream" : "-"); }
}
setInterval(applyRobotAudio, 1000);

function setRobotMuted(m) {
  robotMuted = m;
  roomAudio.resume();   // usually called from a tap: lets the browser start audio
  applyRobotAudio();
  status.sound = m ? "muted" : "on";
  $("mute").textContent = m ? "🔇 Robot muted: tap to unmute" : "🔊 Mute robot";
}

// Two-way audio, like a video call: your mic (headset, or the laptop's mic in the browser) goes to the
// robot speaker while Reachy is awake. On by default; the big mic button mutes you.
const mic = createMic({
  onVoice: (buf) => captions?.sendVoice(buf),   // "translate me": your voice -> backend -> Reachy speaks it
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

/**
 * The headset user laughs -> Reachy laughs (burst, "ha-ha" rhythm, settle; see Laugh in gestures.js). Today: the Laugh button (page, VR,
 * key L). The native app will call this from the Galaxy XR face tracking (smile / laugh blendshapes); WebXR in
 * Chrome does not expose face tracking; it can pass intensity 0.6-1.4 (e.g. from how wide the smile is).
 * Works while muted too: it does not depend on the microphone.
 */
function userLaughed(source = "button", intensity) {
  if (!awake) return;
  laugh.trigger(performance.now() / 1000, intensity);
  log("laugh", source, `intensity ${laugh.k.toFixed(1)}`);
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
    // The VR room turns with the base (speaker yaw + framing pitch): the window shows where the robot looks
    // RELATIVE to the speaker, so it stays centred in front of you while Reachy frames a face.
    scene.setRobotHead(recenter.toWorld(robotToHeadset(roll, pitch - basePitch, yaw - speaker.base)));
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
    if (wantRecenter) { recenter.set(q); wantRecenter = false; notes.layout(); scene?.recenterView(); log("recentered"); }
    if (!awake || status.xr === "off" || !robot.connected || now - lastSend < 1000 / cfg.sendHz) return;
    const dt = (now - lastSend) / 1000;
    lastSend = now;
    const raw = headsetToRobot(recenter.toRelative(q));
    raw[2] += stepBase(dt, now / 1000);   // user's head rotation on top of the speaker direction
    raw[1] += basePitch;                  // ... and on top of the framing pitch
    send(mirror.step(raw, dt), now / 1000);
  },
  // Head-locked buttons in VR: point (controller ray / hand pinch) and select. Select elsewhere = recenter.
  windowMode: params.get("window") === "robot" ? "robot" : "head",   // video window follows your head
  vrButtons: [
    { kind: "mic", muted: () => mic.muted || status.mic !== "on", label: () => (mic.muted ? "Muted" : status.mic === "on" ? "Mic on" : "Mic off"), onClick: toggleMic },
    { icon: "🙋", label: "Talk", onClick: wantToTalk },
    { icon: "😂", label: "Laugh", onClick: () => userLaughed("vr button") },
    { icon: "🌐", label: () => (mic.translate ? "Translating" : "Translate"), active: () => mic.translate,
      onClick: () => mic.setTranslate(!mic.translate) },
    { icon: "💬", label: () => ({ both: "Both", translation: "Translated", original: "Original" })[captions?.mode ?? "both"],
      onClick: () => captions.cycleMode() },
    { icon: "📝", label: "Notes", active: () => !!notes?.visible, onClick: () => notes.toggle() },
    { icon: () => (robotMuted ? "🔇" : "🔊"), label: "Sound", active: () => !robotMuted, onClick: () => setRobotMuted(!robotMuted) },
    { icon: "⟳", label: "Recenter", more: true, onClick: () => { wantRecenter = true; } },
    { icon: "🐞", label: "Debug", more: true, active: () => debugOn, onClick: () => { debugOn = !debugOn; scene.toggleDebug(); } },
    { icon: "✕", label: "Exit VR", more: true, onClick: () => scene.exitVR() },
  ],
  // Without the caption server there is no speech detection, so Reachy cannot turn to whoever speaks.
  warning: () => (status.captions === "on" ? ""
    : status.captions === "not allowed" ? "⚠ Caption server refused this Hugging Face account"
    : "⚠ No caption server: no captions, Reachy won't turn to speakers"),
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
  // false most of the time (measured: 8 of 8 readings while someone talked), the angle is still good.
  // While the followed person is in the picture, the mic direction (±10-20°, plus wall reflections) only
  // counts when it points clearly outside the picture: someone out of view speaks. Inside the picture the
  // face (and mouth movement) is far more precise; letting both steer made the head twitch and turn away.
  const focus = focusTrack();
  // The followed person moves their mouth: they are the one talking, the mic direction has nothing to add
  // (in the test it jumped between -121° and +43° while the face sat still at -20°).
  const focusTalking = focus && faceSpeakers.activity(focus) > 0.03;
  for (const [t, a] of doaBuf) {
    if (t <= doaPushedUntil || t < fromS) continue;
    if (focusTalking) continue;
    if (focus && Math.abs(90 - a * 180 / Math.PI) < hfovDeg / 2 + 10) continue;
    speaker.pushDoa(t, a, true);
  }
  doaPushedUntil = performance.now() / 1000;
  noteTarget("mic");
}

/** Track of the person Reachy follows if they are in the picture (seen within the last 0.5 s), else null. */
function focusTrack() {
  if (focusPid == null) return null;
  return faceSpeakers.tracks.find((t) => t.pid === focusPid && (faceSpeakers.lastT ?? 0) - t.seen < 0.5) ?? null;
}
// Instant "someone is speaking" from the backend's neural VAD (~0.1 s after the first word, no text yet):
// the mic directions count right away, and the speaking face in view becomes the focus person.
function onVadEvent(msg) {
  if (!msg.speaking || !awake) return;
  lastSpeechS = performance.now() / 1000;
  flushDoa(lastSpeechS - 0.6);
  const last = doaBuf[doaBuf.length - 1];
  const doaRel = last ? 90 - last[1] * 180 / Math.PI : null;   // relative to the head, + = left
  const tr = faceSpeakers.pick(doaRel);   // mouth movement + mic direction
  if (tr && tr.seen === faceSpeakers.lastT) {
    if (tr.pid !== focusPid) speaker.speakers.push([lastSpeechS, speaker.target]);
    focusPid = tr.pid;
    frameFocus();
  } else if (!faceSpeakers.tracks.some((t) => (faceSpeakers.lastT ?? 0) - t.seen < 1.5)
             && (!focusLast || (faceSpeakers.lastT ?? 0) - focusLast.t > 1.5)
             && (doaRel == null || Math.abs(doaRel) < 35)) {
    // (no face for 1.5 s: a detection flicker must not trigger this, it made the pitch twitch)
    // Someone talks in front of Reachy but no face is in the picture: their head is above it (standing, or
    // close to the robot). Look up step by step (~12 °/s at 4 events/s) until the face shows up.
    targetPitch = Math.max(-PITCH_UP, targetPitch - 3);
    noteTarget("search up (speech, no face)");
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

const PITCH_UP = 35, PITCH_DOWN = 20;   // same as HeadMirror's limits in pose.js
let focusLast = null;                   // {pid, top, bottom, t, yaw} of the focus face when last seen
let focusVel = 0;                       // its speed through the room, deg/s (smoothed)
function frameFocus() {
  if (!awake || focusPid == null) return;
  const tr = faceSpeakers.tracks.find((t) => t.pid === focusPid && t.seen === faceSpeakers.lastT);
  if (!tr) {
    // A face cut off at the image edge is no longer detected: if the focus person left at the top (or
    // bottom), keep tilting that way for a moment until their face is back in the picture.
    const t = faceSpeakers.lastT ?? 0;
    if (focusLast && t - focusLast.t < 2.5) {
      if (focusLast.top < 0.12) targetPitch = Math.max(-PITCH_UP, targetPitch - 2);
      else if (focusLast.bottom > 0.9) targetPitch = Math.min(PITCH_DOWN, targetPitch + 2);
      noteTarget("edge search");
    }
    return;
  }
  const t = faceSpeakers.lastT;
  const offX = faceSpeakers.angleDeg(tr);                     // face left/right of the image centre (deg)
  const yawNow = headYawAt(t) + offX;                         // where the face is in the room
  // Lead a moving person: the target only updates ~8x/s and the motion limiter brakes at every target, so
  // without a lead Reachy lags behind a walking person. Room speed of the face, smoothed; detections jitter
  // by a few percent, so below 8 °/s it counts as standing still (no lead, no jitter).
  if (focusLast?.pid === focusPid && t > focusLast.t && t - focusLast.t < 0.5) {
    focusVel += 0.25 * ((yawNow - focusLast.yaw) / (t - focusLast.t) - focusVel);
  } else focusVel = 0;
  focusLast = { pid: focusPid, top: tr.top, bottom: tr.top + tr.h, t, yaw: yawNow };
  const moving = Math.abs(focusVel) > 8;
  // Dead zone around the framing point: a face that is already well placed does not move the head at all.
  if (moving || Math.abs(offX) > 4) {
    const lead = moving ? Math.max(-15, Math.min(15, focusVel * 0.3)) : 0;
    const yaw = Math.max(-150, Math.min(150, yawNow + lead));
    if (Math.abs(yaw - speaker.target) > 2) speaker.target = yaw;
  }
  const up = Math.atan((0.5 - tr.cy) * 2 * tanV) * 180 / Math.PI;   // face above the image centre (deg)
  if (Math.abs(up - FRAME_UP) > 3) {
    const pitch = status.meas[1] - (up - FRAME_UP);                 // pitch + = look down
    targetPitch = Math.max(-PITCH_UP, Math.min(PITCH_DOWN, pitch));
  }
  noteTarget(`face p${tr.pid} off ${offX.toFixed(0)}°`);
}
const faces = createFaces({
  getSource: () => (awake ? scene.videoFrame() : null),
  onFaces: (list, t) => { faceSpeakers.focusPid = focusPid; faceSpeakers.update(list, t, headYawAt(t)); frameFocus(); },
  log,
});
notes = createNotes({ three: scene.three, recenter, distM: cfg.distM, cardEl: $("notes") });
scene.addDraggable(notes.panel, () => notes.moved());   // grab the notes and put them where you like
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
  // what Reachy said for you (translate me): shown above the dock for a moment
  onMe: (msg) => { log("me:", msg.text, "->", msg.translation ?? "(no translation)"); scene.info(`🗣 ${msg.translation ?? msg.text}`, 6000); },
  onAudio: (buf) => roomAudio.push(buf),
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
  roomAudio.resume();   // inside the tap: browsers start audio only from a user gesture
  video.play().catch((e) => log("video.play:", e?.message ?? e));
  show("busy", "Reachy is waking up…");
  await robot.wake();
  mirror = new HeadMirror({ smoothing: cfg.smoothing });   // start from neutral, where the wake-up motion ends
  speaker.reset();
  focusPid = null; basePitch = 0; targetPitch = 0;
  lastSend = performance.now();
  awake = true;
  applyRobotAudio();
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
$("laugh").onclick = () => userLaughed("button");
addEventListener("keydown", (e) => { if ((e.key === "l" || e.key === "L") && !e.target.closest?.("input, select, textarea")) userLaughed("key"); });
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
