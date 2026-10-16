// Clock sync with the caption backend (Cristian's algorithm): for the latency test across devices, where the QR
// is shown on one computer (qr.html) and decoded in another (the headset). Both sync to the backend's clock.
// Pure logic + timers: send(id) is given by the caller (the backend WebSocket), replies come in via handle().
//
// Each round: ~12 pings; the one with the shortest round trip wins (least queueing), and
//   offset = server time + rtt / 2 - local time at the reply      (local + offset = backend time)
// The true offset is within ± rtt / 2 of that (worst case: all of the delay on one leg).

const nowEpoch = () => performance.timeOrigin + performance.now();

export function createClockSync({ send, pings = 12, gapMs = 120, log = () => {} }) {
  const pending = new Map();   // id -> local send time
  let seq = 0, best = null, offset = null, rtt = null, round = null;

  function handle(msg) {
    const t0 = pending.get(msg.id);
    if (t0 == null || typeof msg.t !== "number") return;
    pending.delete(msg.id);
    const t1 = nowEpoch(), r = t1 - t0;
    if (!best || r < best.rtt) best = { rtt: r, offset: msg.t + r / 2 - t1 };
  }

  /** One sync round (~1.5 s). Resolves {offset, rtt} or null if the backend did not answer. */
  function sync() {
    if (round) return round;
    best = null;
    round = (async () => {
      for (let i = 0; i < pings; i++) {
        const id = `clk${++seq}`;
        pending.set(id, nowEpoch());
        if (send(id) === false) break;
        await new Promise((r) => setTimeout(r, gapMs));
      }
      await new Promise((r) => setTimeout(r, 300));
      pending.clear();
      round = null;
      if (!best) return null;
      offset = best.offset; rtt = best.rtt;
      log(`clock sync: offset ${offset.toFixed(1)} ms, accuracy ±${(rtt / 2).toFixed(1)} ms`);
      return { offset, rtt };
    })();
    return round;
  }

  return {
    handle, sync,
    /** local epoch ms + offset = backend epoch ms; null before the first successful sync */
    get offset() { return offset; },
    get rtt() { return rtt; },
    /** Backend time now (ms), or null. */
    now() { return offset == null ? null : nowEpoch() + offset; },
  };
}
