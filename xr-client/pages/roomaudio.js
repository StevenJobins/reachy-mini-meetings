// Room sound from the caption backend (binary WebSocket frames: int16 PCM, 16 kHz mono), played with a small
// jitter buffer. Replaces the robot's WebRTC audio, which drops ~55 % of the sound (daemon-side, 0 packets
// lost, measured with the status-line diagnostics). DOM/media only, no three.js.

const SRC_RATE = 16000;

// AudioWorklet: ring buffer + linear resampling 16 kHz -> device rate. Starts playing at TARGET of buffered
// audio, re-buffers after an underrun, and skips ahead when the buffer grows (clock drift) so latency stays low.
const WORKLET = `
const TARGET = ${0.08 * SRC_RATE}, MAX = ${0.3 * SRC_RATE};
class RoomAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(${SRC_RATE * 4}); this.r = 0; this.w = 0; this.n = 0;
    this.playing = false; this.pos = 0; this.step = ${SRC_RATE} / sampleRate; this.underruns = 0; this.frames = 0;
    this.port.onmessage = ({ data }) => {
      for (let i = 0; i < data.length; i++) { this.buf[this.w] = data[i]; this.w = (this.w + 1) % this.buf.length; }
      this.n = Math.min(this.buf.length, this.n + data.length);
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0], L = this.buf.length;
    if (!this.playing && this.n >= TARGET) this.playing = true;
    for (let i = 0; i < out.length; i++) {
      if (!this.playing || this.n < 2) {
        out[i] = 0;
        if (this.playing) { this.playing = false; this.underruns++; }
        continue;
      }
      const a = this.buf[this.r], b = this.buf[(this.r + 1) % L];
      out[i] = a + (b - a) * this.pos;
      this.pos += this.step;
      while (this.pos >= 1) { this.pos -= 1; this.r = (this.r + 1) % L; this.n--; }
    }
    if (this.n > MAX) { const drop = this.n - TARGET; this.r = (this.r + drop) % L; this.n -= drop; }
    if (++this.frames % 50 === 0) this.port.postMessage({ bufMs: this.n / ${SRC_RATE / 1000}, underruns: this.underruns });
    return true;
  }
}
registerProcessor("room-audio", RoomAudio);
`;

export function createRoomAudio({ log }) {
  let ctx = null, node = null, gain = null, enabled = false, lastPush = 0, bufMs = 0, underruns = 0, starting = null;

  async function ensure() {
    if (node) return;
    if (starting) return starting;
    starting = (async () => {
      ctx = new AudioContext({ latencyHint: "interactive" });
      await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" })));
      node = new AudioWorkletNode(ctx, "room-audio", { outputChannelCount: [1] });
      node.port.onmessage = ({ data }) => { bufMs = data.bufMs; underruns = data.underruns; };
      gain = ctx.createGain();
      gain.gain.value = enabled ? 1 : 0;
      node.connect(gain).connect(ctx.destination);
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
      enabled = on;
      if (gain) gain.gain.setTargetAtTime(on ? 1 : 0, ctx.currentTime, 0.02);
      if (on) this.resume();
    },

    /** Audio arrived within the last 2 s: the stream is usable, so the WebRTC robot audio can stay off. */
    get live() { return performance.now() - lastPush < 2000; },

    stats() { return this.live ? `stream buf ${bufMs.toFixed(0)}ms underruns ${underruns}` : "stream off"; },
  };
}
