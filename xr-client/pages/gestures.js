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
 * Laugh, in three phases like a real laugh:
 *   1. burst (0-0.22 s): head tilts back, both antennas spring up together,
 *   2. "ha-ha" (1.8 s): the head bounces down on every "ha" (4.5 per second, getting weaker), the antennas
 *      bounce in the same beat (left a little later, so it looks alive), and the head sways a little,
 *   3. settle (0.7 s): a small nod forward, the antennas sink briefly below rest (a sigh), then rest.
 * Intensity 0.6 (chuckle) .. 1.4 (big laugh). Triggering again while laughing makes it stronger (+0.2) and
 * longer. Same sign on both antennas = both move the same way (as attention/antennas_sad in robot/).
 * Layered on the mirrored pose; the caller clamps to the meeting limits.
 */
const smooth = (a, b, x) => { const u = Math.max(0, Math.min(1, (x - a) / (b - a))); return u * u * (3 - 2 * u); };
const bump = (x) => Math.sin(Math.PI * Math.max(0, Math.min(1, x)));   // 0 -> 1 -> 0

export class Laugh {
  constructor({ burstS = 0.22, pulsesS = 1.8, settleS = 0.7, haHz = 4.5, decayS = 1.1, maxIntensity = 1.4 } = {}) {
    Object.assign(this, { burstS, pulsesS, settleS, haHz, decayS, maxIntensity });
    this.start = -Infinity; this.pulseEnd = -Infinity; this.until = -Infinity; this.lastTrig = -Infinity; this.k = 1;
  }

  /** intensity: from the native face tracking later; the button leaves it out (1.0, +0.2 per extra press). */
  trigger(t, intensity) {
    if (t > this.pulseEnd) { this.start = t; this.k = intensity ?? 1; }          // new laugh
    else this.k = Math.min(this.maxIntensity, intensity ?? this.k + 0.2);       // laughing harder
    this.lastTrig = t;
    this.pulseEnd = Math.max(t, this.start + this.burstS) + this.pulsesS;
    this.until = this.pulseEnd + this.settleS;
  }

  active(t) { return t <= this.until; }

  /** -> {antennas: [right, left], pitch, roll} in degrees (pitch + = down). Zero when idle. */
  step(t) {
    if (!this.active(t)) return { antennas: [0, 0], pitch: 0, roll: 0 };
    const { k } = this, s = t - this.start;
    const burst = smooth(0, this.burstS, s);
    const settle = smooth(this.pulseEnd, this.until, t);
    const inPulses = s > this.burstS && t < this.pulseEnd;
    const A = inPulses ? k * Math.exp(-(t - Math.max(this.lastTrig, this.start + this.burstS)) / this.decayS) : 0;
    const ha = Math.abs(Math.sin(Math.PI * this.haHz * (s - this.burstS)));
    const haLeft = Math.abs(Math.sin(Math.PI * this.haHz * (s - this.burstS - 0.08)));
    const after = bump((t - this.pulseEnd) / this.settleS);   // the settle phase, 0 -> 1 -> 0
    const keep = 1 - settle;
    const pitch = keep * (-10 * k * burst + 5 * A * ha) + 4 * after;
    const roll = keep * burst * 5 * k * Math.sin(2 * Math.PI * 0.8 * s);
    const up = keep * burst * 30 * k, droop = -10 * after;
    return { antennas: [up + 16 * A * ha + droop, up + 16 * A * haLeft + droop], pitch, roll };
  }
}
