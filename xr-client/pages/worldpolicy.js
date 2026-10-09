// World view decisions (pure logic, no DOM, no three.js; tested in node): which camera frames may be painted into
// the panorama, and the optional "look around" that waits for real stillness at every stop.
// Angles in degrees, robot frame (yaw + = left, pitch + = down); times in seconds (the page's pose clock).

import { DEFAULT_STOPS } from "./roomscan.js";

/**
 * Robot world direction (x forward, y left, z up) -> camera frame (x right, y down, z forward) for a head pose
 * [roll, pitch, yaw] in degrees (pose.js: R = Rz(yaw) Ry(pitch) Rx(roll)). Row-major 3 x 3: rows = camera axes in the
 * world. Assumes the camera looks along the head's forward axis (no mount offset; see README for the measurement).
 */
export function worldToCamRows(roll, pitch, yaw) {
  const D = Math.PI / 180;
  const cr = Math.cos(roll * D), sr = Math.sin(roll * D), cp = Math.cos(pitch * D), sp = Math.sin(pitch * D);
  const cy = Math.cos(yaw * D), sy = Math.sin(yaw * D);
  const f = [cy * cp, sy * cp, -sp];                                     // head forward in the world
  const l = [cy * sp * sr - sy * cr, sy * sp * sr + cy * cr, cp * sr];    // head left
  const u = [cy * sp * cr + sy * sr, sy * sp * cr - cy * sr, cp * cr];    // head up
  return [-l[0], -l[1], -l[2], -u[0], -u[1], -u[2], f[0], f[1], f[2]];
}

const wrap = (a) => ((a + 540) % 360) - 180;
export const poseDistance = (a, b) => Math.hypot(wrap(a[2] - b[2]) * Math.cos(a[1] * Math.PI / 180), a[1] - b[1], a[0] - b[0]);

/**
 * When may the frame on screen be painted? Only a SHARP frame at a KNOWN pose: the head was still around the frame's
 * capture time (no motion blur, and an error in the lag estimate does not matter). With a measured lag the stillness
 * is checked around now - lag; without one, over the whole possible lag range (the robot must have held still for
 * longer than any lag could be).
 */
export class PaintPolicy {
  constructor({
    maxHz = 3,               // paints per second at most (GPU work only on accepted frames)
    stillDeg = 0.8,          // head moved less than this around the capture time = still (pose noise at rest: README)
    holdS = 0.08,            // stillness needed before the capture time (exposure + ~2 frame periods at 30 fps)
    afterS = 0.03,           // ... and after it
    lagMarginS = 0.04,       // uncertainty of the measured lag
    unknownLagS = 0.9,       // without a lag estimate: still for this long (longer than the largest lag searched)
    refreshS = 1.0,          // same view again only after this long (it refreshes moving people)
    sameDeg = 3,
  } = {}) {
    Object.assign(this, { maxHz, stillDeg, holdS, afterS, lagMarginS, unknownLagS, refreshS, sameDeg });
    this.lastPaint = -Infinity; this.lastPose = null; this.lastSeq = null; this.reason = "";
  }

  /**
   * tFrame: when the frame on screen arrived (pose clock); lagS: measured lag or null; seq: frame counter.
   * Returns {pose, tc, still} for a frame worth painting, else null (this.reason says why).
   */
  decide(tFrame, poses, lagS, seq) {
    if (seq === this.lastSeq) { this.reason = "same frame"; return null; }
    if (tFrame - this.lastPaint < 1 / this.maxHz) { this.reason = "rate"; return null; }
    if (poses.length < 2 || poses.last < tFrame - 0.5) { this.reason = "no poses"; return null; }
    let t0, t1, tc;
    if (lagS == null) { tc = tFrame; t0 = tFrame - this.unknownLagS; t1 = tFrame; }
    else {
      tc = tFrame - lagS;
      t0 = tc - this.holdS - this.lagMarginS; t1 = Math.min(poses.last, tc + this.afterS + this.lagMarginS);
    }
    const span = poses.span(t0, t1);
    if (!(span < this.stillDeg)) { this.reason = `moving ${span.toFixed(1)}°`; return null; }
    const pose = poses.at(tc);
    if (this.lastPose && poseDistance(pose, this.lastPose) < this.sameDeg && tFrame - this.lastPaint < this.refreshS) {
      this.reason = "same view"; return null;
    }
    this.reason = "ok";
    return { pose, tc, still: 1 - 0.3 * span / this.stillDeg };
  }

  /** The decided frame was painted. */
  painted(tFrame, pose, seq) { this.lastPaint = tFrame; this.lastPose = pose.slice(); this.lastSeq = seq; }
}

/**
 * Quick look around: Reachy looks at each stop (roomscan.js stops: 6 directions x 2 rows) and moves on as soon as
 * a frame from there was painted (i.e. the head really was still at the frame's capture time), instead of a fixed
 * timeout. The app pauses the head mirroring and the speaker following meanwhile (scanTarget path).
 */
export class LookAround {
  constructor({ stops = DEFAULT_STOPS, nearDeg = 10, timeoutS = 6 } = {}) {
    Object.assign(this, { stops, nearDeg, timeoutS });
    this.active = false; this.i = 0; this.log = [];
  }

  start(t) { this.active = true; this.i = 0; this.since = t; this.log = []; }
  cancel() { this.active = false; }

  /** [roll, pitch, yaw] to command, or null. */
  get target() { const s = this.active ? this.stops[this.i] : null; return s ? [0, s.pitch, s.yaw] : null; }
  get progress() { return `${Math.min(this.i + 1, this.stops.length)}/${this.stops.length}`; }

  next(t, how) {
    this.log.push({ key: this.stops[this.i].key, s: +(t - this.since).toFixed(2), how });
    this.i++; this.since = t;
    if (this.i >= this.stops.length) { this.active = false; return "done"; }
    return "next";
  }

  /** A frame taken at pose was painted. Returns "next" / "done" when that completed the current stop, else null. */
  onPaint(t, pose) {
    const tg = this.target;
    if (!tg || poseDistance(pose, tg) > this.nearDeg) return null;
    return this.next(t, "painted");
  }

  /** Call regularly: gives up on a stop after timeoutS (e.g. out of reach). */
  step(t) {
    if (!this.active || t - this.since < this.timeoutS) return null;
    return this.next(t, "timeout");
  }
}
