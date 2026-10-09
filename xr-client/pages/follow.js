// Speaker following: who talks and where Reachy looks. Pure logic: no DOM, no three.js, time passed in (seconds),
// so xr-client/tools/sim_follow.mjs can replay scenarios through exactly this code in node.
//
// Inputs (app.js wires them): measured head pose, mic direction (DoA), the backend's VAD events and captions,
// faces (speakers.js tracks). Output: speaker.target (yaw, via speaker.js's SpeakerTracker, which also smooths
// it into the base yaw) and targetPitch / basePitch (framing).

const PITCH_UP = 35, PITCH_DOWN = 20;   // same as HeadMirror's limits in pose.js
const PITCH_DEAD = 6, PITCH_GAIN = 0.5, EDGE_SEARCH_MAX = 10;   // deg; half the error per face update
// Who talks (tuned with xr-client/tools/sim_follow.mjs, numbers in xr-client/README.md "Speaker following")
export const TUNING = {
  DOA_LATENCY_S: 0.15,   // a mic reading refers to the head yaw this long ago (as in speaker.js)
  DOA_WINDOW_S: 1.0,     // mic direction = densest cluster of this many seconds of readings ...
  DOA_SPREAD: 15,        // ... within ± this (deg) ...
  DOA_MIN: 5,            // ... with at least this many readings (and half of all)
  DOA_SIGMA: 15,         // deg: a face this far from the mic direction gets 0.6 of the mic bonus
  TALK_MIN: 0.03,        // jaw std above this = mouth moves (talking ~0.05-0.2, silent ~0.01)
  SWITCH_MARGIN: 0.5,    // a new speaker's score must beat the followed person's by this ...
  SWITCH_HOLD_S: 0.4,    // ... for this long (half when the followed person's mouth is still)
  MIC_TURN_GAP_S: 1.5,   // after a turn towards the voice, give the faces this long to show up
};
const T = TUNING;

export class Follow {
  /** speaker = SpeakerTracker (speaker.js), faces = FaceSpeakers (speakers.js), camera = camera.js model. */
  constructor({ speaker, faces, camera, log = () => {} }) {
    Object.assign(this, { speaker, faces, camera, log });
    this.awake = false;
    this.pitchHist = [];      // [t, measured pitch]
    this.faceLatencyS = 0.12; // headset faces lag the pose stream; backend faces are stamped at capture (0)
    this.lastLoggedYaw = 0;
    this.lastLoggedPitch = 0;
    this.reset();
  }

  reset() {
    this.focusPid = null;     // the person Reachy follows (speakers.js pid)
    this.basePitch = 0;
    this.targetPitch = 0;
    this.lastSpeechS = 0;
    this.doaBuf = [];         // [t, angle, speech] of the last seconds
    this.focusLast = null;    // {pid, top, bottom, t, yaw, pitch} of the focus face when last seen
    this.focusVel = 0;        // its speed through the room, deg/s (smoothed)
    this.vadOn = false;
    this.lastVadS = -1e9;     // last VAD "speaking" event
    this.speechSinceS = 0;    // start of the current stretch of speech
    this.candidate = null;    // {pid, since}: a face that would take over the focus (hysteresis)
    this.micTurnS = -1e9;     // last turn towards the mic direction
  }

  /** Follow diagnostics: who moved the target (face / mic / search / talk), logged when it jumps > 4°. */
  noteTarget(source) {
    if (Math.abs(this.speaker.target - this.lastLoggedYaw) > 4) { this.log(`follow yaw ${this.speaker.target.toFixed(0)} by ${source}`); this.lastLoggedYaw = this.speaker.target; }
    if (Math.abs(this.targetPitch - this.lastLoggedPitch) > 4) { this.log(`follow pitch ${this.targetPitch.toFixed(0)} by ${source}`); this.lastLoggedPitch = this.targetPitch; }
  }

  /** Measured head pose (degrees) at time t. */
  pushHead(t, pitch, yaw) {
    this.pitchHist.push([t, pitch]);
    if (this.pitchHist.length > 100) this.pitchHist.shift();
    this.speaker.pushHeadYaw(t, yaw);
  }

  /** Framing pitch for this tick, smoothly at ≤ 30 °/s. */
  stepPitch(dt) {
    this.basePitch += Math.max(-30 * dt, Math.min(30 * dt, this.targetPitch - this.basePitch));
    return this.basePitch;
  }

  /** Robot head yaw when a camera frame was taken (t = capture time, s): frames lag the pose stream by ~0.1-0.2 s,
   *  and using the current yaw for an old frame overshoots while the robot turns. Headset faces are stamped when
   *  the frame was grabbed from a lagging <video> (~0.12 s behind the pose stream), backend faces at capture. */
  headYawAt(t, latencyS = this.faceLatencyS) {
    const h = this.speaker.headHist;
    for (let i = h.length - 1; i >= 0; i--) if (h[i][0] <= t - latencyS) return h[i][1];
    return h.length ? h[0][1] : 0;
  }

  /** Same for the pitch: correcting an old frame's face position against the CURRENT pitch made the head
   *  overshoot and nod up and down (headset log 2026-10-08: target -26° -> -4° -> -26° -> +17° within 12 s). */
  headPitchAt(t, latencyS = this.faceLatencyS) {
    const h = this.pitchHist;
    for (let i = h.length - 1; i >= 0; i--) if (h[i][0] <= t - latencyS) return h[i][1];
    return h.length ? h[0][1] : 0;
  }

  /** Mic direction reading (SDK convention: 0 = left, π/2 = front, π = right), stored with the room direction
   *  it points to (head yaw T.DOA_LATENCY_S earlier + angle). Used only while the backend confirms speech. */
  onDoa(now, angle, speech) {
    const rel = 90 - angle * 180 / Math.PI;   // relative to the head, + = left
    this.doaBuf.push([now, angle, speech, this.headYawAt(now, T.DOA_LATENCY_S) + rel]);
    while (this.doaBuf.length && this.doaBuf[0][0] < now - 6) this.doaBuf.shift();
  }

  /**
   * Where the mic says the voice is (room yaw), or null: the densest cluster of the readings of the last
   * T.DOA_WINDOW_S while speech was confirmed. A single reading is ±10-20° and a quarter of them are wall
   * reflections (headset log 2026-10-08: -121° and +43° while the speaker sat still at -20°), so it needs
   * T.DOA_MIN readings, and at least half of all readings within ±T.DOA_SPREAD of each other.
   */
  doaDirection(now) {
    const ys = this.doaBuf.filter(([t]) => t >= now - T.DOA_WINDOW_S && t >= this.speechSinceS - 0.3).map((r) => r[3]);
    let best = null, bestN = 0;
    for (const y of ys) {
      const near = ys.filter((z) => Math.abs(z - y) <= T.DOA_SPREAD);
      if (near.length > bestN) { bestN = near.length; best = near.reduce((a, b) => a + b, 0) / near.length; }
    }
    return bestN >= T.DOA_MIN && bestN >= 0.5 * ys.length ? best : null;
  }

  /** Track of the person Reachy follows if they are in the picture (seen within the last 0.5 s), else null. */
  focusTrack() {
    if (this.focusPid == null) return null;
    return this.faces.tracks.find((t) => t.pid === this.focusPid && (this.faces.lastT ?? 0) - t.seen < 0.5) ?? null;
  }

  /** Room yaw of a face track (latest frame). */
  faceYaw(tr) { return this.headYawAt(tr.seen) + this.faces.angleDeg(tr); }

  /**
   * Instant "someone is speaking" from the backend's neural VAD (~0.2 s after the first word, then every 0.25 s
   * while it lasts, no text yet). Decides who talks:
   *  - faces in the picture: speaking score = mouth movement + agreement with the mic direction; a new person
   *    takes over only after winning for T.SWITCH_HOLD_S (no ping-pong between two faces);
   *  - nobody in the picture moves their mouth and the mic points outside the picture: turn there once and
   *    let the faces take over when the person comes into view (the mic never fights a talking face).
   */
  onVad(now, msg) {
    if (!msg.speaking) { this.vadOn = false; return; }
    if (!this.vadOn && now - this.lastSpeechS > 1.5) { this.speechSinceS = now; if (this.awake) this.log("speech start"); }
    this.vadOn = true;
    this.lastVadS = now;
    this.lastSpeechS = now;
    if (!this.awake) return;
    const faces = this.faces;
    const doa = this.doaDirection(now);
    const visible = faces.tracks.filter((t) => t.seen === faces.lastT);
    let best = null, bestScore = -1;
    for (const tr of visible) {
      const s = this.score(tr, doa);
      if (s > bestScore) { bestScore = s; best = tr; }
    }
    const focus = visible.find((t) => t.pid === this.focusPid) ?? null;
    const talking = best && faces.activity(best) > T.TALK_MIN;
    const half = this.camera.hfovDeg / 2;
    const head = this.headYawAt(now, 0);
    const doaOutside = doa != null && Math.abs(doa - head) > half - 5;
    // A talking face in the picture is the speaker, unless the mic clearly says someone outside talks and the
    // face does not match it (someone chewing or smiling while the speaker sits out of view)
    if (talking && !(doaOutside && Math.abs(this.faceYaw(best) - doa) > 35 && bestScore < 2.5)) {
      this.challenger = null;
      if (best.pid === this.focusPid) { this.candidate = null; return; }
      const focusScore = focus ? this.score(focus, doa) : -1;
      if (focus && bestScore < focusScore + T.SWITCH_MARGIN) { this.candidate = null; return; }
      if (this.candidate?.pid !== best.pid) this.candidate = { pid: best.pid, since: now };
      const hold = !focus || faces.activity(focus) < T.TALK_MIN / 2 ? T.SWITCH_HOLD_S / 2 : T.SWITCH_HOLD_S;
      if (now - this.candidate.since >= hold || this.focusPid == null) { this.candidate = null; this.setFocus(best.pid, now); }
      return;
    }
    this.candidate = null;
    if (doaOutside && now - this.micTurnS > T.MIC_TURN_GAP_S && Math.abs(this.speaker.base - this.speaker.target) < 8) {
      // the speaker is out of view: turn towards the voice (the faces take over once they are in the picture)
      if (Math.abs(doa - this.speaker.target) > 10) {
        this.micTurnS = now;
        this.focusPid = null;
        this.speaker.target = Math.max(-150, Math.min(150, doa));
        this.speaker.speakers.push([now, this.speaker.target]);
        this.noteTarget("mic");
      }
      return;
    }
    if (!faces.tracks.some((t) => (faces.lastT ?? 0) - t.seen < 1.5)
        && (!this.focusLast || (faces.lastT ?? 0) - this.focusLast.t > 1.5)
        && (doa == null || Math.abs(doa - head) < 35)) {
      // (no face for 1.5 s: a detection flicker must not trigger this, it made the pitch twitch)
      // Someone talks in front of Reachy but no face is in the picture: their head is above it (standing, or
      // close to the robot). Look up step by step (~12 °/s at 4 events/s) until the face shows up.
      this.targetPitch = Math.max(-PITCH_UP, this.targetPitch - 3);
      this.noteTarget("search up (speech, no face)");
    }
  }

  /** Speaking score of a face: mouth movement (0..3) + how well it matches the mic direction (0..1). */
  score(tr, doa) {
    let s = Math.min(3, this.faces.activity(tr) / 0.05);
    if (doa != null) s += Math.exp(-0.5 * ((this.faceYaw(tr) - doa) / T.DOA_SIGMA) ** 2);
    return s;
  }

  /** A caption (partial or final) arrived; track = the face its bubble sits on, or null. ageS = s since the
   *  caption's speech ended (backend clock mapped onto this page). */
  onCaption(now, msg, track, ageS) {
    // Only captions about speech going on NOW: a final comes ~0.8 s + Whisper after the last word, its
    // translation even seconds later (code review 2026-10-08).
    if ((msg.final && msg.translation) || ageS > 1.2) return;
    this.lastSpeechS = now;
    // The bubble picks its face once, at its first partial (~1 s in, often before the speaker is in view), and
    // keeps it: steering on it fought the VAD decision every 0.6 s (simulator: face ping-pong). Only without
    // VAD events (older backend) does the caption decide.
    if (!this.awake || !track || now - this.lastVadS < 2) return;
    this.setFocus(track.pid, now);
  }

  setFocus(pid, now) {
    const prev = this.focusPid;
    this.focusPid = pid;
    this.frameFocus();
    if (pid !== prev) this.speaker.speakers.push([now, this.speaker.target]);   // the NEW speaker's direction ("I want to talk")
  }
  /** Faces of one camera frame (t = capture time on this clock). */
  onPeople(list, t, latencyS = this.faceLatencyS) {
    this.faceLatencyS = latencyS;
    this.faces.focusPid = this.focusPid;
    this.faces.update(list, t, this.headYawAt(t));
    this.frameFocus();
  }

  // Framing: Reachy keeps the person who spoke last (also while everyone is quiet) in the picture, head
  // centred left/right and 1/3 from the top, like a camera operator. World direction of the face =
  // measured head pose + its angle in the image, so the user's own headset rotation stays on top.
  frameFocus() {
    if (!this.awake || this.focusPid == null) return;
    const faces = this.faces, speaker = this.speaker, cam = this.camera;
    const tr = faces.tracks.find((t) => t.pid === this.focusPid && t.seen === faces.lastT);
    if (!tr) {
      // A face cut off at the image edge is no longer detected: if the focus person left at the top (or
      // bottom), keep tilting that way for a moment until their face is back in the picture.
      const t = faces.lastT ?? 0, fl = this.focusLast;
      if (fl && t - fl.t < 2.5) {   // at most EDGE_SEARCH_MAX beyond where the face was last seen
        if (fl.top < 0.12) this.targetPitch = Math.max(-PITCH_UP, fl.pitch - EDGE_SEARCH_MAX, this.targetPitch - 1);
        else if (fl.bottom > 0.9) this.targetPitch = Math.min(PITCH_DOWN, fl.pitch + EDGE_SEARCH_MAX, this.targetPitch + 1);
        this.noteTarget("edge search");
      }
      return;
    }
    const t = faces.lastT;
    const offX = faces.angleDeg(tr);                     // face left/right of the image centre (deg)
    const yawNow = this.headYawAt(t) + offX;             // where the face is in the room
    // Lead a moving person: the target only updates ~8x/s and the motion limiter brakes at every target, so
    // without a lead Reachy lags behind a walking person. Room speed of the face, smoothed; detections jitter
    // by a few percent, so below 8 °/s it counts as standing still (no lead, no jitter).
    const fl = this.focusLast;
    if (fl?.pid === this.focusPid && t > fl.t && t - fl.t < 0.5) {
      this.focusVel += 0.25 * ((yawNow - fl.yaw) / (t - fl.t) - this.focusVel);
    } else this.focusVel = 0;
    this.focusLast = { pid: this.focusPid, top: tr.top, bottom: tr.top + tr.h, t, yaw: yawNow, pitch: this.targetPitch };
    const moving = Math.abs(this.focusVel) > 8;
    // Dead zone around the framing point: a face that is already well placed does not move the head at all.
    if (moving || Math.abs(offX) > 4) {
      const lead = moving ? Math.max(-15, Math.min(15, this.focusVel * 0.3)) : 0;
      const yaw = Math.max(-150, Math.min(150, yawNow + lead));
      if (Math.abs(yaw - speaker.target) > 2) speaker.target = yaw;
    }
    const up = cam.upDeg(tr.cx, tr.cy);                  // face above the image centre (deg)
    const FRAME_UP = cam.upDeg(0.5, 1 / 3);              // head 1/3 from the top = this far above the axis
    if (Math.abs(up - FRAME_UP) > PITCH_DEAD) {
      const pitch = this.headPitchAt(t) - (up - FRAME_UP);   // pitch + = look down
      this.targetPitch += PITCH_GAIN * (Math.max(-PITCH_UP, Math.min(PITCH_DOWN, pitch)) - this.targetPitch);
    }
    this.noteTarget(`face p${tr.pid} off ${offX.toFixed(0)}°`);
  }
}
