// Robot connection through Pollen's JS SDK: HF sign-in -> central signalling -> WebRTC
// (video + set_target on the data channel + measured pose stream). No three.js, no DOM except the <video>.

import { ReachyMini, rpyToMatrix, matrixToRpy } from "reachy";
import { DEG } from "./pose.js";

const SILENT_TRIED = "reachy-xr-silent-signin-tried";

/**
 * The SDK uses the current URL as OAuth redirect_uri, and HF only accepts the exact registered URL
 * (page URL without query/hash, see xr-client/README.md). A failed silent sign-in comes back with
 * ?error=..., so strip query, hash and "index.html" right before every login redirect.
 */
function cleanUrlForLogin() {
  const path = location.pathname.replace(/index\.html$/, "");
  if (location.search || location.hash || path !== location.pathname) history.replaceState(null, "", path);
}

const CONNECT_TIMEOUT_S = 15;

export function createRobot({ clientId, onStatus, onMeasuredHead, onDoa = () => {}, log }) {
  const reachy = new ReachyMini({ clientId, appName: "Reachy Meetings XR", videoJitterBufferTargetMs: 0 });
  let streaming = false, lastPitch = null;   // measured head pitch (deg, + = down)

  // Reload / close: end the session, else the daemon keeps the robot locked for this (gone) page and the
  // reloaded page finds it busy.
  addEventListener("pagehide", () => { reachy.stopSession().catch(() => {}); });
  reachy.addEventListener("iceStateChange", (e) => onStatus({ ice: e.detail?.state }));
  reachy.addEventListener("sessionStopped", (e) => {
    streaming = false;
    onStatus({ robot: "stopped" });
    log("session stopped", JSON.stringify(e.detail));
  });
  reachy.addEventListener("sessionReconnecting", () => onStatus({ robot: "reconnecting" }));
  reachy.addEventListener("sessionReconnected", () => { streaming = true; onStatus({ robot: "connected" }); });
  reachy.addEventListener("error", (e) => log("error", JSON.stringify(e.detail)));
  reachy.addEventListener("state", (e) => {
    // mic array: {angle (rad, 0 = left, π/2 = front, π = right), speech_detected} (DoaSnapshot in the daemon)
    const doa = e.detail?.doa;
    if (doa && typeof doa.angle === "number") onDoa(doa.angle, !!doa.speech_detected);
    const h = e.detail?.head;
    if (!h || h.length !== 16) return;
    const { roll, pitch, yaw } = matrixToRpy([h.slice(0, 4), h.slice(4, 8), h.slice(8, 12), h.slice(12, 16)]);
    lastPitch = pitch;
    onMeasuredHead(roll, pitch, yaw);
  });

  return {
    /**
     * Resolve the sign-in state. Not signed in yet -> one silent attempt (OAuth prompt=none):
     * users who already authorized the app come straight back signed in, without any screen.
     * Returns "signed-in", "signed-out", or "redirecting" (page is about to navigate away).
     */
    async signInState() {
      const ok = await reachy.authenticate().catch((e) => { log("auth", e?.message ?? e); return false; });
      if (ok) { sessionStorage.removeItem(SILENT_TRIED); return "signed-in"; }
      if (!clientId || sessionStorage.getItem(SILENT_TRIED)) return "signed-out";
      sessionStorage.setItem(SILENT_TRIED, "1");
      cleanUrlForLogin();
      await reachy.login({ prompt: "none" });
      return "redirecting";
    },

    signIn() { cleanUrlForLogin(); return reachy.login(); },
    signOut() { reachy.logout(); sessionStorage.removeItem(SILENT_TRIED); },

    attachVideo(video) {
      reachy.attachVideo(video);   // before connecting: the videoTrack event fires during session setup
      reachy.addEventListener("videoTrack", () => {
        onStatus({ video: "track" });
        video.play().catch((e) => log("video.play:", e?.message ?? e));
      });
    },

    /** Connect to the robot. Single free robot -> picked automatically; several -> first free one. */
    async connect() {
      onStatus({ robot: "connecting" });
      // A session request that reaches the daemon while it is still starting is accepted (the robot gets
      // locked for us) but its video session never starts: the page waited forever and every new attempt
      // found the robot busy ("No reachable robots"; daemon log "Pending sessions: 1", 2026-10-08). So give
      // up after CONNECT_TIMEOUT_S, end that session (frees the lock) and let the caller retry.
      let timer;
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("Reachy did not answer (session stuck), retrying")), CONNECT_TIMEOUT_S * 1000);
      });
      let res;
      try {
        res = await Promise.race([reachy.autoConnect({
          wakeOnConnect: false,   // the robot stays asleep until the user taps Start (wake())
          pickRobot: async (robots) => {
            log("robots:", robots.map((r) => `${r.name ?? r.id}${r.busy ? " (busy)" : ""}`).join(", "));
            return robots.find((r) => !r.busy)?.id ?? null;   // TODO picker when the team has several robots
          },
        }), timeout]);
      } catch (e) {
        await reachy.stopSession().catch(() => {});
        throw e;
      } finally {
        clearTimeout(timer);
      }
      reachy.subscribePose();
      streaming = true;
      onStatus({ robot: res.robotName ?? res.robotId });
      return res;
    },

    get connected() { return streaming; },

    /** The Hugging Face sign-in token (for the caption server's tunnel). The SDK keeps it in memory; its
     *  sessionStorage copy was missing on the headset, so the tunnel refused it as "no account". */
    get token() {
      try { return reachy._token ?? sessionStorage.getItem("hf_token"); } catch { return reachy._token ?? null; }
    },

    /** Live RTCPeerConnection (null between sessions; replaced on reconnect, so re-read it each time). */
    get peerConnection() { return reachy.peerConnection; },

    /** Robot speaker volume 0-100. Resolves with the volume the daemon reports (or null). */
    setVolume(percent) { return reachy.setVolume(percent); },
    getVolume() { return reachy.getVolume(); },

    /** Robot microphone on/off (the audio track of the video element). Turn on inside a user gesture. */
    setAudio(on) { reachy.setAudioMuted(!on); },

    /** Wake the robot (plays the wake-up motion, motors on). Resolves when it is ready for head targets. */
    async wake() {
      onStatus({ motors: "waking" });
      await reachy.ensureAwake(3000);
      // ensureAwake only looks at the motor mode: after a daemon restart the motors came up enabled with the
      // head still in the sleep pose (pitch ~26° down), so it did nothing and Reachy stayed slumped (2026-10-08).
      if (lastPitch != null && lastPitch > 18) {
        log(`wake: motors on but head in sleep pose (pitch ${lastPitch.toFixed(0)}°), playing wake-up`);
        await reachy.wakeUp({ timeoutMs: 5000 }).catch((e) => log("wake-up:", e?.message ?? e));
      }
      onStatus({ motors: "awake" });
    },

    /** Back to the sleep pose, then motors off. Never throws: the user may already be gone. */
    async sleep() {
      onStatus({ motors: "sleeping" });
      try {
        await reachy.gotoSleep();
        reachy.setMotorMode("disabled");
        onStatus({ motors: "asleep" });
      } catch (e) {
        log("goto sleep:", e?.message ?? e);
      }
    },

    /** Head target in degrees (robot frame), body yaw and antennas [right, left] in degrees. Returns true if queued. */
    setHead({ roll, pitch, yaw, bodyYaw, antennas = [0, 0] }) {
      if (!streaming) return false;
      return reachy.setTarget({
        head: rpyToMatrix(roll, pitch, yaw).flat(),
        antennas: antennas.map((a) => a / DEG),   // SDK order: [right, left]
        body_yaw: bodyYaw / DEG,
      });
    },
  };
}
