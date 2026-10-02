// Your voice -> the robot's speaker (two-way audio, like a FaceTime call). DOM/media only, no three.js.
//
// Works the same in the headset and in a desktop browser: getUserMedia picks the device's microphone
// (headset mic, or the Mac's mic when testing from the browser). The SDK negotiates an audio sender to
// the robot and feeds it a silent placeholder; we swap our mic track onto that sender. A watchdog
// re-attaches it after reconnects (the SDK then builds a new peer connection with a new sender).

export function createMic({ getPeerConnection, onStatus, log }) {
  let stream = null, track = null, muted = false, wanted = false, attachedTo = null;

  const state = () => (!wanted ? "off" : !track ? "starting" : !attachedTo ? "no channel" : muted ? "muted" : "on");
  const report = () => onStatus({ mic: state() });

  function audioSender(pc) {
    // The SDK adds exactly one audio sender (its placeholder, later our track).
    return pc.getSenders().find((s) => s.track?.kind === "audio")
      ?? pc.getTransceivers().find((t) => t.receiver.track?.kind === "audio" && /send/.test(t.direction))?.sender
      ?? null;
  }

  async function attach() {
    const pc = getPeerConnection();
    if (!wanted || !track || !pc) { attachedTo = null; report(); return; }
    const sender = audioSender(pc);
    if (!sender) { attachedTo = null; report(); return; }
    if (sender.track !== track) {
      try {
        await sender.replaceTrack(track);
        log("mic: sending to the robot speaker");
      } catch (e) {
        log("mic: replaceTrack failed:", e?.message ?? e);
        attachedTo = null; report(); return;
      }
    }
    attachedTo = sender;
    report();
  }
  setInterval(attach, 2000);   // reconnects, late SDP negotiation

  return {
    get muted() { return muted; },
    get state() { return state(); },

    /** Start the microphone. Call from a tap: the first time the browser asks for permission. */
    async start() {
      wanted = true;
      report();
      if (!track || track.readyState !== "live") {
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          });
        } catch (e) {
          log("mic: no access:", e?.message ?? e);
          wanted = false;
          onStatus({ mic: "blocked" });
          return false;
        }
        track = stream.getAudioTracks()[0];
        track.enabled = !muted;
        track.onended = () => { log("mic: track ended"); track = null; report(); };
        log("mic:", track.label || "default microphone");
      }
      await attach();
      return true;
    },

    /** Stop sending (robot asleep). The sender gets silence again. */
    stop() {
      wanted = false;
      attachedTo?.replaceTrack(null).catch(() => {});
      attachedTo = null;
      stream?.getTracks().forEach((t) => t.stop());
      stream = null; track = null;
      report();
    },

    setMuted(m) {
      muted = m;
      if (track) track.enabled = !m;   // disabled track = silence, the connection stays up
      log("mic", m ? "muted" : "on");
      report();
    },
  };
}
