// Speaker following: who talks and where Reachy looks. Pure logic: no DOM, no three.js, time passed in (seconds),
// so xr-client/tools/sim_follow.mjs can replay scenarios through exactly this code in node.
//
// Inputs (app.js wires them): measured head pose, mic direction (DoA), the backend's VAD events and captions,
// faces (speakers.js tracks). Output: speaker.target (yaw, via speaker.js's SpeakerTracker, which also smooths
// it into the base yaw) and targetPitch / basePitch (framing).

const PITCH_UP = 35, PITCH_DOWN = 20;   // same as HeadMirror's limits in pose.js
const PITCH_DEAD = 6, PITCH_GAIN = 0.5, EDGE_SEARCH_MAX = 10;   // deg; half the error per face update

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
    this.doaPushedUntil = 0;
    this.focusLast = null;    // {pid, top, bottom, t, yaw, pitch} of the focus face when last seen
    this.focusVel = 0;        // its speed through the room, deg/s (smoothed)
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

  /** Mic direction reading (SDK convention: 0 = left, π/2 = front, π = right). Buffered, and only used for
   *  speech the backend confirms (neural VAD + text): noise never turns the robot. */
  onDoa(now, angle, speech) {
    this.doaBuf.push([now, angle, speech]);
    while (this.doaBuf.length && this.doaBuf[0][0] < now - 6) this.doaBuf.shift();
    if (this.awake && now - this.lastSpeechS < 1.5) this.flushDoa(now, now - 1.5);
  }

  /** Feed the buffered mic directions since `fromS` into the speaker tracker (each reading once, in order). */
  flushDoa(now, fromS) {
    // speech = true: the backend confirmed speech for this time span; the mic array's own speech flag is
    // false most of the time (measured: 8 of 8 readings while someone talked), the angle is still good.
    // While the followed person is in the picture, the mic direction (±10-20°, plus wall reflections) only
    // counts when it points clearly outside the picture: someone out of view speaks. Inside the picture the
    // face (and mouth movement) is far more precise; letting both steer made the head twitch and turn away.
    const focus = this.focusTrack();
    // The followed person moves their mouth: they are the one talking, the mic direction has nothing to add
    // (in the test it jumped between -121° and +43° while the face sat still at -20°).
    const focusTalking = focus && this.faces.activity(focus) > 0.03;
    for (const [t, a] of this.doaBuf) {
      if (t <= this.doaPushedUntil || t < fromS) continue;
      if (focusTalking) continue;
      if (focus && Math.abs(90 - a * 180 / Math.PI) < this.camera.hfovDeg / 2 + 10) continue;
      this.speaker.pushDoa(t, a, true);
    }
    this.doaPushedUntil = now;
    this.noteTarget("mic");
  }

  /** Track of the person Reachy follows if they are in the picture (seen within the last 0.5 s), else null. */
  focusTrack() {
    if (this.focusPid == null) return null;
    return this.faces.tracks.find((t) => t.pid === this.focusPid && (this.faces.lastT ?? 0) - t.seen < 0.5) ?? null;
  }

  /** Instant "someone is speaking" from the backend's neural VAD (~0.1 s after the first word, no text yet):
   *  the mic directions count right away, and the speaking face in view becomes the focus person. */
  onVad(now, msg) {
    if (msg.speaking && this.awake && now - this.lastSpeechS > 1.5) this.log("speech start");   // for measuring the turn delay
    if (!msg.speaking || !this.awake) return;
    this.lastSpeechS = now;
    this.flushDoa(now, now - 0.6);
    const last = this.doaBuf[this.doaBuf.length - 1];
    const doaRel = last ? 90 - last[1] * 180 / Math.PI : null;   // relative to the head, + = left
    const faces = this.faces;
    const tr = faces.pick(doaRel);   // mouth movement + mic direction
    if (tr && tr.seen === faces.lastT) {
      this.setFocus(tr.pid, now);
    } else if (!faces.tracks.some((t) => (faces.lastT ?? 0) - t.seen < 1.5)
               && (!this.focusLast || (faces.lastT ?? 0) - this.focusLast.t > 1.5)
               && (doaRel == null || Math.abs(doaRel) < 35)) {
      // (no face for 1.5 s: a detection flicker must not trigger this, it made the pitch twitch)
      // Someone talks in front of Reachy but no face is in the picture: their head is above it (standing, or
      // close to the robot). Look up step by step (~12 °/s at 4 events/s) until the face shows up.
      this.targetPitch = Math.max(-PITCH_UP, this.targetPitch - 3);
      this.noteTarget("search up (speech, no face)");
    }
  }

  /** A caption (partial or final) arrived; track = the face its bubble sits on, or null. ageS = s since the
   *  caption's speech ended (backend clock mapped onto this page). */
  onCaption(now, msg, track, ageS) {
    // Only steer on captions about speech going on NOW: a final comes ~0.8 s + Whisper after the last word, its
    // translation even seconds later; steering on those pulled the head back to the previous speaker while
    // the next one already talked (code review 2026-10-08).
    if ((msg.final && msg.translation) || ageS > 1.2) return;
    this.lastSpeechS = now;
    if (!this.awake) return;
    // the caption confirms speech for its whole duration: use the mic directions from that time
    this.flushDoa(now, now - (msg.t_end - msg.t_start) - 0.7);
    if (!track) return;
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
