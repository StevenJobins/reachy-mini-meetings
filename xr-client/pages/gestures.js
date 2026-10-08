// Gestures layered on top of head mirroring. Pure maths: no three.js, no DOM (portable to C#/Kotlin).
// Angles in degrees, time in seconds.

/**
 * "I want to talk": the right antenna waves (fast, a bit irregular), the left one stays still, and
 * the body swings ±27° around where it is, faded in and out. Same values as robot/turn_to_speaker.py.
 * The head keeps following the user, so the remote person keeps looking where they look.
 */
export class WantToTalk {
  constructor({ durationS = 3, antennaDeg = 32, antennaHz = 4.5, bodyDeg = 27, bodyHz = 1, fadeS = 0.3 } = {}) {
    Object.assign(this, { durationS, antennaDeg, antennaHz, bodyDeg, bodyHz, fadeS });
    this.start = -Infinity;
    this.until = -Infinity;
  }

  /** Start, or extend while already waving (like pressing the button again). */
  trigger(t) {
    if (t > this.until) this.start = t;
    this.until = t + this.durationS;
  }

  active(t) { return t <= this.until; }

  /** -> {antennas: [right, left], bodyOffset} in degrees. Zero when idle. */
  step(t) {
    if (!this.active(t)) return { antennas: [0, 0], bodyOffset: 0 };
    const s = t - this.start;
    const env = Math.max(0, Math.min(1, s / this.fadeS, (this.until - t) / this.fadeS));
    const right = this.antennaDeg * Math.sin(2 * Math.PI * this.antennaHz * t) + (Math.random() * 12 - 6);
    return { antennas: [right, 0], bodyOffset: env * this.bodyDeg * Math.sin(2 * Math.PI * this.bodyHz * s) };
  }
}

/**
 * Laugh: the antennas wiggle against each other (fast, a bit irregular, like antennas_happy in robot/),
 * and the head chuckles: a quick small up-down bounce, tilted back a little. Faded in and out.
 * Triggering again while laughing extends it. Layered on the mirrored pose, like WantToTalk.
 */
export class Laugh {
  constructor({ durationS = 2.2, antennaDeg = 28, antennaHz = 5, bobDeg = 4, bobHz = 4, backDeg = 6, fadeS = 0.25 } = {}) {
    Object.assign(this, { durationS, antennaDeg, antennaHz, bobDeg, bobHz, backDeg, fadeS });
    this.start = -Infinity;
    this.until = -Infinity;
  }

  trigger(t) {
    if (t > this.until) this.start = t;
    this.until = t + this.durationS;
  }

  active(t) { return t <= this.until; }

  /** -> {antennas: [right, left], pitch} in degrees (pitch + = down). Zero when idle. */
  step(t) {
    if (!this.active(t)) return { antennas: [0, 0], pitch: 0 };
    const s = t - this.start;
    const env = Math.max(0, Math.min(1, s / this.fadeS, (this.until - t) / this.fadeS));
    const w = env * (this.antennaDeg * Math.sin(2 * Math.PI * this.antennaHz * s) + (Math.random() * 8 - 4));
    const pitch = env * (-this.backDeg + this.bobDeg * Math.sin(2 * Math.PI * this.bobHz * s));
    return { antennas: [w, -w], pitch };
  }
}
