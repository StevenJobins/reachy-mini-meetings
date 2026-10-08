// Wires robot connection, pose logic and rendering together, plus the one-button UI.
//
// Flow: open page -> silent HF sign-in -> auto-connect to the robot (stays asleep)
//   -> tap Wake up: robot wakes, its camera and microphone switch on
//   -> tap Start: VR, the robot follows your head. Sleep puts it back to sleep (motors, camera, mic off).
// Only "Start" needs a tap: browsers allow entering VR only from a user gesture.

import { createRobot } from "./robot.js";
import { createScene } from "./scene.js";
import { HeadMirror, Recenter, headsetToRobot, qinv, qmul, robotToHeadset } from "./pose.js";
import { RoomScan } from "./roomscan.js";
import { Laugh, WantToTalk } from "./gestures.js";
import { SpeakerTracker } from "./speaker.js";
import { captionsUrl, createCaptions, extraTunnelKeys, onBackendFaces, setCaptionsUrl, setExtraTunnelKeys } from "./captions.js";
import { createFaces } from "./faces.js";
import { FaceSpeakers } from "./speakers.js";
import { createNotes } from "./notes.js";
import { createRoomAudio } from "./roomaudio.js";
import { createMic } from "./mic.js";
import { explainVolume, volumeCommand } from "./voicecmd.js";
import { CameraModel, factoryLite } from "./camera.js";

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

// ---------------------------------------------------------------- camera model (lens, see camera.js)
// Used for the video sphere in VR, bubbles / face frames, and face direction when following. "auto": our
// checkerboard calibration (camera.json, robot/scripts/calibrate_camera.py) when it exists, else the old
// 54° pinhole estimate. "factory": Pollen's calibration of the Lite camera (unverified scaling for the stream).
const CAM_KEY = "reachy-xr-camera-model";
const cameraModels = { estimate: CameraModel.pinhole(cfg.vfovDeg), factory: factoryLite(), calibrated: null };
let camChoice = "auto";
try { camChoice = localStorage.getItem(CAM_KEY) || "auto"; } catch {}
const pickCamera = () => (camChoice === "factory" ? cameraModels.factory
  : camChoice === "estimate" ? cameraModels.estimate : cameraModels.calibrated ?? cameraModels.estimate);
let cam = pickCamera();
function applyCamera() {
  cam = pickCamera();
  scene.setCamera(cam);
  captions?.setCamera(cam);
  faceSpeakers.camera = cam;
}
fetch("camera.json", { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : null))
  .then((j) => {
    if (!j?.fx) return;
    cameraModels.calibrated = new CameraModel({ ...j, name: j.name ?? "calibrated" });
    log(`camera.json: ${cameraModels.calibrated.name}, rms ${j.rms ?? "?"} px`);
    applyCamera();
  })
  .catch(() => {});
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
// The same lines go to the caption backend, which writes them to a file on the Mac
// (~/Library/Logs/reachy-headset.log): the headset's own log is unreachable without a cable.
let logSent = 0, logTotal = 0;
setInterval(() => {
  if (!captions?.connected) return;
  const fresh = Math.min(logTotal - logSent, logLines.length);
  if (fresh > 0) captions.sendLog(logLines.slice(-fresh));
  logSent = logTotal;
}, 2000);
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
  logTotal++;
  if (logLines.length > 300) logLines = logLines.slice(-300);
  logDirty = true;
}
addEventListener("error", (e) => log("ERROR", e.message, `${e.filename}:${e.lineno}`));
addEventListener("unhandledrejection", (e) => log("UNHANDLED", e.reason?.message ?? e.reason));

// ---------------------------------------------------------------- status
const status = { user: "-", robot: "-", motors: "-", ice: "-", video: "-", xr: "off", send: 0, cmd: [0, 0, 0], body: 0, meas: [0, 0, 0], captions: "-", audioIn: "", sound: "muted", doa: "none", mic: "off", volume: "-", micKbps: 0, videoIn: "" };
let sentCount = 0;
let connectedAt = Infinity;   // when the robot session came up (for the "no camera image" warning)
setInterval(() => { status.send = sentCount; sentCount = 0; }, 1000);
function statusText() {
  const f = (v) => v.map((x) => x.toFixed(1).padStart(6)).join(" ");
  return [
    `robot ${status.robot}   motors ${status.motors}   ice ${status.ice}   video ${status.video} ${status.videoIn}   send ${status.send} Hz   mic ${status.mic} ${status.micKbps.toFixed(0)} kbps   volume ${status.volume}`,
    `cmd  r/p/y ${f(status.cmd)}   body ${status.body.toFixed(1)}   speaker target ${speaker.target.toFixed(0)} base ${speaker.base.toFixed(0)}   cam ${cam.name} ${cam.hfovDeg.toFixed(0)}x${cam.vfovDeg.toFixed(0)}°   room ${scene.roomInfo.patches}${scan.active ? ` scanning ${status.scan ?? ""}` : ""} depth ${scene.roomInfo.depth}${depthError ? "(off)" : ""}   view ${viewMode}${viewMode === "world" ? `/${status.follow ?? "-"}` : ""} delay ${(videoDelayS * 1000).toFixed(0)}ms turn ${(status.turn ?? 0).toFixed(0)}°/s`,
    `meas r/p/y ${f(status.meas)}   captions ${status.captions}   ${backendFacesLive() ? `people (backend) @ ${backendFacesFps} fps` : faces.stats()}   robot sound ${status.sound} ${roomAudio.stats()} ${status.audioIn}   doa ${status.doa}`,
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
    `send ${status.send} Hz`, scene.videoStats(), `in ${status.videoIn}`, roomAudio.stats(), backendFacesLive() ? `people backend ${backendFacesFps} fps` : faces.stats());
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
  cmdHist.push([nowS, t.roll, t.pitch - basePitch, t.yaw - speaker.base]);   // for the world-locked sanity check
  if (cmdHist.length > 150) cmdHist.shift();
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
const volumeDone = new Set();   // "<backend session>:<caption id>": ids restart at 0 with every backend start
function onFinalCaption(msg) {
  const key = `${captions?.session}:${msg.id}`;
  if (volumeDone.has(key)) return;
  const v = volumeCommand(msg);
  if (v === null) {
    // A volume word without a command: say why, so a failed "Reachy, volume 9" can be diagnosed.
    const why = explainVolume(msg.text) ?? explainVolume(msg.translation);
    if (why) log(`volume word heard, no command (${why}): "${msg.text}"`);
    return;
  }
  volumeDone.add(key);
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
  const scanRaw = scanTarget();
  send(mirror.step(scanRaw ?? [0, basePitch, stepBase(dt, performance.now() / 1000)], dt), performance.now() / 1000);
}, 1000 / 50);

const robot = createRobot({
  clientId: HF_CLIENT_ID,
  log,
  onStatus: (s) => {
    for (const k of ["robot", "ice", "motors"]) if (k in s && s[k] !== status[k]) log(k, "->", s[k]);
    if ("robot" in s && s.robot !== status.robot) connectedAt = ["connecting", "stopped", "reconnecting", "-"].includes(s.robot) ? Infinity : performance.now();
    Object.assign(status, s);
  },
  onMeasuredHead: (roll, pitch, yaw) => {
    status.meas = [roll, pitch, yaw];
    pitchHist.push([performance.now() / 1000, pitch]);
    if (pitchHist.length > 100) pitchHist.shift();
    speaker.pushHeadYaw(performance.now() / 1000, yaw);
    // The VR room turns with the base (speaker yaw + framing pitch): the window shows where the robot looks
    // RELATIVE to the speaker, so it stays centred in front of you while Reachy frames a face.
    // Stored with its arrival time; the window uses the pose from when the shown frame was captured (below).
    poseHist.push([performance.now() / 1000, roll, pitch - basePitch, yaw - speaker.base]);
    if (poseHist.length > 90) poseHist.shift();
    absHist.push([performance.now() / 1000, roll, pitch, yaw]);   // absolute: window + room panorama
    if (absHist.length > 90) absHist.shift();
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
// ---------------------------------------------------------------- view: world-locked (reprojection) or comfort
// World-locked (default, as in the proposal): the video window hangs where the robot camera looked when the
// shown frame was captured. Frames carry no pose (Pollen's WebRTC), so we pair them by time: the measured
// head pose arrives with almost no delay on the data channel, the video ~0.1 s later (capture + encode +
// network jitter buffer + decode). The window therefore uses the pose from videoDelayS ago, estimated live
// from the WebRTC stats. Comfort: the calm, lazily following window (no latency hiding).
const VIEW_KEY = "reachy-xr-view";
let viewMode = "world";
try { if (localStorage.getItem(VIEW_KEY) === "comfort") viewMode = "comfort"; } catch {}
if (params.get("window")) viewMode = params.get("window") === "robot" || params.get("window") === "world" ? "world" : "comfort";
const poseHist = [];                 // [t, roll, pitch, yaw] (deg, relative to the base), arrival time
const cmdHist = [];                  // same for what we commanded
const absHist = [];                  // [t, roll, pitch, yaw] absolute (robot frame), arrival time
const CAPTURE_ENCODE_S = 0.045;      // camera exposure/readout + H.264 encode on the robot side (estimate)
let videoDelayS = 0.12;              // shown frame age relative to the pose stream, updated from getStats
let vjb = null;                      // previous jitter-buffer / decode counters
setInterval(async () => {
  const pc = robot.peerConnection;
  if (!pc) return;
  try {
    (await pc.getStats()).forEach((r) => {
      if (r.type !== "inbound-rtp" || r.kind !== "video") return;
      const now = { jb: r.jitterBufferDelay ?? 0, n: r.jitterBufferEmittedCount ?? 0, dec: r.totalDecodeTime ?? 0, f: r.framesDecoded ?? 0 };
      if (vjb && now.n > vjb.n && now.f > vjb.f) {
        const est = (now.jb - vjb.jb) / (now.n - vjb.n) + (now.dec - vjb.dec) / (now.f - vjb.f) + CAPTURE_ENCODE_S;
        if (est > 0 && est < 1) videoDelayS += 0.3 * (est - videoDelayS);
      }
      vjb = now;
    });
  } catch {}
}, 1000);

/** Head pose (relative to the base) at time t, interpolated between the stored samples. */
function poseAt(t, h = poseHist) {
  if (!h.length) return null;
  if (t <= h[0][0]) return h[0].slice(1);
  for (let i = h.length - 1; i > 0; i--) {
    if (h[i - 1][0] <= t) {
      const [t0, ...a] = h[i - 1], [t1, ...b] = h[i];
      const u = t1 > t0 ? Math.min(1, (t - t0) / (t1 - t0)) : 1;
      return a.map((v, k) => v + (b[k] - v) * u);
    }
  }
  return h[h.length - 1].slice(1);
}

// Vignette: darken the edge while the picture turns without the user's own head motion, i.e. while the base
// (speaker direction + framing pitch) moves. 0 below 6 °/s, full at 45 °/s.
let lastBase = null;
function updateView() {
  const nowS = performance.now() / 1000;
  // Room frame: the robot's world turned by the base (speaker direction + framing pitch), so that looking straight
  // ahead = looking at the speaker. The scanned room and the live window both live in it.
  const roomQ = recenter.toWorld(qinv(robotToHeadset(0, basePitch, speaker.base)));
  scene.setRoomFrame(roomQ);
  const p = poseAt(nowS - videoDelayS);
  const a = poseAt(nowS - videoDelayS, absHist);
  if (p && a && robotFollows(p, nowS)) scene.setRobotHead(qmul(roomQ, robotToHeadset(a[0], a[1], a[2])));
  else scene.clearRobotHead();
  const b = [speaker.base, basePitch, nowS];
  if (lastBase && b[2] > lastBase[2]) {
    const rate = Math.hypot(b[0] - lastBase[0], b[1] - lastBase[1]) / (b[2] - lastBase[2]);
    scene.setVignette(awake ? (rate - 6) / 39 : 0);
    status.turn = rate;
  }
  lastBase = b;
}

// World-locked only while the robot head really does what we command: awake, fresh poses, and the measured
// pose within FOLLOW_TOL of the command (the robot lags ~0.1-0.4 s, so the command is compared a bit earlier).
// Otherwise (asleep, sleep pose after a failed wake-up, wake-up motion, stuck, no pose stream) the measured pose
// says nothing about where the user looks, and the window would hang at the floor or off to the side: then it
// stands in front of the user like in comfort mode. Hysteresis so it does not flicker on fast head turns.
const FOLLOW_TOL = 30, FOLLOW_OK = 15;
let following = false, followSince = 0;
function robotFollows(p, nowS) {
  const fresh = poseHist.length && nowS - poseHist[poseHist.length - 1][0] < 0.7;
  const c = poseAt(nowS - videoDelayS - 0.25, cmdHist);
  const err = c ? Math.max(...p.map((v, k) => Math.abs(v - c[k]))) : Infinity;
  const ok = awake && fresh && (following ? err < FOLLOW_TOL : err < FOLLOW_OK);
  if (ok !== following && nowS - followSince > (ok ? 0.3 : 0.5)) {
    following = ok; followSince = nowS;
    log(`view: ${ok ? "world-locked at the robot pose" : `robot not following (${awake ? (fresh ? `off by ${err.toFixed(0)}°` : "no pose") : "asleep"}), window in front of you`}`);
  } else if (ok === following) followSince = nowS;
  status.follow = following ? "locked" : "front";
  return following;
}

function setViewMode(m) {
  viewMode = m === "comfort" ? "comfort" : "world";
  scene.setWindowMode(viewMode);
  try { localStorage.setItem(VIEW_KEY, viewMode); } catch {}
  $("view-mode").value = viewMode;
}

// ---------------------------------------------------------------- room scan + panorama (WP2)
// After wake-up Reachy looks around once (roomscan.js, 12 stops, ~20 s) and every frame becomes a patch of the
// room panorama, world-locked in the room frame: turning the head shows the room at once, only moving people
// wait for the live video. While in use, the panorama refreshes itself whenever Reachy holds still. Each frame
// also goes to the laptop for metric depth (backend depth.py, Depth Anything V2): the patch becomes 3D, so
// moving the head gives parallax. Without the backend or its [depth] extra the panorama stays a flat sphere.
const ROOM_KEY = "reachy-xr-room";
const roomSettings = (() => { try { return JSON.parse(localStorage.getItem(ROOM_KEY)) ?? {}; } catch { return {}; } })();
let scanAfterWake = roomSettings.scanAfterWake ?? true;
let roomVisible = roomSettings.visible ?? true;
const saveRoomSettings = () => { try { localStorage.setItem(ROOM_KEY, JSON.stringify({ scanAfterWake, visible: roomVisible })); } catch {} };
const scan = new RoomScan();
const roomSlots = new Map();          // key -> { pitch, yaw, t } where the patch was taken (absolute)
let depthSeq = 0, depthPending = 0, depthError = null;
const depthLatest = new Map();        // key -> request id of the newest frame (older replies are dropped)

const scanTarget = () => (scan.active ? [0, scan.target.pitch, scan.target.yaw] : null);

function startRoomScan() {
  if (!awake) return;
  scene.clearRoom(); roomSlots.clear(); depthLatest.clear(); depthError = null;
  scan.start(performance.now() / 1000);
  log("room scan: start");
  flash("🔄 Reachy is scanning the room…");
}

/** Current camera frame -> room patch `key` at the pose it was taken with, and ask the laptop for depth. */
function captureRoomPatch(key, nowS) {
  const img = scene.captureFrame(960, 540);
  const a = poseAt(nowS - videoDelayS, absHist);
  if (!img || !a) return false;
  scene.setRoomPatch(key, img, robotToHeadset(a[0], a[1], a[2]), null);
  roomSlots.set(key, { pitch: a[1], yaw: a[2], t: nowS });
  if (!depthError && captions) {
    const id = `${key}#${++depthSeq}`;
    const b64 = img.toDataURL("image/jpeg", 0.85).split(",")[1];
    if (captions.requestDepth(id, b64, 49, 28)) { depthLatest.set(key, id); depthPending++; }
  }
  return true;
}

function onDepth(msg) {
  depthPending = Math.max(0, depthPending - 1);
  const key = String(msg.id ?? "").split("#")[0];
  if (msg.error) {
    if (!depthError) log(`room depth unavailable on the laptop: ${msg.error} (backend: pip install -e ".[depth]")`);
    depthError = msg.error;
    return;
  }
  if (depthLatest.get(key) !== msg.id || !Array.isArray(msg.depth)) return;   // a newer frame replaced it
  scene.setRoomPatchDepth(key, msg.depth);
}

setInterval(() => {   // scan driver
  if (!scan.active) return;
  if (!awake) { scan.cancel(); log("room scan: cancelled (asleep)"); return; }
  const nowS = performance.now() / 1000;
  const r = scan.step(nowS, status.meas, videoDelayS + 0.1);
  if (!r) return;
  const tg = scan.target;
  if (r === "shoot-late") log(`room scan: ${tg.key} not settled, taking it anyway`);
  captureRoomPatch(tg.key, nowS);
  const { done, total } = scan.progress;
  status.scan = `${done + 1}/${total}`;
  if (scan.shot(nowS)) { status.scan = ""; log("room scan: done"); flash("✅ Room scanned"); }
}, 50);

setInterval(() => {   // keep the panorama fresh: re-take the patch in the direction Reachy holds still in
  if (!awake || scan.active || !roomSlots.size || status.follow !== "locked") return;
  const nowS = performance.now() / 1000;
  const recent = absHist.filter(([t]) => nowS - t < 0.6);
  if (recent.length < 5) return;
  const span = (k) => Math.max(...recent.map((r) => r[k])) - Math.min(...recent.map((r) => r[k]));
  if (span(2) > 1 || span(3) > 1) return;   // still moving
  const [, , pitch, yaw] = recent[recent.length - 1];
  let best = null, bestD = Infinity;
  for (const [key, sl] of roomSlots) {
    const d = Math.hypot(((yaw - sl.yaw + 540) % 360) - 180, pitch - sl.pitch);
    if (d < bestD) { bestD = d; best = key; }
  }
  if (bestD > 25) {   // a new direction: own slot (at most 24, then the oldest refresh slot goes)
    best = `r${Math.round(yaw)}_${Math.round(pitch)}`;
    const extra = [...roomSlots.entries()].filter(([k]) => k.startsWith("r")).sort((x, y) => x[1].t - y[1].t);
    if (roomSlots.size >= 24 && extra.length) roomSlots.delete(extra[0][0]);
  }
  if (depthPending > 2) return;   // laptop busy
  captureRoomPatch(best, nowS);
}, 2000);

let debugOn = false;                 // VR debug panel (More -> Debug)
let videoMode = "";                  // label of the Video button, set after the scene exists
const scene = createScene({
  video,
  vfovDeg: cfg.vfovDeg,
  cameraModel: cam,
  distM: cfg.distM,
  statusText,
  onHeadsetPose: (q, now) => {
    if (wantRecenter) { recenter.set(q); wantRecenter = false; notes.layout(); scene?.recenterView(); log("recentered"); }
    if (!awake || status.xr === "off" || !robot.connected || now - lastSend < 1000 / cfg.sendHz) return;
    const dt = (now - lastSend) / 1000;
    lastSend = now;
    const scanRaw = scanTarget();   // room scan: Reachy looks around on its own for ~20 s
    if (scanRaw) { send(mirror.step(scanRaw, dt), now / 1000); return; }
    const raw = headsetToRobot(recenter.toRelative(q));
    raw[2] += stepBase(dt, now / 1000);   // user's head rotation on top of the speaker direction
    raw[1] += basePitch;                  // ... and on top of the framing pitch
    send(mirror.step(raw, dt), now / 1000);
  },
  // Head-locked buttons in VR: point (controller ray / hand pinch) and select. Select elsewhere = recenter.
  windowMode: viewMode,
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
    { icon: "🗣", label: () => (captions?.voiceGender === "female" ? "Siri" : "Viktor"), more: true,
      onClick: () => captions.setVoiceGender(captions.voiceGender === "female" ? "male" : "female") },
    { icon: () => (viewMode === "world" ? "🌐" : "🛋"), label: () => (viewMode === "world" ? "World-locked" : "Comfort"),
      more: true, onClick: () => setViewMode(viewMode === "world" ? "comfort" : "world") },
    { icon: "🔄", label: () => (scan.active ? `Scan ${status.scan ?? ""}` : "Scan room"), more: true, onClick: startRoomScan },
    { icon: "🏠", label: () => (roomVisible ? "Room on" : "Room off"), more: true, active: () => roomVisible,
      onClick: () => { roomVisible = !roomVisible; scene.setRoomVisible(roomVisible); saveRoomSettings(); } },
    { icon: "⟳", label: "Recenter", more: true, onClick: () => { wantRecenter = true; } },
    { icon: "🎞", label: () => `Video: ${videoMode}`, more: true, onClick: () => { videoMode = scene.cycleVideo(); } },   // A/B the frame paths (videosource.js)
    { icon: "🐞", label: "Debug", more: true, active: () => debugOn, onClick: () => { debugOn = !debugOn; scene.toggleDebug(); } },
    { icon: "✕", label: "Exit VR", more: true, onClick: () => scene.exitVR() },
  ],
  // Without the caption server there is no speech detection, so Reachy cannot turn to whoever speaks.
  warning: () => (robot.connected && !scene.hasVideo && performance.now() - connectedAt > 10000
    ? "⚠ No camera image: the video connection hangs, reload the page"
    : status.captions === "on" ? ""
    : status.captions === "not allowed" ? "⚠ Caption server refused this Hugging Face account"
    : "⚠ No caption server: no captions, Reachy won't turn to speakers"),
  onFrame: () => { captions?.follow(); updateView(); },
  log,
  onSelect: () => { wantRecenter = true; },
  onEnd: () => { log("VR session ended"); status.xr = "off"; show("awake", "Reachy is awake. Tap Start to look around again, or Sleep."); },
});

// Speech bubbles over the speaker's head: faces in the camera image + mouth movement + mic direction.
const faceSpeakers = new FaceSpeakers({ hfovDeg: cam.hfovDeg, camera: cam });
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
    if (focus && Math.abs(90 - a * 180 / Math.PI) < cam.hfovDeg / 2 + 10) continue;
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
  if (msg.speaking && awake && performance.now() / 1000 - lastSpeechS > 1.5) log("speech start");   // for measuring the turn delay
  if (!msg.speaking || !awake) return;
  lastSpeechS = performance.now() / 1000;
  flushDoa(lastSpeechS - 0.6);
  const last = doaBuf[doaBuf.length - 1];
  const doaRel = last ? 90 - last[1] * 180 / Math.PI : null;   // relative to the head, + = left
  const tr = faceSpeakers.pick(doaRel);   // mouth movement + mic direction
  if (tr && tr.seen === faceSpeakers.lastT) {
    const prev = focusPid;
    focusPid = tr.pid;
    frameFocus();
    if (tr.pid !== prev) speaker.speakers.push([lastSpeechS, speaker.target]);   // the NEW speaker's direction ("I want to talk")
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
  // Only steer on captions about speech going on NOW: a final comes ~0.8 s + Whisper after the last word, its
  // translation even seconds later; steering on those pulled the head back to the previous speaker while
  // the next one already talked (code review 2026-10-08).
  const age = Date.now() / 1000 - (captions?.clockOffsetS ?? 0) - msg.t_end;   // s since the caption's speech ended
  if ((msg.final && msg.translation) || age > 1.2) return;
  lastSpeechS = performance.now() / 1000;
  if (!awake) return;
  // the caption confirms speech for its whole duration: use the mic directions from that time
  flushDoa(lastSpeechS - (msg.t_end - msg.t_start) - 0.7);
  if (!track) return;
  const prev = focusPid;
  focusPid = track.pid;
  frameFocus();
  if (track.pid !== prev) speaker.speakers.push([lastSpeechS, speaker.target]);   // the NEW speaker's direction ("I want to talk")
}

// Framing: Reachy keeps the person who spoke last (also while everyone is quiet) in the picture, head
// centred left/right and 1/3 from the top, like a camera operator. World direction of the face =
// measured head pose + its angle in the image, so the user's own headset rotation stays on top.
let focusPid = null, basePitch = 0, targetPitch = 0;
const frameUp = () => cam.upDeg(0.5, 1 / 3);   // head 1/3 from the top = this far above the axis (deg)
/** Robot head yaw when a camera frame was taken (t = capture time, s): frames lag the pose stream by ~0.1-0.2 s,
 *  and using the current yaw for an old frame overshoots while the robot turns. */
// Faces from the headset detector are stamped when the frame was grabbed from a lagging <video> (~0.12 s behind
// the pose stream); backend faces are stamped at capture: subtracting 0.12 s again turned a robot turning at
// 80 °/s into ~10° error (code review 2026-10-08). Set per source in onBackendFaces / onPeople.
let faceLatencyS = 0.12;
function headYawAt(t, latencyS = faceLatencyS) {
  const h = speaker.headHist;
  for (let i = h.length - 1; i >= 0; i--) if (h[i][0] <= t - latencyS) return h[i][1];
  return h.length ? h[0][1] : status.meas[2];
}

/** Same for the pitch: correcting an old frame's face position against the CURRENT pitch made the head
 *  overshoot and nod up and down (headset log 2026-10-08: target -26° -> -4° -> -26° -> +17° within 12 s). */
const pitchHist = [];   // [t, measured pitch]
function headPitchAt(t, latencyS = faceLatencyS) {
  for (let i = pitchHist.length - 1; i >= 0; i--) if (pitchHist[i][0] <= t - latencyS) return pitchHist[i][1];
  return pitchHist.length ? pitchHist[0][1] : status.meas[1];
}

const PITCH_UP = 35, PITCH_DOWN = 20;   // same as HeadMirror's limits in pose.js
const PITCH_DEAD = 6, PITCH_GAIN = 0.5, EDGE_SEARCH_MAX = 10;   // deg; half the error per face update
let focusLast = null;                   // {pid, top, bottom, t, yaw, pitch} of the focus face when last seen
let focusVel = 0;                       // its speed through the room, deg/s (smoothed)
function frameFocus() {
  if (!awake || focusPid == null) return;
  const tr = faceSpeakers.tracks.find((t) => t.pid === focusPid && t.seen === faceSpeakers.lastT);
  if (!tr) {
    // A face cut off at the image edge is no longer detected: if the focus person left at the top (or
    // bottom), keep tilting that way for a moment until their face is back in the picture.
    const t = faceSpeakers.lastT ?? 0;
    if (focusLast && t - focusLast.t < 2.5) {   // at most EDGE_SEARCH_MAX beyond where the face was last seen
      if (focusLast.top < 0.12) targetPitch = Math.max(-PITCH_UP, focusLast.pitch - EDGE_SEARCH_MAX, targetPitch - 1);
      else if (focusLast.bottom > 0.9) targetPitch = Math.min(PITCH_DOWN, focusLast.pitch + EDGE_SEARCH_MAX, targetPitch + 1);
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
  focusLast = { pid: focusPid, top: tr.top, bottom: tr.top + tr.h, t, yaw: yawNow, pitch: targetPitch };
  const moving = Math.abs(focusVel) > 8;
  // Dead zone around the framing point: a face that is already well placed does not move the head at all.
  if (moving || Math.abs(offX) > 4) {
    const lead = moving ? Math.max(-15, Math.min(15, focusVel * 0.3)) : 0;
    const yaw = Math.max(-150, Math.min(150, yawNow + lead));
    if (Math.abs(yaw - speaker.target) > 2) speaker.target = yaw;
  }
  const up = cam.upDeg(tr.cx, tr.cy);                             // face above the image centre (deg)
  const FRAME_UP = frameUp();
  if (Math.abs(up - FRAME_UP) > PITCH_DEAD) {
    const pitch = headPitchAt(t) - (up - FRAME_UP);                 // pitch + = look down
    targetPitch += PITCH_GAIN * (Math.max(-PITCH_UP, Math.min(PITCH_DOWN, pitch)) - targetPitch);
  }
  noteTarget(`face p${tr.pid} off ${offX.toFixed(0)}°`);
}
function onPeople(list, t) { faceSpeakers.focusPid = focusPid; faceSpeakers.update(list, t, headYawAt(t)); frameFocus(); }
// Faces come from the backend when it sends them (MediaPipe on the robot's computer, ~20/s, vision.py): on the
// headset the detector only managed ~3/s, too few to see who moves their mouth. The headset's own detector
// pauses while they arrive and takes over again 1.5 s after they stop.
let backendFacesAt = -1e9, backendFacesN = 0, backendFacesFps = 0;
setInterval(() => { backendFacesFps = backendFacesN; backendFacesN = 0; }, 1000);
onBackendFaces((msg) => {
  backendFacesAt = performance.now();
  backendFacesN++;
  if (!awake) return;
  // backend capture time -> this page's clock, via the backend clock from its hello (no NTP assumption)
  const age = Math.max(0, Date.now() / 1000 - (captions?.clockOffsetS ?? 0) - msg.t);
  faceLatencyS = 0;   // stamped at capture (see headYawAt)
  onPeople(msg.people, performance.now() / 1000 - age);
});
const backendFacesLive = () => performance.now() - backendFacesAt < 1500;
const faces = createFaces({
  getSource: () => (awake && !backendFacesLive() ? scene.videoFrame() : null),
  onFaces: (list, t) => { faceLatencyS = 0.12; onPeople(list, t); },
  log,
});
videoMode = scene.videoMode;
notes = createNotes({ three: scene.three, recenter, distM: cfg.distM, cardEl: $("notes") });
scene.addDraggable(notes.panel, () => notes.moved());   // grab the notes and put them where you like
captions = createCaptions({
  three: scene.three,
  distM: cfg.distM,
  vfovDeg: cfg.vfovDeg,
  cameraModel: cam,
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
  getToken: () => robot.token,
  log,
  onStatus: (s) => Object.assign(status, s),
});
$("captions-url").value = captionsUrl();
$("tunnel-keys").value = extraTunnelKeys();
$("tunnel-keys").onchange = (e) => { setExtraTunnelKeys(e.target.value); captions.reconnect(); };
$("captions-url").onchange = (e) => { setCaptionsUrl(e.target.value.trim()); captions.reconnect(); };
$("view-mode").value = viewMode;
captions.onDepth = onDepth;
scene.setRoomVisible(roomVisible);
$("scan-after-wake").checked = scanAfterWake;
$("scan-after-wake").onchange = (e) => { scanAfterWake = e.target.checked; saveRoomSettings(); };
$("room-visible").checked = roomVisible;
$("room-visible").onchange = (e) => { roomVisible = e.target.checked; scene.setRoomVisible(roomVisible); saveRoomSettings(); };
$("scan-now").onclick = startRoomScan;
$("camera-model").value = camChoice;
$("camera-model").onchange = (e) => {
  camChoice = e.target.value;
  try { localStorage.setItem(CAM_KEY, camChoice); } catch {}
  if (camChoice === "calibrated" && !cameraModels.calibrated) log("no camera.json yet: using the estimate");
  applyCamera();
};

// Calibration frames: full-resolution stills of exactly the stream the headset sees, downloaded as PNG for
// robot/scripts/calibrate_camera.py. Show the checkerboard (calib-board.html) on a tablet or a second screen.
let calibCount = 0, calibTimer = null;
function captureCalibFrame() {
  if (!video.videoWidth) { log("calibration: no video yet (wake Reachy up first)"); return; }
  const c = document.createElement("canvas");
  c.width = video.videoWidth; c.height = video.videoHeight;
  c.getContext("2d").drawImage(video, 0, 0);
  c.toBlob((blob) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `reachy-calib-${String(++calibCount).padStart(2, "0")}-${c.width}x${c.height}.png`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    $("calib-count").textContent = `${calibCount} frame${calibCount === 1 ? "" : "s"} (aim for 20+)`;
  }, "image/png");
}
$("calib-shot").onclick = captureCalibFrame;
$("calib-auto").onclick = () => {
  if (calibTimer) { clearInterval(calibTimer); calibTimer = null; }
  else calibTimer = setInterval(captureCalibFrame, 2000);
  $("calib-auto").textContent = `Auto-capture every 2 s: ${calibTimer ? "on" : "off"}`;
};
$("view-mode").onchange = (e) => setViewMode(e.target.value);
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
  if (scanAfterWake) setTimeout(startRoomScan, 800);   // video needs a moment after the wake-up motion
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
