// Your voice -> the robot's speaker (two-way audio, like a FaceTime call). DOM/media only, no three.js.
//
// Works the same in the headset and in a desktop browser: getUserMedia picks the device's microphone
// (headset mic, or the Mac's mic when testing from the browser). The SDK negotiates an audio sender to
// the robot and feeds it a silent placeholder; we swap our mic track onto that sender. A watchdog
// re-attaches it after reconnects (the SDK then builds a new peer connection with a new sender).
//
// "Translate me": your voice goes to the caption backend instead (onVoice: int16 PCM, 16 kHz mono, 40 ms frames),
// which translates it into the meeting language and lets Reachy say it; the robot gets silence meanwhile.

const VOICE_WORKLET = `
class VoiceTap extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor("voice-tap", VoiceTap);
`;

export function createMic({ getPeerConnection, onStatus, onVoice = () => {}, log }) {
  let stream = null, track = null, muted = false, wanted = false, attachedTo = null, translate = false;
  let voiceCtx = null;
  let audioCtx = null, analyser = null, levelBuf = null, lastBytes = 0, kbps = 0, packets = 0, announced = false;

  // Diagnostics: how loud the mic is (0..1) and how much audio actually leaves towards the robot.
  setInterval(async () => {
    if (!attachedTo) { kbps = 0; return; }
    try {
      const stats = await attachedTo.getStats();
      stats.forEach((r) => {
        if (r.type !== "outbound-rtp") return;
        kbps = Math.max(0, (r.bytesSent - lastBytes) * 8 / 1000);
        lastBytes = r.bytesSent; packets = r.packetsSent;
      });
      if (!announced && packets > 50) { announced = true; log(`mic: audio is flowing to the robot (${packets} packets)`); }
      onStatus({ micKbps: kbps });
    } catch {}
  }, 1000);

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
    const want = translate ? null : track;   // translating: Reachy speaks for you, your own voice stays off
    if (sender.track !== want) {
      try {
        await sender.replaceTrack(want);
        log(want ? "mic: sending to the robot speaker" : "mic: translating, nothing direct to the robot");
      } catch (e) {
        log("mic: replaceTrack failed:", e?.message ?? e);
        attachedTo = null; report(); return;
      }
    }
    attachedTo = sender;
    report();
  }
  setInterval(attach, 2000);   // reconnects, late SDP negotiation

  /** Your voice at 16 kHz for the backend (Chrome resamples the mic into a 16 kHz context). */
  async function startTap() {
    if (voiceCtx || !stream) return;
    voiceCtx = new AudioContext({ sampleRate: 16000 });
    await voiceCtx.audioWorklet.addModule(URL.createObjectURL(new Blob([VOICE_WORKLET], { type: "text/javascript" })));
    const node = new AudioWorkletNode(voiceCtx, "voice-tap");
    const frame = new Int16Array(640);
    let n = 0;
    node.port.onmessage = ({ data }) => {
      if (!translate || muted) { n = 0; return; }
      for (const v of data) {
        frame[n++] = Math.max(-32768, Math.min(32767, v * 32768));
        if (n === frame.length) { onVoice(frame.slice().buffer); n = 0; }
      }
    };
    voiceCtx.createMediaStreamSource(stream).connect(node);
    voiceCtx.resume().catch(() => {});
  }
  function stopTap() { voiceCtx?.close().catch(() => {}); voiceCtx = null; }

  const api = {
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
        try {
          audioCtx = new AudioContext();
          audioCtx.resume().catch(() => {});   // may start suspended outside a tap
          analyser = audioCtx.createAnalyser();
          analyser.fftSize = 512;
          levelBuf = new Float32Array(analyser.fftSize);
          audioCtx.createMediaStreamSource(stream).connect(analyser);
        } catch { analyser = null; }
        track.enabled = !muted;
        track.onended = () => {
          // device gone (e.g. the iPhone's Continuity mic disconnected): take the current default mic again
          log("mic: track ended, restarting with the default microphone");
          track = null; report();
          if (wanted) setTimeout(() => { if (wanted && !track) api.start(); }, 500);
        };
        log("mic:", track.label || "default microphone");
        stopTap();   // new stream: rebuild the voice tap on it
      }
      if (translate) startTap().catch((e) => log("mic: voice tap failed:", e?.message ?? e));
      await attach();
      return true;
    },

    /** Stop sending (robot asleep). The sender gets silence again. */
    stop() {
      wanted = false;
      attachedTo?.replaceTrack(null).catch(() => {});
      attachedTo = null;
      stream?.getTracks().forEach((t) => t.stop());
      audioCtx?.close().catch(() => {});
      stopTap();
      stream = null; track = null; audioCtx = null; analyser = null; announced = false; lastBytes = 0;
      report();
    },

    /** Mic level 0..1 (RMS, roughly: 0.05 quiet room, 0.3+ speaking). 0 when muted. */
    level() {
      if (!analyser || muted) return 0;
      analyser.getFloatTimeDomainData(levelBuf);
      let sum = 0;
      for (const v of levelBuf) sum += v * v;
      return Math.min(1, Math.sqrt(sum / levelBuf.length) * 4);
    },

    get kbps() { return kbps; },

    get translate() { return translate; },
    /** Translate me on/off (call from a tap: starts audio). */
    setTranslate(on) {
      translate = on;
      if (on) startTap().catch((e) => log("mic: voice tap failed:", e?.message ?? e));
      else stopTap();
      log("mic: translate", on ? "on" : "off");
      attach();
    },

    setMuted(m) {
      muted = m;
      audioCtx?.resume().catch(() => {});
      if (track) track.enabled = !m;   // disabled track = silence, the connection stays up
      log("mic", m ? "muted" : "on");
      report();
    },
  };
  return api;
}
