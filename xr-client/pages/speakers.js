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
  constructor({ hfovDeg, windowS = 1.5, maxJump = 0.15, ttlS = 1.0, reIdDeg = 15, memoryS = 900 } = {}) {
    this.tanHalf = Math.tan((hfovDeg / 2) * Math.PI / 180);
    this.windowS = windowS;   // mouth movement is judged over this window
    this.maxJump = maxJump;   // max face movement between two detections (fraction of the image)
    this.ttlS = ttlS;         // a face not seen for this long is dropped
    this.tracks = [];
    this.nextId = 1;
    this.reIdDeg = reIdDeg;   // same person if within this many degrees of where they were
    this.memoryS = memoryS;   // forget people not seen for this long
    this.people = [];         // [{pid, yaw (room direction, degrees), seen}]
    this.nextPid = 1;
  }

  /**
   * faces = [{cx, cy, top, w, h, mouth}] (head boxes, mouth may be null), t = seconds, headYawDeg = robot head yaw
   * in the room when the frame was taken (null if unknown). Nearest-centre matching, then identity by direction.
   */
  update(faces, t, headYawDeg = null) {
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
        if (!best) { best = { pid: this.nextPid++, yaw: yaw ?? 0, seen: t }; this.people.push(best); }
        tr.pid = best.pid;
        taken.add(best.pid);
      }
      const p = this.people.find((q) => q.pid === tr.pid);
      if (p) { if (yaw != null) p.yaw += 0.2 * (yaw - p.yaw); p.seen = t; }
    }
  }

  get(id) { return this.tracks.find((tr) => tr.id === id); }

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
