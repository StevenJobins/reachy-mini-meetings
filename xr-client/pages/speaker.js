// Speaker tracking from the robot's microphone array (DoA). Pure maths: no three.js, no DOM.
// Ported from robot/turn_to_speaker.py. Angles in degrees, robot frame (yaw + = left), time in seconds.
//
// The result is a slowly moving BASE yaw: "where the current speaker is". The headset adds the user's
// head rotation on top, so looking straight ahead in VR = looking at the speaker.

import { RateLimiter } from "./pose.js";

export class SpeakerTracker {
  constructor({
    deadbandDeg = 10,      // ignore direction changes smaller than this
    latencyS = 0.15,       // DoA refers to the head yaw this long ago
    maxYawDeg = 150,       // world yaw limit for the speaker direction (body limit ±160)
    confirmN = 3,          // a new direction needs this many speech readings ...
    confirmWindowS = 0.6,  // ... within this time ...
    confirmSpreadDeg = 12, // ... within ± this of their median (outliers are ignored, not blocking)
    memoryS = 60,          // speakers of the last minute, for center() ("I want to talk")
    binDeg = 20,           // center(): each 20° sector counts once
    maxVel = 80,           // deg/s: smooth, not hectic (40 felt too slow in the test)
    maxAcc = 80,           // deg/s²
  } = {}) {
    Object.assign(this, { deadbandDeg, latencyS, maxYawDeg, confirmN, confirmWindowS, confirmSpreadDeg, memoryS, binDeg });
    this.headHist = [];   // [t, measured world head yaw]
    this.recent = [];     // [t, world yaw] speech readings, short window
    this.speakers = [];   // [t, world yaw] confirmed speaker directions, memoryS
    this.target = 0;
    this.override = null; // fixed target (e.g. speakers' center while waving), or null
    this.limiter = new RateLimiter(maxVel, maxAcc);
  }

  get base() { return this.limiter.pos; }

  /** Measured head yaw of the robot (world frame), from the robot state stream. */
  pushHeadYaw(t, yaw) {
    this.headHist.push([t, yaw]);
    while (this.headHist.length > 100) this.headHist.shift();
  }

  /** DoA reading: angleRad in the SDK convention (0 = left, π/2 = front, π = right). */
  pushDoa(t, angleRad, speech) {
    if (!speech) return;
    const rel = 90 - angleRad * 180 / Math.PI;   // relative to the head, + = left
    let y0 = this.headHist.length ? this.headHist[this.headHist.length - 1][1] : this.base;
    for (let i = this.headHist.length - 1; i >= 0; i--) if (this.headHist[i][0] <= t - this.latencyS) { y0 = this.headHist[i][1]; break; }
    const world = Math.max(-this.maxYawDeg, Math.min(this.maxYawDeg, y0 + rel));
    this.recent.push([t, world]);
    this.recent = this.recent.filter(([ts]) => t - ts <= this.confirmWindowS);
    while (this.speakers.length && t - this.speakers[0][0] > this.memoryS) this.speakers.shift();

    // Cluster around the median: enough readings agree -> that is the speaker. Single wild readings
    // (noise, reflections, the robot's own motors) neither move the target nor block it.
    const ys = this.recent.map(([, y]) => y).sort((a, b) => a - b);
    const med = ys[Math.floor(ys.length / 2)];
    const near = ys.filter((y) => Math.abs(y - med) <= this.confirmSpreadDeg);
    if (near.length < this.confirmN) return;
    const mean = near.reduce((a, b) => a + b, 0) / near.length;
    this.speakers.push([t, mean]);
    if (Math.abs(mean - this.target) > this.deadbandDeg) this.target = mean;
  }

  /** Center of all speakers of the last minute, each sector counted once (as in turn_to_speaker.py). */
  center(t) {
    const recent = this.speakers.filter(([ts]) => t - ts < this.memoryS).map(([, y]) => y);
    if (!recent.length) return this.target;
    const bins = new Map();
    for (const y of recent) {
      const k = Math.round(y / this.binDeg);
      bins.set(k, [...(bins.get(k) ?? []), y]);
    }
    const means = [...bins.values()].map((v) => v.reduce((a, b) => a + b, 0) / v.length);
    return means.reduce((a, b) => a + b, 0) / means.length;
  }

  /** Advance the smooth base yaw. */
  step(dt) {
    return this.limiter.step(this.override ?? this.target, Math.max(0.001, Math.min(0.1, dt)));
  }

  reset() { this.target = 0; this.override = null; this.limiter.pos = 0; this.limiter.vel = 0; this.recent = []; }
}
