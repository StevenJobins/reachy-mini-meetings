// node xr-client/tests/worldpolicy.test.mjs
// worldToCamRows must agree with how scene.js places the live video sphere (robotToHeadset + camera frame -> three.js),
// and the paint policy / look-around must behave.
import { worldToCamRows, PaintPolicy, LookAround, poseDistance } from "../pages/worldpolicy.js";
import { robotToHeadset } from "../pages/pose.js";
import { PoseHistory } from "../pages/videolag.js";
import { CameraModel } from "../pages/camera.js";
import { readFileSync } from "node:fs";

let fails = 0;
const check = (name, ok, msg) => { console.log(`${ok ? "ok  " : "FAIL"} ${name}: ${msg}`); if (!ok) fails++; };

function rotate(q, v) {   // quaternion {x,y,z,w} * vector
  const { x, y, z, w } = q;
  const tx = 2 * (y * v[2] - z * v[1]), ty = 2 * (z * v[0] - x * v[2]), tz = 2 * (x * v[1] - y * v[0]);
  return [v[0] + w * tx + (y * tz - z * ty), v[1] + w * ty + (z * tx - x * tz), v[2] + w * tz + (x * ty - y * tx)];
}

// ---- 1. convention: a camera ray placed like scene.js's screenGeometry, mapped back, must give the same ray
{
  const cam = new CameraModel(JSON.parse(readFileSync(new URL("../pages/camera.json", import.meta.url))));
  let worst = 0;
  for (const [r, p, y] of [[0, 0, 0], [0, 20, 0], [0, 0, 45], [10, -15, 120], [-12, 18, -160], [5, 30, 70]]) {
    for (const [u, v] of [[0.5, 0.5], [0, 0], [1, 0.3], [0.2, 1], [0.8, 0.9]]) {
      const d = cam.unproject(u, v);
      const local = [d[0], -d[1], -d[2]];                         // scene.js: camera frame -> three.js local
      const xr = rotate(robotToHeadset(r, p, y), local);          // robotView orientation (room frame = identity)
      const world = [-xr[2], -xr[0], xr[1]];                      // three.js -> robot world (worldview.js display)
      const M = worldToCamRows(r, p, y);
      const c = [0, 1, 2].map((i) => M[3 * i] * world[0] + M[3 * i + 1] * world[1] + M[3 * i + 2] * world[2]);
      const back = cam.project(c);
      worst = Math.max(worst, Math.hypot((back[0] - u) * 1920, (back[1] - v) * 1080));
    }
  }
  check("rotation convention", worst < 1e-6, `worst pixel error ${worst.toExponential(1)} px over 30 rays x poses`);
  // sanity: yaw + = left -> the optical axis points to +y (left) in the world
  const M = worldToCamRows(0, 0, 90);
  check("yaw left", Math.abs(M[7] - 1) < 1e-9, `camera z axis at yaw 90 = [${M.slice(6).map((x) => x.toFixed(2))}]`);
}

// ---- 2. paint policy
{
  const poses = new PoseHistory(30);
  // still at yaw 0 until 2 s, turn to 40 by 3 s, still afterwards
  for (let t = 0; t < 6; t += 0.02) poses.push(t, 0, 0, t < 2 ? 0 : t < 3 ? 40 * (t - 2) : 40);
  const pol = new PaintPolicy();
  const at = (t, lag, seq) => pol.decide(t, poses, lag, seq);
  const r1 = at(1.0, 0.2, 1);
  check("still -> paint", !!r1 && Math.abs(r1.pose[2]) < 1e-9, `decision ${JSON.stringify(r1)}`);
  pol.painted(1.0, r1.pose, 1);
  check("rate limit", at(1.1, 0.2, 2) === null && pol.reason === "rate", pol.reason);
  check("same view", at(1.5, 0.2, 3) === null && pol.reason === "same view", pol.reason);
  check("moving -> no paint", at(2.5, 0.2, 4) === null && pol.reason.startsWith("moving"), pol.reason);
  // at t = 3.1 with lag 0.2 the frame shows t = 2.9: still turning -> no; at 3.35 it shows 3.15: still -> yes
  check("lag respected (frame from the turn)", at(3.1, 0.2, 5) === null, pol.reason);
  const r2 = at(3.35, 0.2, 6);
  check("lag respected (settled frame)", !!r2 && Math.abs(r2.pose[2] - 40) < 1e-6, `${pol.reason} ${r2?.pose}`);
  // unknown lag: needs 0.9 s of stillness
  const pol2 = new PaintPolicy();
  check("unknown lag waits", pol2.decide(3.5, poses, null, 1) === null, pol2.reason);
  check("unknown lag then paints", !!pol2.decide(3.95, poses, null, 2), pol2.reason);
}

// ---- 3. look around
{
  const la = new LookAround({ timeoutS: 6 });
  la.start(0);
  const tg = la.target;
  check("look around target", tg[1] === la.stops[0].pitch && tg[2] === la.stops[0].yaw, JSON.stringify(tg));
  check("far paint ignored", la.onPaint(1, [0, 0, -100]) === null, "");
  check("near paint advances", la.onPaint(1.5, [0, tg[1] + 4, tg[2] - 5]) === "next" && la.i === 1, `stop ${la.i}`);
  check("timeout advances", la.step(7.6) === "next" && la.log[1].how === "timeout", JSON.stringify(la.log));
  check("pose distance wraps", Math.abs(poseDistance([0, 0, 179], [0, 0, -179]) - 2) < 1e-9, "");
}

process.exit(fails ? 1 : 0);
