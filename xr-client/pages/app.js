// Wires robot connection, pose logic and rendering together, plus the one-button UI.
//
// Flow: open page -> silent HF sign-in -> auto-connect to the robot -> tap Start -> VR.
// Only "Start" needs a tap: browsers allow entering VR only from a user gesture.

import { createRobot } from "./robot.js";
import { createScene } from "./scene.js";
import { HeadMirror, Recenter, headsetToRobot, robotToHeadset } from "./pose.js";

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
const status = { user: "-", robot: "-", ice: "-", video: "-", xr: "off", send: 0, cmd: [0, 0, 0], body: 0, meas: [0, 0, 0] };
let sentCount = 0;
setInterval(() => { status.send = sentCount; sentCount = 0; }, 1000);
function statusText() {
  const f = (v) => v.map((x) => x.toFixed(1).padStart(6)).join(" ");
  return [
    `robot ${status.robot}   ice ${status.ice}   video ${status.video}   send ${status.send} Hz`,
    `cmd  r/p/y ${f(status.cmd)}   body ${status.body.toFixed(1)}`,
    `meas r/p/y ${f(status.meas)}`,
  ].join("\n");
}
setInterval(() => { $("debug-text").textContent = `user ${status.user}   xr ${status.xr}\n` + statusText(); }, 200);

function show(state, message) {
  $("message").textContent = message;
  $("start").hidden = state !== "ready";
  $("signin").hidden = state !== "signed-out";
  $("retry").hidden = state !== "failed";
}

// ---------------------------------------------------------------- wiring
const recenter = new Recenter();
const mirror = new HeadMirror({ smoothing: cfg.smoothing });
let wantRecenter = true;
let lastSend = 0;

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
    if (wantRecenter) { recenter.set(q); wantRecenter = false; log("recentered"); }
    if (!robot.connected || now - lastSend < 1000 / cfg.sendHz) return;
    lastSend = now;
    const t = mirror.step(headsetToRobot(recenter.toRelative(q)));
    if (robot.setHead(t)) sentCount++;
    status.cmd = [t.roll, t.pitch, t.yaw];
    status.body = t.bodyYaw;
  },
  onSelect: () => { wantRecenter = true; },
  onEnd: () => { status.xr = "off"; show("ready", "Connected. Tap Start to look around again."); },
});

video.onplaying = () => { status.video = `${video.videoWidth}x${video.videoHeight}`; };
robot.attachVideo(video);

// ---------------------------------------------------------------- UI flow
async function connect() {
  show("busy", "Connecting to Reachy Mini…");
  try {
    const res = await robot.connect();
    show("ready", `Connected to ${res.robotName ?? "Reachy Mini"}. Put on the headset, look straight ahead and tap Start.`);
  } catch (e) {
    log("connect failed:", e?.message ?? e);
    show("failed", `Could not connect: ${e?.message ?? e}. Is the robot on and its daemon signed in to Hugging Face?`);
  }
}

$("start").onclick = async () => {
  if (!(await scene.xrSupported())) { show("ready", "VR is not available in this browser. Open this page on the headset."); return; }
  try {
    await scene.enterVR();
    wantRecenter = true;
    status.xr = "on";
    video.play().catch(() => {});
  } catch (e) {
    log("enter VR failed:", e?.message ?? e);
  }
};
$("signin").onclick = () => robot.signIn();
$("retry").onclick = connect;
$("recenter").onclick = () => { wantRecenter = true; };
$("signout").onclick = () => { robot.signOut(); location.reload(); };

show("busy", "Signing in…");
const signIn = await robot.signInState();
status.user = signIn;
if (signIn === "signed-in") await connect();
else if (signIn === "signed-out") show("signed-out", "Sign in with Hugging Face to reach your robot.");
