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
import { captionsUrl, createCaptions, setCaptionsUrl } from "./captions.js";

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

function log(...a) {
  const line = `${(performance.now() / 1000).toFixed(1)} ${a.join(" ")}`;
  console.log(line);
  $("log").textContent = (line + "\n" + $("log").textContent).slice(0, 6000);
}

// ---------------------------------------------------------------- status
const status = { user: "-", robot: "-", motors: "-", ice: "-", video: "-", xr: "off", send: 0, cmd: [0, 0, 0], body: 0, meas: [0, 0, 0], captions: "-" };
let sentCount = 0;
setInterval(() => { status.send = sentCount; sentCount = 0; }, 1000);
function statusText() {
  const f = (v) => v.map((x) => x.toFixed(1).padStart(6)).join(" ");
  return [
    `robot ${status.robot}   motors ${status.motors}   ice ${status.ice}   video ${status.video}   send ${status.send} Hz`,
    `cmd  r/p/y ${f(status.cmd)}   body ${status.body.toFixed(1)}`,
    `meas r/p/y ${f(status.meas)}   captions ${status.captions}`,
  ].join("\n");
}
setInterval(() => { $("debug-text").textContent = `user ${status.user}   xr ${status.xr}\n` + statusText(); }, 200);

function show(state, message) {
  $("message").textContent = message;
  $("wake").hidden = state !== "asleep";
  $("start").hidden = state !== "awake";
  $("sleep").hidden = state !== "awake";
  $("talk").hidden = state !== "awake";
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
let last = { roll: 0, pitch: 0, yaw: 0, bodyYaw: 0 };   // last mirrored pose, the gesture is layered on it

/** Mirrored pose + "I want to talk" gesture -> robot. Body swing stays inside the head/body window. */
function send(t, nowS) {
  last = t;
  const g = talk.step(nowS);
  const bodyYaw = Math.max(t.yaw - 60, Math.min(t.yaw + 60, t.bodyYaw + g.bodyOffset));
  if (robot.setHead({ ...t, bodyYaw, antennas: g.antennas })) sentCount++;
  status.cmd = [t.roll, t.pitch, t.yaw];
  status.body = bodyYaw;
}

function wantToTalk() {
  if (!awake) return;
  talk.trigger(performance.now() / 1000);
  log("I want to talk");
}

// Outside VR nothing else sends targets: play the gesture from here (and send one rest pose after it).
let talkWasActive = false;
setInterval(() => {
  if (!awake || status.xr === "on" || !robot.connected) return;
  const nowS = performance.now() / 1000;
  const active = talk.active(nowS);
  if (active || talkWasActive) send(last, nowS);
  talkWasActive = active;
}, 1000 / 50);

const robot = createRobot({
  clientId: HF_CLIENT_ID,
  log,
  onStatus: (s) => Object.assign(status, s),
  onMeasuredHead: (roll, pitch, yaw) => {
    status.meas = [roll, pitch, yaw];
    scene.setRobotHead(recenter.toWorld(robotToHeadset(roll, pitch, yaw)));
  },
});

const scene = createScene({
  video,
  vfovDeg: cfg.vfovDeg,
  distM: cfg.distM,
  statusText,
  onHeadsetPose: (q, now) => {
    if (wantRecenter) { recenter.set(q); wantRecenter = false; captions.layout(); log("recentered"); }
    if (!awake || status.xr !== "on" || !robot.connected || now - lastSend < 1000 / cfg.sendHz) return;
    const dt = (now - lastSend) / 1000;
    lastSend = now;
    send(mirror.step(headsetToRobot(recenter.toRelative(q)), dt), now / 1000);
  },
  // Head-locked buttons in VR: point (controller ray / hand pinch) and select. Select elsewhere = recenter.
  vrButtons: [
    { label: "I want to talk", onClick: wantToTalk },
    { label: "Recenter", onClick: () => { wantRecenter = true; } },
    { label: "Exit VR", onClick: () => scene.exitVR() },
  ],
  onSelect: () => { wantRecenter = true; },
  onEnd: () => { status.xr = "off"; show("awake", "Reachy is awake. Tap Start to look around again, or Sleep."); },
});

const captions = createCaptions({
  three: scene.three,
  recenter,
  distM: cfg.distM,
  vfovDeg: cfg.vfovDeg,
  listEl: $("captions"),
  log,
  onStatus: (s) => Object.assign(status, s),
});
$("captions-url").value = captionsUrl();
$("captions-url").onchange = (e) => { setCaptionsUrl(e.target.value.trim()); captions.reconnect(); };

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
    entering.catch(() => {});
    show("awake", "VR is not available in this browser. Open this page on the headset.");
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
  // Inside the tap: browsers allow unmuted playback (robot microphone) only during a user gesture.
  robot.setAudio(true);
  video.play().catch((e) => log("video.play:", e?.message ?? e));
  show("busy", "Reachy is waking up…");
  await robot.wake();
  mirror = new HeadMirror({ smoothing: cfg.smoothing });   // start from neutral, where the wake-up motion ends
  last = { roll: 0, pitch: 0, yaw: 0, bodyYaw: 0 };
  lastSend = performance.now();
  awake = true;
  video.hidden = false;
  show("awake", "Reachy is awake: camera and microphone are on. Put on the headset, look straight ahead and tap Start.");
};
$("sleep").onclick = async () => {
  awake = false;
  robot.setAudio(false);
  video.hidden = true;
  show("busy", "Reachy is going to sleep…");
  await robot.sleep();
  show("asleep", "Reachy is asleep. Tap Wake up to start again.");
};
$("talk").onclick = wantToTalk;
$("signin").onclick = () => robot.signIn();
$("retry").onclick = connect;
$("recenter").onclick = () => { wantRecenter = true; };
$("signout").onclick = () => { robot.signOut(); location.reload(); };

show("busy", "Signing in…");
const signIn = await robot.signInState();
status.user = signIn;
if (signIn === "signed-in") await connect();
else if (signIn === "signed-out") show("signed-out", "Sign in with Hugging Face to reach your robot.");
