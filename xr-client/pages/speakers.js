// Who is speaking: person tracks in the robot camera image + mouth movement + microphone direction (DoA).
// Pure logic: no DOM, no three.js (portable to C#/Kotlin).
//
// Faces come in normalized image coordinates (0..1, origin top left) from faces.js, several times a second.
// Angles: degrees, + = left of the camera axis, like doa_deg from backend/ (see backend/README.md).
//
// Identity: a track only lives while the face is in view. Who it IS (`pid`, "Speaker 1") is decided by the
// direction in the room (robot head yaw + face angle): people sit still in a meeting, so a face that shows up
// again where someone was before is that person again, also after the robot looked elsewhere.

export class FaceSpeakers {
  constructor({ hfovDeg, windowS = 1.5, maxJump = 0.25, ttlS = 1.0, reIdDeg = 25, lostS = 4, focusS = 60, focusDeg = 60, memoryS = 900 } = {}) {
    this.tanHalf = Math.tan((hfovDeg / 2) * Math.PI / 180);
    this.windowS = windowS;   // mouth movement is judged over this window
    this.maxJump = maxJump;   // max face movement between two detections (fraction of the image)
    this.ttlS = ttlS;         // a face not seen for this long is dropped
    this.tracks = [];
    this.nextId = 1;
    this.reIdDeg = reIdDeg;   // same person if within this many degrees of where they were
    this.memoryS = memoryS;   // forget people not seen for this long
    this.lostS = lostS;       // someone who walked off: a lone new face within this time is still them
    this.focusS = focusS;     // the followed person is recognised again this long after leaving the picture ...
    this.focusDeg = focusDeg; // ... within this many degrees of where they were (Reachy looked away and back)
    this.people = [];         // [{pid, yaw (room direction, degrees), seen}]
    this.nextPid = 1;
    this.focusPid = null;     // set by the app: the person Reachy follows; kept when they move around
  }

  /**
   * faces = [{cx, cy, top, w, h, mouth}] (head boxes, mouth may be null), t = seconds, headYawDeg = robot head yaw
   * in the room when the frame was taken (null if unknown). Nearest-centre matching, then identity by direction.
   */
  update(faces, t, headYawDeg = null) {
    this.lastT = t;   // time of the latest frame: tracks with seen === lastT are in view now
    const free = new Set(this.tracks);
    for (const f of faces) {
      let best = null, bestD = this.maxJump;
      for (const tr of free) {
        const d = Math.hypot(tr.cx - f.cx, tr.cy - f.cy);
        if (d < bestD) { bestD = d; best = tr; }
      }
      if (best) free.delete(best);
      else { best = { id: this.nextId++, mouth: [] }; this.tracks.push(best); }
      // smooth the box: detections jitter by a few percent from frame to frame
      const k = best.seen == null ? 1 : 0.5;
      for (const key of ["cx", "cy", "top", "w", "h"]) best[key] = best[key] == null ? f[key] : best[key] + k * (f[key] - best[key]);
      best.seen = t;
      if (f.mouth != null) best.mouth.push([t, f.mouth]);
      while (best.mouth.length && best.mouth[0][0] < t - this.windowS) best.mouth.shift();
    }
    this.tracks = this.tracks.filter((tr) => t - tr.seen < this.ttlS);
    this.identify(t, headYawDeg);
  }

  /** Give every track a person id: the known person at that room direction, else a new one. */
  identify(t, headYawDeg) {
    this.people = this.people.filter((p) => t - p.seen < this.memoryS);
    const taken = new Set(this.tracks.filter((tr) => tr.pid != null && tr.seen === t).map((tr) => tr.pid));
    for (const tr of this.tracks) {
      if (tr.seen !== t) continue;   // not in this frame
      const yaw = headYawDeg == null ? null : headYawDeg + this.angleDeg(tr);
      if (tr.pid == null) {
        let best = null;
        if (yaw != null) {
          for (const p of this.people) {
            const d = Math.abs(p.yaw - yaw);
            if (!taken.has(p.pid) && d < this.reIdDeg && (!best || d < Math.abs(best.yaw - yaw))) best = p;
          }
        }
        if (!best) best = this.walkedOff(t, taken, yaw);
        if (!best) { best = { pid: this.nextPid++, yaw: yaw ?? 0, seen: t }; this.people.push(best); }
        tr.pid = best.pid;
        taken.add(best.pid);
      }
      const p = this.people.find((q) => q.pid === tr.pid);
      if (p) { if (yaw != null) p.yaw += 0.2 * (yaw - p.yaw); p.seen = t; }
    }
  }

  get(id) { return this.tracks.find((tr) => tr.id === id); }

  /**
   * A face shows up far from where anyone sat: if the person Reachy follows (or exactly one person) was
   * lost only seconds ago and is not in view, it is them, having walked to a new place.
   */
  walkedOff(t, taken, yaw) {
    const focus = this.people.find((p) => p.pid === this.focusPid && !taken.has(p.pid) && p.seen < t);
    if (focus && t - focus.seen < this.focusS && (yaw == null || Math.abs(focus.yaw - yaw) < this.focusDeg)) return focus;
    const lost = this.people.filter((p) => !taken.has(p.pid) && t - p.seen < this.lostS && p.seen < t);
    return lost.length === 1 ? lost[0] : null;
  }

  /** Mouth movement: standard deviation of jaw opening over the window (talking ~0.05-0.2, silent ~0.01). */
  activity(tr) {
    const v = tr.mouth.map(([, m]) => m);
    if (v.length < 3) return 0;
    const mean = v.reduce((a, b) => a + b, 0) / v.length;
    return Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / v.length);
  }

  /** Horizontal angle of the face centre, + = left. */
  angleDeg(tr) { return Math.atan((0.5 - tr.cx) * 2 * this.tanHalf) * 180 / Math.PI; }

  /** Most likely speaker track, or null when no face is visible. doaDeg may be null. */
  pick(doaDeg) {
    if (this.tracks.length <= 1) return this.tracks[0] ?? null;
    let best = null, bestScore = -1;
    for (const tr of this.tracks) {
      let score = Math.min(2, this.activity(tr) / 0.05);
      if (doaDeg != null) score += Math.max(0, 1 - Math.abs(this.angleDeg(tr) - doaDeg) / 30);
      if (score > bestScore) { bestScore = score; best = tr; }
    }
    return best;
  }
}
