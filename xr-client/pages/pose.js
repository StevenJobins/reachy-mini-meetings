// Headset pose <-> robot head pose. Pure maths: no three.js, no DOM (portable to C#/Kotlin).
//
// Frames (see xr-client/README.md):
//   WebXR: x right, y up, z back (-z = forward).   Robot: x forward, y left, z up.
//   Robot (x, y, z) = (-xr.z, -xr.x, xr.y)
// Angles: degrees, Euler ZYX as in the SDK (R = Rz(yaw) Ry(pitch) Rx(roll)):
//   yaw + = look left, pitch + = look down, roll + = tilt right.
// Quaternions: {x, y, z, w}, unit length.

export const DEG = 180 / Math.PI;

export function qmul(a, b) {
  return {
    w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
    x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
    y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
    z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
  };
}

export const qinv = (q) => ({ x: -q.x, y: -q.y, z: -q.z, w: q.w });

function qaxis(ax, ay, az, rad) {
  const s = Math.sin(rad / 2);
  return { x: ax * s, y: ay * s, z: az * s, w: Math.cos(rad / 2) };
}

/** Headset rotation (XR frame, relative to "straight ahead") -> robot head [roll, pitch, yaw] in degrees. */
export function headsetToRobot(q) {
  const w = q.w, x = -q.z, y = -q.x, z = q.y;
  const r00 = 1 - 2 * (y * y + z * z), r10 = 2 * (x * y + w * z);
  const r20 = 2 * (x * z - w * y), r21 = 2 * (y * z + w * x), r22 = 1 - 2 * (x * x + y * y);
  return [
    Math.atan2(r21, r22) * DEG,
    Math.asin(Math.max(-1, Math.min(1, -r20))) * DEG,
    Math.atan2(r10, r00) * DEG,
  ];
}

/** Inverse of headsetToRobot: robot head roll/pitch/yaw (degrees) -> rotation in the XR frame. */
export function robotToHeadset(rollDeg, pitchDeg, yawDeg) {
  const q = qmul(qmul(qaxis(0, 0, 1, yawDeg / DEG), qaxis(0, 1, 0, pitchDeg / DEG)), qaxis(1, 0, 0, rollDeg / DEG));
  return { x: -q.y, y: q.z, z: -q.x, w: q.w };
}

/** "Straight ahead" of the user. Everything the robot does is relative to this. */
export class Recenter {
  constructor() { this.q0 = { x: 0, y: 0, z: 0, w: 1 }; this.q0inv = this.q0; }
  // Explicit copy: WebXR orientations are DOMPointReadOnly, whose x/y/z/w are getters ({...q} copies nothing).
  set(q) { this.q0 = { x: q.x, y: q.y, z: q.z, w: q.w }; this.q0inv = qinv(this.q0); }
  toRelative(qWorld) { return qmul(this.q0inv, qWorld); }
  toWorld(qRel) { return qmul(this.q0, qRel); }
}

/**
 * Same pipeline as robot/src/reachy_meetings_robot (head_mirror + safety):
 * exponential smoothing -> clamp -> body follows when head yaw leaves the head/body window.
 */
export class HeadMirror {
  constructor({ smoothing = 0.35, limits = { roll: 40, pitch: 40, yaw: 180, body: 160, headBody: 65 } } = {}) {
    this.smoothing = smoothing;
    this.lim = limits;
    this.filt = [0, 0, 0];
    this.bodyYaw = 0;
  }

  /** raw = [roll, pitch, yaw] degrees -> {roll, pitch, yaw, bodyYaw} degrees, safe to send. */
  step(raw) {
    const a = this.smoothing, L = this.lim;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    for (let i = 0; i < 3; i++) this.filt[i] = a * this.filt[i] + (1 - a) * raw[i];
    const roll = clamp(this.filt[0], -L.roll, L.roll);
    const pitch = clamp(this.filt[1], -L.pitch, L.pitch);
    let yaw = clamp(this.filt[2], -L.yaw, L.yaw);
    if (yaw > this.bodyYaw + L.headBody) this.bodyYaw = yaw - L.headBody;
    if (yaw < this.bodyYaw - L.headBody) this.bodyYaw = yaw + L.headBody;
    this.bodyYaw = clamp(this.bodyYaw, -L.body, L.body);
    yaw = clamp(yaw, this.bodyYaw - L.headBody, this.bodyYaw + L.headBody);
    return { roll, pitch, yaw, bodyYaw: this.bodyYaw };
  }
}
