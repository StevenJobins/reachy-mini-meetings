// Offline evaluation of speaker following: synthetic meetings replayed through the page's own logic
// (follow.js + speakers.js + speaker.js + camera.js), closed loop with a simulated robot head.
//
//   node xr-client/tools/sim_follow.mjs [--pages xr-client/pages] [--seeds 10] [--scenario all|two|three|side|backchannel|chewer] [-v]
//
// --pages lets you run an older copy of the page modules on the same scenarios (A/B, e.g. `git archive` of a
// previous commit). Everything is seeded: same seeds = same meeting, same noise.
//
// The simulated world (assumptions from the 2026-10-08 tests, see README "Speaker following"):
//   people   sit still at fixed room yaws (deg, + = left), heads ~8° above the camera axis
//   head     SpeakerTracker's rate limiter (80 °/s) drives the head; measured pose arrives 50 Hz, 0.08 s late
//   faces    backend vision at 19 Hz, calibrated camera (camera.json, 88.9° wide), stamped at capture,
//            3 % missed detections, 0.4 % jitter; jawOpen: silent 0.03 ± 0.01, talking 0.05-0.35 at ~4 syllables/s
//   VAD      speaking=true 0.2 s after speech starts, every 0.25 s, until 0.8 s after it ends (segmenter silence_s)
//   DoA      10 Hz, true direction ± 12° (gaussian); 25 % of readings a wall reflection (a fixed wrong direction per
//            person) ± 12°; the XVF3800 cannot tell front from back (|angle| > 90° is mirrored to the front);
//            while the head turns > 30 °/s, 50 % of readings point at the robot's own motor noise (random);
//            in silence the readings point at a fan at +70°
//   captions first partial 1.0 s after speech start, then every 0.6 s; the bubble picks its face once
//            (speakers.pick with the mean DoA of the utterance), like captions.js
//
// Metrics per utterance (ground truth speaker P), over [start, end + 1 s]:
//   acquire   s from speech start until the measured head is within 10° of P (and stays 0.5 s); median and 90th
//             percentile over all utterances, a miss (never within the utterance + 1 s) counts as ∞
//   on        share of [start + 1.5 s, end] the head is within 12° of P
//   wrong     the target went within 10° of someone who is not speaking (a turn to the wrong person)
//   back      the target went back to the previous speaker after having reached P
//   reversals target moves > 10° in opposite directions within 2 s (oscillation), as analyze_follow.py
//   moves     target changes > 4° (the page's "follow yaw" log lines)
//   travel    head travel per minute, and divided by the travel the speaker changes need (1.0 = no detours)

import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const here = dirname(fileURLToPath(import.meta.url));
const pagesDir = resolve(opt("--pages", resolve(here, "../pages")));
const SEEDS = Number(opt("--seeds", 10));
const ONLY = opt("--scenario", "all");
const VERBOSE = args.includes("-v");
const DUMP = args.includes("--dump");

const { SpeakerTracker } = await import(pathToFileURL(resolve(pagesDir, "speaker.js")));
const { FaceSpeakers } = await import(pathToFileURL(resolve(pagesDir, "speakers.js")));
const { Follow } = await import(pathToFileURL(resolve(pagesDir, "follow.js")));
const { CameraModel } = await import(pathToFileURL(resolve(pagesDir, "camera.js")));
const camJson = JSON.parse(readFileSync(resolve(here, "../pages/camera.json"), "utf8"));
// app.js may pass its own SpeakerTracker settings: read them from follow.js if it exports them, else app.js's old ones
const followMod = await import(pathToFileURL(resolve(pagesDir, "follow.js")));
const TRACKER = followMod.TRACKER_OPTIONS ?? { confirmN: 5, confirmWindowS: 1.0 };
// --set KEY=value,... overrides follow.js TUNING; --faces key=value,... FaceSpeakers options (parameter sweeps)
const kv = (s) => Object.fromEntries((s ?? "").split(",").filter(Boolean).map((x) => { const [k, v] = x.split("="); return [k, Number(v)]; }));
if (followMod.TUNING) Object.assign(followMod.TUNING, kv(opt("--set")));
const FACE_OPTS = kv(opt("--faces"));

const DEG = Math.PI / 180;

function rng(seed) {   // mulberry32
  let a = seed >>> 0;
  const next = () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  next.gauss = () => Math.sqrt(-2 * Math.log(1 - next())) * Math.cos(2 * Math.PI * next());
  next.range = (lo, hi) => lo + (hi - lo) * next();
  return next;
}

/** Turn-taking script: [{who, t0, t1}] for `n` people over `dur` s. */
function conversation(r, people, dur, { minS = 1.5, maxS = 5, gapLo = 0.3, gapHi = 1.6, backchannel = 0 } = {}) {
  const out = [];
  let t = 2, last = -1;
  while (t < dur - 2) {
    let who = Math.floor(r() * people.length);
    if (who === last) who = (who + 1) % people.length;
    const len = r.range(minS, maxS);
    out.push({ who, t0: t, t1: t + len });
    if (backchannel && people.length > 1) {   // short "mhm" by someone else in the middle (overlapping)
      for (let b = t + 1.2; b < t + len - 0.8; b += r.range(1.5, 3)) {
        if (r() < backchannel) out.push({ who: (who + 1 + Math.floor(r() * (people.length - 1))) % people.length, t0: b, t1: b + 0.35, back: true });
      }
    }
    last = who;
    t += len + r.range(gapLo, gapHi);
  }
  return out.sort((a, b) => a.t0 - b.t0);
}

const SCENARIOS = {
  two: { people: [-22, 24], conv: {} },
  three: { people: [-60, 0, 58], conv: {} },
  side: { people: [-35, 30, 105], conv: {} },
  backchannel: { people: [-28, 30], conv: { minS: 4, maxS: 9, backchannel: 0.8 } },
  chewer: { people: [-25, 28, -70], conv: {}, chewer: 2 },
};

function simulate(name, seed) {
  const sc = SCENARIOS[name];
  const r = rng(seed * 7919 + name.length * 104729);
  const dur = 90;
  const people = sc.people.map((yaw, i) => ({ yaw, up: 8 + r.range(-4, 4), reflect: yaw + r.range(-80, 80), amp: r.range(0.6, 1.2), i }));
  const utts = conversation(r, people, dur, sc.conv);
  const speaking = (i, t) => utts.some((u) => u.who === i && t >= u.t0 && t < u.t1);
  const anySpeech = (t) => utts.some((u) => t >= u.t0 && t < u.t1);

  const cam = new CameraModel(camJson);
  const lines = [];
  let now = 0;
  const speaker = new SpeakerTracker(TRACKER);
  const faces = new FaceSpeakers({ hfovDeg: cam.hfovDeg, camera: cam, ...FACE_OPTS });
  const follow = new Follow({ speaker, faces, camera: cam, log: (s) => lines.push([now, s]) });
  follow.awake = true;

  const dt = 0.02;
  const yawHist = [];   // [t, actual head yaw]
  const yawAt = (t) => { for (let i = yawHist.length - 1; i >= 0; i--) if (yawHist[i][0] <= t) return yawHist[i][1]; return 0; };
  const trace = [];     // [t, head yaw, target]
  let nextFace = 0, nextDoa = 0, nextVad = 0, vadOn = false;
  const bubbles = new Map();   // utterance -> {trackId, nextPartial}
  const chew = { until: -1 };

  for (let k = 0; now < dur; k++, now = k * dt) {
    const base = speaker.step(dt);
    follow.stepPitch(dt);
    yawHist.push([now, base]);
    if (yawHist.length > 400) yawHist.shift();
    const meas = yawAt(now - 0.08);
    follow.pushHead(now, follow.basePitch, meas);
    trace.push([now, base, speaker.target]);
    const headVel = yawHist.length > 5 ? Math.abs(base - yawHist[yawHist.length - 6][1]) / (5 * dt) : 0;

    // VAD (backend segmenter): on 0.2 s after a speech start, off 0.8 s after the last speech
    const heard = utts.some((u) => now >= u.t0 + 0.2 && now < u.t1 + 0.8);
    if (heard !== vadOn || (heard && now >= nextVad)) {
      vadOn = heard; nextVad = now + 0.25;
      follow.onVad(now, { speaking: heard });
    }

    // DoA, 10 Hz
    if (now >= nextDoa) {
      nextDoa = now + 0.1;
      const yaw = yawAt(now - 0.1);
      const talkers = people.filter((p) => speaking(p.i, now));
      let world;
      if (headVel > 30 && r() < 0.5) world = yaw + r.range(-90, 90);
      else if (talkers.length) {
        const p = talkers[Math.floor(r() * talkers.length)];
        world = (r() < 0.25 ? p.reflect : p.yaw) + 12 * r.gauss();
      } else world = 70 + 20 * r.gauss();
      let rel = ((world - yaw + 540) % 360) - 180;
      if (rel > 90) rel = 180 - rel;
      if (rel < -90) rel = -180 - rel;
      follow.onDoa(now, (90 - rel) * DEG, false);
    }

    // faces, 19 Hz, stamped at capture
    if (now >= nextFace) {
      nextFace = now + 1 / 19;
      const yaw = yawAt(now), pitch = follow.basePitch;
      const list = [];
      if (sc.chewer != null && now > chew.until && r() < 1 / (8 * 19)) chew.until = now + r.range(1, 2.5);
      for (const p of people) {
        const rel = p.yaw - yaw;
        if (Math.abs(rel) > cam.hfovDeg / 2 - 2 || r() < 0.03) continue;
        const upc = (p.up + pitch) * DEG;
        const uv = cam.project([-Math.sin(rel * DEG) * Math.cos(upc), -Math.sin(upc), Math.cos(rel * DEG) * Math.cos(upc)]);
        if (!uv || uv[0] < 0.02 || uv[0] > 0.98 || uv[1] < 0.05 || uv[1] > 0.95) continue;
        let mouth = 0.03 + 0.01 * r.gauss();
        if (speaking(p.i, now)) mouth = 0.05 + p.amp * 0.3 * Math.abs(Math.sin(2 * Math.PI * 4 * now + p.i)) * r.range(0.4, 1) + 0.02 * r.gauss();
        else if (p.i === sc.chewer && now < chew.until) mouth = 0.08 + 0.12 * Math.abs(Math.sin(2 * Math.PI * 1.5 * now));
        const w = 0.06, h = 0.1;
        list.push({ cx: uv[0] + 0.004 * r.gauss(), cy: uv[1] + 0.004 * r.gauss(), top: uv[1] - h / 2, w, h, mouth: Math.max(0, Math.min(1, mouth)) });
      }
      follow.onPeople(list, now, 0);
    }

    // captions: partials from 1.0 s after the start, every 0.6 s; the bubble picks its face once
    for (const [ui, u] of utts.entries()) {
      if (u.back && u.t1 - u.t0 < 0.4) continue;   // too short for Whisper text (min_s 0.4)
      const b = bubbles.get(ui) ?? { trackId: null, next: u.t0 + 1.0 };
      if (now < b.next || now > u.t1 + 0.3) { bubbles.set(ui, b); continue; }
      b.next = now + 0.6;
      const yaw = yawAt(now);
      const doa = [];
      for (const [t, a] of follow.doaBuf) if (t >= u.t0 && t <= now) doa.push(90 - a / DEG);
      const doaDeg = doa.length ? doa.reduce((x, y) => x + y, 0) / doa.length : null;
      if (b.trackId == null || !faces.get(b.trackId)) { const tr = faces.pick(doaDeg); b.trackId = tr?.id ?? null; }
      bubbles.set(ui, b);
      const msg = { final: false, t_start: u.t0, t_end: Math.min(now, u.t1), doa_deg: doaDeg };
      follow.onCaption(now, msg, b.trackId != null ? faces.get(b.trackId) ?? null : null, now - Math.min(now, u.t1) + 0.2);
      void yaw;
    }
  }
  if (DUMP) {
    const ev = [...utts.map((u) => [u.t0, `>>> p${u.who} (${people[u.who].yaw}°) speaks ${(u.t1 - u.t0).toFixed(1)} s${u.back ? " (backchannel)" : ""}`]),
      ...lines.filter(([, s]) => !s.startsWith("follow pitch"))].sort((a, b) => a[0] - b[0]);
    for (const [t, s] of ev) if (t < 40) console.log(t.toFixed(2).padStart(6), s, s.startsWith(">>>") ? "" : `head ${trace[Math.round(t / 0.02)]?.[1].toFixed(0)}`);
  }
  return evaluate(people, utts.filter((u) => !u.back), trace, lines, dur);
}

function evaluate(people, utts, trace, lines, dur) {
  const at = (t) => trace[Math.min(trace.length - 1, Math.max(0, Math.round(t / 0.02)))];
  const res = [];
  let prevWho = null;
  for (const u of utts) {
    const P = people[u.who].yaw;
    let acquire = null, holdFrom = null;
    for (let t = u.t0; t <= u.t1 + 1; t += 0.02) {
      const near = Math.abs(at(t)[1] - P) < 10;
      if (near && holdFrom == null) holdFrom = t;
      if (!near) holdFrom = null;
      if (holdFrom != null && t - holdFrom >= 0.5) { acquire = holdFrom - u.t0; break; }
    }
    let onN = 0, n = 0;
    for (let t = u.t0 + 1.5; t < u.t1; t += 0.02) { n++; if (Math.abs(at(t)[1] - P) < 12) onN++; }
    let wrong = 0, back = 0, reached = false, wasWrong = false, wasBack = false;
    for (let t = u.t0 + 0.3; t <= u.t1 + 1; t += 0.02) {
      const tg = at(t)[2];
      if (Math.abs(tg - P) < 10) reached = true;
      const w = people.some((p, i) => i !== u.who && Math.abs(p.yaw - P) > 20 && Math.abs(tg - p.yaw) < 10);
      if (w && !wasWrong) wrong++;
      wasWrong = w;
      const b = reached && prevWho != null && prevWho !== u.who && Math.abs(tg - people[prevWho].yaw) < 10;
      if (b && !wasBack) back++;
      wasBack = b;
    }
    const moves = lines.filter(([t, s]) => t >= u.t0 && t <= u.t1 + 1 && s.startsWith("follow yaw")).map(([t, s]) => [t, Number(s.split(" ")[2])]);
    res.push({ acquire, on: n ? onN / n : null, wrong, back, moves: moves.length, reversals: reversals(moves, at(u.t0)[2]) });
    prevWho = u.who;
  }
  let travel = 0, needed = 0;
  for (let i = 1; i < trace.length; i++) travel += Math.abs(trace[i][1] - trace[i - 1][1]);
  for (let i = 1; i < utts.length; i++) needed += Math.abs(people[utts[i].who].yaw - people[utts[i - 1].who].yaw);
  return { utts: res, travelPerMin: travel / (dur / 60), excess: travel / Math.max(1, needed) };
}

function reversals(moves, y0) {
  let n = 0, last = null, y = y0;
  for (const [t, yaw] of moves) {
    const d = yaw - y;
    if (Math.abs(d) > 10) {
      if (last && t - last[0] <= 2 && d * last[1] < 0) n++;
      last = [t, d];
    }
    y = yaw;
  }
  return n;
}

const median = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : NaN; };
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);

console.log(`pages: ${pagesDir}, seeds ${SEEDS}, tracker ${JSON.stringify(TRACKER)}`);
console.log("scenario      utts  acquire med/p90 s  miss %  on-target %  wrong/utt  back/utt  reversals/utt  moves/utt  travel °/min  travel/needed");
const totals = [];
for (const name of Object.keys(SCENARIOS)) {
  if (ONLY !== "all" && ONLY !== name) continue;
  const all = [], travel = [], excess = [];
  for (let s = 1; s <= SEEDS; s++) { const r = simulate(name, s); all.push(...r.utts); travel.push(r.travelPerMin); excess.push(r.excess); }
  totals.push(...all);
  const acq = all.map((u) => u.acquire ?? Infinity).sort((a, b) => a - b);   // a miss counts as never
  const p90 = acq[Math.floor(acq.length * 0.9)] ?? NaN;
  const row = [name.padEnd(12), String(all.length).padStart(5),
    `${median(acq).toFixed(2)} / ${p90.toFixed(2)}`.replaceAll("Infinity", "∞").padStart(18),
    (100 * all.filter((u) => u.acquire == null).length / all.length).toFixed(0).padStart(7),
    (100 * mean(all.map((u) => u.on).filter((x) => x != null))).toFixed(0).padStart(12),
    mean(all.map((u) => u.wrong)).toFixed(2).padStart(10), mean(all.map((u) => u.back)).toFixed(2).padStart(9),
    mean(all.map((u) => u.reversals)).toFixed(2).padStart(14), mean(all.map((u) => u.moves)).toFixed(1).padStart(10),
    mean(travel).toFixed(0).padStart(13), mean(excess).toFixed(2).padStart(8)];
  console.log(row.join(" "));
}
if (VERBOSE) console.log(JSON.stringify(totals.slice(0, 20)));
