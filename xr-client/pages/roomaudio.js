// Room sound from the caption backend (binary WebSocket frames: int16 PCM, 16 kHz mono), played with a jitter
// buffer and a speech filter. Replaces the robot's WebRTC audio, which drops ~55 % of the sound (daemon-side,
// 0 packets lost, measured with the status-line diagnostics). DOM/media only, no three.js.
//
// Quality notes (measured on the robot mic): it is natively 16 kHz (nothing above 8 kHz exists), and ~90 % of
// the energy is room hum below 1 kHz. So: the AudioContext runs at 16 kHz and the browser's own resampler does
// the upsampling; a high-pass removes the hum, a presence boost and a compressor make voices clear and even.

const SRC_RATE = 16000;

// AudioWorklet at 16 kHz: plain ring buffer, no resampling. Starts at TARGET, refills after an underrun (with a
// short fade, no click), and corrects clock drift and bursts by dropping single samples when above SLACK (1 in 25: 0.4 s extra delay is gone in
// ~10 s; 1 in 100 took 40 s, and the tunnel's TCP bursts kept the buffer near HARD = extra delay).
const WORKLET = `
const TARGET = ${0.12 * SRC_RATE}, SLACK = ${0.2 * SRC_RATE}, HARD = ${0.45 * SRC_RATE};
class RoomAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(${SRC_RATE * 4}); this.r = 0; this.w = 0; this.n = 0;
    this.playing = false; this.last = 0; this.underruns = 0; this.frames = 0; this.skip = 0;
    this.port.onmessage = ({ data }) => {
      for (let i = 0; i < data.length; i++) { this.buf[this.w] = data[i]; this.w = (this.w + 1) % this.buf.length; }
      this.n = Math.min(this.buf.length, this.n + data.length);
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0], L = this.buf.length;
    if (this.n > HARD) { const drop = this.n - TARGET; this.r = (this.r + drop) % L; this.n -= drop; }   // after a stall
    if (!this.playing && this.n >= TARGET) this.playing = true;
    for (let i = 0; i < out.length; i++) {
      if (!this.playing || this.n < 1) {
        if (this.playing) { this.playing = false; this.underruns++; }
        this.last *= 0.97;   // fade out instead of a click
        out[i] = this.last;
        continue;
      }
      if (this.n > SLACK && ++this.skip >= 25) { this.skip = 0; this.r = (this.r + 1) % L; this.n--; }   // drift
      this.last = out[i] = this.buf[this.r];
      this.r = (this.r + 1) % L; this.n--;
    }
    if (++this.frames % 25 === 0) this.port.postMessage({ bufMs: this.n / ${SRC_RATE / 1000}, underruns: this.underruns });
    return true;
  }
}
registerProcessor("room-audio", RoomAudio);
`;

export function createRoomAudio({ log }) {
  let ctx = null, node = null, out = null, enabled = false, lastPush = 0, bufMs = 0, underruns = 0, starting = null;

  async function ensure() {
    if (node) return;
    if (starting) return starting;
    starting = (async () => {
      ctx = new AudioContext({ sampleRate: SRC_RATE, latencyHint: "interactive" });
      await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" })));
      node = new AudioWorkletNode(ctx, "room-audio", { outputChannelCount: [1] });
      node.port.onmessage = ({ data }) => { bufMs = data.bufMs; underruns = data.underruns; };
      // speech chain: hum out, presence up, levels even
      const highpass = new BiquadFilterNode(ctx, { type: "highpass", frequency: 120, Q: 0.7 });
      const presence = new BiquadFilterNode(ctx, { type: "peaking", frequency: 3000, Q: 0.9, gain: 4 });
      const comp = new DynamicsCompressorNode(ctx, { threshold: -28, knee: 10, ratio: 3.5, attack: 0.005, release: 0.15 });
      out = new GainNode(ctx, { gain: 0 });
      node.connect(highpass).connect(presence).connect(comp).connect(out).connect(ctx.destination);
      out.gain.setTargetAtTime(enabled ? 1.8 : 0, ctx.currentTime, 0.02);   // 1.8: make-up gain after the compressor
      log("room audio: ready at", ctx.sampleRate, "Hz");
    })();
    return starting;
  }

  return {
    /** Call inside a tap the first time (browsers start audio only from a user gesture). */
    resume() { ensure().then(() => ctx.resume()).catch((e) => log("room audio:", e?.message ?? e)); },

    /** One binary frame from the caption WebSocket. */
    push(arrayBuffer) {
      lastPush = performance.now();
      if (!node) return;
      const pcm = new Int16Array(arrayBuffer), f = new Float32Array(pcm.length);
      for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 32768;
      node.port.postMessage(f, [f.buffer]);
    },

    setEnabled(on) {
      if (on === enabled) return;
      enabled = on;
      if (out) out.gain.setTargetAtTime(on ? 1.8 : 0, ctx.currentTime, 0.02);
      if (on) this.resume();
    },

    /** Audio arrived within the last 2 s: the stream is usable, so the WebRTC robot audio can stay off. */
    get live() { return performance.now() - lastPush < 2000; },

    stats() {
      if (!this.live) return "stream off";
      const outMs = ctx ? ((ctx.baseLatency ?? 0) + (ctx.outputLatency ?? 0)) * 1000 : 0;   // audio device / OS delay
      return `stream buf ${bufMs.toFixed(0)}ms out ${outMs.toFixed(0)}ms underruns ${underruns}`;
    },
  };
}
