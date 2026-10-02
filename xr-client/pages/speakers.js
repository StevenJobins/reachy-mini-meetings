// Who is speaking: face tracks in the robot camera image + mouth movement + microphone direction (DoA).
// Pure logic: no DOM, no three.js (portable to C#/Kotlin).
//
// Faces come in normalized image coordinates (0..1, origin top left) from faces.js, several times a second.
// Angles: degrees, + = left of the camera axis, like doa_deg from backend/ (see backend/README.md).

export class FaceSpeakers {
  constructor({ hfovDeg, windowS = 1.5, maxJump = 0.15, ttlS = 1.0 } = {}) {
    this.tanHalf = Math.tan((hfovDeg / 2) * Math.PI / 180);
    this.windowS = windowS;   // mouth movement is judged over this window
    this.maxJump = maxJump;   // max face movement between two detections (fraction of the image)
    this.ttlS = ttlS;         // a face not seen for this long is dropped
    this.tracks = [];
    this.nextId = 1;
  }

  /** faces = [{cx, cy, top, w, h, mouth}], t = seconds. Matches faces to tracks by nearest centre. */
  update(faces, t) {
    const free = new Set(this.tracks);
    for (const f of faces) {
      let best = null, bestD = this.maxJump;
      for (const tr of free) {
        const d = Math.hypot(tr.cx - f.cx, tr.cy - f.cy);
        if (d < bestD) { bestD = d; best = tr; }
      }
      if (best) free.delete(best);
      else { best = { id: this.nextId++, mouth: [] }; this.tracks.push(best); }
      Object.assign(best, { cx: f.cx, cy: f.cy, top: f.top, w: f.w, h: f.h, seen: t });
      best.mouth.push([t, f.mouth]);
      while (best.mouth.length && best.mouth[0][0] < t - this.windowS) best.mouth.shift();
    }
    this.tracks = this.tracks.filter((tr) => t - tr.seen < this.ttlS);
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
