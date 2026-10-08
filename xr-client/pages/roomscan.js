// Room scan after wake-up: where Reachy looks, when the head has settled, when to take the picture.
// Pure logic: no three.js, no DOM (portable). Angles in degrees, robot frame (yaw + = left, pitch + = down).
//
// 12 stops: 6 directions around the robot (60° apart; with the calibrated ~89° wide camera neighbouring frames
// overlap by ~30°) in 2 rows (18° up, 15° down; ~58° tall frames cover ~47° up to ~44° down). Snake order,
// so every move is ~60° and the body never has to swing across the back:
//   up:   30, 90, 150 | down: 150, 90, 30, -30, -90, -150 | up: -150, -90, -30
// Reachable: head yaw ±180 in the world, body follows beyond 65° (HeadMirror), meeting pitch limits 35 up / 20 down.

const UP = -18, DOWN = 15;

export const DEFAULT_STOPS = [
  [UP, 30], [UP, 90], [UP, 150],
  [DOWN, 150], [DOWN, 90], [DOWN, 30], [DOWN, -30], [DOWN, -90], [DOWN, -150],
  [UP, -150], [UP, -90], [UP, -30],
].map(([pitch, yaw]) => ({ pitch, yaw, key: `${pitch > 0 ? "d" : "u"}${yaw}` }));

const wrap = (a) => ((a + 540) % 360) - 180;

export class RoomScan {
  constructor({ stops = DEFAULT_STOPS, settleDeg = 3, settleS = 0.4, timeoutS = 5 } = {}) {
    Object.assign(this, { stops, settleDeg, settleS, timeoutS });
    this.active = false;
    this.i = 0;
  }

  get target() { return this.active ? this.stops[this.i] : null; }
  get progress() { return { done: this.i, total: this.stops.length }; }

  start(t) { this.active = true; this.i = 0; this.moveSince = t; this.steadySince = null; }
  cancel() { this.active = false; }

  /**
   * Call often (e.g. 20 Hz) with the measured head pose [roll, pitch, yaw]. Returns "shoot" once the head has
   * been within settleDeg of the target for settleS + extraDelayS (the video lags the pose stream, so the frame
   * must show the settled view), or after timeoutS anyway ("shoot-late"). Else null.
   */
  step(t, meas, extraDelayS = 0) {
    const tg = this.target;
    if (!tg) return null;
    const near = Math.abs(wrap(meas[2] - tg.yaw)) < this.settleDeg && Math.abs(meas[1] - tg.pitch) < this.settleDeg;
    if (!near) this.steadySince = null;
    else this.steadySince ??= t;
    if (this.steadySince != null && t - this.steadySince >= this.settleS + extraDelayS) return "shoot";
    if (t - this.moveSince > this.timeoutS) return "shoot-late";
    return null;
  }

  /** The picture of the current stop was taken: next stop. Returns true when the scan is complete. */
  shot(t) {
    this.i++;
    this.moveSince = t; this.steadySince = null;
    if (this.i >= this.stops.length) { this.active = false; return true; }
    return false;
  }
}
